const $ = (id) => document.getElementById(id);
const NL = String.fromCharCode(10);
const PAGE_SIZE = 1500;
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
  fileTotal: 0,
  searchTotal: 0,
  searchQuery: "",
  loadingMore: false,
};

let previewToken = 0;
let listToken = 0;
let moreObserver = null;

function icon(name) {
  return `<svg class="icon" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
}

function brandIcon(name) {
  return `<svg class="icon brand" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
}

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

const TEXT_THUMB_EXT = new Set([
  ".json", ".xml", ".txt", ".conf", ".plist", ".atlas", ".lod", ".ini",
  ".csv", ".yaml", ".yml", ".html", ".css", ".js", ".lua", ".md", ".spr",
  ".animgraph", ".animation", ".animconfig", ".trackgroup",
]);

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

function isTextView(file) {
  const kind = displayKind(file.kind, file.ext);
  const ext = (file.ext || "").toLowerCase();
  return kind === "text" || TEXT_THUMB_EXT.has(ext);
}

function itemAction(file) {
  const kind = displayKind(file.kind, file.ext);
  if (kind === "audio") return { name: "play", label: "播放", icon: "play" };
  if (kind === "image" || isTextView(file)) return { name: "view", label: "放大查看", icon: "zoom" };
  return { name: "preview", label: "放大查看", icon: "zoom" };
}

function thumbMarkup(file, small = false) {
  const kind = displayKind(file.kind, file.ext);
  const ext = (file.ext || "").toLowerCase();
  const width = small ? 96 : 360;
  const thumbUrl = apiUrl("/api/thumb", { path: file.path, w: width, v: 3 });
  const fallback = `<span class="thumb-fallback">${badgeShort(file)}</span>`;
  if (kind === "image") {
    return `<div class="thumb thumb-media">
      <span class="thumb-skeleton skeleton" aria-hidden="true"></span>
      <img decoding="async" data-thumb-src="${thumbUrl}" alt="">
      ${fallback}
    </div>`;
  }
  if (kind === "audio" || kind === "bank") {
    const tag = small ? "" : `<span class="thumb-tag">${esc((file.ext || "WAV").replace(".", "").slice(0, 4).toUpperCase())}</span>`;
    return `<div class="thumb thumb-media thumb-wave">
      <span class="thumb-skeleton skeleton" aria-hidden="true"></span>
      <img decoding="async" data-thumb-src="${thumbUrl}" alt="">
      ${fallback}${tag}
    </div>`;
  }
  if (kind === "video") {
    return `<div class="thumb thumb-video" data-video-src="${esc(apiUrl("/api/video", { path: file.path }))}">
      <span class="thumb-skeleton skeleton" aria-hidden="true"></span>
      <span class="thumb-play">${icon("play")}</span>
      ${fallback}
    </div>`;
  }
  if (!small && TEXT_THUMB_EXT.has(ext)) {
    const lines = `<span class="sk-line"></span>`.repeat(6);
    return `<div class="thumb thumb-text" data-snippet="${esc(apiUrl("/api/snippet", { path: file.path, chars: 420, lines: 8 }))}">
      <span class="thumb-skeleton-lines" aria-hidden="true">${lines}</span>
      ${fallback}
    </div>`;
  }
  return `<div class="thumb">${badgeShort(file)}</div>`;
}

const PREFETCH_MARGIN = 1200;
let thumbPrefetchObserver = null;
let thumbRevealObserver = null;

function clearThumbSource(thumb) {
  const img = thumb.querySelector("img[data-thumb-src]");
  if (img) delete img.dataset.thumbSrc;
  delete thumb.dataset.snippet;
  delete thumb.dataset.videoSrc;
}

function unpreviewThumb(thumb) {
  if (thumbPrefetchObserver) thumbPrefetchObserver.unobserve(thumb);
  if (thumbRevealObserver) thumbRevealObserver.unobserve(thumb);
}

function revealThumb(thumb) {
  if (thumb.dataset.visible !== "1" || thumb.dataset.ready !== "1") return;
  if (thumb.classList.contains("loaded") || thumb.classList.contains("failed")) return;
  clearThumbSource(thumb);
  requestAnimationFrame(() => thumb.classList.add("loaded"));
  unpreviewThumb(thumb);
}

function startThumbLoad(thumb) {
  if (thumb.dataset.started === "1" || thumb.dataset.ready === "1") return;
  thumb.dataset.started = "1";
  const img = thumb.querySelector("img[data-thumb-src]");
  if (img) {
    const src = img.dataset.thumbSrc;
    img.addEventListener("load", () => {
      thumb.dataset.ready = "1";
      revealThumb(thumb);
    }, { once: true });
    img.addEventListener("error", () => {
      img.style.display = "none";
      clearThumbSource(thumb);
      thumb.classList.add("failed");
      unpreviewThumb(thumb);
    }, { once: true });
    img.src = src;
    return;
  }
  if (thumb.dataset.snippet) {
    loadThumbSnippet(thumb);
    return;
  }
  if (thumb.dataset.videoSrc) {
    loadThumbVideo(thumb);
  }
}

function setThumbVisible(thumb, visible) {
  thumb.dataset.visible = visible ? "1" : "0";
  if (visible) revealThumb(thumb);
}

function hydrateThumbs(root) {
  if (thumbPrefetchObserver) {
    thumbPrefetchObserver.disconnect();
    thumbPrefetchObserver = null;
  }
  if (thumbRevealObserver) {
    thumbRevealObserver.disconnect();
    thumbRevealObserver = null;
  }
  const nodes = root.querySelectorAll("[data-thumb-src], [data-snippet], [data-video-src]");
  if (!nodes.length) return;
  const wraps = new Set();
  nodes.forEach((node) => {
    const thumb = node.closest(".thumb");
    if (thumb && !thumb.classList.contains("loaded") && !thumb.classList.contains("failed")) wraps.add(thumb);
  });
  if (!wraps.size) return;
  if (!window.IntersectionObserver) {
    wraps.forEach((thumb) => {
      setThumbVisible(thumb, true);
      startThumbLoad(thumb);
    });
    return;
  }
  thumbPrefetchObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) startThumbLoad(entry.target);
    }
  }, { rootMargin: "1200px 0px" });
  thumbRevealObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) setThumbVisible(entry.target, entry.isIntersecting);
  }, { rootMargin: "0px" });
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
  wraps.forEach((thumb) => {
    thumb.dataset.visible = "0";
    const rect = thumb.getBoundingClientRect();
    const inView = rect.bottom >= 0 && rect.top <= viewportHeight;
    const near = rect.bottom >= -PREFETCH_MARGIN && rect.top <= viewportHeight + PREFETCH_MARGIN;
    thumbPrefetchObserver.observe(thumb);
    thumbRevealObserver.observe(thumb);
    if (inView) setThumbVisible(thumb, true);
    if (near) startThumbLoad(thumb);
  });
}

async function loadThumbSnippet(thumb) {
  const url = thumb.dataset.snippet;
  try {
    const data = await getJson(url);
    if (!data.text) {
      clearThumbSource(thumb);
      thumb.classList.add("failed");
      unpreviewThumb(thumb);
      return;
    }
    const pre = document.createElement("pre");
    pre.className = "thumb-snippet";
    pre.textContent = data.text;
    thumb.appendChild(pre);
    thumb.dataset.ready = "1";
    revealThumb(thumb);
  } catch (_) {
    clearThumbSource(thumb);
    thumb.classList.add("failed");
    unpreviewThumb(thumb);
  }
}

function loadThumbVideo(thumb) {
  const src = thumb.dataset.videoSrc;
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.preload = "metadata";
  video.addEventListener("loadedmetadata", () => {
    try { video.currentTime = Math.min(1, (video.duration || 2) * 0.1); } catch (_) {}
  }, { once: true });
  video.addEventListener("loadeddata", () => {
    thumb.dataset.ready = "1";
    revealThumb(thumb);
  }, { once: true });
  video.addEventListener("error", () => {
    video.remove();
    clearThumbSource(thumb);
    thumb.classList.add("failed");
    unpreviewThumb(thumb);
  }, { once: true });
  video.src = src;
  thumb.insertBefore(video, thumb.firstChild);
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

function fileRowMarkup(file) {
  const act = itemAction(file);
  return `<div class="file-row" data-file="${esc(file.path)}">
    <div class="file-row-thumb">${thumbMarkup(file, true)}</div>
    <div class="file-row-name" title="${esc(file.path)}">${esc(file.name)}</div>
    <div class="file-row-size">${formatBytes(file.size)}</div>
    <div class="file-row-pkg">${esc(file.package)}</div>
    <div class="file-row-action">
      <button class="zoom-btn ${act.name === "play" ? "play-btn" : ""}" type="button" data-action="${act.name}" title="${act.label}" aria-label="${act.label}">${icon(act.icon)}</button>
    </div>
  </div>`;
}

function fileCardMarkup(file) {
  const act = itemAction(file);
  return `<div class="file" data-file="${esc(file.path)}" title="${esc(file.path)}">
    ${thumbMarkup(file)}
    <div class="file-info">
      <div class="file-text">
        <div class="file-name">${esc(file.name)}</div>
        <div class="file-meta">${esc(KIND_LABEL[displayKind(file.kind, file.ext)] || file.kind)} · ${formatBytes(file.size)}</div>
      </div>
      <button class="zoom-btn ${act.name === "play" ? "play-btn" : ""}" type="button" data-action="${act.name}" title="${act.label}" aria-label="${act.label}">${icon(act.icon)}</button>
    </div>
  </div>`;
}

function renderFiles(files) {
  if (!files.length) return "";
  if (state.view === "list") {
    return `<div class="file-list">${files.map(fileRowMarkup).join("")}</div>`;
  }
  return `<div class="file-grid">${files.map(fileCardMarkup).join("")}</div>`;
}

function appendFiles(files) {
  if (!files.length) return;
  const container = document.querySelector("#content .file-list, #content .file-grid");
  if (!container) return;
  const markup = files.map(state.view === "list" ? fileRowMarkup : fileCardMarkup).join("");
  container.insertAdjacentHTML("beforeend", markup);
}

function skeletonScreen(hint) {
  const items = [];
  for (let i = 0; i < 12; i += 1) {
    if (state.view === "list") {
      items.push(`<div class="file-row skeleton-row" aria-hidden="true">
        <div class="file-row-thumb skeleton"></div>
        <div class="sk-line sk-line-name"></div>
        <div class="sk-line sk-line-size"></div>
        <div class="sk-line sk-line-pkg file-row-pkg"></div>
        <div></div>
      </div>`);
    } else {
      items.push(`<div class="file skeleton-card" aria-hidden="true">
        <div class="thumb skeleton"></div>
        <div class="file-info">
          <div class="file-text">
            <div class="sk-line sk-line-name"></div>
            <div class="sk-line sk-line-meta"></div>
          </div>
        </div>
      </div>`);
    }
  }
  const wrapClass = state.view === "list" ? "file-list" : "file-grid";
  return `<div class="skeleton-hint"><span class="spinner"></span>${esc(hint)}</div>
    <div class="${wrapClass} skeleton-wrap">${items.join("")}</div>`;
}

function metaSkeleton() {
  let html = "";
  for (let i = 0; i < 8; i += 1) {
    html += `<div class="sk-line sk-key"></div><div class="sk-line sk-val"></div>`;
  }
  return html;
}

function currentTotal() {
  return state.searchMode ? state.searchTotal : state.fileTotal;
}

function updateCounts() {
  const loaded = state.files.length;
  if (state.searchMode) {
    const total = state.searchTotal;
    $("count").textContent = `${total.toLocaleString()} 个结果${loaded < total ? ` · 已加载 ${loaded.toLocaleString()}` : ""}`;
    $("index-summary").textContent = `${total.toLocaleString()} 个结果${loaded < total ? `（已加载 ${loaded.toLocaleString()}）` : ""}`;
    return;
  }
  const total = state.fileTotal;
  const activeKind = state.kind ? ` · ${KIND_LABEL[state.kind] || state.kind}` : "";
  $("count").textContent = `${Number(state.dirs.length).toLocaleString()} 个目录 · ${total.toLocaleString()} 个文件${activeKind}${loaded < total ? ` · 已加载 ${loaded.toLocaleString()}` : ""}`;
}

function setSentinelIdle() {
  const sentinel = $("list-sentinel");
  if (!sentinel) return;
  sentinel.className = "list-sentinel";
  sentinel.innerHTML = `<span class="load-hint">继续滚动加载更多</span>`;
}

function checkSentinel() {
  if (state.loadingMore) return;
  const sentinel = $("list-sentinel");
  if (!sentinel) return;
  const rect = sentinel.getBoundingClientRect();
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
  if (rect.top <= viewportHeight + 600) loadMore();
}

function ensureSentinel() {
  const content = $("content");
  if (!content) return;
  let sentinel = $("list-sentinel");
  if (state.files.length >= currentTotal()) {
    if (sentinel) sentinel.remove();
    if (moreObserver) {
      moreObserver.disconnect();
      moreObserver = null;
    }
    return;
  }
  if (!sentinel) {
    sentinel = document.createElement("div");
    sentinel.id = "list-sentinel";
    sentinel.className = "list-sentinel";
    content.appendChild(sentinel);
    if (!moreObserver) {
      moreObserver = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadMore();
      }, { rootMargin: "600px 0px" });
    }
    moreObserver.observe(sentinel);
  } else {
    content.appendChild(sentinel);
  }
  setSentinelIdle();
  requestAnimationFrame(checkSentinel);
}

function showLoadError(message) {
  const sentinel = $("list-sentinel");
  if (!sentinel) return;
  sentinel.className = "list-sentinel failed";
  sentinel.innerHTML = `<span>加载失败：${esc(message)}</span><button id="retry-more" class="mini-btn">重试</button>`;
}

async function loadMore() {
  if (state.loadingMore) return;
  const total = currentTotal();
  if (state.files.length >= total) return;
  const token = listToken;
  state.loadingMore = true;
  const sentinel = $("list-sentinel");
  if (sentinel) {
    sentinel.className = "list-sentinel";
    sentinel.innerHTML = `<span class="spinner"></span>正在加载…`;
  }
  try {
    const data = state.searchMode
      ? await getJson(apiUrl("/api/search", { q: state.searchQuery, sort: state.sort, limit: PAGE_SIZE, offset: state.files.length }))
      : await getJson(apiUrl("/api/list", { path: state.path, sort: state.sort, kind: state.kind, limit: PAGE_SIZE, offset: state.files.length }));
    if (token !== listToken) return;
    const more = data.files || [];
    if (state.searchMode) state.searchTotal = Number(data.total ?? state.searchTotal);
    else state.fileTotal = Number(data.file_total ?? state.fileTotal);
    if (!more.length) {
      if (state.searchMode) state.searchTotal = state.files.length;
      else state.fileTotal = state.files.length;
      ensureSentinel();
      return;
    }
    state.files = state.files.concat(more);
    appendFiles(more);
    hydrateThumbs($("content"));
    updateCounts();
    updatePreviewNav();
    ensureSentinel();
  } catch (error) {
    if (token !== listToken) return;
    showLoadError(error.message);
  } finally {
    if (token === listToken) state.loadingMore = false;
  }
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
  const loaded = (data.files || []).length;
  const matchTotal = Number(data.file_total ?? loaded);
  const summary = search
    ? `${matchTotal.toLocaleString()} 个结果${loaded < matchTotal ? `（已加载 ${loaded.toLocaleString()}）` : ""}`
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
    $("stat-files").textContent = "—";
    $("stat-size").textContent = "—";
    $("roots").innerHTML = "";
  }
}

function updateViewButtons() {
  $("view-grid").classList.toggle("active", state.view === "grid");
  $("view-list").classList.toggle("active", state.view === "list");
  $("sort").value = state.sort;
}

async function loadList(path, push = true, keepKind = false) {
  const token = ++listToken;
  stopAudio();
  closeViewer();
  state.loadingMore = false;
  if (moreObserver) {
    moreObserver.disconnect();
    moreObserver = null;
  }
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
  $("content").innerHTML = skeletonScreen("正在读取目录…");
  $("where").textContent = state.path || "全部资源";
  $("index-path").textContent = state.path || "全部资源";
  $("count").textContent = "";
  $("index-summary").textContent = "正在读取…";
  $("index-kinds").innerHTML = "";
  $("index-dirs").innerHTML = "";
  try {
    const data = await getJson(apiUrl("/api/list", {
      path: state.path, sort: state.sort, kind: state.kind, limit: PAGE_SIZE, offset: 0,
    }));
    if (token !== listToken) return;
    state.files = data.files || [];
    state.dirs = data.dirs || [];
    state.dirSummary = data.dir_summary || null;
    state.kindCounts = data.kind_counts || {};
    state.fileTotal = Number(data.file_total ?? state.files.length);
    renderCrumbs(data.breadcrumbs);
    $("where").innerHTML = state.path
      ? `${esc(state.path.split("/").pop())}<span class="sub">${esc(state.path)}</span>`
      : `全部资源`;
    updateCounts();
    $("content").innerHTML = renderFolders(state.dirs) + renderFiles(state.files) || `<div class="empty">这个目录没有可显示的项目。</div>`;
    hydrateThumbs($("content"));
    renderIndex(data);
    ensureSentinel();
    document.querySelectorAll(".root").forEach((node) => {
      const rootPath = node.dataset.dir;
      node.classList.toggle("active", state.path === rootPath || state.path.startsWith(rootPath + "/"));
    });
  } catch (error) {
    if (token !== listToken) return;
    $("content").innerHTML = `<div class="errorbox">读取失败：${esc(error.message)}</div>`;
    $("index-summary").textContent = "";
    $("index-kinds").innerHTML = "";
    $("index-dirs").innerHTML = "";
  }
}

async function search(query) {
  const token = ++listToken;
  stopAudio();
  closeViewer();
  state.loadingMore = false;
  if (moreObserver) {
    moreObserver.disconnect();
    moreObserver = null;
  }
  state.searchMode = true;
  state.searchQuery = query;
  state.kind = "";
  state.previewPath = null;
  document.querySelectorAll(".root").forEach((node) => node.classList.remove("active"));
  closeDrawer();
  $("where").innerHTML = `搜索结果<span class="sub">${esc(query)}</span>`;
  $("index-path").textContent = `搜索：${query}`;
  $("count").textContent = "";
  $("content").innerHTML = skeletonScreen("正在搜索…");
  $("index-summary").textContent = "正在搜索…";
  $("index-kinds").innerHTML = "";
  try {
    const data = await getJson(apiUrl("/api/search", { q: query, limit: PAGE_SIZE, offset: 0, sort: state.sort }));
    if (token !== listToken) return;
    state.files = data.files || [];
    state.dirs = [];
    state.kindCounts = data.kind_counts || {};
    state.searchTotal = Number(data.total ?? state.files.length);
    renderCrumbs([]);
    updateCounts();
    $("content").innerHTML = renderFiles(state.files) || `<div class="empty">没有匹配的路径。</div>`;
    hydrateThumbs($("content"));
    renderIndex({ path: "", dirs: [], files: state.files, file_total: state.searchTotal, kind_counts: state.kindCounts }, true, query);
    ensureSentinel();
  } catch (error) {
    if (token !== listToken) return;
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

async function showBankPreview(file, container, token) {
  const bank = await getJson(apiUrl("/api/bank", { path: file.path }));
  if (token !== previewToken) return;
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
  swapPreview(container);
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

function swapPreview(container) {
  container.classList.remove("swap-in");
  void container.offsetWidth;
  container.classList.add("swap-in");
}

function renderInspect(info, container) {
  if (info.text !== null && info.text !== undefined) {
    container.innerHTML = `<pre>${esc(info.text)}${info.text_truncated ? NL + "…" : ""}</pre>`;
    swapPreview(container);
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
  swapPreview(container);
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

function quickOpenActions() {
  const actions = [];
  if (state.platform === "win32") actions.push(["explorer", "资源管理器"]);
  if (state.platform === "darwin") actions.push(["finder", "访达"]);
  actions.push(["vscode", "VS Code"], ["idea", "IDEA"]);
  return actions;
}

function actionIcon(app) {
  return brandIcon(app === "finder" ? "explorer" : app);
}

function renderOpenActions() {
  $("open-actions").innerHTML = quickOpenActions().map(([app, label]) =>
    `<button class="btn" data-open="${app}">${actionIcon(app)}${label}</button>`).join("");
}

async function openPathWith(path, app, note) {
  if (!path) return;
  if (note) note.textContent = "正在打开…";
  try {
    const response = await fetch("/api/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, app }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "打开失败");
    if (note) note.textContent = result.converted ? `${result.message}（使用转换后的预览图）` : result.message;
  } catch (error) {
    if (note) note.textContent = error.message;
  }
}

async function openWith(app) {
  if (!state.previewPath) return;
  await openPathWith(state.previewPath, app, $("preview-note"));
}

const viewer = {
  token: 0, path: null, file: null, mode: "",
  scale: 1, fit: 1, ox: 0, oy: 0, nw: 0, nh: 0, sw: 0, sh: 0,
  mapScale: 0, mapLeft: 0, mapTop: 0,
  dragging: false, dragX: 0, dragY: 0, mapDragging: false,
};

function viewerIsOpen() {
  return !$("viewer").classList.contains("hidden");
}

function openViewer(file) {
  closeDrawer();
  closeViewer();
  const token = ++viewer.token;
  viewer.file = file;
  viewer.path = file.path;
  viewer.mode = isTextView(file) ? "text" : "image";
  $("viewer-name").textContent = file.name;
  $("viewer").classList.remove("hidden");
  $("viewer").setAttribute("aria-hidden", "false");
  if (viewer.mode === "image") openImageViewer(file, token);
  else openTextViewer(file, token);
}

function closeViewer() {
  viewer.token += 1;
  viewer.path = null;
  viewer.file = null;
  viewer.mode = "";
  viewer.dragging = false;
  viewer.mapDragging = false;
  $("viewer").classList.add("hidden");
  $("viewer").setAttribute("aria-hidden", "true");
  $("viewer-name").textContent = "";
  $("viewer-stage").innerHTML = "";
  $("viewer-foot").innerHTML = "";
}

function imageViewerFoot(file) {
  const mapUrl = apiUrl("/api/thumb", { path: file.path, w: 360, v: 3 });
  return `<div class="viewer-zoom">
    <button class="icon-btn" id="viewer-zoom-out" title="缩小" aria-label="缩小">${icon("minus")}</button>
    <label class="viewer-zoom-value"><input id="viewer-zoom-input" type="number" min="5" max="800" step="1" value="100"><span>%</span></label>
    <button class="icon-btn" id="viewer-zoom-in" title="放大" aria-label="放大">${icon("plus")}</button>
    <button class="mini-btn" id="viewer-zoom-fit" title="适应窗口">${icon("fit")}适应</button>
  </div>
  <div class="viewer-map" id="viewer-map">
    <img id="viewer-map-img" alt="" src="${esc(mapUrl)}">
    <div class="viewer-map-view" id="viewer-map-view"></div>
  </div>`;
}

function openImageViewer(file, token) {
  const stage = $("viewer-stage");
  $("viewer-foot").innerHTML = imageViewerFoot(file);
  stage.innerHTML = `<div class="viewer-loading"><span class="spinner"></span>正在加载原图…</div>`;
  const img = new Image();
  img.id = "viewer-image";
  img.alt = file.name;
  img.draggable = false;
  img.addEventListener("load", () => {
    if (token !== viewer.token) return;
    viewer.nw = img.naturalWidth;
    viewer.nh = img.naturalHeight;
    stage.innerHTML = "";
    stage.appendChild(img);
    layoutImageViewer();
    bindImageViewerControls();
    updateViewerMap();
  }, { once: true });
  img.addEventListener("error", () => {
    if (token !== viewer.token) return;
    stage.innerHTML = `<div class="errorbox">图片加载失败</div>`;
  }, { once: true });
  img.src = apiUrl("/api/image", { path: file.path });
}

function layoutImageViewer() {
  const stage = $("viewer-stage");
  const img = $("viewer-image");
  if (!stage || !img || !viewer.nw) return;
  viewer.sw = stage.clientWidth;
  viewer.sh = stage.clientHeight;
  viewer.fit = Math.min(viewer.sw / viewer.nw, viewer.sh / viewer.nh, 1);
  viewer.scale = viewer.fit;
  viewer.ox = (viewer.sw - viewer.nw * viewer.scale) / 2;
  viewer.oy = (viewer.sh - viewer.nh * viewer.scale) / 2;
  applyImageViewer();
}

function clampImageViewer() {
  const dw = viewer.nw * viewer.scale;
  const dh = viewer.nh * viewer.scale;
  viewer.ox = dw <= viewer.sw ? (viewer.sw - dw) / 2 : Math.min(0, Math.max(viewer.sw - dw, viewer.ox));
  viewer.oy = dh <= viewer.sh ? (viewer.sh - dh) / 2 : Math.min(0, Math.max(viewer.sh - dh, viewer.oy));
}

function applyImageViewer() {
  const img = $("viewer-image");
  if (!img) return;
  img.style.transform = `translate(${viewer.ox}px, ${viewer.oy}px) scale(${viewer.scale})`;
  const input = $("viewer-zoom-input");
  if (input && document.activeElement !== input) input.value = Math.round(viewer.scale * 100);
  updateViewerMap();
}

function setImageViewerScale(next, ax, ay) {
  if (!viewer.nw) return;
  const scale = Math.min(8, Math.max(0.05, next));
  const cx = ax === undefined ? viewer.sw / 2 : ax;
  const cy = ay === undefined ? viewer.sh / 2 : ay;
  const ix = (cx - viewer.ox) / viewer.scale;
  const iy = (cy - viewer.oy) / viewer.scale;
  viewer.scale = scale;
  viewer.ox = cx - ix * scale;
  viewer.oy = cy - iy * scale;
  clampImageViewer();
  applyImageViewer();
}

function bindImageViewerControls() {
  const stage = $("viewer-stage");
  const zoomInput = $("viewer-zoom-input");
  $("viewer-zoom-in").addEventListener("click", () => setImageViewerScale(viewer.scale * 1.25));
  $("viewer-zoom-out").addEventListener("click", () => setImageViewerScale(viewer.scale / 1.25));
  $("viewer-zoom-fit").addEventListener("click", layoutImageViewer);
  zoomInput.addEventListener("change", () => {
    const value = Number(zoomInput.value);
    if (Number.isFinite(value) && value > 0) setImageViewerScale(value / 100);
    else applyImageViewer();
  });
  zoomInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      zoomInput.blur();
    }
  });
  stage.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    if (viewer.nw * viewer.scale <= viewer.sw && viewer.nh * viewer.scale <= viewer.sh) return;
    viewer.dragging = true;
    viewer.dragX = event.clientX;
    viewer.dragY = event.clientY;
    stage.classList.add("dragging");
    stage.setPointerCapture(event.pointerId);
  });
  stage.addEventListener("pointermove", (event) => {
    if (!viewer.dragging) return;
    viewer.ox += event.clientX - viewer.dragX;
    viewer.oy += event.clientY - viewer.dragY;
    viewer.dragX = event.clientX;
    viewer.dragY = event.clientY;
    clampImageViewer();
    applyImageViewer();
  });
  const endDrag = (event) => {
    if (!viewer.dragging) return;
    viewer.dragging = false;
    stage.classList.remove("dragging");
    try { stage.releasePointerCapture(event.pointerId); } catch (_) {}
  };
  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);
  stage.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = stage.getBoundingClientRect();
    const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
    setImageViewerScale(viewer.scale * factor, event.clientX - rect.left, event.clientY - rect.top);
  }, { passive: false });
  bindViewerMapDrag();
}

function updateViewerMap() {
  const map = $("viewer-map");
  const view = $("viewer-map-view");
  if (!map || !view || !viewer.nw || !viewer.scale) return;
  const mw = map.clientWidth;
  const mh = map.clientHeight;
  const ms = Math.min(mw / viewer.nw, mh / viewer.nh);
  const left = (mw - viewer.nw * ms) / 2;
  const top = (mh - viewer.nh * ms) / 2;
  const vw = Math.min(viewer.nw, viewer.sw / viewer.scale);
  const vh = Math.min(viewer.nh, viewer.sh / viewer.scale);
  const vx = Math.max(0, Math.min(viewer.nw - vw, -viewer.ox / viewer.scale));
  const vy = Math.max(0, Math.min(viewer.nh - vh, -viewer.oy / viewer.scale));
  viewer.mapScale = ms;
  viewer.mapLeft = left;
  viewer.mapTop = top;
  view.style.left = `${left + vx * ms}px`;
  view.style.top = `${top + vy * ms}px`;
  view.style.right = "auto";
  view.style.width = `${Math.max(6, vw * ms)}px`;
  view.style.height = `${Math.max(6, vh * ms)}px`;
}

function bindViewerMapDrag() {
  const map = $("viewer-map");
  if (!map) return;
  const moveTo = (event) => {
    if (!viewer.mapScale) return;
    const rect = map.getBoundingClientRect();
    const ix = (event.clientX - rect.left - viewer.mapLeft) / viewer.mapScale;
    const iy = (event.clientY - rect.top - viewer.mapTop) / viewer.mapScale;
    viewer.ox = viewer.sw / 2 - Math.max(0, Math.min(viewer.nw, ix)) * viewer.scale;
    viewer.oy = viewer.sh / 2 - Math.max(0, Math.min(viewer.nh, iy)) * viewer.scale;
    clampImageViewer();
    applyImageViewer();
  };
  map.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    viewer.mapDragging = true;
    map.setPointerCapture(event.pointerId);
    moveTo(event);
    event.preventDefault();
  });
  map.addEventListener("pointermove", (event) => {
    if (viewer.mapDragging) moveTo(event);
  });
  const end = (event) => {
    viewer.mapDragging = false;
    try { map.releasePointerCapture(event.pointerId); } catch (_) {}
  };
  map.addEventListener("pointerup", end);
  map.addEventListener("pointercancel", end);
}

function textViewerFoot() {
  return `<div class="viewer-text-actions">
    <div class="viewer-actions" id="viewer-actions"></div>
    <div class="viewer-note" id="viewer-note"></div>
  </div>
  <div class="viewer-map" id="viewer-map">
    <canvas id="viewer-map-canvas" class="viewer-map-canvas"></canvas>
    <div class="viewer-map-view" id="viewer-map-view"></div>
  </div>`;
}

async function openTextViewer(file, token) {
  const stage = $("viewer-stage");
  $("viewer-foot").innerHTML = textViewerFoot();
  $("viewer-actions").innerHTML = quickOpenActions().map(([app, label]) =>
    `<button class="btn" data-viewer-open="${app}">${actionIcon(app)}${label}</button>`).join("");
  stage.innerHTML = `<div class="viewer-loading"><span class="spinner"></span>正在读取文本…</div>`;
  try {
    const response = await fetch(apiUrl("/api/text", { path: file.path }));
    if (token !== viewer.token) return;
    if (!response.ok) {
      stage.innerHTML = `<div class="errorbox">这个文件无法作为文本显示</div>`;
      return;
    }
    const text = await response.text();
    if (token !== viewer.token) return;
    stage.innerHTML = `<div class="viewer-text-wrap" id="viewer-text-wrap"><pre class="viewer-text" id="viewer-text"></pre></div>`;
    const pre = $("viewer-text");
    pre.textContent = text;
    requestAnimationFrame(() => {
      if (token !== viewer.token) return;
      drawTextViewerMap(text);
      bindTextViewerMap();
      updateTextViewerMap();
    });
  } catch (error) {
    if (token !== viewer.token) return;
    stage.innerHTML = `<div class="errorbox">读取失败：${esc(error.message)}</div>`;
  }
}

function drawTextViewerMap(text) {
  const map = $("viewer-map");
  const canvas = $("viewer-map-canvas");
  if (!map || !canvas) return;
  const width = map.clientWidth;
  const height = map.clientHeight;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(width * dpr));
  canvas.height = Math.max(1, Math.round(height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "rgba(143, 199, 255, .45)";
  const lines = text.split("\n");
  const step = Math.max(1, Math.floor(lines.length / 1500));
  const rows = Math.ceil(lines.length / step);
  const lineH = (height - 8) / Math.max(1, rows);
  for (let i = 0; i < lines.length; i += step) {
    const line = lines[i];
    if (!line.trim()) continue;
    const ratio = Math.min(120, line.length) / 120;
    ctx.fillRect(4, 4 + (i / step) * lineH, Math.max(1, ratio * (width - 8)), Math.max(0.6, lineH * 0.65));
  }
}

function updateTextViewerMap() {
  const wrap = $("viewer-text-wrap");
  const map = $("viewer-map");
  const view = $("viewer-map-view");
  if (!wrap || !map || !view) return;
  const height = map.clientHeight;
  const max = Math.max(1, wrap.scrollHeight - wrap.clientHeight);
  const ratio = max > 0 ? Math.min(1, Math.max(0, wrap.scrollTop / max)) : 0;
  const vh = Math.min(height, Math.max(10, (wrap.clientHeight / Math.max(1, wrap.scrollHeight)) * height));
  view.style.left = "0";
  view.style.right = "0";
  view.style.width = "auto";
  view.style.top = `${ratio * Math.max(0, height - vh)}px`;
  view.style.height = `${vh}px`;
}

function bindTextViewerMap() {
  const map = $("viewer-map");
  const wrap = $("viewer-text-wrap");
  if (!map || !wrap) return;
  let dragging = false;
  const scrollTo = (event) => {
    const rect = map.getBoundingClientRect();
    const y = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
    wrap.scrollTop = y * Math.max(0, wrap.scrollHeight - wrap.clientHeight);
    updateTextViewerMap();
  };
  map.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    dragging = true;
    map.setPointerCapture(event.pointerId);
    scrollTo(event);
    event.preventDefault();
  });
  map.addEventListener("pointermove", (event) => {
    if (dragging) scrollTo(event);
  });
  const end = (event) => {
    dragging = false;
    try { map.releasePointerCapture(event.pointerId); } catch (_) {}
  };
  map.addEventListener("pointerup", end);
  map.addEventListener("pointercancel", end);
  wrap.addEventListener("scroll", updateTextViewerMap, { passive: true });
}

let audioPlayer = null;
let audioCard = null;
let audioPath = null;
let audioFrame = 0;

function formatClock(value) {
  let total = Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  if (minutes > 0) return `${minutes}:${String(seconds).padStart(2, "0")}`;
  return `0:${String(seconds).padStart(2, "0")}`;
}

function findFileNode(path) {
  return Array.from(document.querySelectorAll("[data-file]")).find((node) => node.dataset.file === path) || null;
}

function stopAudio() {
  if (audioPlayer) {
    const player = audioPlayer;
    audioPlayer = null;
    player.pause();
    player.removeAttribute("src");
    try { player.load(); } catch (_) {}
  }
  if (audioFrame) cancelAnimationFrame(audioFrame);
  audioFrame = 0;
  if (audioCard) {
    audioCard.classList.remove("audio-playing");
    const bar = audioCard.querySelector(".audio-progress");
    if (bar) bar.remove();
  }
  audioCard = null;
  audioPath = null;
}

function playAudio(file) {
  stopAudio();
  const card = findFileNode(file.path);
  if (!card) return;
  audioCard = card;
  audioPath = file.path;
  card.classList.add("audio-playing");
  const bar = document.createElement("div");
  bar.className = "audio-progress";
  bar.innerHTML = `<div class="audio-progress-fill"></div>
    <div class="audio-progress-panel"><div class="audio-progress-time"><span class="audio-time-current">0:00</span><span class="audio-time-total">0:00</span></div></div>`;
  card.appendChild(bar);
  bar.addEventListener("click", (event) => {
    event.stopPropagation();
    seekAudio(event);
  });
  bar.addEventListener("pointerdown", (event) => event.stopPropagation());
  const player = new Audio();
  player.preload = "auto";
  audioPlayer = player;
  const isCurrent = () => audioPlayer === player;
  player.addEventListener("loadedmetadata", () => {
    if (isCurrent()) updateAudioProgress();
  });
  player.addEventListener("timeupdate", () => {
    if (isCurrent()) updateAudioProgress();
  });
  player.addEventListener("ended", () => {
    if (isCurrent()) stopAudio();
  });
  player.addEventListener("error", () => {
    if (isCurrent()) stopAudio();
  });
  player.src = apiUrl("/api/audio", { path: file.path });
  player.currentTime = 0;
  player.play().catch(() => {
    if (isCurrent()) stopAudio();
  });
  updateAudioProgress();
  tickAudio();
}

function tickAudio() {
  if (!audioPlayer || !audioCard) return;
  updateAudioProgress();
  audioFrame = requestAnimationFrame(tickAudio);
}

function updateAudioProgress() {
  if (!audioPlayer || !audioCard) return;
  const bar = audioCard.querySelector(".audio-progress");
  if (!bar) return;
  const duration = audioPlayer.duration;
  const current = audioPlayer.currentTime || 0;
  const ratio = Number.isFinite(duration) && duration > 0 ? Math.min(1, current / duration) : 0;
  const fill = bar.querySelector(".audio-progress-fill");
  if (fill) fill.style.width = `${ratio * 100}%`;
  const cur = bar.querySelector(".audio-time-current");
  const total = bar.querySelector(".audio-time-total");
  if (cur) cur.textContent = formatClock(current);
  if (total) total.textContent = Number.isFinite(duration) ? formatClock(duration) : "0:00";
}

function seekAudio(event) {
  if (!audioPlayer || !audioCard) return;
  const duration = audioPlayer.duration;
  if (!Number.isFinite(duration) || duration <= 0) return;
  const bar = audioCard.querySelector(".audio-progress");
  if (!bar) return;
  const rect = bar.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  audioPlayer.currentTime = ratio * duration;
  updateAudioProgress();
}

function setActiveFile(path) {
  let target = null;
  document.querySelectorAll("[data-file]").forEach((node) => {
    const active = !!path && node.dataset.file === path;
    node.classList.toggle("active", active);
    if (active) {
      node.setAttribute("aria-current", "true");
      target = node;
    } else {
      node.removeAttribute("aria-current");
    }
  });
  if (target) target.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function previewIndex() {
  if (!state.previewPath) return -1;
  return state.files.findIndex((item) => item.path === state.previewPath);
}

function updatePreviewNav() {
  const index = previewIndex();
  const total = currentTotal() || state.files.length;
  $("prev-file").disabled = index <= 0;
  $("next-file").disabled = index < 0 || index >= total - 1;
  $("file-position").textContent = index >= 0 ? `${index + 1} / ${total}` : "";
}

async function stepPreview(delta) {
  const index = previewIndex();
  const next = index + delta;
  if (index < 0 || next < 0) return;
  if (next >= state.files.length) {
    if (state.files.length >= currentTotal()) return;
    await loadMore();
  }
  if (next >= state.files.length) return;
  showPreview(state.files[next]);
}

async function showPreview(file) {
  const token = ++previewToken;
  state.previewPath = file.path;
  setActiveFile(file.path);
  updatePreviewNav();
  $("preview-name").textContent = file.name;
  $("preview-badge").textContent = (file.ext || file.kind || "FILE").replace(".", "").slice(0, 5).toUpperCase();
  $("preview-badge").className = `badge ${displayKind(file.kind, file.ext)}`;
  $("preview-media").classList.remove("swap-in");
  $("preview-media").innerHTML = `<div class="preview-skeleton skeleton" aria-hidden="true"></div>`;
  $("preview-meta").innerHTML = metaSkeleton();
  $("preview-note").textContent = "正在读取文件信息…";
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
    if (token !== previewToken) return;
    $("preview-meta").innerHTML = "";
    $("preview-note").textContent = "";
    $("preview-media").innerHTML = `<div class="errorbox">读取信息失败：${esc(error.message)}</div>`;
    return;
  }
  if (token !== previewToken) return;
  $("preview-meta").innerHTML = metadataRows(info);
  const media = $("preview-media");
  const kind = info.kind;
  try {
    if (kind === "image") {
      const url = apiUrl("/api/image", { path: file.path });
      media.innerHTML = `<div class="preview-image">
        <span class="preview-image-skeleton skeleton" aria-hidden="true"></span>
        <img src="${url}" alt="${esc(file.name)}">
      </div>`;
      const wrap = media.querySelector(".preview-image");
      const img = media.querySelector("img");
      const ready = () => requestAnimationFrame(() => wrap && wrap.classList.add("ready"));
      img.addEventListener("load", ready, { once: true });
      img.addEventListener("error", () => {
        media.innerHTML = `<div class="errorbox">图片加载失败</div>`;
      }, { once: true });
      if (img.complete && img.naturalWidth > 0) ready();
      $("open-image").href = url;
      $("open-image").classList.remove("hidden");
      $("preview-note").textContent = "首次打开可能需要一点时间。";
    } else if (kind === "bank") {
      $("download-link").textContent = "下载音频包";
      $("preview-note").textContent = "正在解析 Bank 结构与内嵌音频…";
      await showBankPreview(file, media, token);
    } else if (kind === "audio") {
      const url = apiUrl("/api/audio", { path: file.path });
      media.innerHTML = `<audio controls autoplay preload="auto" src="${url}"></audio>`;
      $("download-link").textContent = "下载 FLAC";
      $("download-link").href = apiUrl("/api/audio", { path: file.path, download: 1 });
      $("preview-note").textContent = /^\d+\.wem$/i.test(file.name)
        ? "这是 Wwise 媒体 Short ID；原始事件名未随客户端资源提供。音频按需加载。"
        : "音频按需加载，直接播放。";
    } else if (kind === "video") {
      media.innerHTML = `<div class="preview-video">
        <span class="preview-video-skeleton skeleton" aria-hidden="true"></span>
        <video controls preload="metadata" src="${apiUrl("/api/video", { path: file.path })}"></video>
      </div>`;
      const wrap = media.querySelector(".preview-video");
      const video = media.querySelector("video");
      video.addEventListener("loadedmetadata", () => requestAnimationFrame(() => wrap && wrap.classList.add("ready")), { once: true });
      video.addEventListener("error", () => {
        media.innerHTML = `<div class="errorbox">视频加载失败</div>`;
      }, { once: true });
      $("preview-note").textContent = "可以直接播放并拖动进度。";
    } else {
      media.innerHTML = `<div class="preview-skeleton skeleton" aria-hidden="true"></div>`;
      $("preview-note").textContent = "正在读取资源结构…";
      const data = await getJson(apiUrl("/api/inspect", { path: file.path }));
      if (token !== previewToken) return;
      renderInspect(data, media);
    }
  } catch (error) {
    if (token !== previewToken) return;
    $("preview-note").textContent = "";
    media.innerHTML = `<div class="errorbox">预览失败：${esc(error.message)}<br>可以使用“下载文件”查看原始内容。</div>`;
  }
}

function closeDrawer() {
  previewToken += 1;
  $("drawer").classList.add("hidden");
  $("drawer").setAttribute("aria-hidden", "true");
  $("overlay").classList.add("hidden");
  state.previewPath = null;
  setActiveFile(null);
  updatePreviewNav();
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
    if (event.target.closest("#retry-more")) {
      loadMore();
      return;
    }
    const action = event.target.closest("[data-action]");
    if (action) {
      const node = action.closest("[data-file]");
      const match = node ? state.files.find((item) => item.path === node.dataset.file) : null;
      if (match) {
        if (action.dataset.action === "play") playAudio(match);
        else if (action.dataset.action === "view") openViewer(match);
        else showPreview(match);
      }
      return;
    }
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
  $("viewer-close").addEventListener("click", closeViewer);
  $("viewer").addEventListener("click", (event) => {
    if (!event.target.closest(".viewer-panel")) closeViewer();
  });
  $("viewer-foot").addEventListener("click", (event) => {
    const button = event.target.closest("[data-viewer-open]");
    if (button && viewer.path) openPathWith(viewer.path, button.dataset.viewerOpen, $("viewer-note"));
  });
  $("prev-file").addEventListener("click", () => stepPreview(-1));
  $("next-file").addEventListener("click", () => stepPreview(1));
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
  window.addEventListener("resize", () => {
    if (!viewerIsOpen()) return;
    if (viewer.mode === "image" && viewer.nw) layoutImageViewer();
    else if (viewer.mode === "text") {
      drawTextViewerMap($("viewer-text")?.textContent || "");
      updateTextViewerMap();
    }
  });
  if (window.ResizeObserver) {
    new ResizeObserver(syncTopbarHeight).observe($("content-topbar"));
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (viewerIsOpen()) closeViewer();
      else if (!$("drawer").classList.contains("hidden")) closeDrawer();
      else if ($("search").value) { $("search").value = ""; loadList(state.path, false); }
    }
    if (event.key === "/" && !viewerIsOpen() && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      event.preventDefault();
      $("search").focus();
    }
  });
}

async function init() {
  bind();
  updateViewButtons();
  syncTopbarHeight();
  $("stat-files").innerHTML = `<span class="sk-line stat-sk" style="width:46px"></span>`;
  $("stat-size").innerHTML = `<span class="sk-line stat-sk" style="width:54px"></span>`;
  $("roots").innerHTML = Array.from({ length: 6 }, () => `<div class="root skeleton-root">
    <span class="sk-line" style="width:62%"></span><span class="sk-line" style="width:26px"></span>
  </div>`).join("");
  const path = new URLSearchParams(location.search).get("path") || "";
  const statsPromise = loadStats();
  await loadList(path, false);
  await statsPromise;
}

init();
