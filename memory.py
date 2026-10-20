"""当前对话的记忆卡。只记这一段对话，不跨会话。"""

from __future__ import annotations

import json
import re

_KEEP = re.compile(r"(?:不要动|别动|保持|保留)([^，。！？,\s]{1,12})")
_CHANGE = re.compile(r"(?:只改|改成|换成)([^，。！？,\s]{1,12})")
_LEAD = re.compile(r"^(?:请|帮我|给我)?(?:生成|画一张|画一个|画一幅|画个|来一张|做一张|出一张)")


def empty() -> dict:
    return {"subject": "", "keep": [], "size": "", "image": "", "prompt": ""}


def load(raw) -> dict:
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    card = empty()
    card["subject"] = str(data.get("subject") or "")[:40]
    card["keep"] = _clean(data.get("keep"))
    card["size"] = str(data.get("size") or "")[:16]
    card["image"] = str(data.get("image") or "")[:80]
    card["prompt"] = str(data.get("prompt") or "")[:200]
    return card


def line(card: dict) -> str:
    subject = (card or {}).get("subject") or ""
    if not subject:
        return ""
    size = (card or {}).get("size") or ""
    return "当前：" + subject + (("，" + size) if size else "")


def subject_from(text: str) -> str:
    body = _LEAD.sub("", (text or "").strip())
    body = re.sub(r"^[的了吧啊呀呢\s]+", "", body)
    body = re.split(r"[，。！？\n]", body, maxsplit=1)[0].strip()
    return body[:24]


def hints(text: str) -> tuple[list[str], list[str]]:
    raw = text or ""
    return _clean(_KEEP.findall(raw)), _clean(_CHANGE.findall(raw))


def ground(items, sources: list[str]) -> list[str]:
    blob = "\n".join(sources or [])
    kept = []
    for item in _clean(items):
        if item and item in blob:
            kept.append(item)
    return _clean(kept)


def pick(text: str, model_keep, model_change, sources: list[str], editing: bool) -> tuple[list[str], list[str]]:
    spoken_keep, spoken_change = hints(text)
    keep = _clean(spoken_keep + ground(model_keep, sources))
    change = _clean(spoken_change + ground(model_change, sources))
    if editing and not change:
        short = (text or "").strip()
        if 0 < len(short) <= 24:
            change = [short]
    return keep[:3], change[:3]


def merge(keep: list[str], remembered: list[str], change: list[str]) -> list[str]:
    blocked = set(change or [])
    out = []
    for item in list(keep or []) + list(remembered or []):
        if not item or item in blocked:
            continue
        if any(item in other or other in item for other in blocked):
            continue
        if item not in out:
            out.append(item)
    return out[:3]


def sentence(keep, change) -> str:
    parts = []
    if keep:
        parts.append("保留" + "、".join(keep) + "。")
    if change:
        parts.append("只改" + "、".join(change) + "。")
    return "".join(parts)


def attach(prompt: str, keep, change) -> str:
    extra = sentence(keep, change)
    if not extra or extra in (prompt or ""):
        return prompt
    return ((prompt or "").rstrip() + "\n" + extra).strip()


def with_reply(reply: str, keep, change) -> str:
    extra = sentence(keep, change)
    if not extra or extra in (reply or ""):
        return (reply or "").strip()
    return ((reply or "").rstrip() + " " + extra).strip()


def updated(card: dict, fresh: bool, text: str, keep: list[str], size: str) -> dict:
    base = load(card)
    if fresh or not base["subject"]:
        base["subject"] = subject_from(text)
    base["keep"] = list(keep or [])[:3] if fresh or keep else base["keep"]
    if size:
        base["size"] = str(size)[:16]
    if fresh:
        base["image"] = ""
    base["prompt"] = (text or "")[:200]
    return base


def note_image(card: dict, name: str, size: str) -> dict:
    base = load(card)
    if name:
        base["image"] = str(name)[:80]
    if size:
        base["size"] = str(size)[:16]
    return base


def _clean(items) -> list[str]:
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        text = str(item or "").strip()
        text = text.strip("：:，。、 ")
        if not text or len(text) > 12:
            continue
        if text not in out:
            out.append(text)
    return out[:3]
