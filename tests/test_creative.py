"""创意器：空输入不硬画，方向不能另编道具。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import creative

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + str(detail)[:160]) if detail else ""))


def test_empty_needs_a_subject():
    check("blank refused", not creative.ready("", "any", False))
    check("category enough", creative.ready("", "home", False))
    check("image enough", creative.ready("", "any", True))


def test_home_presets_and_size():
    rows = creative.presets("家居")
    check("three directions", len(rows) == 3)
    check("stable id", rows[0]["id"] == "home-1")
    check("scene ratio", creative.size_for("scene") == "4:5")
    check("hero ratio", creative.size_for("主图") == "1:1")


def test_prompt_locks_product_and_keeps_words():
    direction = creative.presets("home")[0]
    prompt = creative.compile_prompt("夏天、干净", direction, True, "home")
    check("user words first", prompt.startswith("夏天、干净"))
    check("product locked", "外形、颜色、包装文字和 logo 必须保持原样" in prompt)
    check("scene included", direction["scene"] in prompt)


def test_no_brand_without_product():
    direction = creative.presets("beauty")[1]
    prompt = creative.compile_prompt("", direction, False, "beauty")
    check("subject named", "一件护肤品" in prompt)
    check("no brand", "不要出现品牌名" in prompt)


def test_invented_prop_dropped():
    base = creative.presets("home")
    rewritten = []
    for item in base:
        copied = dict(item)
        copied["props"] = ["月光", item["props"][0] if item["props"] else "亚麻布"]
        copied["title"] = "更安静的" + item["title"]
        rewritten.append(copied)
    merged = creative.merge_directions(base, rewritten, "想要安静")
    check("title rewritten", merged[0]["title"].startswith("更安静"))
    check("moonlight dropped", all("月光" not in item["props"] for item in merged))
    check("preset prop kept", "亚麻布" in merged[0]["props"])


def test_clean_direction_rejects_blank():
    check("blank direction", creative.clean_direction({}, "", "home") is None)
    cleaned = creative.clean_direction(
        {"title": "窗边", "scene": "白墙", "light": "晨光", "angle": "平视", "props": ["月光", "绿植"]},
        "",
        "home",
    )
    check("known prop", cleaned["props"] == ["绿植"] or "绿植" in cleaned["props"])
    check("unknown prop gone", "月光" not in cleaned["props"])


if __name__ == "__main__":
    test_empty_needs_a_subject()
    test_home_presets_and_size()
    test_prompt_locks_product_and_keeps_words()
    test_no_brand_without_product()
    test_invented_prop_dropped()
    test_clean_direction_rejects_blank()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    raise SystemExit(1 if FAIL else 0)
