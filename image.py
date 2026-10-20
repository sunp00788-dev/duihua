"""Async image generation against MigeAPI."""

from __future__ import annotations

import base64
import json
import math
import mimetypes
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

IMAGE_MODELS = [
    {"id": "gemini-nano-banana-2.1-ext", "label": "Nano Banana 2.1"},
    {"id": "gpt-image-2.5-ext", "label": "GPT Image 2.5"},
]
ALLOWED_MODELS = {item["id"] for item in IMAGE_MODELS}
# 实测：文档只写了 5 个，网关和两个模型的真实名单更宽，而且彼此不同。
GPT_SIZES = (
    "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5",
    "2:1", "1:2", "21:9", "9:21", "3:1", "1:3",
)
GEMINI_SIZES = (
    "1:1", "16:9", "9:16", "21:9", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5",
    "4:1", "1:4", "8:1", "1:8",
)
MODEL_SIZES = {
    "gpt-image-2.5-ext": GPT_SIZES,
    "gemini-nano-banana-2.1-ext": GEMINI_SIZES,
}
SIZES = tuple(dict.fromkeys(GPT_SIZES + GEMINI_SIZES))
RESOLUTIONS = {"1024", "2k", "4k"}
QUALITIES = {"low", "medium", "high", "xhigh", "max"}


class ImageError(Exception):
    """Carries the upstream status so callers can tell a rejected request from a lost one."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = int(status or 0)

    @property
    def rejected(self) -> bool:
        """4xx means the request itself was refused, so resubmitting costs nothing."""
        return 400 <= self.status < 500


def submit(api_key: str, base_url: str, payload: dict) -> str:
    url = base_url.rstrip("/") + "/v1/images/generations?async=true"
    status, raw = _request("POST", url, api_key, payload, timeout=90)
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ImageError("生图接口没有返回 JSON") from exc
    if status >= 400:
        raise ImageError(_error_text(body) or f"生图提交失败 {status}", status)
    task_id = body.get("task_id") or body.get("id")
    data = body.get("data")
    if not task_id and isinstance(data, dict):
        task_id = data.get("task_id") or data.get("id")
    if not task_id:
        raise ImageError(_error_text(body) or "没有拿到任务编号", 502)
    return str(task_id)


def query(api_key: str, base_url: str, task_id: str) -> dict:
    url = base_url.rstrip("/") + "/v1/images/tasks/" + urllib.parse.quote(task_id)
    status, raw = _request("GET", url, api_key, None, timeout=40)
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ImageError("查询接口没有返回 JSON") from exc
    if status >= 400:
        raise ImageError(_error_text(body) or f"查询失败 {status}", status)
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    state = str(data.get("status") or "").upper()
    if state in {"COMPLETED"}:
        state = "SUCCESS"
    elif state in {"FAILED", "ERROR", "CANCELLED"}:
        state = "FAILURE"
    elif state not in {"SUCCESS", "FAILURE"}:
        state = "PENDING"
    return {
        "status": state,
        "progress": str(data.get("progress") or ""),
        "urls": extract_urls(data),
        "fail_reason": str(data.get("fail_reason") or ""),
    }


def extract_urls(data: dict) -> list[str]:
    found = []
    for key in ("result_url", "image_url", "url"):
        _collect(data.get(key), found)
    for key in ("result_urls", "image_urls", "images", "results"):
        _collect(data.get(key), found)
    deduped = []
    for item in found:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _collect(value, found: list[str]) -> None:
    if isinstance(value, str) and value.startswith("http"):
        found.append(value)
    elif isinstance(value, list):
        for item in value:
            _collect(item, found)
    elif isinstance(value, dict):
        for key in ("url", "result_url", "image_url"):
            _collect(value.get(key), found)


def download(url: str, dest: Path) -> None:
    if not trusted_result(url):
        raise ImageError("结果链接不在中转站域名上，已跳过服务器下载")
    req = urllib.request.Request(url, headers={"User-Agent": "duihua/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = resp.read(30_000_001)
    except Exception as exc:
        raise ImageError("下载结果图失败") from exc
    if len(data) > 30_000_000:
        raise ImageError("结果图过大")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def trusted_result(url: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host == "api.migeapi.com" or host.endswith(".migeapi.com")


def data_url_for(path: Path) -> str:
    raw = path.read_bytes()
    if len(raw) > 8_000_000:
        raise ImageError("参考图超过 8MB")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        mime = sniff_mime(raw)
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def sniff_ext(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG"):
        return ".png"
    if raw.startswith(b"\xff\xd8"):
        return ".jpg"
    if raw.startswith(b"GIF8"):
        return ".gif"
    if raw.startswith(b"RIFF") and b"WEBP" in raw[:16]:
        return ".webp"
    return ".img"


def sniff_mime(raw: bytes) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(sniff_ext(raw), "application/octet-stream")


def parse_requested_ratio(text: str):
    """Return (label, width/height) if the user named a ratio, else None."""
    import re
    match = re.search(r"(\d{1,4})\s*[:：比/xX×*]\s*(\d{1,4})", text or "")
    if match:
        width, height = int(match.group(1)), int(match.group(2))
        if width > 0 and height > 0 and width <= 8192 and height <= 8192:
            return f"{width}:{height}", width / height
    if re.search(r"正方形|方图", text or ""):
        return "1:1", 1.0
    if re.search(r"竖版|竖图|竖屏", text or ""):
        return "9:16", 9 / 16
    if re.search(r"横版|横图|宽屏|横屏", text or ""):
        return "16:9", 16 / 9
    return None


def parse_resolution(text: str) -> str:
    low = (text or "").lower().replace(" ", "")
    if "4k" in low:
        return "4k"
    if "2k" in low:
        return "2k"
    if "1024" in low:
        return "1024"
    return ""


def is_size_only_request(text: str) -> bool:
    """True when the user only asks to change ratio or resolution, not the picture."""
    raw = (text or "").strip()
    if not raw or len(raw) > 80:
        return False
    if not re.search(r"[改换调变]", raw):
        return False
    if not re.search(r"(尺寸|比例|分辨率|竖版|横版|竖图|横图|竖屏|横屏|正方形|方图|宽屏|2k|4k|1024|\d{1,4}\s*[:：比/xX×*]\s*\d{1,4})", raw, re.I):
        return False
    stripped = re.sub(r"\d{1,4}\s*[:：比/xX×*]\s*\d{1,4}", "", raw, flags=re.I)
    stripped = re.sub(r"(?i)2k|4k|1024", "", stripped)
    stripped = re.sub(
        r"(尺寸|比例|分辨率|竖版|横版|竖图|横图|竖屏|横屏|正方形|方图|宽屏|修改|改成|改为|换成|调整|变成|变为|改|换|把|将|为|成|到|一下|请|帮我|这张|上一张|图片|画面|的|了|吧|吗)",
        "",
        stripped,
    )
    stripped = re.sub(r"[\s，。,.!！?？:：;；、~～\-—]", "", stripped)
    return stripped == ""


def is_layout_overhaul(text: str) -> bool:
    """The user asked to rebuild the layout, background, or palette, not just replace text."""
    return bool(
        re.search(
            r"(换背景|改背景|背景换成|背景改为|重排|重新排版|改排版|换排版|换构图|重新构图|换风格|改风格|换配色|改配色|重新设计|整张重做)",
            text or "",
        )
    )


def model_family(model: str) -> str:
    return "gpt" if "gpt-image" in (model or "") else "gemini"


def places_into_previous(text: str) -> bool:
    """Upload supplies a person or object; the previous picture is the canvas."""
    raw = text or ""
    destination = re.search(r"(?<!传)上图|上面的?图|上一张|上张|刚才那张|原图|已生成|生成的图|前面的图|那张图", raw)
    action = re.search(
        r"(换成|换到|换进|替换|换为|替换成|添加|放到|放进|放入|放上|移到|搬进|合成|融合|加到|贴到|放在|替上)",
        raw,
    )
    if not (destination and action and re.search(r"(上传|我传|参考图)", raw)):
        return False
    if re.search(r"(文字|文案|标题)", raw) and not re.search(r"(人物|人像|人脸|脸)", raw):
        return False
    return True


def replaces_person(text: str) -> bool:
    raw = text or ""
    return bool(re.search(r"(换成|替换|换为)", raw) and re.search(r"(人物|人像|人脸|脸)", raw))


def is_direct_generate(text: str) -> bool:
    """A clear request to draw a new picture. The user's words are the prompt."""
    raw = (text or "").strip()
    if not raw or is_size_only_request(raw) or is_edit_request(raw) or places_into_previous(raw):
        return False
    return bool(re.search(r"(生成|画一张|画一个|画一幅|来一张|做一张|出一张)", raw))


def is_realism_redo(text: str) -> bool:
    """The user rejected the last picture as fake and wants it re-shot, not edited in place."""
    raw = (text or "").strip()
    if not raw or is_direct_generate(raw) or is_size_only_request(raw):
        return False
    return bool(re.search(r"(太假|不真实|不够真实|要写真|要真实|像实拍|像照片|不要假|不够逼真)", raw))


def is_vague(text: str) -> bool:
    """Too few words to draw anything specific. Only these get extra detail added."""
    raw = re.sub(r"\s+", "", text or "")
    if not raw:
        return True
    if re.search(r"(随便|随便画|随便来|随便生成|你看着办|都行)", raw):
        return True
    core = re.sub(
        r"(生成|画一张|画一个|画一幅|画个|来一张|来做?一张|做一张|出一张|帮我|请|一个|一张|一只|一条|图片|图)",
        "",
        raw,
    )
    core = re.sub(r"[，。,.！!？?、]", "", core)
    return len(core) <= 4


AUTO_MODEL = "auto"
TEXT_TASK = re.compile(
    r"(文字|文案|标题|海报|logo|LOGO|标志|字幕|排版|字体|字号|二维码|宣传单|菜单|证书|名片|封面|水印|价目|横幅|易拉宝|KT板)"
)
PERSON_TASK = re.compile(r"(人物|人像|人脸|脸|换脸|写真|实拍|照片|自拍|证件照|肖像)")


def pick_model(text: str, chosen: str, annotated: bool = False, editing: bool = False) -> str:
    """Follow an explicit choice; otherwise route by what the job needs.

    Text-heavy work goes to GPT Image, which holds lettering and layout better.
    Photo and person work, and anything that edits an existing picture, stays on Nano.
    """
    if chosen in ALLOWED_MODELS:
        return chosen
    raw = text or ""
    if PERSON_TASK.search(raw):
        return "gemini-nano-banana-2.1-ext"
    if TEXT_TASK.search(raw):
        return "gpt-image-2.5-ext"
    return "gemini-nano-banana-2.1-ext"


def model_label(model: str) -> str:
    for item in IMAGE_MODELS:
        if item["id"] == model:
            return item["label"]
    return model


def faithful_prompt(text: str, photo: bool = False) -> str:
    """Pass the user's words through, and only pin the medium they already named."""
    body = (text or "").strip()[:4000]
    if photo or re.search(r"(写真|实拍|照片|真实|逼真|太假)", body):
        extra = "按真实相机拍摄的照片来画，自然光，细节清楚。只保留用户写出的景物、人和文字。"
    else:
        extra = "只画用户写出的内容，保持用户指定的风格。"
    return body + "\n" + extra


def is_followup_edit(text: str) -> bool:
    """A later turn that changes the last picture, without asking for a new one."""
    raw = (text or "").strip()
    if not raw or is_new_image_request(raw) or is_size_only_request(raw):
        return False
    if re.search(r"画一张|生成一张|做一张|画一幅", raw):
        return False
    return bool(re.search(r"(改|换|调|去掉|移除|加上|添加|暗一点|亮一点|清楚一点|模糊一点)", raw))


def match_file_size(path: Path, model: str) -> str:
    """Nearest allowed ratio for an existing image, so an edit keeps its frame."""
    try:
        from PIL import Image

        with Image.open(path) as handle:
            width, height = handle.size
    except Exception:
        return ""
    if width <= 0 or height <= 0:
        return ""
    return nearest_size(width / height, model_sizes(model))


def is_new_image_request(text: str) -> bool:
    return bool(re.search(r"重新画|新画|新的一张|另画|再画一张|重新生成|画一张新|画一幅新", text or ""))


def is_edit_request(text: str) -> bool:
    """Only an explicit change to an existing picture. A reference image starts a new one."""
    raw = (text or "").strip()
    if not raw or is_new_image_request(raw) or is_size_only_request(raw):
        return False
    if re.search(r"根据参考|参考图|照着|风格|画一张|生成一张|做一张", raw):
        return False
    return bool(
        re.search(
            r"(上一张|上张|原图|这张图|刚才那张|在图上|保持原|其余|不要动|别的不动|暗一点|亮一点|清楚一点|模糊一点|只改)",
            raw,
        )
    )


def mask_boxes(mask_path, grid: int = 96, threshold: int = 128, min_ratio: float = 0.004):
    """Rough connected areas of a black/white mask, as normalized boxes."""
    try:
        from PIL import Image
    except Exception:
        return []
    try:
        with Image.open(mask_path) as img:
            small = img.convert("L").resize((grid, grid), Image.BILINEAR)
    except Exception:
        return []
    pixels = small.load()
    seen = [[False] * grid for _ in range(grid)]
    boxes = []
    for y in range(grid):
        for x in range(grid):
            if seen[y][x] or pixels[x, y] < threshold:
                continue
            stack = [(x, y)]
            seen[y][x] = True
            minx = maxx = x
            miny = maxy = y
            count = 0
            while stack:
                cx, cy = stack.pop()
                count += 1
                minx = min(minx, cx)
                maxx = max(maxx, cx)
                miny = min(miny, cy)
                maxy = max(maxy, cy)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < grid and 0 <= ny < grid and not seen[ny][nx] and pixels[nx, ny] >= threshold:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            if count >= grid * grid * min_ratio:
                boxes.append((minx / grid, miny / grid, (maxx + 1) / grid, (maxy + 1) / grid))
    return boxes


def region_words(boxes) -> list[str]:
    """Turn boxes into rough positions so the prompt can name every marked area."""
    words = []
    for x0, y0, x1, y1 in boxes:
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        col = "左" if cx < 0.36 else ("右" if cx > 0.64 else "中")
        row = "上" if cy < 0.36 else ("下" if cy > 0.64 else "")
        if col == "中":
            label = (row + "中") if row else "正中"
        elif row:
            label = row + col
        else:
            label = col
        words.append(label + "一处")
    return words


def mask_union(mask_path, pad: float = 0.12):
    """One padded box around every marked area, in 0..1 coordinates."""
    boxes = mask_boxes(mask_path, grid=160)
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    grow_x = max(0.02, (x1 - x0) * pad)
    grow_y = max(0.02, (y1 - y0) * pad)
    return (max(0.0, x0 - grow_x), max(0.0, y0 - grow_y), min(1.0, x1 + grow_x), min(1.0, y1 + grow_y))


def crop_to(src_path, box, dest_path):
    """Cut a normalized box out of an image and save it as PNG."""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(src_path) as img:
            width, height = img.size
            px = (
                max(0, int(box[0] * width)),
                max(0, int(box[1] * height)),
                min(width, int(round(box[2] * width))),
                min(height, int(round(box[3] * height))),
            )
            if px[2] - px[0] < 32 or px[3] - px[1] < 32:
                return None
            piece = img.convert("RGB").crop(px)
            piece.save(dest_path, "PNG")
            return px
    except Exception:
        return None


def paste_crop(canvas_path, edited_path, px, dest_path) -> bool:
    """Put an edited crop back where it came from."""
    try:
        from PIL import Image
    except Exception:
        return False
    try:
        with Image.open(canvas_path) as canvas_img:
            base = canvas_img.convert("RGB")
        with Image.open(edited_path) as edited_img:
            piece = edited_img.convert("RGB").resize((px[2] - px[0], px[3] - px[1]), Image.LANCZOS)
        base.paste(piece, (px[0], px[1]))
        if str(dest_path).lower().endswith(".png"):
            base.save(dest_path, optimize=True)
        else:
            base.save(dest_path, quality=97, subsampling=0)
        return True
    except Exception:
        return False


def mask_composite(canvas_path, mask_path, edited_path, out_path) -> bool:
    """Keep the original outside the mask, so nothing else can drift."""
    try:
        from PIL import Image, ImageFilter
    except Exception:
        return False
    try:
        with Image.open(canvas_path) as canvas_img:
            base = canvas_img.convert("RGB")
        with Image.open(mask_path) as mask_img:
            mask = mask_img.convert("L").resize(base.size, Image.NEAREST)
        with Image.open(edited_path) as edited_img:
            edited = edited_img.convert("RGB").resize(base.size, Image.LANCZOS)
        if mask.getbbox() is None:
            return False
        grown = mask.filter(ImageFilter.MaxFilter(5))
        alpha = grown.filter(ImageFilter.GaussianBlur(2))
        merged = Image.composite(edited, base, alpha)
        suffix = str(out_path).lower()
        if suffix.endswith(".png"):
            merged.save(out_path, optimize=True)
        else:
            merged.save(out_path, quality=97, subsampling=0)
        return True
    except Exception:
        return False


def ratio_value(label: str) -> float:
    width, height = label.split(":")
    return int(width) / int(height)


def model_sizes(model: str) -> tuple[str, ...]:
    return MODEL_SIZES.get(model, SIZES)


def nearest_size(ratio: float, allowed: tuple[str, ...] | list[str]) -> str:
    ranked = sorted(allowed, key=lambda name: abs(math.log(ratio) - math.log(ratio_value(name))))
    return ranked[0]


def fit_size(label: str, ratio: float, allowed: tuple[str, ...] | list[str]) -> tuple[str, str]:
    allowed = [item for item in allowed if item]
    if not allowed:
        return "", "这个比例没有被接口接受，已改用默认比例生成。"
    if label in allowed:
        return label, ""
    size = nearest_size(ratio, allowed)
    return size, f"你要的是 {label}，当前模型不支持，已按最接近的 {size} 生成。"


def resolve_size(text: str, ui_size: str, llm_size: str, model: str) -> tuple[str, str]:
    """Return (api size or empty, user-facing note). Never raises."""
    allowed = model_sizes(model)
    chosen = (ui_size or "auto").strip()
    if chosen not in {"", "auto"}:
        requested = parse_requested_ratio(chosen) or parse_requested_ratio(text)
        label, ratio = requested if requested else (chosen, 1.0)
        if chosen in SIZES:
            label, ratio = chosen, ratio_value(chosen)
        return fit_size(label, ratio, allowed)
    requested = parse_requested_ratio(text)
    if requested:
        return fit_size(requested[0], requested[1], allowed)
    llm_label = str(llm_size or "").strip()
    parsed = parse_requested_ratio(llm_label)
    if parsed:
        return fit_size(parsed[0], parsed[1], allowed)
    return "", ""


def sizes_from_error(text: str) -> list[str]:
    raw = text or ""
    match = re.search(
        r"(?:allowed|supported|valid)\s*(?:ratios?|sizes?|aspect\s*ratios?)?\s*[:：]\s*([0-9:：,/ ]+)",
        raw,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(r"([0-9]{1,2}\s*[:：]\s*[0-9]{1,2}(?:\s*[,，/]\s*[0-9]{1,2}\s*[:：]\s*[0-9]{1,2})+)", raw)
    if not match:
        return []
    found = []
    for item in re.findall(r"\d{1,2}\s*[:：]\s*\d{1,2}", match.group(1)):
        label = re.sub(r"\s+", "", item).replace("：", ":")
        if label not in found:
            found.append(label)
    return found


def recover_size(model: str, sent: str, error_text: str, requested_label: str = "") -> tuple[str, str]:
    """Pick a different allowed size after the API rejects one. Empty means omit size."""
    allowed = sizes_from_error(error_text) or [item for item in model_sizes(model) if item != sent]
    label = requested_label or sent or "1:1"
    parsed = parse_requested_ratio(label)
    ratio = parsed[1] if parsed else (ratio_value(sent) if sent in SIZES or (sent and ":" in sent) else 1.0)
    size, note = fit_size(label, ratio, [item for item in allowed if item != sent])
    if size == sent:
        return "", "这个比例没有被接口接受，已改用默认比例生成。"
    return size, note or "这个比例没有被接口接受，已改用默认比例生成。"


def build_payload(model: str, prompt: str, size: str, resolution: str, quality: str, count: int, image_urls: list[str]) -> dict:
    if model not in ALLOWED_MODELS:
        raise ImageError("不支持的生图模型")
    prompt = (prompt or "").strip()
    if not prompt:
        raise ImageError("提示词是空的")
    payload = {"model": model, "prompt": prompt[:4000]}
    if isinstance(size, str) and re.fullmatch(r"\d{1,2}:\d{1,2}", size):
        payload["size"] = size
    if resolution in RESOLUTIONS:
        payload["resolution"] = resolution
    if quality in QUALITIES:
        payload["quality"] = quality
    if count in (2, 4):
        payload["n"] = count
    urls = [item for item in image_urls if item][:5]
    if urls:
        payload["image_urls"] = urls
    return payload


def _request(method: str, url: str, api_key: str, payload: dict | None, timeout: int) -> tuple[int, str]:
    data = None
    headers = {"Authorization": f"Bearer {api_key}"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        resp = exc
    except Exception as exc:
        raise ImageError("连接生图接口失败") from exc
    timer = threading.Timer(max(1, timeout), resp.close)
    timer.daemon = True
    timer.start()
    try:
        raw = resp.read().decode("utf-8", "replace")
        return getattr(resp, "status", None) or getattr(resp, "code", 0), raw
    except Exception as exc:
        raise ImageError("生图接口超时") from exc
    finally:
        timer.cancel()
        resp.close()


def _error_text(body: dict) -> str:
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or "")
    return str(body.get("message") or body.get("fail_reason") or "")
