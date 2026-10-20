"""对画：员工账号对话生图。只在一台电脑上跑，密钥不出浏览器。"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import secrets
import socket
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import image
import llm
import memory
import mindmap
import store
import router
from prompts import (
    _prompt_with_size,
    composite_prompt,
    edit_prompt,
    mask_edit_prompt,
    reference_prompt,
    size_only_prompt,
)

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
ENV_PATH = ROOT / ".env"

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env()
API_KEY = os.environ.get("MIGE_API_KEY", "").strip()
BASE_URL = os.environ.get("MIGE_BASE_URL", "https://api.migeapi.com").rstrip("/")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8765"))
MINDMAP_DAILY_LIMIT = max(1, int(os.environ.get("MINDMAP_DAILY_LIMIT") or 60))
UPLOAD_QUOTA_MB = max(20, int(os.environ.get("UPLOAD_QUOTA_MB") or 300))
UPLOAD_MAX_FILES = max(20, int(os.environ.get("UPLOAD_MAX_FILES") or 400))
UPLOAD_KEEP_DAYS = max(1, int(os.environ.get("UPLOAD_KEEP_DAYS") or 30))
ADMIN_USER = os.environ.get("ADMIN_USER", "admin").strip() or "admin"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

LOGIN_FAILS: dict[str, list[float]] = {}
LOGIN_LOCK = threading.Lock()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()
    return salt, digest


def check_password(password: str, salt: str, digest: str) -> bool:
    _, got = hash_password(password, salt)
    return secrets.compare_digest(got, digest)


def ensure_admin() -> None:
    if not ADMIN_PASSWORD:
        raise SystemExit("请在 .env 里设置 ADMIN_PASSWORD")
    existing = store.get_user_by_name(ADMIN_USER)
    if existing:
        return
    salt, digest = hash_password(ADMIN_PASSWORD)
    store.create_user(ADMIN_USER, salt, digest, role="admin")


def lan_ips() -> list[str]:
    found = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        found.append(sock.getsockname()[0])
        sock.close()
    except Exception:
        pass
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = item[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except Exception:
        pass
    return found


def log(text: str) -> None:
    line = time.strftime("%H:%M:%S ") + text
    print(line, flush=True)
    try:
        store.DATA.mkdir(parents=True, exist_ok=True)
        with (store.DATA / "server.log").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def _basis_text(basis: str, history: list, current: str) -> str:
    quoted = (basis or "").strip()
    if quoted and quoted != current.strip():
        for item in history:
            if item.get("role") == "user" and quoted in (item.get("text") or ""):
                return quoted
    for item in reversed(history[:-1]):
        said = (item.get("text") or "").strip()
        if item.get("role") == "user" and said and not image.is_realism_redo(said):
            return said
    return ""


def resolve_intent(user: dict, chat_id: str, text: str, history: list, ui: dict, has_previous: bool, has_refs: bool, uploads: list, urls: list) -> dict | None:
    """The language model only chooses the task. The picture prompt stays the user's words."""
    intent, tried = llm.classify(API_KEY, BASE_URL, llm.build_user_text(text, history[:-1], ui, has_previous))
    if not intent:
        return None
    if intent["action"] == "chat":
        return {
            "plan": {"action": "chat", "reply": intent.get("reply") or "好。", "prompt": "", "use_previous_image": False, "keep": intent.get("keep") or [], "change": intent.get("change") or []},
            "tried": tried,
            "editing": False,
            "fresh": True,
            "size_only": False,
            "source_image": "",
        }
    task = intent["task"]
    canvas = intent["canvas"]
    photo = intent["medium"] == "photo"
    model = ui.get("image_model") or ""
    reply = intent.get("reply") or "按你的要求处理。"
    previous = last_generated(user["id"], chat_id) if has_previous else None
    ref_count = len(uploads) + len(urls)

    def pack(plan: dict, editing: bool = False, fresh: bool = False, size_only: bool = False, source: str = "", ui_size: str = "", resolution: str = "") -> dict:
        plan["keep"] = intent.get("keep") or []
        plan["change"] = intent.get("change") or []
        return {
            "plan": plan,
            "tried": tried,
            "editing": editing,
            "fresh": fresh,
            "size_only": size_only,
            "source_image": source,
            "ui_size": ui_size,
            "resolution": resolution,
        }

    if task == "new":
        return pack(
            {"action": "generate", "reply": reply, "prompt": image.faithful_prompt(text, photo), "use_previous_image": False},
            fresh=True,
        )
    if task == "realism":
        subject = _basis_text(intent.get("basis") or "", history, text)
        merged = (subject + "\n用户补充：" + text.strip()).strip() if subject else text.strip()
        return pack(
            {"action": "generate", "reply": reply or "按原来的主题重画成真实照片。", "prompt": image.faithful_prompt(merged, True), "use_previous_image": False},
            fresh=True,
        )
    if task == "size":
        if not previous:
            return None
        spoken = image.parse_resolution(text)
        ui_size = "auto" if image.parse_requested_ratio(text) else (previous.get("size") or "")
        return pack(
            {
                "action": "generate",
                "reply": "按上一张图只改尺寸，画面内容保持不变。",
                "prompt": size_only_prompt(previous.get("prompt") or "", text),
                "use_previous_image": True,
            },
            editing=True,
            size_only=True,
            source=previous["name"],
            ui_size=ui_size,
            resolution=spoken,
        )
    if task in {"swap_person", "insert"}:
        if not previous:
            return None
        return pack(
            {
                "action": "generate",
                "reply": reply,
                "prompt": composite_prompt(model, text, ref_count, task == "swap_person"),
                "use_previous_image": True,
            },
            editing=True,
            source=previous["name"],
            ui_size=previous.get("size") or "",
        )
    if canvas == "upload" or (has_refs and canvas != "previous"):
        prompt = reference_prompt(model, text, ref_count) if task == "text" else edit_prompt(model, text, ref_count)
        return pack(
            {"action": "generate", "reply": reply, "prompt": prompt, "use_previous_image": False},
            fresh=True,
        )
    if previous:
        ui_size = "auto" if image.parse_requested_ratio(text) else (previous.get("size") or "")
        return pack(
            {
                "action": "generate",
                "reply": reply,
                "prompt": edit_prompt(model, text, ref_count),
                "use_previous_image": True,
            },
            editing=True,
            source=previous["name"],
            ui_size=ui_size,
        )
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")

    def do_PUT(self):
        self._route("PUT")

    def _route(self, method: str) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = urllib.parse.unquote(parsed.path)
            if path.startswith("/api/"):
                self._api(method, path)
            elif path == "/healthz":
                self._json(200, {"ok": True})
            elif path.startswith("/media/"):
                self._media(path)
            else:
                self._static(path)
        except Exception as exc:
            log("error " + repr(exc))
            traceback.print_exc()
            self._json(500, {"error": "服务器内部错误"})

    def _api(self, method: str, path: str) -> None:
        user = self._user()
        if path == "/api/login" and method == "POST":
            self._login()
            return
        if path == "/api/logout" and method == "POST":
            store.delete_auth(self._token())
            self._json(200, {"ok": True}, clear_cookie=True)
            return
        if not user:
            self._json(401, {"error": "请先登录"})
            return
        if path == "/api/me" and method == "GET":
            self._json(200, {"user": store.user_public(user)})
            return
        if path == "/api/password" and method == "POST":
            self._change_password(user)
            return
        if path == "/api/bootstrap" and method == "GET":
            self._json(200, self._bootstrap(user))
            return
        if path == "/api/sessions" and method == "GET":
            self._json(200, {"sessions": store.list_chats(user["id"])})
            return
        if path == "/api/sessions" and method == "POST":
            body = self._read_json()
            title = str(body.get("title") or "新对话")
            self._json(200, {"session": present_chat(store.create_chat(user["id"], title))})
            return
        match = re.fullmatch(r"/api/sessions/([0-9a-f]+)", path)
        if match and method == "GET":
            self._session(user, match.group(1))
            return
        if match and method == "DELETE":
            chat_id = match.group(1)
            files = []
            for message in store.list_messages(user["id"], chat_id):
                info = (message.get("body") or {}).get("image") or {}
                for name in info.get("local_names") or []:
                    files.append(name)
            ok = store.delete_chat(user["id"], chat_id)
            if ok:
                folder = store.DATA / "images" / user["id"]
                for name in files:
                    if not re.fullmatch(r"[A-Za-z0-9._-]+", str(name)):
                        continue
                    path = folder / str(name)
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            self._json(200 if ok else 404, {"ok": ok})
            return
        if path == "/api/chat" and method == "POST":
            self._chat(user)
            return
        if path == "/api/retry" and method == "POST":
            self._retry(user)
            return
        if path == "/api/upload" and method == "POST":
            self._upload(user)
            return
        if path == "/api/mindmaps" and method == "GET":
            self._json(200, {"maps": store.list_mindmaps(user["id"])})
            return
        if path == "/api/mindmaps" and method == "POST":
            self._create_mindmap(user)
            return
        export_match = re.fullmatch(r"/api/mindmaps/([0-9a-f]+)/export", path)
        if export_match and method == "POST":
            self._export_mindmap(user, export_match.group(1))
            return
        regen_match = re.fullmatch(r"/api/mindmaps/([0-9a-f]+)/regenerate", path)
        if regen_match and method == "POST":
            self._regenerate_mindmap(user, regen_match.group(1))
            return
        mind_match = re.fullmatch(r"/api/mindmaps/([0-9a-f]+)", path)
        if mind_match and method == "GET":
            row = store.get_mindmap(user["id"], mind_match.group(1))
            if not row:
                self._json(404, {"error": "没有这张导图"})
                return
            self._json(200, {"map": store.mindmap_public(row)})
            return
        if mind_match and method == "PUT":
            self._update_mindmap(user, mind_match.group(1))
            return
        if mind_match and method == "DELETE":
            self._delete_mindmap(user, mind_match.group(1))
            return
        if user["role"] != "admin":
            self._json(403, {"error": "需要管理员"})
            return
        if path == "/api/admin/users" and method == "GET":
            self._json(200, {"users": [store.user_public(row) for row in store.list_users()]})
            return
        if path == "/api/admin/users" and method == "POST":
            self._create_employee()
            return
        user_match = re.fullmatch(r"/api/admin/users/([0-9a-f]+)", path)
        if user_match and method == "POST":
            self._update_employee(user_match.group(1))
            return
        if path == "/api/admin/chain" and method == "POST":
            body = self._read_json()
            chain = llm.save_chain(body.get("chain") or [])
            self._json(200, {"chain": chain})
            return
        if path == "/api/admin/cooldown/clear" and method == "POST":
            llm.clear_health()
            self._json(200, {"chain": llm.chain_status()})
            return
        self._json(404, {"error": "没有这个接口"})

    def _login(self) -> None:
        ip = self.client_address[0]
        if self._limited(ip):
            self._json(429, {"error": "试错太多，请一分钟后再试"})
            return
        body = self._read_json()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        row = store.get_user_by_name(username)
        if not row or row["disabled"] or not check_password(password, row["salt"], row["password_hash"]):
            self._fail(ip)
            self._json(401, {"error": "账号或密码不对"})
            return
        token = store.create_auth(row["id"])
        self._json(200, {"user": store.user_public(row)}, cookie=token)

    def _change_password(self, user: dict) -> None:
        body = self._read_json()
        old = str(body.get("old") or "")
        new = str(body.get("new") or "")
        if len(new) < 6:
            self._json(400, {"error": "新密码至少 6 位"})
            return
        if not check_password(old, user["salt"], user["password_hash"]):
            self._json(400, {"error": "原密码不对"})
            return
        salt, digest = hash_password(new)
        store.update_user(user["id"], salt=salt, password_hash=digest)
        self._json(200, {"ok": True})

    def _create_employee(self) -> None:
        body = self._read_json()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not re.fullmatch(r"[A-Za-z0-9_\-\u4e00-\u9fff]{2,32}", username):
            self._json(400, {"error": "用户名用 2 到 32 位中文、字母或数字"})
            return
        if len(password) < 6:
            self._json(400, {"error": "密码至少 6 位"})
            return
        if store.get_user_by_name(username):
            self._json(400, {"error": "这个用户名已经有了"})
            return
        limit = body.get("daily_limit")
        daily = int(limit) if str(limit).isdigit() else None
        salt, digest = hash_password(password)
        row = store.create_user(username, salt, digest, role="employee", daily_limit=daily)
        self._json(200, {"user": store.user_public(row)})

    def _update_employee(self, user_id: str) -> None:
        row = store.get_user(user_id)
        if not row:
            self._json(404, {"error": "没有这个账号"})
            return
        body = self._read_json()
        fields = {}
        if "disabled" in body:
            fields["disabled"] = 1 if body.get("disabled") else 0
        if "daily_limit" in body:
            limit = body.get("daily_limit")
            fields["daily_limit"] = int(limit) if str(limit).isdigit() else None
        if body.get("password"):
            if len(str(body["password"])) < 6:
                self._json(400, {"error": "密码至少 6 位"})
                return
            salt, digest = hash_password(str(body["password"]))
            fields["salt"] = salt
            fields["password_hash"] = digest
        store.update_user(user_id, **fields)
        self._json(200, {"user": store.user_public(store.get_user(user_id))})

    def _bootstrap(self, user: dict) -> dict:
        return {
            "user": store.user_public(user),
            "image_models": image.IMAGE_MODELS,
            "chain": llm.chain_status(),
            "usage_today": store.usage_today(user["id"]),
            "key_hint": API_KEY[-4:] if API_KEY else "",
        }

    def _session(self, user: dict, chat_id: str) -> None:
        chat = store.get_chat(user["id"], chat_id)
        if not chat:
            self._json(404, {"error": "没有这组对话"})
            return
        self._json(200, {"session": present_chat(chat), "messages": store.list_messages(user["id"], chat_id)})

    def _chat(self, user: dict) -> None:
        if not API_KEY:
            self._json(500, {"error": "服务器还没配置密钥"})
            return
        body = self._read_json()
        text = str(body.get("text") or "").strip()
        if not text:
            self._json(400, {"error": "先写一句要画什么"})
            return
        if store.pending_count(user["id"]) >= 2:
            self._json(429, {"error": "还有两张在画，等它们完成再发"})
            return
        limit = user.get("daily_limit")
        if limit and store.usage_today(user["id"]) >= int(limit):
            self._json(429, {"error": "今天的张数已经用完"})
            return
        chat_id = str(body.get("session_id") or "")
        chat = store.get_chat(user["id"], chat_id) if chat_id else None
        if not chat:
            chat = store.create_chat(user["id"], text)
            chat_id = chat["id"]
        elif chat["title"] in {"新对话", ""}:
            store.touch_chat(chat_id, text)
        uploads = body.get("upload_ids") or []
        if not isinstance(uploads, list):
            uploads = []
        urls = [item for item in (body.get("reference_urls") or []) if isinstance(item, str) and item.startswith("http")]
        source_image = str(body.get("source_image") or "")
        if source_image and not owned_image(user["id"], source_image):
            self._json(400, {"error": "这张已生成的图不存在"})
            return
        annotated = bool(body.get("annotated"))
        store.add_message(
            chat_id,
            user["id"],
            "user",
            {
                "text": text,
                "attachments": uploads[:5],
                "reference_urls": urls[:5],
                "source_image": source_image,
                "annotated": annotated,
            },
        )
        ui = {
            "image_model": body.get("image_model"),
            "size": body.get("size") or "auto",
            "resolution": body.get("resolution") or "",
            "quality": body.get("quality") or "",
            "n": body.get("n") or 1,
            "use_previous": bool(body.get("use_previous")) or bool(source_image),
            "attachment_count": len(uploads) + len(urls),
            "annotated": annotated,
            "source_image": source_image,
        }
        chosen_model = str(body.get("image_model") or "")
        routed_model = image.pick_model(text, chosen_model, annotated=annotated, editing=bool(source_image))
        ui["image_model"] = routed_model
        ui["model_auto"] = chosen_model not in image.ALLOWED_MODELS
        use_llm = body.get("use_llm") is not False
        history = []
        for message in store.list_messages(user["id"], chat_id)[-8:]:
            item = message["body"]
            history.append(
                {
                    "role": message["role"],
                    "text": item.get("text") or "",
                    "prompt": item.get("prompt") or "",
                }
            )
        has_previous = any((message["body"].get("image") or {}).get("local_names") for message in store.list_messages(user["id"], chat_id))
        has_refs = bool(uploads or urls)
        tried = []
        orchestrated = False
        size_only = False
        fresh = False
        editing = False
        direct_draw = (
            not annotated
            and not source_image
            and not has_refs
            and image.is_direct_generate(text)
        )
        routed = None
        if use_llm and not annotated and not source_image and not direct_draw:
            routed = resolve_intent(user, chat_id, text, history, ui, has_previous, has_refs, uploads, urls)
        if routed:
            plan = routed['plan']
            tried = routed['tried']
            orchestrated = True
            size_only = routed['size_only']
            editing = routed['editing']
            fresh = routed['fresh']
            if routed.get('source_image') and not source_image:
                source_image = routed['source_image']
                ui['use_previous'] = True
                ui['source_image'] = source_image
            if routed.get('ui_size'):
                ui['size'] = routed['ui_size']
            if routed.get('resolution'):
                ui['resolution'] = routed['resolution']
        else:
            decision = router.decide(
                {
                    "text": text,
                    "annotated": annotated,
                    "has_previous": has_previous,
                    "uploads": uploads,
                    "urls": urls,
                    "source_image": source_image,
                    "previous": last_generated(user["id"], chat_id) if has_previous else None,
                    "meta": generated_meta(user["id"], chat_id, source_image) if source_image else {},
                    "model": ui.get("image_model") or "",
                    "history": history,
                    "ui_size": ui.get("size") or "",
                    "ui_use_previous": bool(ui["use_previous"]),
                }
            )
            plan = decision["plan"]
            fresh = decision["fresh"]
            editing = decision["editing"]
            size_only = decision["size_only"]
            if decision.get("source_image") and not source_image:
                source_image = decision["source_image"]
                ui["use_previous"] = True
                ui["source_image"] = source_image
            if decision.get("ui_size"):
                ui["size"] = decision["ui_size"]
            if decision.get("ui_resolution"):
                ui["resolution"] = decision["ui_resolution"]
            if plan:
                orchestrated = True
            elif use_llm:
                plan, tried = llm.orchestrate(
                    API_KEY,
                    BASE_URL,
                    llm.build_user_text(text, history[:-1], ui, has_previous),
                )
                orchestrated = plan is not None
            else:
                plan = None
        if not plan:
            plan = {
                "action": "generate",
                "reply": "编排暂时不可用，已按原话生成。" if use_llm else "按原话生成。",
                "prompt": text[:4000],
                "use_previous_image": bool(ui["use_previous"]),
            }
        if not fresh and (ui["use_previous"] or source_image):
            plan["use_previous_image"] = True
        if source_image and plan.get("action") != "generate":
            plan["action"] = "generate"
            plan["prompt"] = text[:4000]
            plan["reply"] = plan.get("reply") or "按你的标注修改。"
        if plan["action"] == "chat":
            message = store.add_message(
                chat_id,
                user["id"],
                "assistant",
                {"text": plan.get("reply") or "好。", "llm": {"tried": tried}},
            )
            self._json(200, {"session": present_chat(store.get_chat(user["id"], chat_id)), "message": message, "chain": llm.chain_status()})
            return
        crop_box = None
        crop_px = None
        crop_files = {}
        refs: list[str] = []
        prompt = plan.get("prompt") or text
        if not orchestrated and not source_image and not annotated:
            prompt = image.faithful_prompt(prompt)
        mask_id = ""
        if annotated:
            raw_mask = str(body.get("mask_id") or "")
            if re.fullmatch(r"[A-Za-z0-9._-]+", raw_mask):
                candidate = store.DATA / "uploads" / user["id"] / raw_mask
                if candidate.is_file():
                    mask_id = raw_mask
            if not editing:
                prompt = mask_edit_prompt(ui.get("image_model") or "", prompt)
            if mask_id:
                boxes = image.mask_boxes(store.DATA / "uploads" / user["id"] / mask_id)
                if boxes:
                    words = "、".join(image.region_words(boxes))
                    prompt += f"\n标注区域共 {len(boxes)} 处（{words}），每一处都要按指令改到位，不要漏。"
                    prompt += "\n标注以外的地方会被原图覆盖，所以只需要把标注处改对。"
                if source_image and owned_image(user["id"], source_image):
                    canvas_path = store.DATA / "images" / user["id"] / source_image
                    mask_path = store.DATA / "uploads" / user["id"] / mask_id
                    box = image.mask_union(mask_path)
                    if box:
                        area = (box[2] - box[0]) * (box[3] - box[1])
                        if 0.02 <= area <= 0.7:
                            temps = store.DATA / "temps" / user["id"]
                            temps.mkdir(parents=True, exist_ok=True)
                            token = store.new_id()
                            crop_canvas = temps / f"{token}-canvas.png"
                            px = image.crop_to(canvas_path, box, crop_canvas)
                            if px:
                                crop_box = box
                                crop_px = px
                                crop_files = {"canvas": crop_canvas, "token": token}
                                refs = [image.data_url_for(crop_canvas)]
                                raw_annotation = str(body.get("annotation_id") or "")
                                if re.fullmatch(r"[A-Za-z0-9._-]+", raw_annotation):
                                    source = store.DATA / "uploads" / user["id"] / raw_annotation
                                    if source.is_file():
                                        target = temps / f"{token}-mark.jpg"
                                        if image.crop_to(source, box, target):
                                            crop_files["mark"] = target
                                            refs.append(image.data_url_for(target))
                                target_mask = temps / f"{token}-mask.png"
                                if image.crop_to(mask_path, box, target_mask):
                                    crop_files["mask"] = target_mask
                                    refs.append(image.data_url_for(target_mask))
                                removing = bool(re.search(r"(删除|删掉|去掉|去除|移除|抹掉|擦掉|清掉|拿掉|盖掉)", text))
                                prompt = (
                                    "这是一整张图被放大的局部（第 1 张），不是新图。只把标注处改成："
                                    + text.strip()
                                    + "\n这一块的比例、光线和其余内容都保持原样，输出只给这块区域。"
                                )
                                if len(refs) > 1:
                                    prompt += "\n后面几张是同一块的标注图和蒙版，标出的就是可改范围。"
                                if removing:
                                    prompt += "\n标注范围内的东西要消失，被遮住的地方按周围纹理和光线补全自然。"

        forced_refs = refs if crop_px else None
        if use_llm and not source_image and not annotated and not has_refs and image.is_vague(text):
            extra = llm.enhance(API_KEY, BASE_URL, text)
            if extra:
                prompt = (prompt.rstrip() + "\n" + extra).strip()
                plan["reply"] = ((plan.get("reply") or "") + " 原话之外补了几句画面细节。").strip()
        remembered = memory.load(store.read_memory(chat_id))
        sources = [text] + [item.get("text") or "" for item in history]
        keep, change = memory.pick(text, plan.get("keep"), plan.get("change"), sources, editing=not fresh)
        if not fresh and not re.search(r"(换成|改成|不要|去掉|删除)", text):
            keep = memory.merge(keep, remembered.get("keep") or [], change)
        prompt = memory.attach(prompt, keep, change)
        plan["reply"] = memory.with_reply(plan.get("reply") or "", keep, change)
        plan["_memory_keep"] = keep
        model = ui["image_model"] if ui["image_model"] in image.ALLOWED_MODELS else image.IMAGE_MODELS[0]["id"]
        if ui.get("model_auto"):
            plan["reply"] = ((plan.get("reply") or "") + f" 这类任务自动用了 {image.model_label(model)}。").strip()
        size, size_note = image.resolve_size(text, ui.get("size") or "auto", plan.get("size"), model)
        if not size and (ui.get("size") in {"", "auto"}) and not image.parse_requested_ratio(text):
            canvas = None
            if source_image and owned_image(user["id"], source_image):
                canvas = store.DATA / "images" / user["id"] / source_image
            elif uploads:
                upload_id = str(uploads[0])
                candidate = store.DATA / "uploads" / user["id"] / upload_id
                if re.fullmatch(r"[A-Za-z0-9._-]+", upload_id) and candidate.is_file():
                    canvas = candidate
            if canvas is not None:
                matched = image.match_file_size(canvas, model)
                if matched:
                    size = matched
        resolution = ui["resolution"] if ui["resolution"] in image.RESOLUTIONS else ""
        quality = ui["quality"] if ui["quality"] in image.QUALITIES else ""
        count = ui["n"] if ui["n"] in (1, 2, 4) else 1
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 1
        if count not in (1, 2, 4):
            count = 1
        store.write_memory(
            chat_id,
            json.dumps(
                memory.updated(remembered, fresh=fresh, text=text, keep=plan.get("_memory_keep") or [], size=size or ""),
                ensure_ascii=False,
            ),
        )
        try:
            image_urls = forced_refs if forced_refs else collect_refs(
                user["id"],
                chat_id,
                uploads,
                urls,
                bool(plan.get("use_previous_image")),
                source_image,
            )
            payload = image.build_payload(model, _prompt_with_size(prompt, size), size, resolution, quality, count, image_urls)
            try:
                task_id = image.submit(API_KEY, BASE_URL, payload)
            except image.ImageError as exc:
                if not (_size_rejected(exc) and exc.rejected):
                    raise
                size, recovered = image.recover_size(model, size, str(exc), _requested_label(text, ui.get("size")))
                size_note = recovered or size_note
                payload = image.build_payload(model, _prompt_with_size(prompt, size), size, resolution, quality, count, image_urls)
                try:
                    task_id = image.submit(API_KEY, BASE_URL, payload)
                except image.ImageError as again:
                    if not (_size_rejected(again) and again.rejected):
                        raise
                    size = ""
                    size_note = "这个比例没有被接口接受，已改用默认比例生成。"
                    payload = image.build_payload(model, prompt, size, resolution, quality, count, image_urls)
                    task_id = image.submit(API_KEY, BASE_URL, payload)
        except image.ImageError as exc:
            message = store.add_message(
                chat_id,
                user["id"],
                "assistant",
                {
                    "text": plan.get("reply") or "没能提交生图。",
                    "prompt": prompt,
                    "llm": {"tried": tried},
                    "image": {"status": "FAILURE", "fail_reason": str(exc), "model": model},
                },
            )
            self._json(200, {"session": present_chat(store.get_chat(user["id"], chat_id)), "message": message, "chain": llm.chain_status()})
            return
        reply = (plan.get("reply") or "正在画。").strip()
        if size_note:
            reply = f"{reply} {size_note}".strip()
        assistant = {
            "text": reply,
            "prompt": prompt,
            "llm": {"tried": tried},
            "image": {
                "model": model,
                "task_id": task_id,
                "status": "PENDING",
                "progress": "0%",
                "remote_urls": [],
                "local_names": [],
                "fail_reason": "",
                "size": size,
                "n": count,
                "resolution": resolution,
                "quality": quality,
                "mask": mask_id,
                "canvas": source_image if mask_id else "",
                "crop_px": [int(v) for v in crop_px] if crop_px else [],
                "crop_token": str(crop_files.get("token") or ""),
                "submitted_at": store.now(),
            },
        }
        message = store.add_message(chat_id, user["id"], "assistant", assistant)
        store.create_job(message["id"], chat_id, user["id"], task_id)
        log(f"image submit user={user['username']} task={task_id} model={model}")
        self._json(200, {"session": present_chat(store.get_chat(user["id"], chat_id)), "message": message, "chain": llm.chain_status()})

    def _retry(self, user: dict) -> None:
        body = self._read_json()
        message = store.get_message(user["id"], str(body.get("message_id") or ""))
        if not message:
            self._json(404, {"error": "没有这条消息"})
            return
        image_body = message["body"].get("image") or {}
        prompt = message["body"].get("prompt") or ""
        model = image_body.get("model") or image.IMAGE_MODELS[0]["id"]
        if image_body.get("status") == "PENDING":
            self._json(400, {"error": "这一张还在画"})
            return
        if store.pending_count(user["id"]) >= 2:
            self._json(429, {"error": "还有两张在画，等它们完成再发"})
            return
        limit = user.get("daily_limit")
        if limit and store.usage_today(user["id"]) >= int(limit):
            self._json(429, {"error": "今天的张数已经用完"})
            return
        refs = []
        canvas = str(image_body.get("canvas") or "")
        if canvas and owned_image(user["id"], canvas):
            refs.append(image.data_url_for(store.DATA / "images" / user["id"] / canvas))
        mask = str(image_body.get("mask") or "")
        if mask and re.fullmatch(r"[A-Za-z0-9._-]+", mask):
            path = store.DATA / "uploads" / user["id"] / mask
            if path.is_file():
                refs.append(image.data_url_for(path))
        old_task = str(image_body.get("task_id") or "")
        if old_task:
            try:
                previous = image.query(API_KEY, BASE_URL, old_task)
            except image.ImageError:
                previous = None
            if previous and previous.get("status") == "PENDING":
                self._json(400, {"error": "这一张还在画"})
                return
            if previous and previous.get("status") == "SUCCESS" and previous.get("urls"):
                # 上一次其实已经画好了，直接把它接回来，不要再花钱重画
                store.supersede_jobs(message["id"])
                image_body.update(
                    {
                        "status": "PENDING",
                        "progress": previous.get("progress") or "99%",
                        "fail_reason": "",
                        "submitted_at": store.now(),
                        "local_names": [],
                        "remote_urls": previous.get("urls") or [],
                        "mask_applied": False,
                    }
                )
                message["body"]["image"] = image_body
                message["body"]["text"] = "上一张其实已经画好了，正在取回。"
                store.update_message(message["id"], message["body"])
                store.create_job(message["id"], message["chat_id"], user["id"], old_task)
                self._json(200, {"message": message})
                return
        try:
            payload = image.build_payload(
                model,
                prompt,
                image_body.get("size") or "",
                image_body.get("resolution") or "",
                image_body.get("quality") or "",
                image_body.get("n") or 1,
                refs,
            )
            task_id = image.submit(API_KEY, BASE_URL, payload)
        except image.ImageError as exc:
            self._json(400, {"error": str(exc)})
            return
        store.supersede_jobs(message["id"])
        image_body.update({"task_id": task_id, "status": "PENDING", "progress": "0%", "fail_reason": "", "submitted_at": store.now(), "local_names": [], "remote_urls": [], "mask_applied": False})
        message["body"]["image"] = image_body
        message["body"]["text"] = "按同一句提示词再画一次。"
        store.update_message(message["id"], message["body"])
        store.create_job(message["id"], message["chat_id"], user["id"], task_id)
        self._json(200, {"message": message})

    def _upload(self, user: dict) -> None:
        body = self._read_json(limit=12_000_000)
        data_url = str(body.get("data_url") or "")
        match = re.match(r"data:image/(png|jpeg|jpg|webp|gif);base64,([A-Za-z0-9+/=\s]+)", data_url)
        if not match:
            self._json(400, {"error": "只接收 png、jpg、webp、gif"})
            return
        raw = _b64decode(match.group(2))
        if len(raw) > 8_000_000:
            self._json(400, {"error": "单张参考图不要超过 8MB"})
            return
        folder = store.DATA / "uploads" / user["id"]
        used = 0
        count = 0
        if folder.is_dir():
            for item in folder.iterdir():
                if item.is_file():
                    count += 1
                    try:
                        used += item.stat().st_size
                    except OSError:
                        pass
        if count >= UPLOAD_MAX_FILES or used + len(raw) > UPLOAD_QUOTA_MB * 1024 * 1024:
            self._json(
                429,
                {
                    "error": f"你的参考图空间满了（{count} 个 / {used // (1024 * 1024)}MB）。"
                    "删掉旧对话后再试，或找管理员清理。",
                },
            )
            return
        ext = image.sniff_ext(raw)
        if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            self._json(400, {"error": "这不是可用的图片文件"})
            return
        folder.mkdir(parents=True, exist_ok=True)
        name = store.new_id() + ext
        (folder / name).write_bytes(raw)
        self._json(200, {"id": name, "name": str(body.get("name") or name)})

    def _mindmap_quota(self, user: dict, kind: str = "mindmap") -> bool:
        if user["role"] == "admin":
            return True
        used = store.use_today(user["id"], kind)
        if used >= MINDMAP_DAILY_LIMIT:
            self._json(
                429,
                {
                    "error": f"今天的导图次数用完了（每天 {MINDMAP_DAILY_LIMIT} 次），明天再来或找管理员。",
                    "used": used,
                    "limit": MINDMAP_DAILY_LIMIT,
                },
            )
            return False
        return True

    def _regenerate_mindmap(self, user: dict, map_id: str) -> None:
        row = store.get_mindmap(user["id"], map_id)
        if not row:
            self._json(404, {"error": "没有这张导图"})
            return
        source = str(row.get("source_text") or "").strip()
        if not source:
            self._json(400, {"error": "这张导图没有保存原文，无法重新整理"})
            return
        if not self._mindmap_quota(user, "regen"):
            return
        body = self._read_json()
        hint = str(body.get("hint") or "").strip()[:300]
        is_doc = len(source.splitlines()) > 5
        system = mindmap.DOCUMENT_SYSTEM if is_doc else mindmap.OUTLINE_SYSTEM
        prompt = source[:12000]
        if hint:
            prompt = "用户要求：" + hint + "\n\n原文：\n" + prompt
        plan, _tried = llm.orchestrate(
            API_KEY,
            BASE_URL,
            prompt,
            system=system,
            parser=mindmap.accept_outline,
            max_tokens=6000,
            seconds=180,
        )
        outline = (plan or {}).get("outline") or ""
        if not outline:
            self._json(502, {"error": "这次没有整理出大纲，请再试一次"})
            return
        try:
            tree, notes = mindmap.build_tree(outline)
        except mindmap.OutlineError:
            self._json(502, {"error": "大纲无法整理成导图，请换一种说法"})
            return
        store.add_use(user["id"], "regen")
        updated = store.update_mindmap(
            user["id"],
            map_id,
            tree["text"],
            tree,
            row["theme"],
            row.get("spacing") or 34,
            row.get("layout") or "right",
        )
        self._json(200, {"map": store.mindmap_public(updated, notes)})

    def _create_mindmap(self, user: dict) -> None:
        body = self._read_json(12_000_000)
        hint = str(body.get("text") or "").strip()[:500]
        document = str(body.get("document") or "")
        if document:
            self._create_mindmap_from_document(user, hint, str(body.get("filename") or ""), document)
            return
        if not hint:
            self._json(400, {"error": "先写要整理的内容，或上传文档"})
            return
        if not self._mindmap_quota(user):
            return
        result = self._mindmap_from_prompt(user, hint, hint, mindmap.OUTLINE_SYSTEM, "大纲没有生成，已先放上你的原话。可以改节点，或再试一次。")
        if result is None:
            self._json(502, {"error": "大纲无法整理成导图，请换一种说法"})
            return
        store.add_use(user["id"], "mindmap")
        self._json(200, {"map": result})

    def _create_mindmap_from_document(self, user: dict, hint: str, filename: str, document: str) -> None:
        match = re.match(r"data:[^,]*,([A-Za-z0-9+/=\s]+)", document) or re.match(r"([A-Za-z0-9+/=\s]+)", document)
        if not match:
            self._json(400, {"error": "文档数据不对"})
            return
        raw = _b64decode(match.group(1))
        try:
            text, headings = mindmap.read_upload(filename, raw)
        except mindmap.OutlineError as exc:
            self._json(400, {"error": str(exc)})
            return
        if not hint:
            built = None
            if headings and len(headings) >= 2:
                try:
                    tree, notes = mindmap.tree_from_headings(headings)
                    built = (tree, notes, "按 Word 里的标题层级整理")
                except mindmap.OutlineError:
                    built = None
            if built is None:
                built = mindmap.structure_tree(text)
            if built:
                tree, notes, how = built
                notes = [how + "，没有改写正文。"] + notes
                row = store.create_mindmap(user["id"], tree["text"], text[:8000], text[:8000], tree, "classic")
                self._json(200, {"map": store.mindmap_public(row, notes)})
                return
        prompt = text[:12000]
        if hint:
            prompt = "用户要求：" + hint + "\n\n文档正文：\n" + prompt
        if not self._mindmap_quota(user):
            return
        result = self._mindmap_from_prompt(user, text[:8000], prompt, mindmap.DOCUMENT_SYSTEM, "文档大纲没有生成，已先用文档标题占位。")
        if result is None:
            self._json(502, {"error": "大纲无法整理成导图，请换一种说法"})
            return
        store.add_use(user["id"], "mindmap")
        self._json(200, {"map": result})

    def _mindmap_from_prompt(self, user: dict, source: str, prompt: str, system: str, empty_note: str) -> dict:
        plan, _tried = llm.orchestrate(
            API_KEY,
            BASE_URL,
            prompt,
            system=system,
            parser=mindmap.accept_outline,
            max_tokens=6000,
            seconds=180,
        )
        notes = []
        outline = ""
        if plan and plan.get("outline"):
            outline = plan["outline"]
            try:
                tree, notes = mindmap.build_tree(outline)
            except mindmap.OutlineError:
                return None
        else:
            tree = mindmap.fallback_tree(source)
            notes = [empty_note]
        row = store.create_mindmap(user["id"], tree["text"], source[:8000], outline, tree, "classic")
        return store.mindmap_public(row, notes)

    def _update_mindmap(self, user: dict, map_id: str) -> None:
        body = self._read_json()
        current = store.get_mindmap(user["id"], map_id)
        if not current:
            self._json(404, {"error": "没有这张导图"})
            return
        try:
            tree, notes = mindmap.validate_tree(body.get("tree"))
        except mindmap.OutlineError as exc:
            self._json(400, {"error": str(exc)})
            return
        theme = mindmap.theme_name(str(body.get("theme") or current["theme"]))
        spacing = int(body.get("spacing") or current.get("spacing") or 34)
        spacing = max(20, min(160, spacing))
        if not body.get("force"):
            version = body.get("version")
            try:
                stale = version is not None and int(version) != int(current.get("revision") or 1)
            except (TypeError, ValueError):
                stale = False
            if stale:
                self._json(
                    409,
                    {
                        "error": "这张导图在别处被改过",
                        "map": store.mindmap_public(current),
                    },
                )
                return
        layout = mindmap.layout_name(str(body.get("layout") or current.get("layout") or "right"))
        row = store.update_mindmap(user["id"], map_id, tree["text"], tree, theme, spacing, layout)
        self._json(200, {"map": store.mindmap_public(row, notes)})

    def _delete_mindmap(self, user: dict, map_id: str) -> None:
        ok = store.delete_mindmap(user["id"], map_id)
        if ok:
            folder = store.DATA / "mindmaps" / user["id"]
            for suffix in (".svg", ".png"):
                path = folder / f"{map_id}{suffix}"
                if path.is_file():
                    path.unlink()
        self._json(200 if ok else 404, {"ok": ok})

    def _export_mindmap(self, user: dict, map_id: str) -> None:
        if not store.get_mindmap(user["id"], map_id):
            self._json(404, {"error": "没有这张导图"})
            return
        body = self._read_json(12_000_000)
        kind = str(body.get("format") or "")
        folder = store.DATA / "mindmaps" / user["id"]
        folder.mkdir(parents=True, exist_ok=True)
        if kind == "png":
            match = re.match(r"data:image/png;base64,([A-Za-z0-9+/=\s]+)", str(body.get("data_url") or ""))
            if not match:
                self._json(400, {"error": "PNG 数据不对"})
                return
            raw = _b64decode(match.group(1))
            if len(raw) > 8_000_000:
                self._json(400, {"error": "导出的图片不要超过 8MB"})
                return
            name = map_id + ".png"
            (folder / name).write_bytes(raw)
        elif kind == "svg":
            svg = str(body.get("svg") or "")
            if svg.startswith("data:image/svg"):
                match = re.match(r"data:image/svg\+xml;base64,([A-Za-z0-9+/=\s]+)", svg)
                svg = _b64decode(match.group(1)).decode("utf-8", "replace") if match else ""
            if "<svg" not in svg[:400].lower():
                self._json(400, {"error": "SVG 数据不对"})
                return
            if len(svg.encode("utf-8")) > 4_000_000:
                self._json(400, {"error": "导出的 SVG 太大"})
                return
            svg = _clean_svg(svg)
            name = map_id + ".svg"
            (folder / name).write_text(svg, encoding="utf-8")
        else:
            self._json(400, {"error": "只导出 SVG 或 PNG"})
            return
        self._json(200, {"name": name, "url": f"/media/mindmaps/{user['id']}/{name}"})

    def _user(self):
        return store.user_for_token(self._token())

    def _token(self) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            if "=" not in part:
                continue
            key, value = part.strip().split("=", 1)
            if key == "duihua_session":
                return value
        return ""

    def _read_json(self, limit: int = 1_000_000) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise ValueError("body too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def _json(self, code: int, obj: dict, cookie: str | None = None, clear_cookie: bool = False) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", f"duihua_session={cookie}; HttpOnly; Path=/; SameSite=Lax; Max-Age=1209600")
        if clear_cookie:
            self.send_header("Set-Cookie", "duihua_session=; HttpOnly; Path=/; Max-Age=0")
        self.end_headers()
        self.wfile.write(raw)

    def _static(self, path: str) -> None:
        if path in {"/", ""}:
            path = "/index.html"
        rel = path.lstrip("/")
        if rel.startswith("static/"):
            rel = rel[len("static/"):]
        file_path = (STATIC / rel).resolve()
        try:
            file_path.relative_to(STATIC.resolve())
        except ValueError:
            self._json(404, {"error": "没有这个页面"})
            return
        if not file_path.is_file():
            self._json(404, {"error": "没有这个页面"})
            return
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if file_path.suffix in {".html", ".js", ".css"}:
            mime += "; charset=utf-8"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(data)

    def _media(self, path: str) -> None:
        user = self._user()
        if not user:
            self._json(401, {"error": "请先登录"})
            return
        match = re.fullmatch(r"/media/(images|uploads|mindmaps)/([0-9a-f]+)/([A-Za-z0-9._-]+)", path)
        if not match or match.group(2) != user["id"]:
            self._json(404, {"error": "没有这个文件"})
            return
        file_path = store.DATA / match.group(1) / match.group(2) / match.group(3)
        if not file_path.is_file():
            self._json(404, {"error": "没有这个文件"})
            return
        if match.group(1) == "mindmaps" and file_path.suffix == ".svg":
            mime = "image/svg+xml"
        elif match.group(1) == "mindmaps":
            mime = "image/png"
        else:
            mime = image.sniff_mime(file_path.read_bytes()[:16])
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(data)

    def _limited(self, ip: str) -> bool:
        with LOGIN_LOCK:
            recent = [item for item in LOGIN_FAILS.get(ip, []) if time.time() - item < 60]
            LOGIN_FAILS[ip] = recent
            return len(recent) >= 8

    def _fail(self, ip: str) -> None:
        with LOGIN_LOCK:
            LOGIN_FAILS.setdefault(ip, []).append(time.time())


def _b64decode(text: str) -> bytes:
    import base64
    return base64.b64decode(re.sub(r"\s+", "", text))


def _clean_svg(svg: str) -> str:
    """Stored SVGs are served back from our own origin, so drop anything active."""
    cleaned = re.sub(r"<script\b[^>]*>.*?</script\s*>", "", svg, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<foreignObject\b[^>]*>.*?</foreignObject\s*>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\son[a-zA-Z]+\s*=\s*(\"[^\"]*\"|'[^']*')", "", cleaned)
    cleaned = re.sub(r"javascript\s*:", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<(iframe|object|embed|use)\b[^>]*/?>", "", cleaned, flags=re.IGNORECASE)
    return cleaned


# moved to prompts.py: _prompt_with_size
def _requested_label(text: str, ui_size: str) -> str:
    chosen = (ui_size or "").strip()
    if chosen and chosen != "auto":
        return chosen
    parsed = image.parse_requested_ratio(text)
    return parsed[0] if parsed else ""


def _size_rejected(exc: image.ImageError) -> bool:
    text = str(exc).lower()
    return any(word in text for word in ("size", "ratio", "aspect", "比例", "尺寸"))


def present_chat(row: dict | None) -> dict | None:
    if not row:
        return None
    card = memory.load(row.get("memory"))
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "memory_line": memory.line(card),
    }


def last_generated(user_id: str, chat_id: str) -> dict | None:
    for message in reversed(store.list_messages(user_id, chat_id)):
        body = message.get("body") or {}
        info = body.get("image") or {}
        names = info.get("local_names") or []
        if not names or info.get("status") == "FAILURE":
            continue
        name = names[-1]
        if owned_image(user_id, name):
            return {"name": name, "prompt": body.get("prompt") or "", "size": info.get("size") or ""}
    return None


def generated_meta(user_id: str, chat_id: str, name: str) -> dict:
    for message in reversed(store.list_messages(user_id, chat_id)):
        body = message.get("body") or {}
        info = body.get("image") or {}
        if name and name in (info.get("local_names") or []):
            return {"prompt": body.get("prompt") or "", "size": info.get("size") or ""}
    return {}


# moved to prompts.py: _named_color_change
# moved to prompts.py: _color_lock
# moved to prompts.py: reference_prompt
# moved to prompts.py: composite_prompt
# moved to prompts.py: edit_prompt
# moved to prompts.py: mask_edit_prompt
# moved to prompts.py: size_only_prompt
def owned_image(user_id: str, name: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return False
    return (store.DATA / "images" / user_id / name).is_file()


def collect_refs(user_id: str, chat_id: str, upload_ids: list, urls: list[str], use_previous: bool, source_image: str = "") -> list[str]:
    found = []
    if source_image and owned_image(user_id, source_image):
        found.append(image.data_url_for(store.DATA / "images" / user_id / source_image))
    for url in urls:
        if url.startswith("https://") or url.startswith("http://"):
            found.append(url)
    for upload_id in upload_ids[:5]:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", str(upload_id)):
            continue
        path = store.DATA / "uploads" / user_id / str(upload_id)
        if path.is_file():
            found.append(image.data_url_for(path))
    if use_previous and not source_image:
        messages = store.list_messages(user_id, chat_id)
        for message in reversed(messages):
            info = message["body"].get("image") or {}
            names = info.get("local_names") or []
            remote = info.get("remote_urls") or []
            if remote and info.get("submitted_at", 0) > store.now() - 23 * 3600:
                found.append(remote[0])
                break
            if names:
                path = store.DATA / "images" / user_id / names[0]
                if path.is_file():
                    found.append(image.data_url_for(path))
                break
    return found[:5]


def poll_once() -> None:
    if not API_KEY:
        return
    for job in store.pending_jobs():
        fresh = store.get_job(job["id"])
        if not fresh or fresh.get("status") != "pending":
            continue
        message = store.get_message(job["user_id"], job["message_id"])
        if not message:
            store.update_job(job["id"], "missing")
            continue
        info = message["body"].setdefault("image", {})
        submitted = int(info.get("submitted_at") or job["updated_at"])
        if store.now() - submitted > 15 * 60:
            info["status"] = "FAILURE"
            info["fail_reason"] = "超过 15 分钟还没有结果"
            store.update_message(message["id"], message["body"])
            store.update_job(job["id"], "FAILURE")
            continue
        try:
            result = image.query(API_KEY, BASE_URL, job["upstream_id"])
        except image.ImageError as exc:
            log(f"poll {job['upstream_id']} {exc}")
            continue
        info["progress"] = result["progress"]
        if result["status"] == "SUCCESS":
            names = []
            for url in result["urls"]:
                try:
                    ext = ".img"
                    dest = store.DATA / "images" / job["user_id"] / (store.new_id() + ext)
                    image.download(url, dest)
                    sniffed = image.sniff_ext(dest.read_bytes()[:16])
                    if sniffed != ext:
                        renamed = dest.with_suffix(sniffed)
                        dest.rename(renamed)
                        dest = renamed
                    names.append(dest.name)
                except image.ImageError as exc:
                    log(f"download {exc}")
            if names and info.get("mask") and info.get("canvas"):
                mask_path = store.DATA / "uploads" / job["user_id"] / str(info["mask"])
                canvas_path = store.DATA / "images" / job["user_id"] / str(info["canvas"])
                crop_px = info.get("crop_px") or []
                if mask_path.is_file() and canvas_path.is_file():
                    merged = 0
                    temps = store.DATA / "temps" / job["user_id"]
                    temps.mkdir(parents=True, exist_ok=True)
                    for name in names:
                        target = store.DATA / "images" / job["user_id"] / name
                        if crop_px and len(crop_px) == 4:
                            full = temps / (store.new_id() + "-full.jpg")
                            if image.paste_crop(canvas_path, target, tuple(crop_px), full):
                                if image.mask_composite(canvas_path, mask_path, full, target):
                                    merged += 1
                            if full.is_file():
                                full.unlink()
                        elif image.mask_composite(canvas_path, mask_path, target, target):
                            merged += 1
                    info["mask_applied"] = bool(merged)
                    log(f"mask composite {job['upstream_id']} merged={merged} crop={bool(crop_px)}")
                    token = str(info.get("crop_token") or "")
                    if token:
                        for leftover in temps.glob(token + "-*"):
                            try:
                                leftover.unlink()
                            except OSError:
                                pass
            info["status"] = "SUCCESS"
            info["remote_urls"] = result["urls"]
            info["local_names"] = names
            info["fail_reason"] = "" if names else "图已生成，但服务器没能保存下来"
            store.update_message(message["id"], message["body"])
            if names:
                card = memory.load(store.read_memory(job["chat_id"]))
                store.write_memory(
                    job["chat_id"],
                    json.dumps(memory.note_image(card, names[-1], info.get("size") or ""), ensure_ascii=False),
                )
            store.update_job(job["id"], "SUCCESS")
            if names:
                store.add_usage(job["user_id"], max(1, len(names)))
            log(f"image done {job['upstream_id']} files={len(names)}")
        elif result["status"] == "FAILURE":
            info["status"] = "FAILURE"
            info["fail_reason"] = result["fail_reason"] or "生成失败"
            store.update_message(message["id"], message["body"])
            store.update_job(job["id"], "FAILURE")
            log(f"image fail {job['upstream_id']} {info['fail_reason']}")
        else:
            store.update_message(message["id"], message["body"])


def poll_loop() -> None:
    while True:
        try:
            poll_once()
        except Exception as exc:
            log("poll loop " + repr(exc))
        time.sleep(4)


def cleanup_once() -> None:
    """Old uploads and temp crops are never reused; drop them so the disk does not fill."""
    now = time.time()
    upload_cutoff = now - UPLOAD_KEEP_DAYS * 86400
    removed = 0
    root = store.DATA / "uploads"
    if root.is_dir():
        for item in root.rglob("*"):
            if item.is_file():
                try:
                    if item.stat().st_mtime < upload_cutoff:
                        item.unlink()
                        removed += 1
                except OSError:
                    pass
    temps = store.DATA / "temps"
    if temps.is_dir():
        for item in temps.rglob("*"):
            if item.is_file():
                try:
                    if item.stat().st_mtime < now - 86400:
                        item.unlink()
                        removed += 1
                except OSError:
                    pass
    if removed:
        log(f"cleanup removed {removed} stale files")


def cleanup_loop() -> None:
    while True:
        try:
            cleanup_once()
        except Exception as exc:
            log("cleanup loop " + repr(exc))
        time.sleep(6 * 3600)


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    store.init_db()
    ensure_admin()
    threading.Thread(target=poll_loop, name="poll", daemon=True).start()
    threading.Thread(target=cleanup_loop, name="cleanup", daemon=True).start()
    httpd = Server((HOST, PORT), Handler)
    ips = lan_ips()
    log(f"对画已启动。本机 http://127.0.0.1:{PORT}")
    for ip in ips:
        log(f"局域网 http://{ip}:{PORT}")
    log("管理员账号见 .env 的 ADMIN_USER。请保持这个窗口开着。")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
