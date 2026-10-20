const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
let current = null;
let root = null;
let selected = null;
let view = { x: 0, y: 0, scale: 1 };
let sheet = null;
let layer = null;
let dragging = null;
let spacing = 34;
let layoutMode = "right";
let saveTimer = null;
let armed = null;
let forceSave = false;
let matched = new Set();
let history = [];
let future = [];
const boxes = new Map();

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    location.href = "/";
    throw new Error("请先登录");
  }
  if (!response.ok) {
    const error = new Error(data.error || "请求失败");
    error.status = response.status;
    error.payload = data;
    throw error;
  }
  return data;
}

function clean(node) {
  const item = {
    text: String(node.text || ""),
    children: (node.children || []).map(clean),
  };
  if (node.style && Object.keys(node.style).length) item.style = { ...node.style };
  if (node.collapsed) item.collapsed = true;
  if (node.note) item.note = String(node.note);
  if (node.tag) item.tag = String(node.tag);
  if (node.link) item.link = String(node.link);
  return item;
}

function snapshot() {
  return JSON.stringify(clean(root));
}

function arm() {
  if (root && armed === null) armed = snapshot();
}

function pushHistory() {
  if (armed === null) return;
  const now = snapshot();
  if (armed !== now) {
    history.push(armed);
    if (history.length > 40) history.shift();
    future = [];
  }
  armed = null;
}

function applySnapshot(text) {
  root = JSON.parse(text);
  selected = null;
  draw();
  syncPanel();
  markDirty();
}

function undo() {
  if (!history.length) return;
  const prev = history.pop();
  future.push(snapshot());
  applySnapshot(prev);
  $("saveState").textContent = "已撤销";
}

function redo() {
  if (!future.length) return;
  const next = future.pop();
  history.push(snapshot());
  applySnapshot(next);
  $("saveState").textContent = "已重做";
}

function markDirty() {
  if (!current) return;
  $("saveState").textContent = "未保存";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    save().catch((error) => {
      $("saveState").textContent = "保存失败：" + error.message;
    });
  }, 800);
}

function toggleCollapse(node) {
  if (!node.children || !node.children.length) return;
  arm();
  node.collapsed = !node.collapsed;
  pushHistory();
  draw();
  markDirty();
}

function themeName(name) {
  return MindRender.PALETTE[name] ? name : "classic";
}

function palette() {
  return MindRender.PALETTE[themeName(current && current.theme)] || MindRender.PALETTE.classic;
}

function el(name, attrs = {}) {
  const node = document.createElementNS(NS, name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
}

function applyView() {
  if (!layer) return;
  layer.setAttribute("transform", `translate(${view.x} ${view.y}) scale(${view.scale})`);
  $("board").style.background = palette().bg;
}

function fit() {
  if (!sheet) return;
  const box = $("board").getBoundingClientRect();
  const drawer = $("outlineDrawer");
  const reserved = drawer && !drawer.classList.contains("hidden") ? Math.min(430, box.width * 0.5) : 0;
  const availW = Math.max(160, box.width - reserved);
  const width = Number(sheet.dataset.w) || 1;
  const height = Number(sheet.dataset.h) || 1;
  const scale = Math.min(1.2, (availW - 56) / width, (box.height - 56) / height);
  view.scale = Math.max(0.2, scale);
  view.x = Math.max(16, (availW - width * view.scale) / 2);
  view.y = Math.max(16, (box.height - height * view.scale) / 2);
  applyView();
}

function draw() {
  if (!root) return;
  const size = MindRender.prepare(root, { gapX: spacing, layout: layoutMode });
  const paint = palette();
  const svg = el("svg", { class: "sheet", width: "100%", height: "100%" });
  svg.dataset.w = size.width;
  svg.dataset.h = size.height;
  const group = el("g");
  const edges = el("g");
  function edgeColor(node, child) {
    const own = (child.style && child.style.stroke) || "";
    const up = (node.style && node.style.stroke) || "";
    return own || up || paint.line;
  }
  function edgePath(node, child) {
    if (layoutMode === "down") {
      const x1 = node._x + node._w / 2;
      const y1 = node._y + node._h;
      const x2 = child._x + child._w / 2;
      const y2 = child._y;
      const mid = y1 + Math.max(12, (y2 - y1) / 2);
      return `M ${x1} ${y1} V ${mid} H ${x2} V ${y2}`;
    }
    const toLeft = child._x + child._w <= node._x + 1;
    if (toLeft) {
      const x1 = node._x;
      const y1 = node._y + node._h / 2;
      const x2 = child._x + child._w;
      const y2 = child._y + child._h / 2;
      const mid = x1 - Math.max(14, (x1 - x2) / 2);
      return `M ${x1} ${y1} H ${mid} V ${y2} H ${x2}`;
    }
    const x1 = node._x + node._w;
    const y1 = node._y + node._h / 2;
    const x2 = child._x;
    const y2 = child._y + child._h / 2;
    const mid = x1 + Math.max(14, (x2 - x1) / 2);
    return `M ${x1} ${y1} H ${mid} V ${y2} H ${x2}`;
  }
  (function walk(node) {
    if (node.collapsed) return;
    (node.children || []).forEach((child) => {
      edges.appendChild(
        el("path", {
          d: edgePath(node, child),
          fill: "none",
          stroke: edgeColor(node, child),
          "stroke-width": 1.6,
          "stroke-linecap": "round",
          "stroke-linejoin": "round",
        })
      );
      walk(child);
    });
  })(root);
  group.appendChild(edges);

  const nodes = el("g");
  boxes.clear();
  (function build(node) {
    const depth = node._depth;
    const custom = node.style || {};
    const base =
      depth === 0
        ? { fill: paint.rootFill, stroke: "none", width: 0, color: paint.rootText, radius: 9 }
        : depth === 1
        ? { fill: paint.secondFill, stroke: paint.secondStroke, width: 1.6, color: paint.secondText, radius: 8 }
        : { fill: paint.leafFill, stroke: paint.leafStroke, width: 1, color: paint.leafText, radius: 8 };
    const style = {
      fill: custom.fill || base.fill,
      stroke: custom.stroke || base.stroke,
      width: custom.stroke ? Number(custom.width) || 1.6 : base.width,
      color: custom.text || base.color,
      radius: base.radius,
      shape: custom.shape || "round",
    };
    const item = el("g", { "data-node": "1" });
    item.__node = node;
    node._paint = style;
    if (matched.has(node)) {
      item.appendChild(
        el("rect", {
          x: node._x - 4,
          y: node._y - 4,
          width: node._w + 8,
          height: node._h + 8,
          rx: 10,
          ry: 10,
          fill: paint.tagFill,
          "fill-opacity": 0.18,
        })
      );
    }
    let rect;
    if (style.shape === "ellipse") {
      rect = el("ellipse", {
        cx: node._x + node._w / 2,
        cy: node._y + node._h / 2,
        rx: node._w / 2,
        ry: node._h / 2,
      });
    } else {
      rect = el("rect", {
        x: node._x,
        y: node._y,
        width: node._w,
        height: node._h,
        rx: style.shape === "rect" ? 0 : style.radius,
        ry: style.shape === "rect" ? 0 : style.radius,
      });
    }
    rect.setAttribute("fill", style.fill);
    rect.setAttribute("stroke", style.stroke);
    rect.setAttribute("stroke-width", style.width);
    rect.setAttribute("data-box", "1");
    item.appendChild(rect);
    if (node === selected) {
      const pad = 4;
      let outline;
      if (style.shape === "ellipse") {
        outline = el("ellipse", {
          cx: node._x + node._w / 2,
          cy: node._y + node._h / 2,
          rx: node._w / 2 + pad,
          ry: node._h / 2 + pad,
        });
      } else {
        outline = el("rect", {
          x: node._x - pad,
          y: node._y - pad,
          width: node._w + pad * 2,
          height: node._h + pad * 2,
          rx: style.shape === "rect" ? 2 : style.radius + pad,
          ry: style.shape === "rect" ? 2 : style.radius + pad,
        });
      }
      outline.setAttribute("fill", "none");
      outline.setAttribute("stroke", "#e39a4a");
      outline.setAttribute("stroke-width", "1.6");
      outline.setAttribute("stroke-dasharray", "5 4");
      outline.setAttribute("pointer-events", "none");
      outline.setAttribute("class", "sel-outline");
      item.appendChild(outline);
    }
    const font = MindRender.fontOf(node, depth);
    let cursor = node._y + MindRender.NODE.padY;
    if (node._tag) {
      const pill = el("rect", {
        x: node._x + MindRender.NODE.padX,
        y: cursor,
        width: node._tagWidth,
        height: MindRender.NODE.tagHeight,
        rx: 9,
        ry: 9,
        fill: paint.tagFill,
      });
      item.appendChild(pill);
      const tagText = el("text", {
        x: node._x + MindRender.NODE.padX + MindRender.NODE.tagPadX,
        y: cursor + MindRender.NODE.tagHeight / 2,
        "font-family": MindRender.NODE.family,
        "font-size": 11,
        fill: paint.tagText,
        "dominant-baseline": "central",
      });
      tagText.textContent = node._tag;
      item.appendChild(tagText);
      cursor += MindRender.NODE.tagHeight + MindRender.NODE.tagGap;
    }
    node._lines.forEach((line, index) => {
      const base2 = cursor + node._lineHeight * index + node._lineHeight / 2;
      const text = el("text", {
        x: style.shape === "ellipse" ? node._x + node._w / 2 : node._x + MindRender.NODE.padX,
        y: base2,
        "font-family": MindRender.NODE.family,
        "font-size": font.size,
        "font-weight": font.bold ? "bold" : "normal",
        "text-anchor": style.shape === "ellipse" ? "middle" : "start",
        fill: style.color,
        "dominant-baseline": "central",
      });
      text.textContent = line;
      item.appendChild(text);
    });
    if (node._noteLines && node._noteLines.length) {
      const noteTop = cursor + node._lines.length * node._lineHeight + MindRender.NODE.noteGap;
      node._noteLines.forEach((line, index) => {
        const text = el("text", {
          x: style.shape === "ellipse" ? node._x + node._w / 2 : node._x + MindRender.NODE.padX,
          y: noteTop + node._noteLineHeight * index + node._noteLineHeight / 2,
          "font-family": MindRender.NODE.family,
          "font-size": MindRender.noteSize(node, depth),
          "text-anchor": style.shape === "ellipse" ? "middle" : "start",
          fill: paint.note,
          "dominant-baseline": "central",
        });
        text.textContent = line;
        item.appendChild(text);
      });
    }
    if (node.link) {
      const mark = el("text", {
        x: node._x + node._w - 10,
        y: node._y + 12,
        "text-anchor": "end",
        "font-size": 11,
        fill: paint.line,
        "font-family": MindRender.NODE.family,
        style: "cursor:pointer",
      });
      mark.textContent = "↗";
      mark.addEventListener("pointerdown", (event) => {
        event.stopPropagation();
        if (/^https?:\/\//i.test(node.link)) window.open(node.link, "_blank", "noopener");
      });
      item.appendChild(mark);
    }
    item.addEventListener("pointerdown", (event) => {
      trackPointer(event);
      event.stopPropagation();
      if (!pinch) select(node);
    });
    item.addEventListener("dblclick", (event) => {
      event.stopPropagation();
      arm();
      editNode(node, rect);
    });
    boxes.set(node, rect);
    nodes.appendChild(item);
    const kids = node.children || [];
    if (kids.length) {
      const toLeft =
        layoutMode === "both" &&
        node !== root &&
        node._x + node._w / 2 < root._x + root._w / 2;
      let cx;
      let cy;
      if (layoutMode === "down") {
        cx = node._x + node._w / 2;
        cy = node._y + node._h + 11;
      } else if (toLeft) {
        cx = node._x - 11;
        cy = node._y + node._h / 2;
      } else {
        cx = node._x + node._w + 11;
        cy = node._y + node._h / 2;
      }
      const dot = el("g", { class: "smm-toggle" });
      dot.appendChild(el("circle", { cx, cy, r: 8, fill: paint.secondFill, stroke: paint.line, "stroke-width": 1.2 }));
      const sign = el("text", {
        x: cx,
        y: cy,
        "text-anchor": "middle",
        "dominant-baseline": "central",
        "font-size": 11,
        fill: paint.line,
        "font-family": MindRender.NODE.family,
      });
      sign.textContent = node.collapsed ? String(kids.length) : "−";
      dot.appendChild(sign);
      dot.addEventListener("pointerdown", (event) => {
        event.stopPropagation();
        toggleCollapse(node);
      });
      item.appendChild(dot);
    }
    if (!node.collapsed) kids.forEach(build);
  })(root);
  group.appendChild(nodes);
  svg.appendChild(group);
  $("board").replaceChildren(svg);
  sheet = svg;
  layer = group;
  applyView();
}

function select(node) {
  selected = node;
  draw();
  syncPanel();
  syncMeta();
  const label = node === root ? "根节点" : node.text;
  $("note").textContent = "已选中：" + label;
}

function syncPanel() {
  const fields = ["editText", "fill", "stroke", "textColor", "fontSize", "bold", "shape", "resetStyle"];
  fields.forEach((id) => { $(id).disabled = !selected; });
  if (!selected) return;
  const custom = selected.style || {};
  const paint = selected._paint || {};
  const font = MindRender.fontOf(selected, selected._depth || 0);
  $("fill").value = custom.fill || paint.fill || "#ffffff";
  $("stroke").value = custom.stroke || (paint.stroke && paint.stroke !== "none" ? paint.stroke : "#2f6f62");
  $("textColor").value = custom.text || paint.color || "#1c1916";
  $("fontSize").value = String(font.size);
  $("bold").checked = !!font.bold;
  $("shape").value = paint.shape || "round";
}

function toOutline(node, depth = 0, lines = []) {
  lines.push("  ".repeat(depth) + (depth === 0 ? node.text : "- " + node.text));
  (node.children || []).forEach((child) => toOutline(child, depth + 1, lines));
  return lines;
}

function parseOutline(text) {
  const raw = String(text || "").split(/\r?\n/);
  const items = [];
  for (const line of raw) {
    if (!line.trim()) continue;
    const match = line.match(/^([ \t]*)([-*+]|\d+[.)])?\s*(#{1,6})?\s*(.*)$/);
    if (!match) continue;
    const indent = match[1].replace(/\t/g, "  ").length;
    let depth = Math.floor(indent / 2) + 1;
    if (match[3]) depth = match[3].length;
    const label = match[4].trim().slice(0, 60);
    if (!label) continue;
    items.push({ depth, text: label });
    if (items.length >= 300) break;
  }
  if (!items.length) throw new Error("大纲是空的");
  const root = { text: items[0].text, children: [] };
  const stack = [{ depth: 1, node: root }];
  items.slice(1).forEach((item) => {
    const depth = Math.max(2, Math.min(6, item.depth));
    while (stack.length > 1 && stack[stack.length - 1].depth >= depth) stack.pop();
    const node = { text: item.text, children: [] };
    stack[stack.length - 1].node.children.push(node);
    stack.push({ depth, node });
  });
  return root;
}

function outlineMarkdown(node, depth = 0) {
  const line = "#".repeat(depth + 1) + " " + node.text;
  const rest = (node.children || []).map((child) => outlineMarkdown(child, depth + 1));
  return [line, ...rest].join("\n");
}

function outlineOpml(node) {
  const esc = (value) =>
    String(value).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
  const build = (item, pad) => {
    const kids = item.children || [];
    const head = `${pad}<outline text="${esc(item.text)}"`;
    if (!kids.length) return `${head}/>`;
    return `${head}>\n${kids.map((child) => build(child, pad + "  ")).join("\n")}\n${pad}</outline>`;
  };
  return `<?xml version="1.0" encoding="UTF-8"?>\n<opml version="2.0">\n<head><title>${esc(node.text)}</title></head>\n<body>\n${build(node, "  ")}\n</body>\n</opml>\n`;
}

function syncOutline() {
  if (!root || $("outlineDrawer").classList.contains("hidden")) return;
  $("outlineText").value = toOutline(root).join("\n");
}

function openOutline() {
  if (!root) {
    $("note").textContent = "先生成一张导图。";
    return;
  }
  $("outlineDrawer").classList.remove("hidden");
  syncOutline();
  $("outlineNote").textContent = "";
}

function applyOutline() {
  if (!root) return;
  try {
    const next = parseOutline($("outlineText").value);
    arm();
    root = next;
    selected = null;
    pushHistory();
    draw();
    fit();
    syncPanel();
    syncMeta();
    markDirty();
    $("outlineNote").textContent = "已应用到导图。";
  } catch (error) {
    $("outlineNote").textContent = error.message;
  }
}

function downloadText(name, text, type) {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function eachNode(node, fn) {
  fn(node);
  (node.children || []).forEach((child) => eachNode(child, fn));
}

function patchMeta(field, value) {
  if (!selected) return;
  const trimmed = String(value || "").trim();
  if (field === "note" && trimmed.length > 200) {
    $("note").textContent = "备注最多 200 字。";
    return;
  }
  if (field === "tag" && trimmed.length > 12) {
    $("note").textContent = "标签最多 12 字。";
    return;
  }
  if (field === "link" && trimmed && !/^https?:\/\/\S+$/i.test(trimmed)) {
    $("note").textContent = "链接要以 http 或 https 开头。";
    return;
  }
  arm();
  if (trimmed) selected[field] = trimmed;
  else delete selected[field];
  pushHistory();
  draw();
  markDirty();
}

function syncMeta() {
  const fields = ["nodeNote", "nodeTag", "nodeLink"];
  fields.forEach((id) => { $(id).disabled = !selected; });
  if (!selected) {
    $("nodeNote").value = "";
    $("nodeTag").value = "";
    $("nodeLink").value = "";
    return;
  }
  $("nodeNote").value = selected.note || "";
  $("nodeTag").value = selected.tag || "";
  $("nodeLink").value = selected.link || "";
}

function findMatches(keyword) {
  const needle = keyword.trim().toLowerCase();
  if (!needle) return [];
  const out = [];
  (function walk(node) {
    const hay = [node.text, node.note, node.tag].filter(Boolean).join(" ").toLowerCase();
    if (hay.includes(needle)) out.push(node);
    (node.children || []).forEach(walk);
  })(root);
  return out;
}

function focusNode(node) {
  if (!node) return;
  const box = $("board").getBoundingClientRect();
  view.scale = Math.max(0.6, Math.min(1.2, view.scale));
  view.x = box.width / 2 - (node._x + node._w / 2) * view.scale;
  view.y = box.height / 2 - (node._y + node._h / 2) * view.scale;
  applyView();
}

function runSearch() {
  if (!root) return;
  const hits = findMatches($("search").value);
  matched = new Set(hits);
  if (!hits.length) {
    draw();
    $("note").textContent = "没有匹配的节点。";
    return;
  }
  const at = hits.indexOf(selected);
  const next = hits[(at + 1) % hits.length];
  select(next);
  focusNode(next);
  $("note").textContent = `第 ${hits.indexOf(next) + 1} / ${hits.length} 个匹配：` + next.text;
}

function patchStyle(patch, cascade = false) {
  if (!selected) return;
  const apply = (node) => {
    const next = { ...(node.style || {}), ...patch };
    Object.keys(next).forEach((key) => {
      if (next[key] === "" || next[key] === undefined || next[key] === null) delete next[key];
    });
    node.style = next;
  };
  if (cascade) eachNode(selected, apply);
  else apply(selected);
  pushHistory();
  draw();
  syncPanel();
  markDirty();
}

function editNode(node, rect) {
  const box = rect.getBoundingClientRect();
  const input = document.createElement("input");
  input.className = "node-edit";
  input.value = node.text;
  input.style.left = box.left + "px";
  input.style.top = box.top + "px";
  input.style.width = Math.max(140, box.width) + "px";
  input.style.height = box.height + "px";
  document.body.appendChild(input);
  input.focus();
  input.select();
  let closed = false;
  const commit = () => {
    if (closed) return;
    closed = true;
    const value = input.value.trim();
    input.remove();
    if (value && value !== node.text) {
      node.text = value.slice(0, 40);
      pushHistory();
      draw();
      markDirty();
    }
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") commit();
    if (event.key === "Escape") {
      closed = true;
      input.remove();
    }
  });
  input.addEventListener("blur", commit);
}

function findParent(node, target = root) {
  for (const child of target.children || []) {
    if (child === node) return target;
    const found = findParent(node, child);
    if (found) return found;
  }
  return null;
}

function show(map) {
  current = map;
  spacing = Math.min(160, Math.max(20, Number(map.spacing) || 34));
  $("spacing").value = String(spacing);
  $("spacingOut").textContent = String(spacing);
  layoutMode = layoutName(map.layout);
  $("layout").value = layoutMode;
  root = clean(map.tree);
  selected = null;
  history = [];
  future = [];
  armed = null;
  forceSave = false;
  matched = new Set();
  clearTimeout(saveTimer);
  $("saveState").textContent = "";
  $("title").textContent = map.title || "导图";
  $("theme").value = themeName(map.theme);
  $("note").textContent = (map.notes || []).join("");
  draw();
  fit();
  syncPanel();
  syncMeta();
  renderList();
}

async function renderList() {
  const data = await api("/api/mindmaps");
  const box = $("list");
  box.replaceChildren();
  data.maps.forEach((item) => {
    const row = document.createElement("div");
    row.className = "row";
    const open = document.createElement("button");
    open.type = "button";
    open.className = current && current.id === item.id ? "on" : "";
    open.textContent = item.title || "未命名";
    open.onclick = async () => {
      const full = await api("/api/mindmaps/" + item.id);
      show(full.map);
    };
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "ghost";
    remove.textContent = "删";
    remove.onclick = async () => {
      if (!confirm("删除这张导图？")) return;
      await api("/api/mindmaps/" + item.id, { method: "DELETE" });
      if (current && current.id === item.id) current = null;
      await renderList();
    };
    row.append(open, remove);
    box.append(row);
  });
}

async function save() {
  if (!current || !root) return null;
  clearTimeout(saveTimer);
  const targetId = current.id;
  let data;
  try {
    data = await api("/api/mindmaps/" + targetId, {
      method: "PUT",
      body: JSON.stringify({
        tree: clean(root),
        theme: $("theme").value,
        spacing,
        layout: layoutMode,
        version: current.revision,
        force: forceSave,
      }),
    });
  } catch (error) {
    if (error.status === 409) {
      forceSave = true;
      $("saveState").textContent = "别处已修改";
      $("note").textContent = "这张导图在别处被改过，没有自动覆盖。再点一次「保存」会以你的版本为准。";
      return null;
    }
    throw error;
  }
  forceSave = false;
  // 保存期间切到别的图了，就别把旧结果写回当前状态
  if (!current || current.id !== targetId) return null;
  current = data.map;
  current.tree = data.map.tree;
  const stamp = new Date().toTimeString().slice(0, 5);
  $("saveState").textContent = "已保存 " + stamp;
  if ((data.map.title || "") !== $("title").textContent) {
    $("title").textContent = data.map.title || "导图";
    renderList().catch(() => {});
  }
  return data.map;
}

function exportSvgText() {
  const width = Number(sheet.dataset.w) || 1;
  const height = Number(sheet.dataset.h) || 1;
  const svg = el("svg", {
    xmlns: NS,
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
  });
  svg.appendChild(el("rect", { x: 0, y: 0, width, height, fill: palette().bg }));
  const content = layer.cloneNode(true);
  content.removeAttribute("transform");
  content.querySelectorAll(".sel-outline").forEach((node) => node.remove());
  content.querySelectorAll("[data-box]").forEach((rect) => {
    if (rect.getAttribute("stroke") === "#e39a4a") {
      rect.setAttribute("stroke", palette().line);
      rect.setAttribute("stroke-width", "1.6");
    }
  });
  svg.appendChild(content);
  return '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(svg);
}

function svgToPng(svgText, width, height, scale) {
  return new Promise((resolve, reject) => {
    const blob = new Blob([svgText], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = Math.ceil(width * scale);
      canvas.height = Math.ceil(height * scale);
      const context = canvas.getContext("2d");
      context.fillStyle = palette().bg;
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/png"));
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("导出失败"));
    };
    image.src = url;
  });
}

async function exported(format) {
  if (!current || !sheet) return;
  await save();
  const width = Number(sheet.dataset.w) || 1;
  const height = Number(sheet.dataset.h) || 1;
  const svgText = exportSvgText();
  const body = { format };
  let download = "";
  if (format === "svg") {
    body.svg = svgText;
    const blob = new Blob([svgText], { type: "image/svg+xml;charset=utf-8" });
    download = URL.createObjectURL(blob);
  } else {
    let scale = 2;
    while (scale > 1 && (width * scale > 12000 || height * scale > 12000)) scale -= 0.25;
    body.data_url = await svgToPng(svgText, width, height, scale);
    download = body.data_url;
  }
  const saved = await api("/api/mindmaps/" + current.id + "/export", {
    method: "POST",
    body: JSON.stringify(body),
  });
  const link = document.createElement("a");
  link.href = format === "png" ? download : saved.url;
  link.download = (current.title || "导图") + "." + format;
  link.click();
  $("note").textContent = "已导出 " + format.toUpperCase() + "。";
}

function command(kind) {
  if (!root) return;
  arm();
  if (kind === "child") {
    const target = selected || root;
    const child = { text: "新节点", children: [] };
    target.children = target.children || [];
    target.children.push(child);
    target.collapsed = false;
    selected = child;
    pushHistory();
    draw();
    markDirty();
  } else if (kind === "sibling") {
    if (!selected || selected === root) {
      $("note").textContent = "先选中一个子节点。";
      return;
    }
    const parent = findParent(selected);
    if (!parent) return;
    const sibling = { text: "新节点", children: [] };
    parent.children.push(sibling);
    selected = sibling;
    pushHistory();
    draw();
    markDirty();
  } else if (kind === "remove") {
    if (!selected || selected === root) {
      $("note").textContent = "根节点不能删除。";
      return;
    }
    const parent = findParent(selected);
    if (!parent) return;
    parent.children = parent.children.filter((item) => item !== selected);
    selected = parent;
    pushHistory();
    draw();
    if (selected) select(selected);
    markDirty();
  }
}

function visibleList() {
  const out = [];
  (function walk(node) {
    out.push(node);
    if (node.collapsed) return;
    (node.children || []).forEach(walk);
  })(root);
  return out;
}

function moveSelection(step) {
  if (!root) return;
  const list = visibleList();
  const at = list.indexOf(selected);
  const next = at < 0 ? 0 : Math.min(list.length - 1, Math.max(0, at + step));
  if (list[next]) select(list[next]);
}

document.addEventListener("keydown", (event) => {
  const tag = (event.target && event.target.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if (!root) return;
  const meta = event.ctrlKey || event.metaKey;
  if (meta && event.key.toLowerCase() === "z") {
    event.preventDefault();
    if (event.shiftKey) redo();
    else undo();
    return;
  }
  if (meta && event.key.toLowerCase() === "y") {
    event.preventDefault();
    redo();
    return;
  }
  if (meta && event.key.toLowerCase() === "s") {
    event.preventDefault();
    save().catch((error) => { $("note").textContent = error.message; });
    return;
  }
  if (event.key === "Tab") {
    event.preventDefault();
    command("child");
    return;
  }
  if (event.key === "Enter") {
    event.preventDefault();
    command("sibling");
    return;
  }
  if (event.key === "Delete" || event.key === "Backspace") {
    event.preventDefault();
    command("remove");
    return;
  }
  if (event.key === "F2") {
    event.preventDefault();
    if (selected && boxes.get(selected)) editNode(selected, boxes.get(selected));
    return;
  }
  if (event.key === "ArrowDown") {
    event.preventDefault();
    moveSelection(1);
    return;
  }
  if (event.key === "ArrowUp") {
    event.preventDefault();
    moveSelection(-1);
    return;
  }
  if (event.key === "ArrowRight") {
    event.preventDefault();
    if (selected && selected.children && selected.children.length && !selected.collapsed) select(selected.children[0]);
    return;
  }
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    if (selected && selected !== root) {
      const parent = findParent(selected);
      if (parent) select(parent);
    }
    return;
  }
  if (event.key === " ") {
    event.preventDefault();
    if (selected) toggleCollapse(selected);
  }
});

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (sheet) fit(); }, 180);
});

const pointers = new Map();
let pinch = null;

function trackPointer(event) {
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (pointers.size === 2) {
    const [a, b] = [...pointers.values()];
    const box = $("board").getBoundingClientRect();
    pinch = {
      dist: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
      scale: view.scale,
      mx: (a.x + b.x) / 2 - box.left,
      my: (a.y + b.y) / 2 - box.top,
    };
    dragging = null;
  }
}

function releasePointer(event) {
  pointers.delete(event.pointerId);
  if (pointers.size < 2) pinch = null;
  if (!pointers.size) dragging = null;
}

$("board").addEventListener("pointerdown", (event) => {
  trackPointer(event);
  if (pinch) return;
  if (event.target.closest("[data-node]")) return;
  dragging = { x: event.clientX - view.x, y: event.clientY - view.y };
  try {
    $("board").setPointerCapture(event.pointerId);
  } catch (error) {
    // 合成事件或旧浏览器拿不到指针捕获，拖动本身仍然可用
  }
});
$("board").addEventListener("pointermove", (event) => {
  if (pointers.has(event.pointerId)) {
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
  }
  if (pinch && pointers.size >= 2) {
    const [a, b] = [...pointers.values()];
    const dist = Math.max(1, Math.hypot(a.x - b.x, a.y - b.y));
    const next = Math.min(2.4, Math.max(0.2, pinch.scale * (dist / pinch.dist)));
    const ratio = next / view.scale;
    view.x = pinch.mx - (pinch.mx - view.x) * ratio;
    view.y = pinch.my - (pinch.my - view.y) * ratio;
    view.scale = next;
    applyView();
    return;
  }
  if (!dragging) return;
  view.x = event.clientX - dragging.x;
  view.y = event.clientY - dragging.y;
  applyView();
});
$("board").addEventListener("pointerup", releasePointer);
$("board").addEventListener("pointercancel", releasePointer);
$("board").addEventListener("wheel", (event) => {
  event.preventDefault();
  const box = $("board").getBoundingClientRect();
  const px = event.clientX - box.left;
  const py = event.clientY - box.top;
  const next = Math.min(2.4, Math.max(0.2, view.scale * (event.deltaY < 0 ? 1.12 : 0.89)));
  const ratio = next / view.scale;
  view.x = px - (px - view.x) * ratio;
  view.y = py - (py - view.y) * ratio;
  view.scale = next;
  applyView();
}, { passive: false });

$("zoomIn").onclick = () => { view.scale = Math.min(2.4, view.scale * 1.15); applyView(); };
$("zoomOut").onclick = () => { view.scale = Math.max(0.2, view.scale / 1.15); applyView(); };
$("fit").onclick = () => fit();

$("ask").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  $("note").textContent = "正在整理大纲…";
  try {
    const payload = { text: $("text").value.trim() };
    const file = $("file").files && $("file").files[0];
    if (!payload.text && !file) throw new Error("先写内容，或上传文档");
    if (file) {
      if (file.size > 8_000_000) throw new Error("文档不要超过 8MB");
      payload.filename = file.name;
      payload.document = await readFile(file);
    }
    const data = await api("/api/mindmaps", { method: "POST", body: JSON.stringify(payload) });
    $("text").value = "";
    $("file").value = "";
    $("fileName").textContent = "";
    show(data.map);
  } catch (error) {
    $("note").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("文档没有读出来"));
    reader.readAsDataURL(file);
  });
}

$("file").addEventListener("change", () => {
  const file = $("file").files && $("file").files[0];
  $("fileName").textContent = file ? file.name : "";
});

$("theme").onchange = async () => {
  if (!current) return;
  arm();
  current.theme = $("theme").value;
  pushHistory();
  draw();
  markDirty();
};

function layoutName(value) {
  return value === "both" || value === "down" ? value : "right";
}

$("layout").onchange = () => {
  if (!current) return;
  layoutMode = layoutName($("layout").value);
  current.layout = layoutMode;
  draw();
  fit();
  markDirty();
};

$("spacing").oninput = () => {
  spacing = Math.min(160, Math.max(20, Number($("spacing").value) || 34));
  $("spacingOut").textContent = String(spacing);
  draw();
  markDirty();
};

$("addChild").onclick = () => command("child");
$("addSibling").onclick = () => command("sibling");
$("removeNode").onclick = () => command("remove");

$("editText").onclick = () => {
  if (!selected) return;
  const rect = boxes.get(selected);
  if (rect) {
    arm();
    editNode(selected, rect);
  }
};
const styleFields = ["fill", "stroke", "textColor", "fontSize", "bold", "shape"];
styleFields.forEach((id) => {
  const node = $(id);
  node.addEventListener("pointerdown", () => arm());
  node.addEventListener("focus", () => arm());
  node.addEventListener("keydown", () => arm());
});
["nodeNote", "nodeTag", "nodeLink"].forEach((id) => {
  $(id).addEventListener("focus", () => arm());
});
$("nodeTag").onchange = () => patchMeta("tag", $("nodeTag").value);
$("nodeNote").onchange = () => patchMeta("note", $("nodeNote").value);
$("nodeLink").onchange = () => patchMeta("link", $("nodeLink").value);
$("searchGo").onclick = () => runSearch();
$("search").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    runSearch();
  }
});
$("search").addEventListener("input", () => {
  if (!$("search").value.trim()) {
    matched = new Set();
    draw();
  }
});

function renameTitle() {
  if (!root) return;
  const label = $("title");
  const box = label.getBoundingClientRect();
  const input = document.createElement("input");
  input.className = "node-edit";
  input.value = root.text;
  input.style.left = box.left + "px";
  input.style.top = box.top + "px";
  input.style.width = Math.max(180, box.width) + "px";
  document.body.appendChild(input);
  input.focus();
  input.select();
  let closed = false;
  const commit = () => {
    if (closed) return;
    closed = true;
    const value = input.value.trim();
    input.remove();
    if (value && value !== root.text) {
      arm();
      root.text = value.slice(0, 40);
      pushHistory();
      draw();
      markDirty();
    }
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") commit();
    if (event.key === "Escape") {
      closed = true;
      input.remove();
    }
  });
  input.addEventListener("blur", commit);
}

$("title").addEventListener("dblclick", () => renameTitle());

async function regenerate() {
  if (!current) return;
  const hint = $("regenHint").value.trim();
  $("outlineNote").textContent = "正在重新整理…";
  try {
    const data = await api(`/api/mindmaps/${current.id}/regenerate`, {
      method: "POST",
      body: JSON.stringify({ hint }),
    });
    show(data.map);
    syncOutline();
    $("outlineNote").textContent = "已重新整理。";
  } catch (error) {
    $("outlineNote").textContent = error.message;
  }
}

$("regen").onclick = () => regenerate();

$("outlineBtn").onclick = () => openOutline();
$("outlineClose").onclick = () => $("outlineDrawer").classList.add("hidden");
$("outlineApply").onclick = () => applyOutline();
$("outlineRefresh").onclick = () => {
  syncOutline();
  $("outlineNote").textContent = "已从导图刷新。";
};
$("outlineMd").onclick = () => {
  if (!root) return;
  const name = ($("title").textContent || "导图") + ".md";
  downloadText(name, outlineMarkdown(root) + "\n", "text/markdown;charset=utf-8");
  $("outlineNote").textContent = "已导出 Markdown。";
};
$("outlineOpml").onclick = () => {
  if (!root) return;
  const name = ($("title").textContent || "导图") + ".opml";
  downloadText(name, outlineOpml(root), "text/x-opml;charset=utf-8");
  $("outlineNote").textContent = "已导出 OPML，可导入 XMind、Freemind。";
};

let printView = null;
window.addEventListener("beforeprint", () => {
  if (!sheet) return;
  printView = { ...view };
  const box = $("board").getBoundingClientRect();
  const width = Number(sheet.dataset.w) || 1;
  const height = Number(sheet.dataset.h) || 1;
  const scale = Math.min(1, 1000 / width, 700 / height);
  view.scale = Math.max(0.15, scale);
  view.x = 20;
  view.y = 20;
  applyView();
});
window.addEventListener("afterprint", () => {
  if (!printView) return;
  view = printView;
  printView = null;
  applyView();
});
$("print").onclick = () => window.print();
$("fill").oninput = () => patchStyle({ fill: $("fill").value });
$("stroke").oninput = () => patchStyle({ stroke: $("stroke").value }, true);
$("textColor").oninput = () => patchStyle({ text: $("textColor").value });
$("fontSize").onchange = () => patchStyle({ size: Number($("fontSize").value) });
$("bold").onchange = () => patchStyle({ bold: $("bold").checked });
$("shape").onchange = () => patchStyle({ shape: $("shape").value }, true);
$("resetStyle").onclick = () => {
  if (!selected) return;
  arm();
  eachNode(selected, (node) => { delete node.style; });
  pushHistory();
  draw();
  syncPanel();
  markDirty();
};
$("save").onclick = () => save().catch((error) => { $("note").textContent = error.message; });
$("svg").onclick = () => exported("svg").catch((error) => { $("note").textContent = error.message; });
$("png").onclick = () => exported("png").catch((error) => { $("note").textContent = error.message; });

renderList().catch((error) => { $("note").textContent = error.message; });
window.__showMap = show;
window.__state = () => ({ layout: layoutMode, spacing, scale: view.scale, nodes: document.querySelectorAll("#board [data-node]").length });
window.__svgText = () => exportSvgText();
window.__pngText = () =>
  svgToPng(exportSvgText(), Number(sheet.dataset.w) || 1, Number(sheet.dataset.h) || 1, 2);
