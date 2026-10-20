/* Draw the tree ourselves: real SVG text, no foreignObject, no scaling tricks. */
(function () {
  const NODE = {
    family: 'Microsoft YaHei, PingFang SC, HIragino Sans GB, sans-serif',
    size: 15,
    rootSize: 17,
    lineHeight: 1.45,
    padX: 16,
    padY: 10,
    maxText: 300,
    gapX: 34,
    gapY: 12,
    tagHeight: 18,
    tagGap: 6,
    tagPadX: 8,
    noteGap: 5,
    linkWidth: 16,
  };

  const PALETTE = {
    classic: {
      bg: "#f7f4ee",
      line: "#2f6f62",
      rootFill: "#2f6f62",
      rootText: "#ffffff",
      secondFill: "#ffffff",
      secondStroke: "#2f6f62",
      secondText: "#1c1916",
      leafFill: "#fffdf8",
      leafStroke: "#c9bfb2",
      leafText: "#1c1916",
      note: "#8b8175",
      tagFill: "#2f6f62",
      tagText: "#ffffff",
    },
    dark: {
      bg: "#1b1a17",
      line: "#6fae9d",
      rootFill: "#2f6f62",
      rootText: "#ffffff",
      secondFill: "#2a2622",
      secondStroke: "#6fae9d",
      secondText: "#f6f0e6",
      leafFill: "#24211e",
      leafStroke: "#6d6458",
      leafText: "#f6f0e6",
      note: "#a89e90",
      tagFill: "#6fae9d",
      tagText: "#1b1a17",
    },
    warm: {
      bg: "#fdf6ec",
      line: "#b5651d",
      rootFill: "#c2703a",
      rootText: "#ffffff",
      secondFill: "#fff8f0",
      secondStroke: "#c2703a",
      secondText: "#3a2418",
      leafFill: "#fffaf3",
      leafStroke: "#e0c9ae",
      leafText: "#3a2418",
      note: "#9a7a5a",
      tagFill: "#c2703a",
      tagText: "#ffffff",
    },
    ocean: {
      bg: "#eef4f8",
      line: "#2b6c8f",
      rootFill: "#2b6c8f",
      rootText: "#ffffff",
      secondFill: "#ffffff",
      secondStroke: "#2b6c8f",
      secondText: "#16303d",
      leafFill: "#f7fbfd",
      leafStroke: "#b9d2e0",
      leafText: "#16303d",
      note: "#5c7f92",
      tagFill: "#2b6c8f",
      tagText: "#ffffff",
    },
    ink: {
      bg: "#ffffff",
      line: "#333333",
      rootFill: "#222222",
      rootText: "#ffffff",
      secondFill: "#ffffff",
      secondStroke: "#333333",
      secondText: "#111111",
      leafFill: "#ffffff",
      leafStroke: "#bbbbbb",
      leafText: "#111111",
      note: "#666666",
      tagFill: "#333333",
      tagText: "#ffffff",
    },
  };

  let measureCanvas = null;
  function ctx2d() {
    if (!measureCanvas) measureCanvas = document.createElement("canvas");
    return measureCanvas.getContext("2d");
  }

  function fontOf(node, depth) {
    const custom = (node && node.style) || {};
    const size = Number(custom.size) || (depth === 0 ? NODE.rootSize : NODE.size);
    const bold = custom.bold === undefined ? depth === 0 : !!custom.bold;
    return { size, bold };
  }

  function fontFor(node, depth) {
    const { size, bold } = fontOf(node, depth);
    return `${bold ? "bold " : ""}${size}px ${NODE.family}`;
  }

  function wrapText(text, font, maxWidth) {
    const ctx = ctx2d();
    ctx.font = font;
    const chars = Array.from(String(text || " "));
    const lines = [];
    let line = "";
    for (const ch of chars) {
      const next = line + ch;
      if (line && ctx.measureText(next).width > maxWidth) {
        lines.push(line);
        line = ch;
      } else {
        line = next;
      }
    }
    if (line) lines.push(line);
    return lines.length ? lines : [" "];
  }

  function noteFontOf(node, depth) {
    const { size } = fontOf(node, depth);
    return Math.max(11, size - 3);
  }

  function noteFontString(node, depth) {
    return `${noteFontOf(node, depth)}px ${NODE.family}`;
  }

  function measure(node, depth) {
    const ctx = ctx2d();
    const lines = wrapText(node.text, fontFor(node, depth), NODE.maxText);
    const { size } = fontOf(node, depth);
    const lineHeight = Math.round(size * NODE.lineHeight);
    ctx.font = fontFor(node, depth);
    let titleWidth = 0;
    for (const line of lines) titleWidth = Math.max(titleWidth, ctx.measureText(line).width);

    const tag = String(node.tag || "").trim();
    let tagWidth = 0;
    if (tag) {
      ctx.font = `11px ${NODE.family}`;
      tagWidth = Math.ceil(ctx.measureText(tag).width) + NODE.tagPadX * 2;
    }

    const noteLines = node.note
      ? wrapText(node.note, noteFontString(node, depth), NODE.maxText)
      : [];
    const noteLineHeight = Math.round(noteFontOf(node, depth) * 1.4);
    let noteWidth = 0;
    if (noteLines.length) {
      ctx.font = noteFontString(node, depth);
      for (const line of noteLines) noteWidth = Math.max(noteWidth, ctx.measureText(line).width);
    }

    let boxWidth = Math.ceil(Math.max(titleWidth, noteWidth, tagWidth)) + NODE.padX * 2;
    if (node.link) boxWidth += NODE.linkWidth;
    let boxHeight =
      NODE.padY * 2 +
      lines.length * lineHeight +
      (tag ? NODE.tagHeight + NODE.tagGap : 0) +
      (noteLines.length ? NODE.noteGap + noteLines.length * noteLineHeight : 0);

    const shape = (node.style && node.style.shape) || "round";
    if (shape === "ellipse") {
      // An ellipse has to be larger than the text block or the outer lines poke out.
      const halfW = boxWidth / 2;
      const halfH = boxHeight / 2;
      const semiY = halfH * 1.26;
      const ratio = halfH / semiY;
      const semiX = halfW / Math.sqrt(1 - ratio * ratio);
      boxWidth = Math.ceil(semiX * 2);
      boxHeight = Math.ceil(semiY * 2);
    }
    return { lines, noteLines, lineHeight, noteLineHeight, tag, tagWidth, width: boxWidth, height: boxHeight };
  }

  function each(node, fn, depth = 0) {
    fn(node, depth);
    (node.children || []).forEach((child) => each(child, fn, depth + 1));
  }

  function prepare(root, options) {
    const opts = options || {};
    const gapX = Math.max(8, Number(opts.gapX) || NODE.gapX);
    const gapY = Math.max(4, Number(opts.gapY) || NODE.gapY);
    const layout = opts.layout === "both" || opts.layout === "down" ? opts.layout : "right";
    const visible = [];
    (function walk(node, depth) {
      visible.push({ node, depth });
      if (node.collapsed) return;
      (node.children || []).forEach((child) => walk(child, depth + 1));
    })(root, 0);
    visible.forEach(({ node, depth }) => {
      const m = measure(node, depth);
      node._depth = depth;
      node._lines = m.lines;
      node._noteLines = m.noteLines;
      node._noteLineHeight = m.noteLineHeight;
      node._tag = m.tag;
      node._tagWidth = m.tagWidth;
      node._lineHeight = m.lineHeight;
      node._w = m.width;
      node._h = m.height;
    });
    if (layout === "down") return placeDown(root, visible, gapX, gapY);
    if (layout === "both") return placeBoth(root, visible, gapX, gapY);
    return placeRight(root, visible, gapX, gapY);
  }

  function bounds(visible) {
    let minY = Infinity;
    let maxY = -Infinity;
    let maxX = 0;
    let minX = 0;
    visible.forEach(({ node }) => {
      minY = Math.min(minY, node._y);
      maxY = Math.max(maxY, node._y + node._h);
      maxX = Math.max(maxX, node._x + node._w);
      minX = Math.min(minX, node._x);
    });
    if (!isFinite(minY)) {
      minY = 0;
      maxY = 0;
    }
    return { minX, maxX, minY, maxY, width: maxX - minX, height: maxY - minY };
  }

  function placeRight(root, visible, gapX, gapY) {
    const columns = [];
    visible.forEach(({ node }) => {
      columns[node._depth] = Math.max(columns[node._depth] || 0, node._w);
    });
    const offsetX = [];
    let x = 0;
    columns.forEach((width, depth) => {
      offsetX[depth] = x;
      x += width + gapX;
    });
    let cursorY = 0;
    (function place(node, depth) {
      node._depth = depth;
      node._x = offsetX[depth] || 0;
      const children = node.collapsed ? [] : node.children || [];
      if (!children.length) {
        node._y = cursorY;
        cursorY += node._h + gapY;
      } else {
        const start = cursorY;
        children.forEach((child) => place(child, depth + 1));
        const end = cursorY - gapY;
        node._y = (start + end) / 2 - node._h / 2;
      }
    })(root, 0);
    const box = bounds(visible);
    return { width: Math.ceil(box.maxX) + 14, height: Math.ceil(box.height) + 8 };
  }

  function placeDown(root, visible, gapX, gapY) {
    const rows = [];
    visible.forEach(({ node }) => {
      rows[node._depth] = Math.max(rows[node._depth] || 0, node._h);
    });
    const offsetY = [];
    let y = 0;
    rows.forEach((height, depth) => {
      offsetY[depth] = y;
      y += height + gapY;
    });
    (function width(node) {
      node._sw = node._w;
      if (node.collapsed) return;
      const kids = node.children || [];
      if (kids.length) {
        let total = 0;
        kids.forEach((child, index) => {
          width(child);
          total += child._sw + (index ? gapX : 0);
        });
        node._sw = Math.max(node._sw, total);
      }
    })(root);
    (function place(node, left, depth) {
      node._depth = depth;
      node._y = offsetY[depth] || 0;
      node._x = left + (node._sw - node._w) / 2;
      const kids = node.collapsed ? [] : node.children || [];
      if (!kids.length) return;
      let total = 0;
      kids.forEach((child, index) => {
        total += child._sw + (index ? gapX : 0);
      });
      let cursor = left + (node._sw - total) / 2;
      kids.forEach((child) => {
        place(child, cursor, depth + 1);
        cursor += child._sw + gapX;
      });
    })(root, 0, 0);
    const box = bounds(visible);
    return { width: Math.ceil(box.width) + 20, height: Math.ceil(box.maxY) + 14 };
  }

  function placeBoth(root, visible, gapX, gapY) {
    const kids = root.collapsed ? [] : root.children || [];
    const half = Math.ceil(kids.length / 2);
    const rightKids = kids.slice(0, half);
    const leftKids = kids.slice(half);
    const collect = (list) => {
      const out = [];
      const walk = (node) => {
        out.push(node);
        (node.collapsed ? [] : node.children || []).forEach(walk);
      };
      list.forEach(walk);
      return out;
    };
    const rightNodes = collect(rightKids);
    const leftNodes = collect(leftKids);
    const colRight = [];
    const colLeft = [];
    rightNodes.forEach((node) => {
      colRight[node._depth] = Math.max(colRight[node._depth] || 0, node._w);
    });
    leftNodes.forEach((node) => {
      colLeft[node._depth] = Math.max(colLeft[node._depth] || 0, node._w);
    });

    const startX = root._w + gapX;
    const offR = [];
    let cursorX = startX;
    for (let depth = 1; depth < 12; depth += 1) {
      if (!colRight[depth]) break;
      offR[depth] = cursorX;
      cursorX += colRight[depth] + gapX;
    }
    const edgeL = [];
    let cursorEdge = -gapX;
    for (let depth = 1; depth < 12; depth += 1) {
      if (!colLeft[depth]) break;
      edgeL[depth] = cursorEdge;
      cursorEdge -= colLeft[depth] + gapX;
    }

    let cursorR = 0;
    const placeR = (node, depth) => {
      node._depth = depth;
      node._x = offR[depth] || startX;
      const children = node.collapsed ? [] : node.children || [];
      if (!children.length) {
        node._y = cursorR;
        cursorR += node._h + gapY;
      } else {
        const start = cursorR;
        children.forEach((child) => placeR(child, depth + 1));
        node._y = (start + cursorR - gapY) / 2 - node._h / 2;
      }
    };
    rightKids.forEach((child) => placeR(child, 1));

    let cursorL = 0;
    const placeL = (node, depth) => {
      node._depth = depth;
      node._x = (edgeL[depth] || -gapX) - node._w;
      const children = node.collapsed ? [] : node.children || [];
      if (!children.length) {
        node._y = cursorL;
        cursorL += node._h + gapY;
      } else {
        const start = cursorL;
        children.forEach((child) => placeL(child, depth + 1));
        node._y = (start + cursorL - gapY) / 2 - node._h / 2;
      }
    };
    leftKids.forEach((child) => placeL(child, 1));

    const top = Math.min(0, ...rightNodes.map((node) => node._y), ...leftNodes.map((node) => node._y));
    const bottom = Math.max(cursorR - gapY, cursorL - gapY, root._h);
    root._depth = 0;
    root._x = 0;
    root._y = (top + bottom) / 2 - root._h / 2;

    const all = [root, ...rightNodes, ...leftNodes];
    const minX = Math.min(...all.map((node) => node._x));
    const minY = Math.min(...all.map((node) => node._y));
    all.forEach((node) => {
      node._x -= minX;
      node._y -= minY;
    });
    const box = bounds(visible);
    return { width: Math.ceil(box.maxX) + 16, height: Math.ceil(box.maxY) + 10 };
  }

  function esc(text) {
    return String(text).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  function nodeStyle(node, palette) {
    const depth = node._depth;
    if (depth === 0) {
      return { fill: palette.rootFill, stroke: "none", sw: 0, color: palette.rootText, radius: 9 };
    }
    if (depth === 1) {
      return { fill: palette.secondFill, stroke: palette.secondStroke, sw: 1.6, color: palette.secondText, radius: 8 };
    }
    return { fill: palette.leafFill, stroke: palette.leafStroke, sw: 1, color: palette.leafText, radius: 8 };
  }

  function svgMarkup(root, theme) {
    const palette = PALETTE[theme] || PALETTE.classic;
    const size = prepare(root);
    const parts = [];
    parts.push(
      `<svg xmlns="http://www.w3.org/2000/svg" width="${size.width}" height="${size.height}" viewBox="0 0 ${size.width} ${size.height}">`
    );
    parts.push(`<rect x="0" y="0" width="${size.width}" height="${size.height}" fill="${palette.bg}"/>`);
    // edges first
    const edges = [];
    (function walk(node) {
      (node.children || []).forEach((child) => {
        const x1 = node._x + node._w;
        const y1 = node._y + node._h / 2;
        const x2 = child._x;
        const y2 = child._y + child._h / 2;
        const mid = x1 + Math.max(16, (x2 - x1) / 2);
        edges.push(
          `<path d="M ${x1} ${y1} H ${mid} V ${y2} H ${x2}" fill="none" stroke="${palette.line}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>`
        );
        walk(child);
      });
    })(root);
    parts.push(`<g>${edges.join("")}</g>`);
    const nodes = [];
    each(root, (node) => {
      const style = nodeStyle(node, palette);
      const cx = node._x;
      const cy = node._y;
      nodes.push(
        `<g data-node="1"><rect x="${cx}" y="${cy}" width="${node._w}" height="${node._h}" rx="${style.radius}" ry="${style.radius}" fill="${style.fill}" stroke="${style.stroke}" stroke-width="${style.sw}"/>`
      );
      const size2 = node._depth === 0 ? NODE.rootSize : NODE.size;
      node._lines.forEach((line, index) => {
        const base = cy + NODE.padY + node._lineHeight * index + node._lineHeight / 2;
        nodes.push(
          `<text x="${cx + NODE.padX}" y="${base}" font-family="${NODE.family}" font-size="${size2}" font-weight="${node._depth === 0 ? "bold" : "normal"}" fill="${style.color}" dominant-baseline="central">${esc(line)}</text>`
        );
      });
      nodes.push(`</g>`);
    });
    parts.push(`<g>${nodes.join("")}</g>`);
    parts.push(`</svg>`);
    return { svg: parts.join(""), size };
  }

  window.MindRender = { svgMarkup, prepare, fontOf, noteSize: noteFontOf, NODE, PALETTE };})();
