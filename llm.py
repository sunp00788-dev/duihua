"""Prompt orchestration with a short cross-provider fallback chain.

Pattern follows LiteLLM fallbacks: ordered providers, per-model timeout,
cooldown after failure, no retry on the same model. A normal refusal JSON
is success. An HTTP content-filter error is a short cooldown, not a ban.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request

import store

DEFAULT_CHAIN = [
    {
        "id": "gemini-3-flash-preview",
        "label": "Gemini Flash",
        "enabled": True,
        "timeout": 18,
        "note": "默认。非 GPT，这把令牌已测通。",
    },
    {
        "id": "gpt-5.6-sol",
        "label": "GPT 5.6 Sol",
        "enabled": True,
        "timeout": 12,
        "note": "GPT 档。官方或线路出问题会很快跳过。",
    },
    {
        "id": "grok-4.7",
        "label": "Grok 4.7",
        "enabled": True,
        "timeout": 22,
        "note": "最后一家。会走推理，只在前两个都失败时用。",
    },
    {
        "id": "gpt-5.4",
        "label": "GPT 5.4",
        "enabled": False,
        "timeout": 12,
        "note": "令牌目前未放行。放行后打开，并关掉 GPT 5.6 Sol。",
    },
]

SYSTEM = """你是「对画」的提示词编排。只输出一个 JSON 对象，不要 Markdown，不要解释。

字段：
{"action":"generate"|"chat","reply":"给用户看的简短中文","prompt":"完整生图提示词","size":null,"resolution":null,"quality":null,"n":null,"use_previous_image":false}

规则：
- 用户要画一张新图时，action=generate。prompt 先原样写下用户的话，不能改写成另一种画面。用户指定的数量、文字、颜色、风格和比例必须原样保留。
- 禁止添加用户没写的意境、留白、空灵、壮丽、配色方案或人物。写真、实拍、照片、真实只能理解成相机拍摄的照片。不要用 masterpiece、8k、best quality。
- 用户说太假、要写真、要真实时，use_previous_image=false。按用户原来的主题重画成真实照片，禁止写「保持原图」。
- 改已有图、改参考图、只改尺寸时，不要补焦点、配色或留白。那些规则见下面。
- 用户上传参考图，并且明确要求改背景、排版或配色时，use_previous_image=false。prompt 按用户要求重排这些部分，没点名的结构仍沿用参考图。只改文字时不要走这条。
- 同一条对话里接着改上一张成图时，use_previous_image=true。prompt 只写这一处差异，并写明其余保持原图。用户要画一张新图时才重新写完整画面。
- 用户只是修改尺寸、比例或分辨率时，use_previous_image=true。prompt 沿用上一张的完整提示词，只说明画幅按新要求变化，禁止添加新的光线、镜头、材质或重新构图。
- 如果用户在图上做了红色标注，action=generate，use_previous_image=true。prompt 要写明：只改标注指出的位置，未标注区域保持原图，最终画面不要留下红色涂抹、圆圈、箭头或标注文字。
- 用户只是闲聊、问能力，或明确说先别画，action=chat，prompt 为空字符串。
- size 可以是 1:1、21:9、16:9、3:2、4:3、5:4、4:5、3:4、2:3、9:16、2:1、1:2、9:21、3:1、1:3、4:1、1:4、8:1、1:8，或 null。用户没写比例就 null。写了具体比例就原样写入，不要自己改成别的比例。
- resolution、quality、n 一律 null。画质、分辨率和张数由界面决定，不要在 prompt 里改口。
- reply 一两句，说明将要画什么，不要复述整段 prompt。
- 涉及未成年人色情时，action=chat，拒绝，不要写 prompt。
"""

SIZES = {"1:1", "2:3", "3:2", "16:9", "9:16"}
RESOLUTIONS = {"1024", "2k", "4k"}
QUALITIES = {"low", "medium", "high", "xhigh", "max"}
COUNTS = {1, 2, 4}


class LlmError(Exception):
    def __init__(self, kind: str, message: str, cooldown: int):
        super().__init__(message)
        self.kind = kind
        self.cooldown = cooldown


def load_chain() -> list[dict]:
    saved = store.kv_get("llm_chain")
    if isinstance(saved, list) and saved:
        return [_normalize_item(item) for item in saved if isinstance(item, dict) and item.get("id")]
    store.kv_set("llm_chain", DEFAULT_CHAIN)
    return [dict(item) for item in DEFAULT_CHAIN]


def save_chain(chain: list[dict]) -> list[dict]:
    clean = []
    for item in chain:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", model_id):
            continue
        clean.append(_normalize_item(item))
    if not clean:
        raise ValueError("降级链不能为空")
    store.kv_set("llm_chain", clean)
    return clean


def _normalize_item(item: dict) -> dict:
    known = {row["id"]: row for row in DEFAULT_CHAIN}
    base = known.get(item.get("id"), {})
    timeout = item.get("timeout", base.get("timeout", 16))
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 16
    return {
        "id": item.get("id"),
        "label": str(item.get("label") or base.get("label") or item.get("id")),
        "enabled": bool(item.get("enabled", True)),
        "timeout": max(6, min(timeout, 40)),
        "note": str(item.get("note") or base.get("note") or ""),
    }


def health_map() -> dict:
    raw = store.kv_get("llm_health", {})
    return raw if isinstance(raw, dict) else {}


def chain_status() -> list[dict]:
    health = health_map()
    moment = time.time()
    out = []
    for item in load_chain():
        info = health.get(item["id"]) or {}
        until = float(info.get("until") or 0)
        cooling = until > moment
        out.append(
            {
                **item,
                "cooling": cooling,
                "until": until if cooling else 0,
                "kind": info.get("kind") or "",
                "error": info.get("error") or "",
            }
        )
    return out


def clear_health() -> None:
    store.kv_set("llm_health", {})


def _mark(model_id: str, kind: str, cooldown: int, error: str) -> None:
    health = health_map()
    health[model_id] = {
        "until": time.time() + cooldown,
        "kind": kind,
        "error": error[:180],
    }
    store.kv_set("llm_health", health)


def _clear_one(model_id: str) -> None:
    health = health_map()
    if model_id in health:
        health.pop(model_id, None)
        store.kv_set("llm_health", health)


def build_user_text(text: str, history: list[dict], ui: dict, has_previous: bool) -> str:
    lines = [
        f"界面生图模型：{ui.get('image_model') or '未指定'}",
        f"界面比例：{ui.get('size') or '未指定'}",
        f"用户勾选参考上一张：{'是' if ui.get('use_previous') else '否'}",
        f"用户是否在指定图片上做了红色标注：{'是' if ui.get('annotated') else '否'}",
        f"指定要修改的已生成图片：{ui.get('source_image') or '无'}",
        f"是否已有上一张图：{'是' if has_previous else '否'}",
        f"本轮参考图数量：{ui.get('attachment_count') or 0}",
        f"当前记忆：{ui.get('memory') or '还没有'}",
        "最近对话：",
    ]
    for item in history[-12:]:
        role = "用户" if item.get("role") == "user" else "助手"
        snippet = (item.get("text") or "")[:500]
        if item.get("image"):
            snippet += "（这一轮已经生成了图）"
        lines.append(f"{role}：{snippet}")
    lines.append("本轮用户：" + text[:2000])
    return "\n".join(lines)


def orchestrate(
    api_key: str,
    base_url: str,
    user_text: str,
    system: str | None = None,
    parser=None,
    max_tokens: int = 1200,
    seconds: int = 50,
) -> tuple[dict | None, list[dict]]:
    chain = [item for item in load_chain() if item["enabled"]]
    moment = time.time()
    health = health_map()
    fresh, cooling = [], []
    for item in chain:
        until = float((health.get(item["id"]) or {}).get("until") or 0)
        kind = (health.get(item["id"]) or {}).get("kind") or ""
        if until > moment:
            if kind != "denied":
                cooling.append(item)
        else:
            fresh.append(item)
    ordered = fresh or cooling
    tried = []
    deadline = time.time() + max(10, seconds)
    for item in ordered:
        if time.time() >= deadline:
            break
        started = time.time()
        timeout = min(item["timeout"], max(6, int(deadline - started)))
        try:
            raw = _call(api_key, base_url, item["id"], user_text, timeout, system or SYSTEM, max_tokens, deadline)
            plan = (parser or parse_plan)(raw)
            _clear_one(item["id"])
            tried.append({"model": item["id"], "label": item["label"], "ok": True, "ms": int((time.time() - started) * 1000)})
            return plan, tried
        except LlmError as exc:
            _mark(item["id"], exc.kind, exc.cooldown, str(exc))
            tried.append(
                {
                    "model": item["id"],
                    "label": item["label"],
                    "ok": False,
                    "ms": int((time.time() - started) * 1000),
                    "error": str(exc)[:180],
                    "kind": exc.kind,
                }
            )
    return None, tried


def _call(
    api_key: str,
    base_url: str,
    model: str,
    user_text: str,
    timeout: int,
    system: str | None = None,
    max_tokens: int = 1200,
    deadline: float | None = None,
) -> str:
    """Ask once, and if the model ran out of output room, ask again with more room.

    Thinking models spend most of the budget on reasoning, so a short cap returns
    a cut-off answer instead of an error. Retries never run past a caller deadline.
    """
    budget = max_tokens
    text = ""
    for _ in range(3):
        if deadline is not None and time.time() > deadline - 4:
            break
        text, finish = _post_once(api_key, base_url, model, user_text, timeout, system, budget)
        if finish != "length":
            return text
        budget = min(budget * 3, 32000)
    return text


def _post_once(
    api_key: str,
    base_url: str,
    model: str,
    user_text: str,
    timeout: int,
    system: str | None,
    max_tokens: int,
) -> tuple[str, str]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system or SYSTEM},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": False,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        status, raw = _read_http(urllib.request.urlopen(req, timeout=timeout), timeout)
    except urllib.error.HTTPError as exc:
        status, raw = _read_http(exc, timeout)
    except LlmError:
        raise
    except Exception as exc:
        raise LlmError("timeout", f"{model} 超时或网络中断", 300) from exc
    if status >= 400:
        raise _http_error(model, status, raw)
    text = _extract_text(raw)
    if not text.strip():
        raise LlmError("empty", f"{model} 没有返回内容", 120)
    return text, _finish_reason(raw)


def _finish_reason(raw: str) -> str:
    stripped = raw.lstrip()
    if stripped.startswith("data:"):
        reason = ""
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choice = (obj.get("choices") or [{}])[0] or {}
            reason = choice.get("finish_reason") or reason
        return reason
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    choice = (obj.get("choices") or [{}])[0] or {}
    return str(choice.get("finish_reason") or "")


def _read_http(resp, timeout: int) -> tuple[int, str]:
    timer = threading.Timer(max(1, timeout), resp.close)
    timer.daemon = True
    timer.start()
    try:
        raw = resp.read().decode("utf-8", "replace")
        return getattr(resp, "status", None) or getattr(resp, "code", 0), raw
    except Exception as exc:
        raise LlmError("timeout", "编排超时或网络中断", 300) from exc
    finally:
        timer.cancel()
        resp.close()


def _http_error(model: str, status: int, raw: str) -> LlmError:
    low = raw.lower()
    if status == 403 or "not allowed" in low:
        return LlmError("denied", f"{model} 未放行", 12 * 3600)
    if status == 429 or "rate" in low:
        return LlmError("rate", f"{model} 繁忙", 180)
    if "content policy" in low or "content_filter" in low or "unsafe" in low:
        return LlmError("policy", f"{model} 内容拦截", 60)
    if status in (500, 502, 503, 504) or "no available channel" in low:
        return LlmError("down", f"{model} 暂不可用", 300)
    return LlmError("bad", f"{model} 返回 {status}", 120)


def _extract_text(raw: str) -> str:
    stripped = raw.lstrip()
    if stripped.startswith("data:"):
        parts = []
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            parts.append(_choice_text(obj))
        return "".join(parts)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LlmError("bad_json", "返回不是 JSON", 120) from exc
    if isinstance(obj, dict) and obj.get("error"):
        message = obj["error"].get("message") if isinstance(obj["error"], dict) else str(obj["error"])
        raise LlmError("bad", message or "上游错误", 120)
    return _choice_text(obj)


def _choice_text(obj: dict) -> str:
    choices = obj.get("choices") or []
    if not choices:
        return ""
    choice = choices[0] or {}
    message = choice.get("message") or {}
    delta = choice.get("delta") or {}
    content = message.get("content")
    if content is None:
        content = delta.get("content")
    if isinstance(content, list):
        bits = []
        for part in content:
            if isinstance(part, dict):
                bits.append(str(part.get("text") or ""))
            else:
                bits.append(str(part))
        return "".join(bits)
    return content or ""


def parse_plan(text: str) -> dict:
    plan = _json_object(text)
    action = str(plan.get("action") or "").strip().lower()
    if action not in ("generate", "chat"):
        raise LlmError("bad_json", "action 无效", 120)
    prompt = str(plan.get("prompt") or "").strip()
    if action == "generate" and not prompt:
        raise LlmError("bad_json", "缺少提示词", 120)
    plan["action"] = action
    plan["reply"] = str(plan.get("reply") or "").strip()[:300]
    plan["prompt"] = prompt[:4000]
    plan["use_previous_image"] = _as_bool(plan.get("use_previous_image"))
    return plan


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "是"}
    return bool(value)


INTENT = """你是「对画」的对话助手。先理解这一段对话，再决定是接着聊，还是调用生图。只输出一个 JSON。

{"action":"generate"|"chat","reply":"对用户说的话","task":"new"|"edit"|"text"|"swap_person"|"insert"|"realism"|"size","canvas":"none"|"previous"|"upload","medium":"photo"|"keep","basis":"","use_previous_image":false,"memory":"","prompt":"","keep":[],"change":[]}

怎么对话：
- 先看当前记忆和最近对话，再看本轮这句话。它、这张、再暗一点、换成、还是那个，都指当前这张图。
- reply 是说给用户听的一两句中文，像聊天，说明你理解的目的。不要写提示词，不要说自己是模型，不要复述规则。
- 能画就 action=generate，不要为了确认再问一遍。
- 只有看不出要画什么、用户只是在问、或几条要求互相矛盾时，才 action=chat，并用 reply 问一个最关键的问题。这时 prompt 留空。

怎么判断：
- 用户要一张新图，或换了一个新主体：task=new，canvas=none，use_previous_image=false。对话里已经有图，也不能自行改上一张。
- 用户在改刚才那张、延续那张、或只说了调整：task=edit，canvas=previous，use_previous_image=true。
- 上图、上一张、原图，都指已经生成的图。
- 上传图里的人去替换或放进上一张：task=swap_person 或 insert，canvas=previous，use_previous_image=true。上传图不是画布。
- 上传的海报、商品图要改字或改局部：task=text 或 edit，canvas=upload。
- 太假、不真实、要写真、要真实：task=realism，canvas=none，medium=photo。basis 是历史里那句作图要求的原样摘录，不要摘「太假了」。
- 写真、实拍、照片、真实：medium=photo。动漫、插画、水墨、海报：medium=keep。
- 只改尺寸或比例：task=size，canvas=previous，use_previous_image=true。

prompt 和 memory：
- prompt 是交给生图工具的要求。把本轮要求和记忆里已经确定的内容合并。改图时写清改什么、留什么。
- 可以整理语序，不能另起一套故事，不能加用户没说过的人物、品牌、文字或新情节。
- memory 用一句不超过 80 字的话记下当前画面。只写对话里出现过的内容。新主体时改写成新画面，改图时在原记忆上更新。
- keep、change 只摘用户明确说过的短词，没点名就留空。basis 只能是用户说过的连续原文。
"""


def classify(api_key: str, base_url: str, user_text: str) -> tuple[dict | None, list[dict]]:
    return orchestrate(api_key, base_url, user_text, system=INTENT, parser=parse_intent)


def parse_intent(text: str) -> dict:
    plan = _json_object(text)
    action = str(plan.get("action") or "").strip().lower()
    if action not in ("generate", "chat"):
        raise LlmError("bad_json", "action 无效", 120)
    task = str(plan.get("task") or "new").strip()
    if task not in {"new", "edit", "text", "swap_person", "insert", "realism", "size"}:
        task = "new"
    canvas = str(plan.get("canvas") or "none").strip()
    if canvas not in {"none", "previous", "upload"}:
        canvas = "none"
    medium = str(plan.get("medium") or "keep").strip()
    if medium not in {"photo", "keep"}:
        medium = "keep"
    return {
        "action": action,
        "reply": str(plan.get("reply") or "").strip()[:300],
        "task": task,
        "canvas": canvas,
        "medium": medium,
        "basis": str(plan.get("basis") or "").strip()[:500],
        "keep": _short_list(plan.get("keep")),
        "change": _short_list(plan.get("change")),
        "prompt": str(plan.get("prompt") or "").strip()[:1200],
        "memory": str(plan.get("memory") or "").strip()[:80],
        "use_previous_image": _as_bool(plan.get("use_previous_image")),
    }


def _short_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = str(item or "").strip()[:12]
        if text and text not in out:
            out.append(text)
        if len(out) == 3:
            break
    return out


def _json_object(text: str) -> dict:
    match_text = text.strip()
    match_text = re.sub(r"^```(?:json)?\s*", "", match_text)
    match_text = re.sub(r"\s*```$", "", match_text)
    start = match_text.find("{")
    end = match_text.rfind("}")
    if start < 0 or end <= start:
        raise LlmError("bad_json", "没有 JSON", 120)
    blob = match_text[start : end + 1]
    blob = blob.replace("\u201c", '"').replace("\u201d", '"')
    blob = re.sub(r",\s*([}\]])", r"\1", blob)
    try:
        plan = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise LlmError("bad_json", "JSON 无法解析", 120) from exc
    if not isinstance(plan, dict):
        raise LlmError("bad_json", "JSON 不是对象", 120)
    return plan


ENHANCE = """你在帮生图模型补画面细节。只输出 JSON：{"detail":"..."}
规则：
1. 用户原话会原样保留在最前面，你只写「补充」的那一句，不要重复、不要改写原话。
2. 只补用户没写、但画面必须有的具体细节：主体长什么样、在哪里、什么时间什么光线、什么材质。
3. 不许写意境、留白、空灵、壮丽这类词；不许另造一套配色；不许加用户没提的人物或文字。
4. 不许写高清、8K、杰作、获奖作品这类空话。
5. 中文，一句话，不超过 80 字。拿不准就不补，detail 输出空字符串。
"""


def enhance(api_key: str, base_url: str, user_text: str) -> str:
    """Ask for a short extra clause that fills gaps, never a rewrite."""
    plan, _tried = orchestrate(
        api_key,
        base_url,
        "用户原话：" + user_text,
        system=ENHANCE,
        parser=parse_detail,
        max_tokens=1200,
        seconds=40,
    )
    return (plan or {}).get("detail") or ""


def parse_detail(text: str) -> dict:
    plan = _json_object(text)
    detail = str(plan.get("detail") or "").strip()
    return {"detail": detail[:160]}


def clean_choice(value, allowed, fallback):
    if value in allowed:
        return value
    if isinstance(value, str) and value.strip() in allowed:
        return value.strip()
    return fallback
