const state = {
  user: null,
  sessions: [],
  currentId: null,
  messages: [],
  models: [],
  chain: [],
  imageModel: "auto",
  uploads: [],
  polling: false,
  stickToLatest: true,
  pinning: false,
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs || 100000;
  const { timeoutMs: _ignored, ...rest } = options;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      credentials: "same-origin",
      signal: controller.signal,
      ...rest,
      headers: { "Content-Type": "application/json", ...(rest.headers || {}) },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "请求失败");
    return data;
  } catch (err) {
    if (err.name === "AbortError") throw new Error("发送超时，请再试一次");
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

function toast(text) {
  const node = $("toast");
  node.textContent = text;
  node.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.add("hidden"), 3200);
}

async function boot() {
  try {
    const me = await api("/api/me");
    state.user = me.user;
    await enter();
  } catch {
    $("login").classList.remove("hidden");
  }
}

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("loginError").textContent = "";
  const form = new FormData(event.target);
  try {
    const data = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ username: form.get("username"), password: form.get("password") }),
    });
    state.user = data.user;
    $("login").classList.add("hidden");
    await enter();
  } catch (err) {
    $("loginError").textContent = err.message;
  }
});

async function enter() {
  $("app").classList.remove("hidden");
  $("openAdmin").classList.toggle("hidden", state.user.role !== "admin");
  const bootData = await api("/api/bootstrap");
  state.models = bootData.image_models;
  state.chain = bootData.chain;
  renderModels();
  renderChain();
  await loadSessions();
}

const AUTO_MODEL = { id: "auto", label: "自动选" };

function renderModels() {
  $("modelPills").replaceChildren();
  [AUTO_MODEL, ...state.models].forEach((model) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = model.label;
    button.className = model.id === state.imageModel ? "on" : "";
    button.onclick = () => {
      state.imageModel = model.id;
      renderModels();
    };
    $("modelPills").appendChild(button);
  });
}

function renderChain() {
  const used = state.chain.filter((item) => item.enabled).map((item) => {
    return item.cooling ? `${item.label}冷却` : item.label;
  });
  $("chainLine").textContent = used.length ? "编排 " + used.join(" → ") : "编排已关闭";
}

async function loadSessions() {
  const data = await api("/api/sessions");
  state.sessions = data.sessions;
  renderSessions();
  if (!state.currentId && state.sessions[0]) openSession(state.sessions[0].id);
  if (!state.sessions.length) renderThread();
}

function renderSessions() {
  $("sessions").replaceChildren();
  state.sessions.forEach((session) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "session" + (session.id === state.currentId ? " active" : "");
    button.textContent = session.title || "新对话";
    button.onclick = () => {
      state.stickToLatest = true;
      openSession(session.id);
      closeRail();
    };
    $("sessions").appendChild(button);
  });
}

async function openSession(id) {
  state.currentId = id;
  const data = await api("/api/sessions/" + id);
  state.messages = data.messages;
  $("chatTitle").textContent = data.session.title || "新对话";
  showMemory(data.session);
  renderSessions();
  renderThread();
  if (state.messages.some((item) => (item.body.image || {}).status === "PENDING")) startPoll();
}

const rendered = new Map();

function emptyState() {
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.textContent = "直接说要画什么。生成后可以接着改。";
  ["一只在雨夜窗边的白猫，电影光", "改成竖版海报", "同一张，光线再暖一点"].forEach((text) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost";
    button.textContent = text;
    button.onclick = () => { $("input").value = text; $("input").focus(); };
    empty.appendChild(document.createElement("br"));
    empty.appendChild(button);
  });
  return empty;
}

function renderThread() {
  const thread = $("thread");
  rendered.clear();
  thread.replaceChildren();
  state.stickToLatest = true;
  if (!state.messages.length) {
    thread.appendChild(emptyState());
    return;
  }
  applyMessages(state.messages);
  pinLatest();
}

function pinLatest() {
  if (!state.stickToLatest) return;
  const thread = $("thread");
  const target = thread.querySelector(".bubble:last-child img") || thread.querySelector(".bubble:last-child");
  if (!target) return;
  const delta = target.getBoundingClientRect().top - thread.getBoundingClientRect().top;
  if (Math.abs(delta - 12) < 2) return;
  state.pinning = true;
  thread.scrollTop += delta - 12;
  requestAnimationFrame(() => { state.pinning = false; });
}

$("thread").addEventListener("scroll", () => {
  if (state.pinning) return;
  const thread = $("thread");
  const target = thread.querySelector(".bubble:last-child img") || thread.querySelector(".bubble:last-child");
  if (!target) return;
  const delta = target.getBoundingClientRect().top - thread.getBoundingClientRect().top;
  if (delta > 160) state.stickToLatest = false;
}, { passive: true });

// 只替换真正变了的那几条，避免轮询时整列重建把滚动位置和正在看的大图弄丢
function applyMessages(messages) {
  const thread = $("thread");
  const seen = new Set();
  messages.forEach((message) => {
    seen.add(message.id);
    const hash = JSON.stringify(message.body);
    const known = rendered.get(message.id);
    if (known && known.hash === hash) return;
    const node = renderMessage(message);
    if (known) thread.replaceChild(node, known.el);
    else thread.appendChild(node);
    rendered.set(message.id, { hash, el: node });
  });
  rendered.forEach((entry, id) => {
    if (seen.has(id)) return;
    entry.el.remove();
    rendered.delete(id);
  });
  if (!messages.length) {
    if (!thread.querySelector(".empty")) thread.appendChild(emptyState());
    return;
  }
  const empty = thread.querySelector(".empty");
  if (empty) empty.remove();
  pinLatest();
}

function renderMessage(message) {
  const wrap = document.createElement("div");
  wrap.className = "bubble " + message.role;
  const text = document.createElement("div");
  text.className = "text";
  text.textContent = message.body.text || "";
  wrap.appendChild(text);
  const image = message.body.image;
  if (image) {
    if (image.local_names && image.local_names.length) {
      const shots = document.createElement("div");
      shots.className = "shots";
      image.local_names.forEach((name) => {
        const frame = document.createElement("div");
        frame.className = "shot";
        const img = document.createElement("img");
        img.src = imageUrl(name);
        img.alt = "生成图";
        img.title = "点击放大";
        img.onclick = () => openViewer(img.src);
        img.addEventListener("load", () => pinLatest());
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "annotate";
        edit.textContent = "标注修改";
        edit.onclick = () => openEditor(name);
        frame.append(img, edit);
        shots.appendChild(frame);
      });
      wrap.appendChild(shots);
    } else if (image.status === "PENDING") {
      const bar = document.createElement("div");
      bar.className = "progress";
      const span = document.createElement("span");
      const pct = parseInt(image.progress, 10);
      span.style.width = (Number.isFinite(pct) ? pct : 12) + "%";
      bar.appendChild(span);
      wrap.appendChild(bar);
    }
    const meta = document.createElement("div");
    meta.className = "meta";
    const tried = (message.body.llm && message.body.llm.tried || []).map((item) => {
      return item.ok ? item.label : item.label + "失败";
    }).join(" → ");
    meta.textContent = [image.model, image.status, image.progress, image.fail_reason, tried].filter(Boolean).join(" · ");
    wrap.appendChild(meta);
    if (image.status === "FAILURE" && message.body.prompt) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "ghost";
      retry.textContent = "按同一句再画";
      retry.onclick = () => retryImage(message.id);
      wrap.appendChild(retry);
    }
  }
  return wrap;
}

let sending = false;

function setSending(active) {
  sending = active;
  $("send").disabled = active;
  $("send").textContent = active ? "发送中" : "生成";
  $("sendState").textContent = active ? "正在发送。编排和提交要几秒，请不要重复点。" : "";
}

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("input").value.trim();
  if (!text) {
    toast("先写一句要画什么");
    return;
  }
  if (sending) {
    toast("正在发送，请稍等");
    return;
  }
  setSending(true);
  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.currentId,
        text,
        upload_ids: state.uploads.map((item) => item.id),
        image_model: state.imageModel,
        size: $("size").value,
        resolution: $("resolution").value,
        quality: $("quality").value,
        n: Number($("count").value),
        use_llm: $("useLlm").checked,
        use_previous: $("usePrevious").checked,
      }),
    });
    state.currentId = data.session.id;
    $("input").value = "";
    state.uploads = [];
    renderAttach();
    state.chain = data.chain || state.chain;
    renderChain();
    await openSession(state.currentId);
    await loadSessions();
    if ((data.message.body.image || {}).status === "PENDING") startPoll();
  } catch (err) {
    toast(err.message);
  } finally {
    setSending(false);
  }
});

$("input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});

function startPoll() {
  if (state.polling) return;
  state.polling = true;
  const timer = setInterval(async () => {
    if (!state.currentId) return;
    try {
      const data = await api("/api/sessions/" + state.currentId);
      state.messages = data.messages;
      showMemory(data.session);
      applyMessages(data.messages);
      const pending = state.messages.some((item) => (item.body.image || {}).status === "PENDING");
      if (!pending) {
        clearInterval(timer);
        state.polling = false;
      }
    } catch {
      clearInterval(timer);
      state.polling = false;
    }
  }, 3500);
}

async function retryImage(messageId) {
  try {
    await api("/api/retry", { method: "POST", body: JSON.stringify({ message_id: messageId }) });
    await openSession(state.currentId);
    startPoll();
  } catch (err) {
    toast(err.message);
  }
}

function showMemory(session) {
  $("memoryLine").textContent = (session && session.memory_line) || "";
}

function startNewChat() {
  state.currentId = null;
  state.messages = [];
  state.stickToLatest = true;
  $("chatTitle").textContent = "新对话";
  showMemory(null);
  closeRail();
  renderSessions();
  renderThread();
  $("input").focus({ preventScroll: true });
}

function closeRail() {
  $("rail").classList.remove("open");
  $("railShade").classList.add("hidden");
}

$("newChat").onclick = startNewChat;
$("newChatTop").onclick = startNewChat;
$("railShade").onclick = closeRail;

$("logout").onclick = async () => {
  await api("/api/logout", { method: "POST", body: "{}" });
  location.reload();
};

$("openRail").onclick = () => {
  const open = $("rail").classList.toggle("open");
  $("railShade").classList.toggle("hidden", !open);
};
$("attachBtn").onclick = () => $("file").click();
$("file").onchange = async () => {
  for (const file of $("file").files) await uploadFile(file);
  $("file").value = "";
};
$("input").addEventListener("paste", (event) => {
  for (const item of event.clipboardData?.items || []) {
    if (item.type.startsWith("image/")) {
      event.preventDefault();
      uploadFile(item.getAsFile());
    }
  }
});

async function uploadFile(file) {
  if (!file) return;
  const dataUrl = await readFile(file);
  try {
    const saved = await api("/api/upload", {
      method: "POST",
      body: JSON.stringify({ name: file.name, data_url: dataUrl }),
    });
    state.uploads.push(saved);
    renderAttach();
  } catch (err) {
    toast(err.message);
  }
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function renderAttach() {
  $("attachRow").replaceChildren();
  state.uploads.forEach((item, index) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = item.name + " ×";
    chip.onclick = () => {
      state.uploads.splice(index, 1);
      renderAttach();
    };
    $("attachRow").appendChild(chip);
  });
}

$("openAdmin").onclick = async () => {
  $("admin").classList.remove("hidden");
  await refreshAdmin();
};
$("closeAdmin").onclick = () => $("admin").classList.add("hidden");

async function refreshAdmin() {
  const data = await api("/api/admin/users");
  const bootData = await api("/api/bootstrap");
  state.chain = bootData.chain;
  renderChain();
  $("userList").replaceChildren();
  data.users.forEach((user) => {
    const row = document.createElement("div");
    row.className = "user-row";
    const name = document.createElement("span");
    name.textContent = `${user.username} · ${user.role}${user.disabled ? " · 已停用" : ""}`;
    row.appendChild(name);
    if (user.role !== "admin") {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ghost";
      button.textContent = user.disabled ? "启用" : "停用";
      button.onclick = async () => {
        await api("/api/admin/users/" + user.id, {
          method: "POST",
          body: JSON.stringify({ disabled: !user.disabled }),
        });
        refreshAdmin();
      };
      row.appendChild(button);
    }
    $("userList").appendChild(row);
  });
  $("chainEditor").replaceChildren();
  state.chain.forEach((item, index) => {
    const row = document.createElement("label");
    row.className = "check";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = item.enabled;
    input.onchange = async () => {
      const chain = state.chain.map((entry, i) => i === index ? { ...entry, enabled: input.checked } : entry);
      await api("/api/admin/chain", { method: "POST", body: JSON.stringify({ chain }) });
      refreshAdmin();
    };
    row.append(input, document.createTextNode(item.label + (item.cooling ? "（冷却）" : "")));
    $("chainEditor").appendChild(row);
  });
}

$("userForm").onsubmit = async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({
        username: form.get("username"),
        password: form.get("password"),
        daily_limit: form.get("daily_limit"),
      }),
    });
    event.target.reset();
    $("adminError").textContent = "";
    refreshAdmin();
  } catch (err) {
    $("adminError").textContent = err.message;
  }
};

$("passForm").onsubmit = async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    await api("/api/password", {
      method: "POST",
      body: JSON.stringify({ old: form.get("old"), new: form.get("new") }),
    });
    toast("密码已改");
    event.target.reset();
  } catch (err) {
    $("adminError").textContent = err.message;
  }
};

$("clearCooldown").onclick = async () => {
  await api("/api/admin/cooldown/clear", { method: "POST", body: "{}" });
  refreshAdmin();
};

const viewer = { scale: 1, x: 0, y: 0, drag: null, moved: false };

function openViewer(src) {
  viewer.scale = 1;
  viewer.x = 0;
  viewer.y = 0;
  $("viewerImg").src = src;
  applyViewer();
  $("viewer").classList.remove("hidden");
}

function closeViewer() {
  $("viewer").classList.add("hidden");
  $("viewerImg").removeAttribute("src");
}

function applyViewer() {
  $("viewerImg").style.transform = `translate(${viewer.x}px, ${viewer.y}px) scale(${viewer.scale})`;
}

function zoomViewer(next) {
  viewer.scale = Math.min(6, Math.max(1, next));
  if (viewer.scale === 1) {
    viewer.x = 0;
    viewer.y = 0;
  }
  applyViewer();
}

$("viewerClose").onclick = closeViewer;
$("zoomIn").onclick = () => zoomViewer(viewer.scale * 1.25);
$("zoomOut").onclick = () => zoomViewer(viewer.scale / 1.25);
$("zoomReset").onclick = () => zoomViewer(1);
$("viewer").addEventListener("click", (event) => {
  if (viewer.moved) return;
  if (event.target.id === "viewer" || event.target.id === "viewerStage") closeViewer();
});
$("viewerStage").addEventListener("wheel", (event) => {
  event.preventDefault();
  zoomViewer(viewer.scale * (event.deltaY < 0 ? 1.12 : 0.89));
}, { passive: false });
$("viewerStage").addEventListener("pointerdown", (event) => {
  viewer.moved = false;
  viewer.drag = { x: event.clientX - viewer.x, y: event.clientY - viewer.y, sx: event.clientX, sy: event.clientY };
  $("viewerStage").classList.add("dragging");
  $("viewerStage").setPointerCapture(event.pointerId);
});
$("viewerStage").addEventListener("pointermove", (event) => {
  if (!viewer.drag) return;
  if (Math.abs(event.clientX - viewer.drag.sx) + Math.abs(event.clientY - viewer.drag.sy) > 4) viewer.moved = true;
  viewer.x = event.clientX - viewer.drag.x;
  viewer.y = event.clientY - viewer.drag.y;
  applyViewer();
});
function endViewerDrag() {
  viewer.drag = null;
  $("viewerStage").classList.remove("dragging");
}
$("viewerStage").addEventListener("pointerup", endViewerDrag);
$("viewerStage").addEventListener("pointercancel", endViewerDrag);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("viewer").classList.contains("hidden")) closeViewer();
});

function imageUrl(name) {
  return `/media/images/${state.user.id}/${name}`;
}

function generatedNames() {
  const names = [];
  state.messages.forEach((message) => {
    (message.body.image?.local_names || []).forEach((name) => names.push(name));
  });
  return names;
}

const editor = {
  tool: "brush",
  drawing: false,
  dirty: false,
  source: "",
  start: null,
  snapshot: null,
  undo: [],
};

function openEditor(name) {
  if (!name) {
    const names = generatedNames();
    if (!names.length) {
      toast("还没有可以标注的图");
      return;
    }
    name = names[names.length - 1];
  }
  $("editor").classList.remove("hidden");
  renderEditorPicks(name);
  loadEditorImage(name);
}

function renderEditorPicks(selected) {
  const box = $("editorPicks");
  box.replaceChildren();
  generatedNames().forEach((name) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = name === selected ? "on" : "";
    const img = document.createElement("img");
    img.src = imageUrl(name);
    img.alt = "已生成的图";
    button.appendChild(img);
    button.onclick = () => {
      renderEditorPicks(name);
      loadEditorImage(name);
    };
    box.appendChild(button);
  });
}

function loadEditorImage(name) {
  editor.source = name;
  editor.dirty = false;
  editor.undo = [];
  editor.base = null;
  const img = new Image();
  img.onload = () => {
    const canvas = $("editCanvas");
    const maxW = Math.min(1280, window.innerWidth - 48);
    const scale = Math.min(1, maxW / img.naturalWidth, 900 / img.naturalHeight);
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    editor.base = ctx.getImageData(0, 0, canvas.width, canvas.height);
    if (!editor.fill) editor.fill = document.createElement("canvas");
    editor.fill.width = canvas.width;
    editor.fill.height = canvas.height;
    editor.fill.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    pushUndo();
  };
  img.src = imageUrl(name);
}

function buildMaskDataUrl() {
  const canvas = $("editCanvas");
  const current = canvasCtx().getImageData(0, 0, canvas.width, canvas.height);
  const base = editor.base;
  const fillData = editor.fill ? editor.fill.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data : null;
  const mask = document.createElement("canvas");
  mask.width = canvas.width;
  mask.height = canvas.height;
  const ctx = mask.getContext("2d");
  const out = ctx.createImageData(canvas.width, canvas.height);
  const source = current.data;
  const origin = base ? base.data : null;
  for (let i = 0; i < source.length; i += 4) {
    const changed = !origin || Math.abs(source[i] - origin[i]) + Math.abs(source[i + 1] - origin[i + 1]) + Math.abs(source[i + 2] - origin[i + 2]) > 48;
    const enclosed = fillData ? fillData[i + 3] > 0 : false;
    const value = changed || enclosed ? 255 : 0;
    out.data[i] = value;
    out.data[i + 1] = value;
    out.data[i + 2] = value;
    out.data[i + 3] = 255;
  }
  ctx.putImageData(out, 0, 0);
  return mask.toDataURL("image/png");
}

function canvasCtx() {
  return $("editCanvas").getContext("2d");
}

function pushUndo() {
  const canvas = $("editCanvas");
  editor.undo.push({
    main: canvasCtx().getImageData(0, 0, canvas.width, canvas.height),
    fill: editor.fill ? editor.fill.getContext("2d").getImageData(0, 0, canvas.width, canvas.height) : null,
  });
  if (editor.undo.length > 16) editor.undo.shift();
}

function restoreUndo() {
  if (editor.undo.length < 2) return;
  editor.undo.pop();
  const last = editor.undo[editor.undo.length - 1];
  canvasCtx().putImageData(last.main, 0, 0);
  if (editor.fill && last.fill) editor.fill.getContext("2d").putImageData(last.fill, 0, 0);
  editor.dirty = editor.undo.length > 1;
}

function pointerPos(event) {
  const rect = $("editCanvas").getBoundingClientRect();
  const point = event.touches ? event.touches[0] : event;
  return {
    x: (point.clientX - rect.left) * ($("editCanvas").width / rect.width),
    y: (point.clientY - rect.top) * ($("editCanvas").height / rect.height),
  };
}

function paintSetup(ctx) {
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.lineWidth = Number($("editorSize").value) || 14;
  ctx.strokeStyle = "#ff2a2a";
  ctx.fillStyle = "#ff2a2a";
}

function drawArrow(ctx, from, to) {
  const angle = Math.atan2(to.y - from.y, to.x - from.x);
  ctx.beginPath();
  ctx.moveTo(from.x, from.y);
  ctx.lineTo(to.x, to.y);
  ctx.stroke();
  const head = 16 + ctx.lineWidth;
  ctx.beginPath();
  ctx.moveTo(to.x, to.y);
  ctx.lineTo(to.x - head * Math.cos(angle - 0.4), to.y - head * Math.sin(angle - 0.4));
  ctx.lineTo(to.x - head * Math.cos(angle + 0.4), to.y - head * Math.sin(angle + 0.4));
  ctx.closePath();
  ctx.fill();
}

function drawShape(ctx, from, to) {
  paintSetup(ctx);
  if (editor.tool === "circle") {
    const cx = (from.x + to.x) / 2;
    const cy = (from.y + to.y) / 2;
    const rx = Math.abs(to.x - from.x) / 2;
    const ry = Math.abs(to.y - from.y) / 2;
    ctx.beginPath();
    ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
    ctx.stroke();
    const fillCtx = editor.fill && editor.fill.getContext("2d");
    if (fillCtx) {
      fillCtx.beginPath();
      fillCtx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
      fillCtx.fillStyle = "#ffffff";
      fillCtx.fill();
    }
  } else if (editor.tool === "arrow") {
    drawArrow(ctx, from, to);
  } else {
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(to.x, to.y);
    ctx.stroke();
  }
}

$("editCanvas").addEventListener("pointerdown", (event) => {
  if (editor.tool === "text") {
    const text = $("editorLabel").value.trim();
    if (!text) {
      toast("先在旁边写下要标的文字");
      return;
    }
    pushUndo();
    const ctx = canvasCtx();
    const pos = pointerPos(event);
    ctx.font = "700 22px Segoe UI, Microsoft YaHei, sans-serif";
    ctx.lineWidth = 4;
    ctx.strokeStyle = "#fff";
    ctx.strokeText(text, pos.x, pos.y);
    ctx.fillStyle = "#ff2a2a";
    ctx.fillText(text, pos.x, pos.y);
    editor.dirty = true;
    return;
  }
  editor.drawing = true;
  editor.start = pointerPos(event);
  editor.snapshot = canvasCtx().getImageData(0, 0, $("editCanvas").width, $("editCanvas").height);
  if (editor.tool === "brush") {
    const ctx = canvasCtx();
    paintSetup(ctx);
    ctx.beginPath();
    ctx.moveTo(editor.start.x, editor.start.y);
  }
});

$("editCanvas").addEventListener("pointermove", (event) => {
  if (!editor.drawing) return;
  const pos = pointerPos(event);
  if (editor.tool === "brush") {
    const ctx = canvasCtx();
    paintSetup(ctx);
    ctx.beginPath();
    ctx.moveTo(editor.start.x, editor.start.y);
    ctx.lineTo(pos.x, pos.y);
    ctx.stroke();
    editor.start = pos;
    return;
  }
  canvasCtx().putImageData(editor.snapshot, 0, 0);
  drawShape(canvasCtx(), editor.start, pos);
});

function endStroke(event) {
  if (!editor.drawing) return;
  editor.drawing = false;
  if (editor.tool !== "brush" && editor.start) {
    canvasCtx().putImageData(editor.snapshot, 0, 0);
    drawShape(canvasCtx(), editor.start, pointerPos(event));
  }
  editor.dirty = true;
  pushUndo();
}

$("editCanvas").addEventListener("pointerup", endStroke);
$("editCanvas").addEventListener("pointerleave", (event) => {
  if (editor.drawing && editor.tool === "brush") endStroke(event);
});

document.querySelectorAll("[data-tool]").forEach((button) => {
  button.onclick = () => {
    editor.tool = button.dataset.tool;
    document.querySelectorAll("[data-tool]").forEach((item) => item.classList.toggle("on", item === button));
  };
});

$("editorUndo").onclick = restoreUndo;
$("editorClear").onclick = () => loadEditorImage(editor.source);
$("editorClose").onclick = () => $("editor").classList.add("hidden");
$("pickEdit").onclick = () => openEditor();

$("editorForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("editorText").value.trim();
  if (!text) {
    toast("写一下要怎么改");
    return;
  }
  if (!editor.source) return;
  const button = event.target.querySelector("button");
  button.disabled = true;
  try {
    const uploads = [];
    let maskId = "";
    let annotationId = "";
    if (editor.dirty) {
      const saved = await api("/api/upload", {
        method: "POST",
        body: JSON.stringify({ name: "annotation.jpg", data_url: $("editCanvas").toDataURL("image/jpeg", 0.86) }),
      });
      const mask = await api("/api/upload", {
        method: "POST",
        body: JSON.stringify({ name: "mask.png", data_url: buildMaskDataUrl() }),
      });
      annotationId = saved.id;
      maskId = mask.id;
      uploads.push(saved.id, mask.id);
      for (const item of state.uploads) uploads.push(item.id);
    }
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.currentId,
        text,
        upload_ids: uploads,
        source_image: editor.source,
        annotated: editor.dirty,
        annotation_id: annotationId,
        mask_id: maskId,
        image_model: state.imageModel,
        size: $("size").value,
        resolution: $("resolution").value,
        quality: $("quality").value,
        n: 1,
        use_llm: $("useLlm").checked,
        use_previous: true,
      }),
    });
    $("editorText").value = "";
    $("editor").classList.add("hidden");
    state.currentId = data.session.id;
    state.chain = data.chain || state.chain;
    renderChain();
    await openSession(state.currentId);
    await loadSessions();
    if ((data.message.body.image || {}).status === "PENDING") startPoll();
  } catch (err) {
    toast(err.message);
  } finally {
    button.disabled = false;
  }
});

window.__applyMessages = applyMessages;
window.__state = state;
boot();
