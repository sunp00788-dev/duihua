"""记忆卡：保留项和修改项必须来自用户原话。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import memory

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS " if ok else "FAIL ") + name + ((" :: " + str(detail)[:160]) if detail else ""))


def test_drops_invented_words():
    keep, change = memory.pick("不要动白猫，只改光线", ["白猫", "月光"], ["光线"], ["不要动白猫，只改光线"], editing=True)
    check("invented moonlight dropped", "月光" not in keep)
    check("spoken cat kept", "白猫" in keep)
    check("spoken change kept", "光线" in change)


def test_prompt_starts_with_user_words():
    prompt = memory.attach("暗一点\n只画用户写出的内容。", ["白猫"], ["光线"])
    check("original first", prompt.startswith("暗一点"))
    check("keep appended", "保留白猫。" in prompt)
    check("change appended", "只改光线。" in prompt)


def test_replace_does_not_carry_old_keep():
    keep, change = memory.pick("把白猫换成黑猫", [], [], ["把白猫换成黑猫"], editing=True)
    merged = keep
    if not re_search_replace("把白猫换成黑猫"):
        merged = memory.merge(keep, ["白猫"], change)
    check("replacement change", "黑猫" in change)
    check("old cat not forced", "白猫" not in merged)


def re_search_replace(text):
    import re

    return re.search(r"(换成|改成|不要|去掉|删除)", text)


def test_line_and_subject():
    card = memory.updated(memory.empty(), fresh=True, text="生成雨夜窗边的白猫，电影光", keep=["白猫"], size="3:2")
    check("subject trimmed", card["subject"] == "雨夜窗边的白猫")
    check("line", memory.line(card) == "当前：雨夜窗边的白猫，3:2")
    noted = memory.note_image(card, "abc.png", "3:2")
    check("image noted", noted["image"] == "abc.png")


if __name__ == "__main__":
    test_drops_invented_words()
    test_prompt_starts_with_user_words()
    test_replace_does_not_carry_old_keep()
    test_line_and_subject()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    raise SystemExit(1 if FAIL else 0)
