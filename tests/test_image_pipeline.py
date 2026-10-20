"""图片后处理与请求构造的纯函数测试，不联网、不写库。

运行：python tests/test_image_pipeline.py
"""

import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import image

PASS, FAIL = [], []
TMP = Path(tempfile.mkdtemp(prefix="duihua-test-"))


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name if not detail else f"{name} :: {detail}")
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + str(detail)[:110]) if detail else ""))


def make_canvas(path, size=(400, 300), color=(20, 160, 90)):
    img = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(img)
    draw.rectangle((250, 180, 340, 260), fill=(200, 60, 60))
    img.save(path, "PNG")
    return path


def make_mask(path, boxes, size=(400, 300)):
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for box in boxes:
        draw.rectangle(box, fill=255)
    mask.save(path, "PNG")
    return path


# --- 蒙版区域识别 ---
mask3 = make_mask(TMP / "m3.png", [(20, 20, 80, 70), (200, 40, 260, 100), (100, 200, 150, 250)])
boxes = image.mask_boxes(mask3)
check("识别出 3 块区域", len(boxes) == 3, boxes)
check("区域落在 0..1", all(0 <= v <= 1 for b in boxes for v in b), boxes)

tiny = make_mask(TMP / "tiny.png", [(5, 5, 11, 11)])
check("太小的噪点被忽略", image.mask_boxes(tiny) == [], image.mask_boxes(tiny))

empty = make_mask(TMP / "empty.png", [])
check("空蒙版无区域", image.mask_boxes(empty) == [])
check("空蒙版不合成", image.mask_composite(make_canvas(TMP / "c0.png"), empty, TMP / "c0.png", TMP / "o0.png") is False)

# --- 外接框与位置词 ---
union = image.mask_union(mask3)
check("外接框包含全部区域", union is not None and union[0] <= 0.05 and union[3] >= 0.83, union)
check("外接框被夹在 0..1", all(0 <= v <= 1 for v in union), union)
check("空蒙版没有外接框", image.mask_union(empty) is None)

words = image.region_words(boxes)
check("位置词数量一致", len(words) == 3, words)
check("位置词可读", all(w.endswith("一处") for w in words), words)
check("左上区域说左上", image.region_words([(0.0, 0.0, 0.2, 0.2)]) == ["上左一处"], image.region_words([(0.0, 0.0, 0.2, 0.2)]))
check("居中区域说正中", image.region_words([(0.4, 0.4, 0.6, 0.6)]) == ["正中一处"], image.region_words([(0.4, 0.4, 0.6, 0.6)]))

# --- 裁剪 ---
canvas = make_canvas(TMP / "c.png")
crop = TMP / "crop.png"
px = image.crop_to(canvas, (0.5, 0.5, 0.9, 0.95), crop)
check("裁剪返回像素框", isinstance(px, tuple) and len(px) == 4, px)
check("裁剪尺寸正确", Image.open(crop).size == (px[2] - px[0], px[3] - px[1]), Image.open(crop).size)
check("过小的框不裁", image.crop_to(canvas, (0.5, 0.5, 0.51, 0.51), TMP / "nope.png") is None)
check("不存在的源文件返回空", image.crop_to(TMP / "missing.png", (0, 0, 1, 1), TMP / "x.png") is None)

# --- 贴回 ---
edited = Image.new("RGB", (60, 60), (10, 10, 200))
edited_path = TMP / "edited.png"
edited.save(edited_path)
full = TMP / "full.png"
ok = image.paste_crop(canvas, edited_path, px, full)
check("贴回成功", ok is True)
merged = Image.open(full).convert("RGB")
check("贴回不改变画布尺寸", merged.size == Image.open(canvas).size, merged.size)
check("贴回位置正确", merged.getpixel((px[0] + 2, px[1] + 2))[2] > 150, merged.getpixel((px[0] + 2, px[1] + 2)))
check("框外仍是原图", merged.getpixel((5, 5)) == Image.open(canvas).convert("RGB").getpixel((5, 5)))

# --- 蒙版合成 ---
mask = make_mask(TMP / "m.png", [(250, 180, 340, 260)])
painted = Image.new("RGB", (400, 300), (0, 0, 255))
painted_path = TMP / "painted.png"
painted.save(painted_path)
out = TMP / "out.png"
check("合成成功", image.mask_composite(canvas, mask, painted_path, out) is True)
result = Image.open(out).convert("RGB")
source = Image.open(canvas).convert("RGB")
check("圈内被替换", result.getpixel((300, 220))[2] > 150, result.getpixel((300, 220)))
check("圈外保持不变", result.getpixel((20, 20)) == source.getpixel((20, 20)), (result.getpixel((20, 20)), source.getpixel((20, 20))))
offscreen = sum(
    1
    for y in range(0, 300, 7)
    for x in range(0, 236, 7)
    if result.getpixel((x, y)) != source.getpixel((x, y))
)
check("远处像素完全没动", offscreen == 0, offscreen)

# --- 文件类型嗅探 ---
check("识别 png", image.sniff_ext(b"\x89PNG\r\n\x1a\n" + b"0" * 8) == ".png")
check("识别 jpg", image.sniff_ext(b"\xff\xd8\xff\xe0" + b"0" * 8) == ".jpg")
check("识别 webp", image.sniff_ext(b"RIFF" + b"0" * 4 + b"WEBP") == ".webp")
check("识别 gif", image.sniff_ext(b"GIF89a" + b"0" * 8) == ".gif")
check("乱码给默认后缀", image.sniff_ext(b"not-an-image") not in {".png", ".jpg", ".webp", ".gif"}, image.sniff_ext(b"not-an-image"))

# --- 请求构造 ---
payload = image.build_payload("gemini-nano-banana-2.1-ext", "画一只猫", "16:9", "2k", "high", 2, ["data:image/png;base64,AAA"])
check("payload 模型正确", payload["model"] == "gemini-nano-banana-2.1-ext")
check("payload 带比例", payload.get("size") == "16:9")
check("payload 带分辨率", payload.get("resolution") == "2k")
check("payload 带清晰度", payload.get("quality") == "high")
check("payload 带张数", payload.get("n") == 2)
check("payload 带参考图", payload.get("image_urls") == ["data:image/png;base64,AAA"])

check("空比例不传", "size" not in image.build_payload("gemini-nano-banana-2.1-ext", "x", "", "", "", 1, []))
check("单张不传 n", "n" not in image.build_payload("gemini-nano-banana-2.1-ext", "x", "", "", "", 1, []))
refs = image.build_payload("gemini-nano-banana-2.1-ext", "x", "", "", "", 1, [f"r{i}" for i in range(9)])
check("参考图最多五张", len(refs["image_urls"]) == 5, len(refs["image_urls"]))
try:
    image.build_payload("unknown-model", "x", "", "", "", 1, [])
    check("未知模型被拒", False)
except image.ImageError:
    check("未知模型被拒", True)
try:
    image.build_payload("gemini-nano-banana-2.1-ext", "   ", "", "", "", 1, [])
    check("空提示词被拒", False)
except image.ImageError:
    check("空提示词被拒", True)

# --- 尺寸换算 ---
size, note = image.resolve_size("画一张竖版海报", "auto", "", "gemini-nano-banana-2.1-ext")
check("口语比例被换算", size == "9:16", f"{size} {note}")
check("支持的比例不问不响", note == "", note)
size, note = image.resolve_size("画一张 5:3 的图", "auto", "", "gemini-nano-banana-2.1-ext")
check("不支持的比例就近换算", size != "" and size != "5:3", size)
check("换算要说明", bool(note), note)
size, note = image.resolve_size("随便画", "auto", "", "gemini-nano-banana-2.1-ext")
check("没提比例就不传", size == "" and note == "", f"{size} {note}")
recovered, note = image.recover_size("gpt-image-2.5-ext", "16:9", "unsupported size, allowed ratios: 1:1, 4:3", "16:9")
check("比例被拒后能换一个", recovered in {"1:1", "4:3"}, recovered)

print("\n==== %d passed, %d failed ====" % (len(PASS), len(FAIL)))
if FAIL:
    for item in FAIL:
        print("  FAILED:", item)
    raise SystemExit(1)
