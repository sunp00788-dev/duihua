"""把「这次该走哪条路」从 server.py 里拆出来。

这个模块只做判断和拼提示词，不读写数据库、不发网络请求，
所以可以离线用一张表把历史踩过的坑全部固化成用例。
"""

from __future__ import annotations

import image
from prompts import (
    composite_prompt,
    edit_prompt,
    mask_edit_prompt,
    reference_prompt,
    size_only_prompt,
)


def decide(ctx: dict) -> dict:
    """ctx 需要的键：

    text, annotated, has_previous, uploads, urls, source_image, previous,
    model, history

    previous 是上一张成图（name/prompt/size）或 None。
    返回字典里 plan 为 None 表示「走到这里没有可用的规则，交给编排」。
    """
    text = str(ctx.get("text") or "")
    annotated = bool(ctx.get("annotated"))
    has_previous = bool(ctx.get("has_previous"))
    uploads = ctx.get("uploads") or []
    urls = ctx.get("urls") or []
    has_refs = bool(uploads or urls)
    source_image = str(ctx.get("source_image") or "")
    previous = ctx.get("previous") or None
    model = str(ctx.get("model") or "")
    history = ctx.get("history") or []
    ui_size = str(ctx.get("ui_size") or "")
    ui_use_previous = bool(ctx.get("ui_use_previous"))
    meta = ctx.get("meta") or {}
    ref_count = len(uploads) + len(urls)

    out = {
        "plan": None,
        "fresh": False,
        "editing": False,
        "size_only": False,
        "source_image": source_image,
        "ui_size": "",
        "ui_resolution": "",
        "reason": "",
    }

    # 1. 只改尺寸
    if not source_image and previous and image.is_size_only_request(text):
        out.update(
            plan={
                "action": "generate",
                "reply": "按上一张图只改尺寸，画面内容保持不变。",
                "prompt": size_only_prompt(previous.get("prompt") or "", text),
                "use_previous_image": True,
            },
            fresh=False,
            editing=False,
            size_only=True,
            source_image=previous["name"],
            ui_size="auto" if image.parse_requested_ratio(text) else (previous.get("size") or ""),
            ui_resolution=image.parse_resolution(text) or "",
            reason="size_only",
        )
        return out

    reference_gen = has_refs and not annotated and not source_image and not image.is_edit_request(text)
    into_previous = (
        has_previous
        and has_refs
        and not annotated
        and not source_image
        and image.places_into_previous(text)
    )

    # 2. 上传的人物/物体放进上一张
    if into_previous and previous:
        swap = image.replaces_person(text)
        out.update(
            plan={
                "action": "generate",
                "reply": "用上传图里的人物替换上一张图中的人物，场景保持原样。"
                if swap
                else "把上传图里的内容放进上一张图，场景保持原样。",
                "prompt": composite_prompt(model, text, ref_count, swap),
                "use_previous_image": True,
            },
            editing=True,
            source_image=previous["name"],
            ui_size=previous.get("size") or "" if ui_size in {"", "auto"} else ui_size,
            reason="swap_person" if swap else "insert",
        )
        return out

    # 3. 有上传、没有点名改字 → 按参考图版式重画
    layout_lock = reference_gen and not into_previous
    if layout_lock and not image.is_layout_overhaul(text):
        out.update(
            plan={
                "action": "generate",
                "reply": "按参考图的版式重画，只改你点名的内容。",
                "prompt": reference_prompt(model, text, ref_count),
                "use_previous_image": ui_use_previous,
            },
            fresh=True,
            reason="reference",
        )
        return out

    # 4. 标注 / 改上一张
    wants_edit = annotated or bool(source_image) or (has_previous and image.is_edit_request(text))
    if not wants_edit and has_previous and not has_refs:
        wants_edit = image.is_followup_edit(text)
    if wants_edit:
        target = source_image
        if not target and previous:
            target = previous["name"]
        if target:
            out.update(
                plan={
                    "action": "generate",
                    "reply": "按标注区域修改，其余保持原图。"
                    if annotated
                    else "按上一张图修改，没提到的地方保持原图。",
                    "prompt": mask_edit_prompt(model, text) if annotated else edit_prompt(model, text, ref_count),
                    "use_previous_image": True,
                },
                editing=True,
                source_image=target,
                ui_size="auto"
                if image.parse_requested_ratio(text)
                else ((meta.get("size") or (previous or {}).get("size") or "") if ui_size in {"", "auto"} else ui_size),
                reason="annotation" if annotated else "edit_previous",
            )
            return out

    # 5. 说出了要画什么 → 原话直通
    if not has_refs and not annotated and not source_image and image.is_direct_generate(text):
        out.update(
            plan={
                "action": "generate",
                "reply": "按你的原话生成。",
                "prompt": image.faithful_prompt(text),
                "use_previous_image": False,
            },
            fresh=True,
            reason="direct",
        )
        return out

    # 6. 上一张太假 → 按原主题重拍
    if has_previous and not has_refs and not annotated and not source_image and image.is_realism_redo(text):
        earlier = ""
        for item in reversed(history[:-1]):
            if item.get("role") == "user" and (item.get("text") or "").strip():
                earlier = item["text"].strip()
                break
        merged = (earlier + "\n用户补充：" + text.strip()).strip() if earlier else text.strip()
        out.update(
            plan={
                "action": "generate",
                "reply": "按原主题重新拍成真实照片，不沿用刚才那张。",
                "prompt": image.faithful_prompt(merged, True),
                "use_previous_image": False,
            },
            fresh=True,
            reason="realism",
        )
        return out

    out["reason"] = "needs_llm"
    return out
