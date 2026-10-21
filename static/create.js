const $ = (id) => document.getElementById(id);

const state = {
  category: "any",
  use: "hero",
  count: 1,
  uploadId: "",
  busy: false,
};

const categories = [
  ["any", "不限"],
  ["beauty", "美妆"],
  ["food", "食品"],
  ["home", "家居"],
  ["fashion", "服饰"],
  ["digital", "数码"],
  ["label", "瓶贴"],
];
const photoUses = [
  ["hero", "主图"],
  ["scene", "场景"],
  ["poster", "海报"],
];
const labelUses = [
  ["face", "正面贴"],
  ["band", "环绕纹样"],
  ["preview", "瓶身预览"],
];

function chips(node, items, current, onPick) {
  node.replaceChildren();
  items.forEach(([id, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.className = id === current ? "on" : "";
    button.onclick = () => onPick(id);
    node.appendChild(button);
  });
}

function renderChoices() {
  const uses = state.category === "label" ? labelUses : photoUses;
  if (!uses.some(([id]) => id === state.use)) state.use = uses[0][0];
  $("labelNote").classList.toggle("hidden", state.category !== "label");
  $("idea").placeholder = state.category === "label"
    ? "品名用短词，例如：白茶精华。风格可以写：细线、留白、淡金。"
    : "例如：夏天、干净、想要高级感。留空也可以。";
  chips($("categories"), categories, state.category, (id) => {
    state.category = id;
    renderChoices();
  });
  chips($("uses"), uses, state.use, (id) => {
    state.use = id;
    renderChoices();
  });
  chips($("counts"), [["1", "1 张"], ["4", "4 张"]], String(state.count), (id) => {
    state.count = Number(id);
    renderChoices();
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    $("auth").classList.remove("hidden");
    throw new Error("请先登录");
  }
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("读不到这张图"));
    reader.readAsDataURL(file);
  });
}

$("file").onchange = async () => {
  const file = $("file").files[0];
  $("error").textContent = "";
  if (!file) return;
  try {
    const dataUrl = await readFile(file);
    const saved = await api("/api/upload", {
      method: "POST",
      body: JSON.stringify({ name: file.name, data_url: dataUrl }),
    });
    state.uploadId = saved.id;
    $("preview").src = dataUrl;
    $("preview").classList.remove("hidden");
    $("uploadText").textContent = "已选商品图，可重新上传";
  } catch (err) {
    $("error").textContent = err.message;
  }
};

function payload() {
  return {
    text: $("idea").value.trim(),
    category: state.category,
    use: state.use,
    upload_ids: state.uploadId ? [state.uploadId] : [],
  };
}

$("plan").onclick = async () => {
  if (state.busy) return;
  state.busy = true;
  $("plan").disabled = true;
  $("error").textContent = "";
  $("directions").replaceChildren();
  try {
    const data = await api("/api/creative/plan", { method: "POST", body: JSON.stringify(payload()) });
    renderDirections(data.directions || []);
  } catch (err) {
    $("error").textContent = err.message;
  } finally {
    state.busy = false;
    $("plan").disabled = false;
  }
};

function renderDirections(directions) {
  const root = $("directions");
  root.replaceChildren();
  directions.forEach((direction) => {
    const card = document.createElement("article");
    card.className = "card";
    const title = document.createElement("h2");
    title.textContent = direction.title;
    const scene = document.createElement("p");
    scene.textContent = direction.scene;
    const light = document.createElement("p");
    light.textContent = direction.light + " · " + direction.angle;
    const props = document.createElement("p");
    props.textContent = (direction.props || []).length ? "道具：" + direction.props.join("、") : "不加额外道具";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "draw";
    button.textContent = "按这个画";
    button.onclick = () => draw(direction, button);
    card.append(title, scene, light, props, button);
    root.appendChild(card);
  });
}

async function draw(direction, button) {
  if (state.busy) return;
  state.busy = true;
  button.disabled = true;
  $("error").textContent = "";
  try {
    const data = await api("/api/creative/generate", {
      method: "POST",
      body: JSON.stringify({ ...payload(), direction, n: state.count }),
    });
    location.href = "/?session=" + encodeURIComponent(data.session.id);
  } catch (err) {
    $("error").textContent = err.message;
    state.busy = false;
    button.disabled = false;
  }
}

renderChoices();
api("/api/me").catch(() => {});
