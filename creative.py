"""电商创意方向。只做预设和提示词，不访问网络，也不写数据库。"""

from __future__ import annotations

CATEGORIES = {
    "beauty": "美妆",
    "food": "食品",
    "home": "家居",
    "fashion": "服饰",
    "digital": "数码",
    "any": "不限",
}

SUBJECTS = {
    "beauty": "一件护肤品",
    "food": "一份食品",
    "home": "一件家居用品",
    "fashion": "一件服饰",
    "digital": "一件数码产品",
    "any": "一件商品",
}

USES = {
    "hero": ("主图", "1:1"),
    "scene": ("场景", "4:5"),
    "poster": ("海报", "3:4"),
}

_PRESETS = {
    "beauty": [
        ("石台柔光", "浅色石台，背景是淡暖灰", "柔和侧光", "略俯视", ["薄纱"]),
        ("晨间梳妆", "浅木梳妆台的一角", "窗边晨光", "平视", ["小镜子"]),
        ("水汽特写", "浅色背景，旁边有细小水珠", "柔光", "微距", ["水珠"]),
    ],
    "food": [
        ("木桌早餐", "浅色木桌，像刚摆上的早餐", "自然侧光", "略俯视", ["餐巾"]),
        ("石面俯拍", "浅灰大理石台面", "均匀柔光", "俯拍", ["瓷碟"]),
        ("窗边午后", "靠窗的浅色桌面", "午后窗光", "平视", ["玻璃杯"]),
    ],
    "home": [
        ("亚麻桌面", "米色亚麻桌布", "柔和自然光", "四分之三", ["亚麻布"]),
        ("白墙窗光", "白墙前，旁边有一小块窗光", "窗边侧光", "平视", ["绿植"]),
        ("材质特写", "干净的浅色背景", "侧光", "特写", []),
    ],
    "fashion": [
        ("衣架棚拍", "浅灰背景里的衣架", "柔光", "正面", ["木衣架"]),
        ("床铺叠放", "浅色床品上整齐叠放", "晨光", "俯拍", ["床单"]),
        ("面料特写", "只露出面料纹理的浅背景", "侧光", "微距", []),
    ],
    "digital": [
        ("深色台面", "深灰桌面，背景简洁", "轮廓光", "四分之三", ["桌面"]),
        ("浅灰几何", "浅灰几何台面", "均匀柔光", "略俯视", []),
        ("侧面轮廓", "干净浅背景", "细轮廓光", "侧面", []),
    ],
    "any": [
        ("白底棚拍", "纯白背景", "柔光", "正面", []),
        ("桌面生活", "浅色桌面", "自然光", "四分之三", ["亚麻布"]),
        ("质感特写", "干净浅背景", "侧光", "特写", []),
    ],
}


def normalize_category(value: str) -> str:
    raw = str(value or "").strip().lower()
    aliases = {label: key for key, label in CATEGORIES.items()}
    raw = aliases.get(raw, raw)
    return raw if raw in _PRESETS else "any"


def normalize_use(value: str) -> str:
    raw = str(value or "").strip().lower()
    aliases = {label: key for key, (label, _size) in USES.items()}
    raw = aliases.get(raw, raw)
    return raw if raw in USES else "hero"


def size_for(use: str) -> str:
    return USES[normalize_use(use)][1]


def use_label(use: str) -> str:
    return USES[normalize_use(use)][0]


def ready(text: str, category: str, has_image: bool) -> bool:
    if has_image or (text or "").strip():
        return True
    return normalize_category(category) != "any"


def presets(category: str) -> list[dict]:
    key = normalize_category(category)
    rows = []
    for index, (title, scene, light, angle, props) in enumerate(_PRESETS[key], start=1):
        rows.append(
            {
                "id": f"{key}-{index}",
                "title": title,
                "scene": scene,
                "light": light,
                "angle": angle,
                "props": list(props),
            }
        )
    return rows


def merge_directions(base: list[dict], rewritten, spoken: str) -> list[dict]:
    if not isinstance(rewritten, list) or len(rewritten) < len(base):
        return [dict(item, props=list(item.get("props") or [])) for item in base]
    merged = []
    for index, origin in enumerate(base):
        fresh = rewritten[index] if isinstance(rewritten[index], dict) else {}
        merged.append(
            {
                "id": origin["id"],
                "title": _clip(fresh.get("title"), 24) or origin["title"],
                "scene": _clip(fresh.get("scene"), 40) or origin["scene"],
                "light": _clip(fresh.get("light"), 24) or origin["light"],
                "angle": _clip(fresh.get("angle"), 24) or origin["angle"],
                "props": _props(origin.get("props") or [], fresh.get("props"), spoken, keep_base=True),
            }
        )
    return merged


def clean_direction(raw, spoken: str, category: str) -> dict | None:
    if not isinstance(raw, dict):
        return None
    title = _clip(raw.get("title"), 24)
    scene = _clip(raw.get("scene"), 40)
    light = _clip(raw.get("light"), 24)
    angle = _clip(raw.get("angle"), 24)
    if not (title and scene and light and angle):
        return None
    allowed = []
    for item in presets(category):
        allowed.extend(item.get("props") or [])
    return {
        "id": _clip(raw.get("id"), 32) or "custom",
        "title": title,
        "scene": scene,
        "light": light,
        "angle": angle,
        "props": _props(allowed, raw.get("props"), spoken, keep_base=False),
    }


def compile_prompt(text: str, direction: dict, has_image: bool, category: str) -> str:
    spoken = (text or "").strip()
    subject = SUBJECTS[normalize_category(category)]
    lines = [spoken or f"为{subject}做一张电商图。"]
    if has_image:
        lines.append("第一张图是商品本身。外形、颜色、包装文字和 logo 必须保持原样，不能重画商品，也不能改标签。只更换场景、光线和道具。")
    else:
        lines.append(f"画面主体是{subject}。不要出现品牌名、价格或广告标题，也不要在画面上写字。")
    lines.append(f"场景：{direction['scene']}。光线：{direction['light']}。角度：{direction['angle']}。")
    props = direction.get("props") or []
    if props:
        lines.append("道具只放" + "、".join(props) + "。不要再加别的东西，不要出现人物。")
    else:
        lines.append("不要添加人物，不要在画面上写字。")
    lines.append("商品完整，阴影自然，背景干净，适合电商上架。")
    return "\n".join(lines)


def _props(base: list[str], incoming, spoken: str, keep_base: bool) -> list[str]:
    source = spoken or ""
    known = [str(item).strip() for item in base if str(item).strip()]
    candidates = [incoming] if isinstance(incoming, str) else list(incoming or [])
    if keep_base:
        candidates.extend(known)
    allowed = []
    for item in candidates:
        text = _clip(item, 12)
        if not text or text in allowed:
            continue
        if text in known or text in source:
            allowed.append(text)
        if len(allowed) == 2:
            break
    if allowed:
        return allowed
    return known[:2] if keep_base else []


def _clip(value, limit: int) -> str:
    return str(value or "").strip()[:limit]
