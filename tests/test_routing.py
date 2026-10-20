"""路由表驱动测试：把历史踩过的坑固化成用例，不联网、不落库。

运行：python tests/test_routing.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import router

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name if not detail else f"{name} :: {detail}")
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + str(detail)[:120]) if detail else ""))


PREV = {"name": "prev.jpg", "prompt": "上一张的提示词", "size": "16:9"}
HISTORY = [
    {"role": "user", "text": "生成一个高山流水的写真图片"},
    {"role": "assistant", "text": "好"},
]


def decide(text, **kwargs):
    ctx = {
        "text": text,
        "annotated": False,
        "has_previous": False,
        "uploads": [],
        "urls": [],
        "source_image": "",
        "previous": None,
        "meta": {},
        "model": "gemini-nano-banana-2.1-ext",
        "history": [],
        "ui_size": "auto",
        "ui_use_previous": False,
    }
    ctx.update(kwargs)
    return router.decide(ctx)


# 1. 说清楚的新图：原话直通，不挂上一张
d = decide("生成一个高山流水的写真图片", has_previous=True, previous=PREV, history=HISTORY)
check("直出命中", d["reason"] == "direct", d["reason"])
check("直出原话在前", d["plan"]["prompt"].startswith("生成一个高山流水的写真图片"), d["plan"]["prompt"][:30])
check("直出不挂上一张", d["plan"]["use_previous_image"] is False)
check("直出带相机描述", "真实相机" in d["plan"]["prompt"])
check("直出不加意境", "意境" not in d["plan"]["prompt"] and "留白" not in d["plan"]["prompt"])

# 2. 太假：按原主题重拍，不挂上一张
d = decide("要的是写真图，这太假了", has_previous=True, previous=PREV, history=HISTORY)
check("重拍命中", d["reason"] == "realism", d["reason"])
check("重拍不挂上一张", d["plan"]["use_previous_image"] is False)
check("重拍带原主题", "高山流水" in d["plan"]["prompt"], d["plan"]["prompt"][:40])
check("重拍不写保持原图", "保持原图" not in d["plan"]["prompt"])

# 3. 上传人物放进上一张
d = decide(
    "将上图中的人物换成我上传图片中的人物",
    has_previous=True,
    previous=PREV,
    uploads=["annotation.jpg"],
)
check("换人命中", d["reason"] == "swap_person", d["reason"])
check("换人用上一张当画布", d["source_image"] == "prev.jpg")
check("换人提示禁止新脸", "不要随机生成一张新的脸" in d["plan"]["prompt"])
check("换人标记编辑", d["editing"] is True)

# 4. 没有上传却说「放到上一张」→ 不按合成处理
d = decide("把人物放到上一张的草地上", has_previous=True, previous=PREV)
check("缺来源不走合成", d["reason"] != "swap_person" and d["reason"] != "insert", d["reason"])

# 5. 标注编辑：用选中的那张图，不能被上一张顶掉
d = decide(
    "在右上角写上李白的诗",
    annotated=True,
    has_previous=True,
    previous=PREV,
    source_image="chosen.jpg",
    meta={"size": "4:3"},
)
check("标注命中", d["reason"] == "annotation", d["reason"])
check("标注用选中的图", d["source_image"] == "chosen.jpg", d["source_image"])
check("标注提示限定区域", "遮罩" in d["plan"]["prompt"])
check("标注带上原图比例", d["ui_size"] == "4:3", d["ui_size"])

# 6. 改上一张（无标注）
d = decide("把背景换成海边", has_previous=True, previous=PREV)
check("改上一张命中", d["reason"] == "edit_previous", d["reason"])
check("改图挂上一张", d["plan"]["use_previous_image"] is True)

# 7. 只改尺寸
d = decide("改成 16:9", has_previous=True, previous=PREV)
check("改尺寸命中", d["reason"] == "size_only", d["reason"])
check("改尺寸复用原提示词", "上一张的提示词" in d["plan"]["prompt"], d["plan"]["prompt"][:30])
check("改尺寸不改画面", "不是新图" in d["plan"]["prompt"] or "尺寸调整" in d["plan"]["prompt"])

# 8. 上传海报改文字 → 参考图路径
d = decide("请把海报上的文案换成新的", uploads=["poster.png"])
check("参考图命中", d["reason"] == "reference", d["reason"])
check("参考图提示要求保留版式", "构图" in d["plan"]["prompt"])

# 9. 上传图但点名要重排版式 → 不走版式锁定
d = decide("按这张图重新设计一版，排版全部换掉", uploads=["poster.png"])
check("重排版式不锁版式", d["reason"] != "reference", d["reason"])

# 10. 纯聊天交给编排
d = decide("今天天气不错")
check("聊天交给编排", d["reason"] == "needs_llm", d["reason"])
check("聊天不硬造图", d["plan"] is None)

# 11. 用户点名尺寸时不被上一张覆盖
d = decide("把背景换成海边", has_previous=True, previous=PREV, ui_size="1:1")
check("点名的尺寸保留", d["ui_size"] == "1:1", d["ui_size"])

# 12. 勾了参考上一张 + 上传图 → 版式路径要带上上一张
d = decide("按这张的风格做一版海报", uploads=["poster.png"], ui_use_previous=True)
check("版式路径尊重勾选", d["plan"]["use_previous_image"] is True)

print("\n==== %d passed, %d failed ====" % (len(PASS), len(FAIL)))
if FAIL:
    for item in FAIL:
        print("  FAILED:", item)
    raise SystemExit(1)
