const $ = (id) => document.getElementById(id);
const NL = String.fromCharCode(10);
const SORT_KEYS = new Set(["name_asc", "name_desc", "size_asc", "size_desc", "type", "package"]);
const storedView = localStorage.getItem("npk-view") || "grid";
const storedSort = localStorage.getItem("npk-sort") || "name_asc";
const state = {
  path: "",
  files: [],
  dirs: [],
  view: ["grid", "list"].includes(storedView) ? storedView : "grid",
  sort: SORT_KEYS.has(storedSort) ? storedSort : "name_asc",
  kind: "",
  searchMode: false,
  previewPath: null,
  platform: "win32",
  dirSummary: null,
  kindCounts: {},
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function formatBytes(n) {
  n = Number(n || 0);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)} ${units[i]}`;
}

function apiUrl(route, params = {}) {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") qs.set(key, value);
  }
  return `${route}?${qs.toString()}`;
}

async function getJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).error || message; } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}

const KIND_LABEL = {
  image: "图片", audio: "音频", bank: "音频包", video: "视频", text: "文本",
  script: "脚本", model: "模型", binary: "二进制", dir: "目录",
};

const ROOT_LABEL = {
  _script: "脚本资源",
  _unresolved: "未识别资源",
};

const EXT_KIND = {
  ".gim": "binary", ".mtg": "binary", ".sfx": "binary", ".efx": "binary",
  ".gmp": "binary", ".xn": "script", ".qm": "binary", ".csb": "binary",
  ".mesh": "model", ".skeleton": "model", ".rawanimation": "model",
  ".animconfig": "binary", ".animgraph": "model", ".animation": "model",
  ".trackgroup": "model",
};

function displayKind(kind, ext) {
  return EXT_KIND[(ext || "").toLowerCase()] || kind;
}

function badge(kind, text) {
  return `<span class="badge ${esc(kind)}">${esc(text || KIND_LABEL[kind] || "BIN")}</span>`;
}

function badgeShort(file) {
  const kind = displayKind(file.kind, file.ext);
  if (kind === "image") return badge("image", (file.ext || ".img").replace(".", "").slice(0, 4).toUpperCase() || "IMG");
  if (kind === "audio") return badge("audio", "WAV");
  if (kind === "bank") return badge("bank", "BNK");
  if (kind === "video") return badge("video", "VID");
  if (kind === "text") return badge("text", "TXT");
  if (kind === "script") return badge("script", "XN");
  if (kind === "model") return badge("model", "3D");
  return badge("binary", (file.ext || "BIN").replace(".", "").slice(0, 4).toUpperCase() || "BIN");
}

function thumbMarkup(file) {
  if (file.kind !== "image") {
    return `<div class="thumb">${badgeShort(file)}</div>`;
  }
  const label = esc((file.ext || "IMG").replace(".", "").slice(0, 4).toUpperCase() || "IMG");
  return `<div class="thumb">
    <img loading="lazy" decoding="async" src="${apiUrl("/api/thumb", { path: file.path, w: 360 })}" alt=""
         onerror="this.style.display='none';this.nextElementSibling.style.display='inline-flex'">
    <span class="badge image" style="display:none;position:absolute">${label}</span>
  </div>`;
}

function renderCrumbs(crumbs) {
  const node = $("crumb");
  const parts = [`<button data-dir="">全部资源</button>`];
  for (const crumb of crumbs || []) {
    parts.push('<span class="sep">/</span>');
    parts.push(`<button data-dir="${esc(crumb.path)}">${esc(crumb.name)}</button>`);
  }
  node.innerHTML = parts.join("");
}

function renderRoots(roots) {
  $("roots").innerHTML = (roots || []).map((root) => {
    const active = state.path === root.path || state.path.startsWith(root.path + "/");
    return `<button class="root ${active ? "active" : ""}" data-dir="${esc(root.path)}" title="${esc(root.path)}">
      <span class="root-name">${esc(ROOT_LABEL[root.path] || root.path)}</span>
      <span class="root-count">${Number(root.file_count).toLocaleString()}</span>
    </button>`;
  }).join("");
}

function renderFolders(dirs) {
  if (!dirs.length) return "";
  return `<div class="folder-grid">${dirs.map((dir) => `
    <button class="folder" data-dir="${esc(dir.path)}">
      <span class="folder-ico"></span>
      <span class="folder-text">
        <span class="folder-name">${esc(dir.name)}</span>
        <span class="folder-meta">${Number(dir.count).toLocaleString()} 个文件 · ${formatBytes(dir.bytes)}</span>
      </span>
    </button>`).join("")}</div>`;
}

function renderFiles(files) {
  if (!files.length) return "";
  if (state.view === "list") {
    return `<div class="file-list">${files.map((file) => `
      <div class="file-row" data-file="${esc(file.path)}">
        ${badgeShort(file)}
        <div class="file-row-name" title="${esc(file.path)}">${esc(file.name)}</div>
        <div class="file-row-size">${formatBytes(file.size)}</div>
        <div class="file-row-pkg">${esc(file.package)}</div>
      </div>`).join("")}</div>`;
  }
  return `<div class="file-grid">${files.map((file) => `
    <div class="file" data-file="${esc(file.path)}" title="${esc(file.path)}">
      ${thumbMarkup(file)}
      <div class="file-info">
        <div class="file-name">${esc(file.name)}</div>
        <div class="file-meta">${esc(KIND_LABEL[displayKind(file.kind, file.ext)] || file.kind)} · ${formatBytes(file.size)}</div>
      </div>
    </div>`).join("")}</div>`;
}

function parentPath(path) {
  if (!path) return "";
  const index = path.lastIndexOf("/");
  return index < 0 ? "" : path.slice(0, index);
}

function renderKindChips(kinds, total, interactive = true) {
  const entries = Object.entries(kinds || {}).sort((a, b) => b[1] - a[1]);
  const chips = [`${interactive ? `<button class="kind-chip ${state.kind === "" ? "active" : ""}" data-kind="">` : `<span class="kind-chip ${state.kind === "" ? "active" : ""}">`}全部 <b>${Number(total || 0).toLocaleString()}</b>${interactive ? "</button>" : "</span>"}`];
  for (const [kind, count] of entries) {
    const active = state.kind === kind ? "active" : "";
    const label = `${esc(KIND_LABEL[kind] || kind)} <b>${Number(count).toLocaleString()}</b>`;
    chips.push(interactive
      ? `<button class="kind-chip ${active}" data-kind="${esc(kind)}">${label}</button>`
      : `<span class="kind-chip ${active}">${label}</span>`);
  }
  return chips.join("");
}

function renderIndex(data, search = false, query = "") {
  const path = data.path || "";
  $("index-path").textContent = search ? `搜索：${query}` : (path || "全部资源");
  const summary = search
    ? `${Number(data.file_total ?? (data.files || []).length).toLocaleString()} 个结果`
    : `${Number(data.dir_summary?.files || 0).toLocaleString()} 个文件（含子目录） · ${formatBytes(data.dir_summary?.bytes || 0)}`;
  $("index-summary").textContent = summary;
  $("index-up").disabled = search || !path;
  $("index-up").dataset.parent = parentPath(path);
  $("copy-index").disabled = search && !query;
  $("copy-index").textContent = search ? "复制关键词" : "复制路径";
  const kinds = data.kind_counts || {};
  const total = Object.values(kinds).reduce((sum, n) => sum + Number(n), 0);
  $("index-kinds").innerHTML = renderKindChips(kinds, total, !search);
  if (search) {
    $("index-dirs").innerHTML = `<div class="muted" style="font-size:11px">搜索结果不显示子目录。</div>`;
  } else {
    const dirs = data.dirs || [];
    $("index-dirs").innerHTML = dirs.length
      ? dirs.map((dir) => `<button class="index-dir" data-dir="${esc(dir.path)}">
          <span>${esc(dir.name)}</span><span>${Number(dir.count).toLocaleString()}</span>
        </button>`).join("")
      : `<div class="muted" style="font-size:11px">没有子目录。</div>`;
  }
}

async function loadStats() {
  try {
    const stats = await getJson("/api/stats");
    state.platform = stats.platform || "win32";
    $("stat-files").textContent = Number(stats.files).toLocaleString();
    $("stat-size").textContent = formatBytes(stats.bytes);
    renderRoots(stats.roots);
  } catch (error) {
    console.error(error);
  }
}

function updateViewButtons() {
  $("view-grid").classList.toggle("active", state.view === "grid");
  $("view-list").classList.toggle("active", state.view === "list");
  $("sort").value = state.sort;
}

async function loadList(path, push = true, keepKind = false) {
  state.searchMode = false;
  $("search").value = "";
  state.path = path || "";
  if (!keepKind) state.kind = "";
  state.previewPath = null;
  closeDrawer();
  if (push) {
    const url = new URL(location.href);
    if (state.path) url.searchParams.set("path", state.path);
    else url.searchParams.delete("path");
    history.pushState({ path: state.path }, "", url);
  }
  $("content").innerHTML = `<div class="loading"><span class="spinner"></span>正在读取目录…</div>`;
  $("where").textContent = state.path || "全部资源";
  $("count").textContent = "";
  try {
    const data = await getJson(apiUrl("/api/list", {
      path: state.path, sort: state.sort, kind: state.kind,
    }));
    state.files = data.files || [];
    state.dirs = data.dirs || [];
    state.dirSummary = data.dir_summary || null;
    state.kindCounts = data.kind_counts || {};
    renderCrumbs(data.breadcrumbs);
    $("where").innerHTML = state.path
      ? `${esc(state.path.split("/").pop())}<span class="sub">${esc(state.path)}</span>`
      : `全部资源`;
    const totalFiles = Number(data.file_total ?? state.files.length);
    const activeKind = state.kind ? ` · ${esc(KIND_LABEL[state.kind] || state.kind)}` : "";
    $("count").textContent = `${Number(state.dirs.length).toLocaleString()} 个目录 · ${totalFiles.toLocaleString()} 个文件${activeKind}`;
    let html = renderFolders(state.dirs) + renderFiles(state.files);
    if (data.truncated) {
      html += `<div class="note" style="margin-top:12px">当前目录文件较多，只显示前 ${state.files.length.toLocaleString()} 个；请用搜索定位其余文件。</div>`;
    }
    $("content").innerHTML = html || `<div class="empty">这个目录没有可显示的项目。</div>`;
    renderIndex(data);
    document.querySelectorAll(".root").forEach((node) => {
      const rootPath = node.dataset.dir;
      node.classList.toggle("active", state.path === rootPath || state.path.startsWith(rootPath + "/"));
    });
  } catch (error) {
    $("content").innerHTML = `<div class="errorbox">读取失败：${esc(error.message)}</div>`;
    $("index-summary").textContent = "";
    $("index-kinds").innerHTML = "";
    $("index-dirs").innerHTML = "";
  }
}

async function search(query) {
  state.searchMode = true;
  state.kind = "";
  state.previewPath = null;
  document.querySelectorAll(".root").forEach((node) => node.classList.remove("active"));
  closeDrawer();
  $("where").innerHTML = `搜索结果<span class="sub">${esc(query)}</span>`;
  $("count").textContent = "";
  $("content").innerHTML = `<div class="loading"><span class="spinner"></span>正在搜索…</div>`;
  try {
    const data = await getJson(apiUrl("/api/search", { q: query, limit: 300, sort: state.sort }));
    state.files = data.files || [];
    state.dirs = [];
    state.kindCounts = {};
    const counts = {};
    for (const file of state.files) counts[file.kind] = (counts[file.kind] || 0) + 1;
    renderCrumbs([]);
    $("count").textContent = `${state.files.length} 个结果${state.files.length >= 300 ? "（已截断）" : ""}`;
    $("content").innerHTML = renderFiles(state.files) || `<div class="empty">没有匹配的路径。</div>`;
    renderIndex({ path: "", dirs: [], files: state.files, file_total: state.files.length, kind_counts: counts }, true, query);
  } catch (error) {
    $("content").innerHTML = `<div class="errorbox">搜索失败：${esc(error.message)}</div>`;
  }
}

function bankMediaDetail(media) {
  const parts = [];
  if (media.codec) parts.push(media.codec);
  if (media.channels) parts.push(`${media.channels} 声道`);
  if (media.sample_rate) parts.push(`${Number(media.sample_rate).toLocaleString()} Hz`);
  return parts.join(" · ") || "内嵌 WEM";
}

async function showBankPreview(file, container) {
  const bank = await getJson(apiUrl("/api/bank", { path: file.path }));
  const entries = bank.media || [];
  const hirc = bank.hirc || {};
  const first = entries.find((item) => item.valid);
  const header = bank.header || {};
  const audio = first
    ? `<audio id="bank-audio" controls autoplay preload="auto" src="${apiUrl("/api/audio", { path: file.path, media: first.id })}"></audio>`
    : "";
  const rows = entries.length
    ? `<div class="bank-media-list">${entries.map((item) => `
        <button class="bank-media-row${first && item.id === first.id ? " active" : ""}" data-media="${esc(item.id)}" ${item.valid ? "" : "disabled"} title="0x${esc(item.id_hex || "")}">
          <span class="bank-media-id">${esc(item.id)}</span>
          <span class="bank-media-main">${esc(bankMediaDetail(item))}</span>
          <span class="bank-media-size">${formatBytes(item.size)}</span>
        </button>`).join("")}</div>`
    : `<div class="bank-empty">这个音频包没有内嵌 WEM 音频，可能只包含事件或容器定义。</div>`;
  const hircTypes = (hirc.types || []).map((type) =>
    `<span class="bank-chip">${esc(type.name)} <b>${Number(type.count).toLocaleString()}</b></span>`).join("");
  container.innerHTML = `<div class="bank-summary">
    <div class="bank-head">
      <div><b>Wwise Bank</b><span class="bank-sub">版本 ${esc(header.version ?? "—")} · ID ${esc(header.bank_id_hex || "—")}</span></div>
      <div class="bank-stats">${entries.length.toLocaleString()} 个内嵌音频 · ${Number(hirc.count || 0).toLocaleString()} 个 HIRC 对象</div>
    </div>
    ${audio}
    ${rows}
    <div class="section-title">结构与数据块</div>
    <div class="bank-chips">${(bank.chunks || []).map((chunk) =>
      `<span class="bank-chip">${esc(chunk.id)} <b>${formatBytes(chunk.size)}</b></span>`).join("")}</div>
    ${hircTypes ? `<div class="section-title">HIRC 对象类型</div><div class="bank-chips">${hircTypes}</div>` : ""}
    ${(bank.warnings || []).length ? `<div class="note">${esc(bank.warnings.join("；"))}</div>` : ""}
  </div>`;
  $("preview-note").textContent = entries.length
    ? "BNK 是 Wwise 音频包；内嵌 WEM 按需解码为 FLAC，不会保存到磁盘。"
    : "这个 Bank 没有内嵌 WEM 音频，可能只包含事件或容器定义。";
  const list = container.querySelector(".bank-media-list");
  if (!list) return;
  list.addEventListener("click", (event) => {
    const row = event.target.closest("[data-media]");
    if (!row) return;
    const audioNode = container.querySelector("#bank-audio");
    if (!audioNode) return;
    list.querySelectorAll(".bank-media-row").forEach((node) => node.classList.toggle("active", node === row));
    audioNode.src = apiUrl("/api/audio", { path: file.path, media: row.dataset.media });
    audioNode.play().catch(() => {});
    $("preview-note").textContent = `正在播放内嵌音频 ${row.dataset.media}；按需解码，不会保存到磁盘。`;
  });
}

function renderInspect(info, container) {
  if (info.text !== null && info.text !== undefined) {
    container.innerHTML = `<pre>${esc(info.text)}${info.text_truncated ? NL + "…" : ""}</pre>`;
    const refs = info.references || [];
    $("preview-note").textContent = `${info.summary}${refs.length ? ` 共找到 ${refs.length} 个引用路径。` : ""}${info.text_truncated ? " 内容较大，仅显示开头部分。" : ""}`;
    return;
  }
  const header = info.header || {};
  const headerChips = Object.entries(header)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([key, value]) => `<span class="bank-chip">${esc(key)} <b>${esc(value)}</b></span>`)
    .join("");
  const references = info.references || [];
  const strings = info.strings || [];
  container.innerHTML = `<div class="inspect-card">
    <div class="inspect-head">
      <div class="inspect-format">${esc(info.format)}</div>
      <div class="inspect-size">${formatBytes(info.size)}</div>
    </div>
    <div class="inspect-summary">${esc(info.summary)}</div>
    ${headerChips ? `<div class="bank-chips">${headerChips}</div>` : ""}
    ${references.length ? `<div class="section-title">引用资源</div><div class="inspect-refs">${references.map((ref) => `<span class="inspect-ref" title="${esc(ref)}">${esc(ref)}</span>`).join("")}</div>` : ""}
    ${strings.length ? `<div class="section-title">可读字段</div><pre class="inspect-strings">${esc(strings.join(NL))}${info.strings_truncated ? NL + "…" : ""}</pre>` : ""}
    <details class="inspect-hex">
      <summary>原始 Hex（前 4 KB）</summary>
      <pre>${esc((info.hex_lines || []).join(NL))}${info.hex_truncated ? NL + "…" : ""}</pre>
    </details>
    ${info.renderable === "none" ? `<div class="note">这个格式主要保存参数和引用关系，不是可直接显示的图片或模型；完整可视化需要对应的引擎模块。</div>` : ""}
  </div>`;
  $("preview-note").textContent = strings.length || references.length
    ? "已提取可读字段和引用资源；原始 Hex 可展开查看。"
    : "未找到可读字段；可以展开 Hex 或下载原文件。";
}

function metadataRows(info) {
  const rows = [
    ["路径", info.path],
    ["资源包", info.package],
    ["类型", `${KIND_LABEL[displayKind(info.kind, info.ext)] || info.kind} ${info.ext || ""}`],
    ["大小", formatBytes(info.unpacked)],
    ["存储大小", formatBytes(info.packed)],
    ["存储方式", info.compression === 3 ? "压缩" : "原始"],
    ["标记", info.flags],
    ["标识", info.signature],
    ["位置", info.offset],
    ["压缩校验", info.packed_hash],
    ["原始校验", info.unpacked_hash],
  ];
  return rows.map(([key, value]) => `<div class="k">${esc(key)}</div><div class="v">${esc(value)}</div>`).join("");
}

function renderOpenActions() {
  const actions = [];
  if (state.platform === "win32") actions.push(["explorer", "资源管理器"]);
  if (state.platform === "darwin") actions.push(["finder", "访达"]);
  actions.push(["vscode", "VS Code"], ["idea", "IDEA"]);
  $("open-actions").innerHTML = actions.map(([app, label]) =>
    `<button class="btn" data-open="${app}">${label}</button>`).join("");
}

async function openWith(app) {
  if (!state.previewPath) return;
  const note = $("preview-note");
  note.textContent = "正在打开…";
  try {
    const response = await fetch("/api/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: state.previewPath, app }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "打开失败");
    note.textContent = result.converted ? `${result.message}（使用转换后的预览图）` : result.message;
  } catch (error) {
    note.textContent = error.message;
  }
}

async function showPreview(file) {
  state.previewPath = file.path;
  $("preview-name").textContent = file.name;
  $("preview-badge").textContent = (file.ext || file.kind || "FILE").replace(".", "").slice(0, 5).toUpperCase();
  $("preview-badge").className = `badge ${displayKind(file.kind, file.ext)}`;
  $("preview-media").innerHTML = `<div class="placeholder"><span class="spinner"></span>正在准备预览…</div>`;
  $("preview-meta").innerHTML = "";
  $("preview-note").textContent = "";
  $("open-image").classList.add("hidden");
  renderOpenActions();
  $("drawer").classList.remove("hidden");
  $("drawer").setAttribute("aria-hidden", "false");
  $("overlay").classList.remove("hidden");
  $("download-link").textContent = "下载文件";
  $("download-link").href = apiUrl("/api/download", { path: file.path });
  $("download-link").setAttribute("download", file.name);

  let info;
  try {
    info = await getJson(apiUrl("/api/info", { path: file.path }));
  } catch (error) {
    $("preview-media").innerHTML = `<div class="errorbox">读取信息失败：${esc(error.message)}</div>`;
    return;
  }
  $("preview-meta").innerHTML = metadataRows(info);
  const media = $("preview-media");
  const kind = info.kind;
  try {
    if (kind === "image") {
      const url = apiUrl("/api/image", { path: file.path });
      media.innerHTML = `<img src="${url}" alt="${esc(file.name)}">`;
      $("open-image").href = url;
      $("open-image").classList.remove("hidden");
      $("preview-note").textContent = "首次打开可能需要一点时间。";
    } else if (kind === "bank") {
      $("download-link").textContent = "下载音频包";
      await showBankPreview(file, media);
    } else if (kind === "audio") {
      const url = apiUrl("/api/audio", { path: file.path });
      media.innerHTML = `<audio controls autoplay preload="auto" src="${url}"></audio>`;
      $("download-link").textContent = "下载 FLAC";
      $("download-link").href = apiUrl("/api/audio", { path: file.path, download: 1 });
      $("preview-note").textContent = /^\d+\.wem$/i.test(file.name)
        ? "这是 Wwise 媒体 Short ID；原始事件名未随客户端资源提供。音频按需加载。"
        : "音频按需加载，直接播放。";
    } else if (kind === "video") {
      media.innerHTML = `<video controls src="${apiUrl("/api/video", { path: file.path })}"></video>`;
      $("preview-note").textContent = "可以直接播放并拖动进度。";
    } else {
      media.innerHTML = `<div class="placeholder"><span class="spinner"></span>正在读取结构…</div>`;
      const data = await getJson(apiUrl("/api/inspect", { path: file.path }));
      renderInspect(data, media);
    }
  } catch (error) {
    media.innerHTML = `<div class="errorbox">预览失败：${esc(error.message)}<br>可以使用“下载文件”查看原始内容。</div>`;
  }
}

function closeDrawer() {
  $("drawer").classList.add("hidden");
  $("drawer").setAttribute("aria-hidden", "true");
  $("overlay").classList.add("hidden");
  state.previewPath = null;
  $("preview-media").innerHTML = "";
  $("open-actions").innerHTML = "";
  $("preview-note").textContent = "";
}

async function copyText(text, button, label) {
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "已复制";
    setTimeout(() => { button.textContent = label; }, 1200);
  } catch (_) {
    button.textContent = "复制失败";
  }
}

async function copyPath() {
  if (!state.previewPath) return;
  await copyText(state.previewPath, $("copy-path"), "复制路径");
}

function syncTopbarHeight() {
  const bar = $("content-topbar");
  if (!bar) return;
  document.documentElement.style.setProperty("--content-topbar-height", `${Math.ceil(bar.getBoundingClientRect().height)}px`);
}

function setView(view) {
  state.view = view;
  localStorage.setItem("npk-view", view);
  updateViewButtons();
  if (state.searchMode) {
    const query = $("search").value.trim();
    if (query.length >= 2) { search(query); return; }
  }
  loadList(state.path, false, true);
}

let searchTimer = null;
function onSearchInput() {
  const query = $("search").value.trim();
  clearTimeout(searchTimer);
  if (query.length >= 2) {
    searchTimer = setTimeout(() => search(query), 260);
  } else if (state.searchMode) {
    state.searchMode = false;
    loadList(state.path, false);
  }
}

function bind() {
  $("content").addEventListener("click", (event) => {
    const dir = event.target.closest("[data-dir]");
    if (dir) { loadList(dir.dataset.dir || ""); return; }
    const file = event.target.closest("[data-file]");
    if (file) {
      const match = state.files.find((item) => item.path === file.dataset.file);
      if (match) showPreview(match);
    }
  });
  $("crumb").addEventListener("click", (event) => {
    const target = event.target.closest("[data-dir]");
    if (target) loadList(target.dataset.dir || "");
  });
  $("roots").addEventListener("click", (event) => {
    const target = event.target.closest("[data-dir]");
    if (target) loadList(target.dataset.dir || "");
  });
  $("index-pane").addEventListener("click", (event) => {
    const kind = event.target.closest("[data-kind]");
    if (kind) {
      state.kind = kind.dataset.kind || "";
      loadList(state.path, false, true);
      return;
    }
    const dir = event.target.closest("[data-dir]");
    if (dir) { loadList(dir.dataset.dir || ""); return; }
    if (event.target.closest("#index-up")) {
      loadList($("index-up").dataset.parent || "", true);
      return;
    }
    if (event.target.closest("#copy-index")) {
      const search = state.searchMode;
      copyText(search ? ($("search").value.trim()) : state.path, $("copy-index"), search ? "复制关键词" : "复制路径");
    }
  });
  $("search").addEventListener("input", onSearchInput);
  $("sort").addEventListener("change", () => {
    state.sort = $("sort").value;
    localStorage.setItem("npk-sort", state.sort);
    if (state.searchMode) {
      const query = $("search").value.trim();
      if (query.length >= 2) search(query); else loadList(state.path, false, true);
    } else {
      loadList(state.path, false, true);
    }
  });
  $("refresh").addEventListener("click", async () => {
    await loadStats();
    if (state.searchMode) {
      const query = $("search").value.trim();
      if (query.length >= 2) search(query); else loadList(state.path, false);
    } else {
      loadList(state.path, false, true);
    }
  });
  $("view-grid").addEventListener("click", () => setView("grid"));
  $("view-list").addEventListener("click", () => setView("list"));
  $("close-drawer").addEventListener("click", closeDrawer);
  $("overlay").addEventListener("click", closeDrawer);
  $("copy-path").addEventListener("click", copyPath);
  $("open-actions").addEventListener("click", (event) => {
    const button = event.target.closest("[data-open]");
    if (button) openWith(button.dataset.open);
  });
  window.addEventListener("popstate", () => {
    const path = new URLSearchParams(location.search).get("path") || "";
    loadList(path, false);
  });
  window.addEventListener("resize", syncTopbarHeight);
  if (window.ResizeObserver) {
    new ResizeObserver(syncTopbarHeight).observe($("content-topbar"));
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (!$("drawer").classList.contains("hidden")) closeDrawer();
      else if ($("search").value) { $("search").value = ""; loadList(state.path, false); }
    }
    if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      event.preventDefault();
      $("search").focus();
    }
  });
}

async function init() {
  bind();
  updateViewButtons();
  syncTopbarHeight();
  const path = new URLSearchParams(location.search).get("path") || "";
  await loadStats();
  await loadList(path, false);
}

init();
