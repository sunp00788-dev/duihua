"""纯文本：生图/改图提示词的拼装，不做任何 IO。"""

from __future__ import annotations

import re

import image


def _prompt_with_size(prompt: str, size: str) -> str:
    if not size:
        return prompt
    return prompt + f"\n输出画面比例为 {size}。"


def _named_color_change(text: str) -> bool:
    raw = text or ""
    if re.search(r"#[0-9A-Fa-f]{3,8}", raw):
        return True
    return bool(re.search(r"(改成|换成|变为|改用|用).{0,6}(红|橙|黄|绿|青|蓝|紫|粉|黑|白|金|灰|色)", raw))


def _color_lock(text: str) -> str:
    if _named_color_change(text):
        return "只改用户点名的那种颜色，其它文字和背景仍用原图的颜色。"
    return "文字颜色、背景色和整体风格保持原图。换字时沿用原位置原来的颜色。提示或建议里的高亮、对比色不要执行。需要强调的词只加大或加粗，不要新造颜色。"


def reference_prompt(model: str, text: str, reference_count: int) -> str:
    count = max(1, reference_count)
    change = text.strip()
    if image.model_family(model) == "gpt":
        lines = [
            "编辑第一张图，只替换用户给出的文字：" + change,
            "构图、背景、商品和字体气质保持第一张。排版可以按用户给的层级稍微理顺，让它更好看。",
            _color_lock(change),
        ]
    else:
        lines = [
            "第一张图是画布。只替换用户给出的文字，构图、背景、商品和字体气质保持第一张。",
            "用户要求：" + change,
            "层级可以按用户写的大小关系理顺，让版面更好看。",
            _color_lock(change),
        ]
    if count > 1:
        lines.append(f"后面{count - 1}张只提供用户点名的素材，不能替换第一张的背景。")
    return "\n".join(lines)


def composite_prompt(model: str, text: str, reference_count: int, swap: bool = False) -> str:
    change = text.strip()
    if swap:
        if image.model_family(model) == "gpt":
            lines = [
                "第一张是要保留的画面。用后面图里的同一个人替换第一张里的人物，脸、发型和身份以后面的图为准。",
                "第一张的背景、构图、姿势、衣服和光线保持不变。不要输出后面那张图的构图，也不要新造一张脸。",
                "用户要求：" + change,
            ]
        else:
            lines = [
                "第一张图是画布。只替换其中的人物，背景、构图、姿势、衣服和光线保持第一张。",
                f"后面{max(1, reference_count)}张只提供要换成的那个人，不要把它们的背景、姿势或画幅带进来。",
                "人物身份必须来自后面的图，不要随机生成一张新的脸。输出仍然是第一张的场景。",
                "用户要求：" + change,
            ]
        return "\n".join(lines)
    if image.model_family(model) == "gpt":
        lines = [
            "第一张是必须保留的场景。从后面的图取出用户指定的人物或物体，放进第一张里自然的位置。",
            "大小、光线、阴影和地面接触要像这个人原来就在第一张照片里。第一张的风景、构图和颜色不要改。",
            "用户要求：" + change,
        ]
    else:
        lines = [
            "第一张图是画布，保留它的场景、构图、光线和比例。",
            f"后面{max(1, reference_count)}张只提供要添加的人物或物体，不要把它们的背景带进来，也不要当成新画布。",
            "把用户指定的内容放进第一张的合适位置，边缘和比例要自然。",
            "用户要求：" + change,
        ]
    return "\n".join(lines)


def edit_prompt(model: str, text: str, reference_count: int) -> str:
    change = text.strip()
    if image.model_family(model) == "gpt":
        lines = [
            "编辑第一张图，只改这一处：" + change,
            "没提到的构图、光线、姿势和背景保持原图。",
            _color_lock(change),
        ]
    else:
        lines = [
            "第一张图是画布。只按这个要求修改：" + change,
            "没提到的地方保持第一张：构图、光线、视角、姿势和背景都不要变。",
            _color_lock(change),
        ]
    if reference_count:
        lines.append(f"后面{reference_count}张只提供用户指定的人物、物体或风格，不要把它们的背景当成新画布。")
    lines.append("除非用户要求改比例，否则输出比例与第一张原图一致。")
    return "\n".join(lines)


def mask_edit_prompt(model: str, text: str) -> str:
    change = text.strip()
    removing = bool(re.search(r"(删除|删掉|去掉|去除|移除|抹掉|擦掉|清掉|拿掉|盖掉)", change))
    pick: list[str] = []
    if removing:
        pick = [
            "凡是标注范围内的东西都要消失，被它们遮住的背景要按周围的纹理和光线自然补全，不要留白色或灰色补丁。",
            "不要把红色标记本身当成要保留的内容，成品里不能出现红圈、箭头或标注文字。",
        ]
    if image.model_family(model) == "gpt":
        lines = [
            "编辑第一张图，只改标注指出的地方：" + change,
            "第二张用颜色标出可改区域，第三张的白色是同一区域。白色以外保持第一张原图，并去掉所有标记。",
        ]
        lines.extend(pick)
        lines.append(_color_lock(change))
        return "\n".join(lines)
    lines = [
        "第一张图是画布。只按这个要求修改：" + change,
        "第二张是同一张图，红色涂抹、圆圈、箭头和文字标出唯一允许修改的区域。",
        "第三张是黑白遮罩，白色是同一块可改区域。只改白色区域里的内容，白色以外保持第一张原图不变，边缘自然衔接。",
        "圈住的是一整块区域，不只是那根线：圈内也算可改范围。",
    ]
    lines.extend(pick)
    lines.extend(
        [
            "最终画面必须去掉红色标记、箭头和标注文字。",
            "如果还有更多参考图，只提供用户指定的人物、物体或风格，不要把参考图当成新画布。",
            "除非用户要求改比例，否则输出比例与第一张原图一致。",
            _color_lock(change),
        ]
    )
    return "\n".join(lines)


def size_only_prompt(previous_prompt: str, text: str) -> str:
    base = re.sub(r"\n输出画面比例为 .+$", "", (previous_prompt or "").strip())
    instruction = (
        "这是同一张图的尺寸调整，不是新图。主体、姿势、光线、背景、材质和构图都保持参考图，"
        "只按用户本次要求改变画幅或分辨率。提示词里的旧比例若与本次要求冲突，以本次要求为准。"
        "不要重新设计画面。用户要求：" + text.strip()
    )
    return (base + "\n" + instruction).strip() if base else instruction
