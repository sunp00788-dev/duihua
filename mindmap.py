"""Mind maps stay a text tree. The model writes an outline; it does not draw."""

from __future__ import annotations

import re
from pathlib import Path

import llm

MAX_DEPTH = 6
MAX_NODES = 300
MAX_TEXT = 60
THEMES = ("classic", "dark", "warm", "ocean", "ink")
LAYOUTS = ("right", "both", "down")

OUTLINE_SYSTEM = """你只输出 Markdown 大纲，不要解释，不要代码围栏。
第一行必须是一个一级标题，作为导图的根节点，用用户给出的主题。
后面只用 ##、###、#### 展开。最多四级。
每个标题不超过 20 个字，整棵树不要超过 60 个分支。
只写用户点到的内容。用户没点到的，只补这个主题下必要的同级分支。
不要写景物、配色、留白或画面描述。
"""

DOCUMENT_SYSTEM = """你只根据用户给的文档正文输出 Markdown 大纲，不要解释，不要代码围栏。
第一行是一个一级标题，用文档自己的标题。
后面只用 ##、###、####。最多四级，每个标题不超过 20 个字，整棵树不要超过 60 个分支。
只能整理正文里已经写到的内容。正文没有的主题不要加。
文档里的每一个大章节都要在大纲里出现，不能只写开头几节，每节下面列出它的要点。
不要写景物、配色、留白或画面描述。
"""


class OutlineError(Exception):
    pass


def strip_fence(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:markdown|md)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def accept_outline(text: str) -> dict:
    cleaned = strip_fence(text)
    if len(cleaned) < 2:
        raise llm.LlmError("empty", "没有大纲", 60)
    return {"outline": cleaned[:8000]}


def clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", str(raw or ""))
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("`")
    return text[:MAX_TEXT]


def _notes(items: list[str]) -> list[str]:
    found = []
    for item in items:
        if item not in found:
            found.append(item)
    return found


def _heading_items(text: str) -> list[tuple[int, str]]:
    items = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line.strip())
        if not match:
            continue
        label = clean_text(match.group(2))
        if label:
            items.append((len(match.group(1)), label))
    return items


def _list_items(text: str) -> list[tuple[int, str]]:
    items = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        body = raw.strip()
        match = re.match(r"^(?:[-*+]|\d+[.)])\s+(.+)$", body)
        if not match:
            continue
        label = clean_text(match.group(1))
        if label:
            items.append((indent // 2 + 1, label))
    return items


MARKER = re.compile(r"(├|└|┌)\s*[─\-—]?\s*(.*)$")


def _ascii_items(text: str) -> list[tuple[int, str]]:
    """Lines drawn as a tree: ├─ and └─ mark children, the indent before them gives the level."""
    rows: list[list] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = MARKER.search(line)
        if match:
            label = clean_text(match.group(2))
            if label:
                rows.append([len(line[: match.start()]), label])
            continue
        stripped = line.strip()
        if not rows:
            if stripped.startswith(("【", "［", "[")):
                rows.append([0, clean_text(stripped)])
            continue
        if line[:1] in (" ", "\t", "│"):
            body = clean_text(stripped.strip("│").strip())
            if body:
                rows[-1][1] = clean_text(rows[-1][1] + " " + body)
    widths = [width for width, _ in rows if width > 0]
    unit = min(widths) if widths else 4
    unit = max(1, unit)
    return [(width // unit + 1, label) for width, label in rows]


def _title_items(text: str) -> list[tuple[int, str]]:
    """Chinese numbered sections such as 一、 二、 when the text has no markers."""
    items = []
    for raw in text.splitlines():
        body = raw.strip()
        match = re.match(r"^(?:[一二三四五六七八九十]{1,3}[、.．]|\d{1,2}[、.．])\s*(.+)$", body)
        if match:
            label = clean_text(match.group(1))
            if label:
                items.append((1, label))
    return items


def _with_root(title: str, items: list[tuple[int, str]]) -> list[tuple[int, str]]:
    merged = [(1, clean_text(title))]
    merged.extend((max(2, level + 1), label) for level, label in items)
    return merged


def tree_from_headings(headings: list) -> tuple[dict, list[str]]:
    """Word 的标题样式本身就是层级，直接用，不经过模型。"""
    items = []
    for level, label in headings or []:
        text = clean_text(label)
        if text:
            items.append((max(1, min(level, MAX_DEPTH)), text))
    if len(items) < 2:
        raise OutlineError("没有可用的标题")
    return _tree_from_levels(items)


def structure_tree(text: str) -> tuple[dict, list[str], str] | None:
    """Rebuild the document's own tree instead of asking a model to retell it."""
    body = strip_fence(text)
    if not body:
        return None
    drawn = _ascii_items(body)
    if len(drawn) >= 3:
        tree, notes = _tree_from_levels(_with_root(drawn[0][1], drawn[1:]))
        return tree, notes, "按文档里画好的层级整理"
    heads = _heading_items(body)
    if len(heads) >= 3:
        tree, notes = _tree_from_levels(heads)
        return tree, notes, "按文档里的标题整理"
    bullets = _list_items(body)
    if len(bullets) >= 3:
        first = bullets[0][0]
        rest = [(max(1, level - first), label) for level, label in bullets[1:]]
        tree, notes = _tree_from_levels(_with_root(bullets[0][1], rest))
        return tree, notes, "按文档里的列表层级整理"
    numbered = _title_items(body)
    if len(numbered) >= 3:
        root = clean_text(body.splitlines()[0]) or "导图"
        tree, notes = _tree_from_levels(_with_root(root, numbered))
        return tree, notes, "按文档里的编号章节整理"
    return None


def _tree_from_levels(items: list[tuple[int, str]]) -> tuple[dict, list[str]]:
    if not items:
        raise OutlineError("大纲是空的")
    notes = []
    root = {"text": items[0][1], "children": []}
    stack = [(items[0][0], root)]
    count = 1
    clipped = False
    too_deep = False
    for level, label in items[1:]:
        if count >= MAX_NODES:
            clipped = True
            break
        while len(stack) > 1 and stack[-1][0] >= level:
            stack.pop()
        if len(stack) >= MAX_DEPTH:
            too_deep = True
            continue
        child = {"text": label, "children": []}
        stack[-1][1]["children"].append(child)
        stack.append((level, child))
        count += 1
    if too_deep:
        notes.append(f"超过 {MAX_DEPTH} 级的分支已去掉。")
    if clipped:
        notes.append(f"超过 {MAX_NODES} 个节点的部分已去掉。")
    return root, notes


def build_tree(outline: str) -> tuple[dict, list[str]]:
    text = strip_fence(outline)
    headings = _heading_items(text)
    lists = _list_items(text)
    items = headings if len(headings) >= len(lists) else lists
    if not items:
        raise OutlineError("大纲无法整理成导图")
    return _tree_from_levels(items)


def _clean_style(raw) -> dict:
    """Only keep the few style keys the page knows how to draw."""
    if not isinstance(raw, dict):
        return {}
    style = {}
    for key in ("fill", "stroke", "text"):
        value = str(raw.get(key) or "").strip()
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            style[key] = value.lower()
    size = raw.get("size")
    if isinstance(size, (int, float)) and 10 <= size <= 28:
        style["size"] = int(size)
    if isinstance(raw.get("bold"), bool):
        style["bold"] = raw["bold"]
    shape = str(raw.get("shape") or "").strip()
    if shape in {"round", "rect", "ellipse"}:
        style["shape"] = shape
    return style


def _clean_note(raw) -> str:
    text = re.sub(r"<[^>]+>", "", str(raw or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:200]


def _clean_link(raw) -> str:
    text = str(raw or "").strip()[:300]
    return text if re.match(r"^https?://\S+$", text, re.IGNORECASE) else ""


def validate_tree(raw) -> tuple[dict, list[str]]:
    notes = []
    count = 0

    def walk(node, depth: int):
        nonlocal count
        if not isinstance(node, dict):
            return None
        if depth > MAX_DEPTH:
            notes.append(f"超过 {MAX_DEPTH} 级的分支已去掉。")
            return None
        if count >= MAX_NODES:
            notes.append(f"超过 {MAX_NODES} 个节点的部分已去掉。")
            return None
        text = clean_text(node.get("text") or "")
        if not text:
            return None
        count += 1
        children = []
        for child in node.get("children") or []:
            item = walk(child, depth + 1)
            if item:
                children.append(item)
        built = {"text": text, "children": children}
        style = _clean_style(node.get("style"))
        if style:
            built["style"] = style
        if node.get("collapsed") is True:
            built["collapsed"] = True
        note = _clean_note(node.get("note"))
        if note:
            built["note"] = note
        tag = clean_text(node.get("tag") or "")[:12]
        if tag:
            built["tag"] = tag
        link = _clean_link(node.get("link"))
        if link:
            built["link"] = link
        return built

    tree = walk(raw, 1)
    if not tree:
        raise OutlineError("导图是空的")
    return tree, _notes(notes)


def theme_name(value: str, fallback: str = "classic") -> str:
    if value in THEMES:
        return value
    return fallback if fallback in THEMES else "classic"


def layout_name(value: str, fallback: str = "right") -> str:
    if value in LAYOUTS:
        return value
    return fallback if fallback in LAYOUTS else "right"


def fallback_tree(text: str) -> dict:
    label = clean_text(text) or "未命名"
    return {"text": label, "children": []}


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W_VAL = f"{{{W_NS}}}val"


def read_upload(filename: str, raw: bytes) -> tuple[str, list[tuple[int, str]] | None]:
    """Return document text and heading levels when the file already has a structure."""
    if not raw:
        raise OutlineError("文档是空的")
    if len(raw) > 8_000_000:
        raise OutlineError("文档不要超过 8MB")
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return _plain_document(raw)
    if suffix == ".docx":
        return _docx_document(raw)
    if suffix == ".pdf":
        return _pdf_document(raw), None
    if suffix == ".doc":
        raise OutlineError("旧版 Word 请另存为 docx 后再上传")
    raise OutlineError("只支持 txt、md、docx、pdf")


def _plain_document(raw: bytes) -> tuple[str, list[tuple[int, str]] | None]:
    text = raw.decode("utf-8-sig", "replace").strip()
    if not text:
        raise OutlineError("文档里没有可读的文字")
    items = _heading_items(text)
    if len(items) < 2:
        items = _list_items(text)
    return text[:20000], items if len(items) >= 2 else None


def _docx_document(raw: bytes) -> tuple[str, list[tuple[int, str]] | None]:
    import zipfile
    import xml.etree.ElementTree as ET
    from io import BytesIO

    try:
        archive = zipfile.ZipFile(BytesIO(raw))
        info = archive.getinfo("word/document.xml")
        if info.file_size > 24_000_000:
            raise OutlineError("这份 Word 内容太大，请先拆分")
        xml = archive.read("word/document.xml")
    except OutlineError:
        raise
    except Exception as exc:
        raise OutlineError("这份 Word 打不开，请另存为 docx") from exc
    root = ET.fromstring(xml)
    paragraphs = []
    headings = []
    for paragraph in root.iter(f"{{{W_NS}}}p"):
        texts = [node.text or "" for node in paragraph.iter(f"{{{W_NS}}}t")]
        label = clean_text("".join(texts))
        if not label:
            continue
        paragraphs.append(label)
        level = _docx_level(paragraph)
        if level:
            headings.append((min(level, 6), label))
    if not paragraphs:
        raise OutlineError("文档里没有可读的文字")
    text = "\n".join(paragraphs)[:20000]
    return text, headings if len(headings) >= 2 else None


def _docx_level(paragraph) -> int:
    style = paragraph.find(f"{{{W_NS}}}pPr/{{{W_NS}}}pStyle")
    if style is not None:
        name = str(style.get(W_VAL) or "").lower().replace(" ", "")
        match = re.search(r"(?:heading|标题)(\d)", name)
        if match:
            return int(match.group(1))
    outline = paragraph.find(f"{{{W_NS}}}pPr/{{{W_NS}}}outlineLvl")
    if outline is not None and str(outline.get(W_VAL) or "").isdigit():
        return int(outline.get(W_VAL)) + 1
    return 0


def _pdf_document(raw: bytes) -> str:
    if not raw.startswith(b"%PDF"):
        raise OutlineError("这不是 PDF 文件")
    try:
        from io import BytesIO
        from pypdf import PdfReader
    except Exception as exc:
        raise OutlineError("服务器还不能读取 PDF") from exc
    try:
        reader = PdfReader(BytesIO(raw))
        pages = []
        for page in reader.pages[:40]:
            pages.append(page.extract_text() or "")
    except Exception as exc:
        raise OutlineError("这份 PDF 读不出文字，可能是扫描件") from exc
    text = "\n".join(pages).strip()
    if not text:
        raise OutlineError("这份 PDF 读不出文字，可能是扫描件")
    return text[:20000]
