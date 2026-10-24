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
let drawerCloseTimer = 0;
let listToken = 0;
let moreObserver = null;

const I18N_CODES = ["zh_cn", "zh_tw", "en_us"];
const I18N_STORAGE = "npk-language";
const PROJECT_URL = "https://github.com/EggyFly/NPK-X";
const RELEASES_URL = `${PROJECT_URL}/releases`;
const UPDATE_TOAST_STORAGE = "npk-update-toast";
const i18nState = { choice: "auto", detected: "zh_cn", code: "zh_cn", data: {}, catalog: {} };
let lastUpdateInfo = null;
let updateToastTimer = 0;

function detectLanguage() {
  const tag = String((navigator.languages && navigator.languages[0]) || navigator.language || "en-US").toLowerCase();
  if (tag.startsWith("zh")) return /(tw|hk|mo)/.test(tag) ? "zh_tw" : "zh_cn";
  if (tag.startsWith("en")) return "en_us";
  return "en_us";
}

function tr(key, vars = {}) {
  let text = i18nState.data[key];
  if (text === undefined) text = key;
  return String(text).replace(/\{(\w+)\}/g, (_, name) => (vars[name] !== undefined ? String(vars[name]) : `{${name}}`));
}

function backendText(key, detail) {
  if (!key) return "";
  return detail ? tr(key, { message: detail }) : tr(key);
}

async function loadI18nCatalog() {
  const entries = await Promise.all(I18N_CODES.map(async (code) => {
    try {
      const response = await fetch(`/language/${code}.json`, { cache: "no-store" });
      if (!response.ok) throw new Error(String(response.status));
      return [code, await response.json()];
    } catch (error) {
      console.warn(`language ${code} failed`, error);
      return [code, { intro: { display: code, region: "" }, i18n: {} }];
    }
  }));
  i18nState.catalog = Object.fromEntries(entries);
}

function applyStaticI18n() {
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    if (node.id === "preview-name" && state.previewPath) return;
    node.textContent = tr(node.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
    node.placeholder = tr(node.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-title]").forEach((node) => {
    node.title = tr(node.dataset.i18nTitle);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((node) => {
    node.setAttribute("aria-label", tr(node.dataset.i18nAria));
  });
}

function renderLanguageOptions() {
  const select = $("language");
  if (!select) return;
  const autoName = i18nState.catalog[i18nState.detected]?.intro?.display || i18nState.detected;
  select.innerHTML = "";
  const auto = document.createElement("option");
  auto.value = "auto";
  auto.textContent = tr("language.autoLabel", { name: autoName });
  select.appendChild(auto);
  for (const code of I18N_CODES) {
    const info = i18nState.catalog[code];
    if (!info) continue;
    const option = document.createElement("option");
    option.value = code;
    option.textContent = info.intro?.display || code;
    select.appendChild(option);
  }
  select.value = i18nState.choice;
}

function refreshLabelMaps() {
  for (const kind of ["image", "audio", "bank", "video", "text", "script", "model", "binary", "dir"]) {
    KIND_LABEL[kind] = tr(`kind.${kind}`);
  }
  ROOT_LABEL._script = tr("root.script");
  ROOT_LABEL._unresolved = tr("root.unresolved");
  MAP_EID_LABEL[100001] = tr("map.eid.trigger");
  MAP_EID_LABEL[100007] = tr("map.eid.spawn");
  MAP_EID_LABEL[100008] = tr("map.eid.mixer");
  MAP_EID_LABEL[100024] = tr("map.eid.corner");
  MAP_EID_LABEL[100029] = tr("map.eid.gear");
  MAP_EID_LABEL[100060] = tr("map.eid.box");
  MAP_EID_LABEL[900001] = tr("map.eid.floor");
}

function renderUpdate(info, toast = true) {
  lastUpdateInfo = info || null;
  const version = info?.current;
  const projectUrl = info?.project_url || PROJECT_URL;
  const releasesUrl = info?.releases_url || RELEASES_URL;
  $("project-version").textContent = version ? `v${version}` : "v—";
  const link = $("project-link");
  const badge = $("project-update");
  if (info?.available && info.latest) {
    link.href = info.url || releasesUrl;
    badge.textContent = tr("update.available", { version: info.latest });
    badge.classList.remove("hidden");
    const toastNode = $("update-toast");
    if (toastNode && !toastNode.classList.contains("hidden")) {
      $("update-toast-title").textContent = tr("update.toast", { version: info.latest });
      $("update-toast-text").textContent = tr("update.current", { version: info.current || "—" });
    }
    if (toast) showUpdateToast(info);
  } else {
    link.href = projectUrl;
    badge.classList.add("hidden");
    badge.textContent = "";
  }
}

function showUpdateToast(info) {
  const key = `${info.latest}|${info.url || ""}`;
  let seen = null;
  try { seen = JSON.parse(localStorage.getItem(UPDATE_TOAST_STORAGE) || "null"); } catch (_) {}
  if (seen && seen.key === key && Date.now() - Number(seen.at || 0) < 86400000) return;
  const toast = $("update-toast");
  $("update-toast-title").textContent = tr("update.toast", { version: info.latest });
  $("update-toast-text").textContent = tr("update.current", { version: info.current || "—" });
  $("update-toast-link").href = info.url || info.releases_url || RELEASES_URL;
  toast.classList.remove("hidden", "closing");
  void toast.offsetWidth;
  clearTimeout(updateToastTimer);
  try { localStorage.setItem(UPDATE_TOAST_STORAGE, JSON.stringify({ key, at: Date.now() })); } catch (_) {}
}

function hideUpdateToast() {
  clearTimeout(updateToastTimer);
  const toast = $("update-toast");
  if (!toast || toast.classList.contains("hidden")) return;
  toast.classList.add("closing");
  updateToastTimer = setTimeout(() => {
    toast.classList.add("hidden");
    toast.classList.remove("closing");
  }, 240);
}

async function setLanguage(choice, rerender = true) {
  const detected = i18nState.detected;
  const code = choice === "auto" ? detected : (i18nState.catalog[choice] ? choice : "zh_cn");
  i18nState.choice = choice;
  i18nState.code = code;
  i18nState.data = i18nState.catalog[code]?.i18n || {};
  localStorage.setItem(I18N_STORAGE, choice);
  document.documentElement.lang = code.replace("_", "-");
  refreshLabelMaps();
  applyStaticI18n();
  if (lastUpdateInfo) renderUpdate(lastUpdateInfo, false);
  renderLanguageOptions();
  if (!rerender) return;
  const preview = state.previewPath;
  const previewFile = preview ? state.files.find((item) => item.path === preview) : null;
  if (viewerIsOpen()) closeViewer();
  if (state.searchMode) await search(state.searchQuery);
  else await loadList(state.path, false, true);
  if (previewFile) showPreview(previewFile);
}

async function initI18n() {
  i18nState.detected = detectLanguage();
  await loadI18nCatalog();
  i18nState.choice = localStorage.getItem(I18N_STORAGE) || "auto";
  if (i18nState.choice !== "auto" && !i18nState.catalog[i18nState.choice]) i18nState.choice = "auto";
  await setLanguage(i18nState.choice, false);
  const select = $("language");
  if (select) select.addEventListener("change", () => setLanguage(select.value, true));
}

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

const KIND_LABEL = {};
const ROOT_LABEL = {};

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
  const ext = (file.ext || "").toLowerCase();
  if (ext === ".gmp") return { name: "view", label: tr("action.map"), icon: "map" };
  if (ext === ".xn") return { name: "script", label: tr("action.script"), icon: "code" };
  if (kind === "audio") return { name: "play", label: tr("action.play"), icon: "play" };
  if (kind === "image" || isTextView(file)) return { name: "view", label: tr("action.view"), icon: "zoom" };
  return { name: "preview", label: tr("action.view"), icon: "zoom" };
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
  const parts = [`<button data-dir="">${tr("index.all")}</button>`];
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
        <span class="folder-meta">${Number(dir.count).toLocaleString()} ${tr("stats.files")} · ${formatBytes(dir.bytes)}</span>
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
    const loadedText = loaded < total ? ` · ${tr("list.loaded", { loaded: loaded.toLocaleString(), total: total.toLocaleString() })}` : "";
    $("count").textContent = `${tr("list.results", { count: total.toLocaleString() })}${loadedText}`;
    $("index-summary").textContent = `${tr("list.results", { count: total.toLocaleString() })}${loadedText}`;
    return;
  }
  const total = state.fileTotal;
  const activeKind = state.kind ? ` · ${KIND_LABEL[state.kind] || state.kind}` : "";
  const loadedText = loaded < total ? ` · ${tr("list.loaded", { loaded: loaded.toLocaleString(), total: total.toLocaleString() })}` : "";
  $("count").textContent = `${tr("list.dirs", { count: Number(state.dirs.length).toLocaleString() })} · ${tr("list.files", { count: total.toLocaleString() })}${activeKind}${loadedText}`;
}

function setSentinelIdle() {
  const sentinel = $("list-sentinel");
  if (!sentinel) return;
  sentinel.className = "list-sentinel";
  sentinel.innerHTML = `<span class="load-hint">${tr("list.scrollMore")}</span>`;
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
  sentinel.innerHTML = `<span>${tr("error.loadMore", { message: esc(message) })}</span><button id="retry-more" class="mini-btn">${tr("list.retry")}</button>`;
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
    sentinel.innerHTML = `<span class="spinner"></span>${tr("status.loading")}`;
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
  const chips = [`${interactive ? `<button class="kind-chip ${state.kind === "" ? "active" : ""}" data-kind="">` : `<span class="kind-chip ${state.kind === "" ? "active" : ""}">`}${tr("index.all")} <b>${Number(total || 0).toLocaleString()}</b>${interactive ? "</button>" : "</span>"}`];
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
  $("index-path").textContent = search ? tr("index.search", { query }) : (path || tr("index.all"));
  const loaded = (data.files || []).length;
  const matchTotal = Number(data.file_total ?? loaded);
  const loadedText = loaded < matchTotal ? ` · ${tr("list.loaded", { loaded: loaded.toLocaleString(), total: matchTotal.toLocaleString() })}` : "";
  const summary = search
    ? `${tr("index.results", { count: matchTotal.toLocaleString() })}${loadedText}`
    : tr("index.filesWithDirs", { count: Number(data.dir_summary?.files || 0).toLocaleString(), size: formatBytes(data.dir_summary?.bytes || 0) });
  $("index-summary").textContent = summary;
  $("index-up").disabled = search || !path;
  $("index-up").dataset.parent = parentPath(path);
  $("copy-index").disabled = search && !query;
  $("copy-index").textContent = search ? tr("list.copyKeyword") : tr("index.copyPath");
  const kinds = data.kind_counts || {};
  const total = Object.values(kinds).reduce((sum, n) => sum + Number(n), 0);
  $("index-kinds").innerHTML = renderKindChips(kinds, total, !search);
  if (search) {
    $("index-dirs").innerHTML = `<div class="muted" style="font-size:11px">${tr("index.searchNoSubdirs")}</div>`;
  } else {
    const dirs = data.dirs || [];
    $("index-dirs").innerHTML = dirs.length
      ? dirs.map((dir) => `<button class="index-dir" data-dir="${esc(dir.path)}">
          <span>${esc(dir.name)}</span><span>${Number(dir.count).toLocaleString()}</span>
        </button>`).join("")
      : `<div class="muted" style="font-size:11px">${tr("index.noSubdirs")}</div>`;
  }
}

async function loadStats() {
  try {
    const stats = await getJson("/api/stats");
    state.platform = stats.platform || "win32";
    if (stats.version) $("project-version").textContent = `v${stats.version}`;
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

async function loadUpdate(retries = 2) {
  try {
    const info = await getJson("/api/update");
    renderUpdate(info);
    if (info.checking && retries > 0) {
      setTimeout(() => loadUpdate(retries - 1), 2500);
    }
  } catch (error) {
    console.warn("update check failed", error);
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
  $("content").innerHTML = skeletonScreen(tr("status.loadingDir"));
  $("where").textContent = state.path || tr("index.all");
  $("index-path").textContent = state.path || tr("index.all");
  $("count").textContent = "";
  $("index-summary").textContent = tr("status.loadingDir");
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
      : tr("index.all");
    updateCounts();
    $("content").innerHTML = renderFolders(state.dirs) + renderFiles(state.files) || `<div class="empty">${tr("list.empty")}</div>`;
    hydrateThumbs($("content"));
    renderIndex(data);
    ensureSentinel();
    document.querySelectorAll(".root").forEach((node) => {
      const rootPath = node.dataset.dir;
      node.classList.toggle("active", state.path === rootPath || state.path.startsWith(rootPath + "/"));
    });
  } catch (error) {
    if (token !== listToken) return;
    $("content").innerHTML = `<div class="errorbox">${tr("error.read", { message: esc(error.message) })}</div>`;
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
  $("where").innerHTML = `${tr("list.searchResults")}<span class="sub">${esc(query)}</span>`;
  $("index-path").textContent = tr("index.search", { query });
  $("count").textContent = "";
  $("content").innerHTML = skeletonScreen(tr("index.searching"));
  $("index-summary").textContent = tr("index.searching");
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
    $("content").innerHTML = renderFiles(state.files) || `<div class="empty">${tr("list.noMatches")}</div>`;
    hydrateThumbs($("content"));
    renderIndex({ path: "", dirs: [], files: state.files, file_total: state.searchTotal, kind_counts: state.kindCounts }, true, query);
    ensureSentinel();
  } catch (error) {
    if (token !== listToken) return;
    $("content").innerHTML = `<div class="errorbox">${tr("error.search", { message: esc(error.message) })}</div>`;
  }
}

function bankMediaDetail(media) {
  const parts = [];
  if (media.codec) parts.push(media.codec);
  if (media.channels) parts.push(`${media.channels} ${tr("bank.channels")}`);
  if (media.sample_rate) parts.push(`${Number(media.sample_rate).toLocaleString()} Hz`);
  return parts.join(" · ") || tr("bank.embedded");
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
    : `<div class="bank-empty">${tr("bank.noWem")}</div>`;
  const hircTypes = (hirc.types || []).map((type) =>
    `<span class="bank-chip">${esc(type.name)} <b>${Number(type.count).toLocaleString()}</b></span>`).join("");
  container.innerHTML = `<div class="bank-summary">
    <div class="bank-head">
      <div><b>Wwise Bank</b><span class="bank-sub">${tr("bank.version")} ${esc(header.version ?? "—")} · ${tr("bank.id")} ${esc(header.bank_id_hex || "—")}</span></div>
      <div class="bank-stats">${tr("bank.embeddedAudio", { count: entries.length.toLocaleString() })} · ${tr("bank.hircObjects", { count: Number(hirc.count || 0).toLocaleString() })}</div>
    </div>
    ${audio}
    ${rows}
    <div class="section-title">${tr("bank.structure")}</div>
    <div class="bank-chips">${(bank.chunks || []).map((chunk) =>
      `<span class="bank-chip">${esc(chunk.id)} <b>${formatBytes(chunk.size)}</b></span>`).join("")}</div>
    ${hircTypes ? `<div class="section-title">${tr("bank.hircTypes")}</div><div class="bank-chips">${hircTypes}</div>` : ""}
    ${(bank.warnings || []).length ? `<div class="note">${esc(bank.warnings.join("；"))}</div>` : ""}
  </div>`;
  swapPreview(container);
  $("preview-note").textContent = entries.length
    ? tr("bank.note")
    : tr("bank.noWem");
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
    $("preview-note").textContent = tr("bank.playEmbedded", { name: row.dataset.media });
  });
}

function swapPreview(container) {
  container.classList.remove("swap-in");
  void container.offsetWidth;
  container.classList.add("swap-in");
}

function renderInspect(info, container) {
  const formatText = tr(info.format);
  const summaryText = tr(info.summary);
  if (info.text !== null && info.text !== undefined) {
    container.innerHTML = `<pre>${esc(info.text)}${info.text_truncated ? NL + "…" : ""}</pre>`;
    swapPreview(container);
    const refs = info.references || [];
    $("preview-note").textContent = `${summaryText}${refs.length ? ` ${tr("inspect.refsFound", { count: refs.length })}` : ""}${info.text_truncated ? ` ${tr("text.tooLarge")}` : ""}`;
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
      <div class="inspect-format">${esc(formatText)}</div>
      <div class="inspect-size">${formatBytes(info.size)}</div>
    </div>
    <div class="inspect-summary">${esc(summaryText)}</div>
    ${headerChips ? `<div class="bank-chips">${headerChips}</div>` : ""}
    ${references.length ? `<div class="section-title">${tr("inspect.references")}</div><div class="inspect-refs">${references.map((ref) => `<span class="inspect-ref" title="${esc(ref)}">${esc(ref)}</span>`).join("")}</div>` : ""}
    ${strings.length ? `<div class="section-title">${tr("inspect.fields")}</div><pre class="inspect-strings">${esc(strings.join(NL))}${info.strings_truncated ? NL + "…" : ""}</pre>` : ""}
    <details class="inspect-hex">
      <summary>${tr("inspect.hex")}</summary>
      <pre>${esc((info.hex_lines || []).join(NL))}${info.hex_truncated ? NL + "…" : ""}</pre>
    </details>
    ${info.renderable === "none" ? `<div class="note">${tr("inspect.renderNone")}</div>` : ""}
  </div>`;
  swapPreview(container);
  $("preview-note").textContent = strings.length || references.length
    ? tr("text.fields")
    : tr("text.hexNote");
}

function renderGmpPreview(data, container) {
  container.classList.add("gmp-mode");
  const units = data.units || [];
  const refs = data.references || [];
  const chips = [
    `<span class="bank-chip">${tr("gmp.units")} <b>${Number(data.unit_count || 0).toLocaleString()}</b></span>`,
    `<span class="bank-chip">${tr("gmp.chunks")} <b>${Number((data.chunks || []).length).toLocaleString()}</b></span>`,
    `<span class="bank-chip">MessagePack <b>${Number(data.parsed_chunks || 0).toLocaleString()}</b></span>`,
    `<span class="bank-chip">${tr("gmp.binary")} <b>${Number(data.binary_chunks || 0).toLocaleString()}</b></span>`,
  ].join("");
  const unitRows = units.slice(0, 200).map((unit) => {
    const position = (unit.position || []).map((value) => Number(value).toFixed(1)).join(", ");
    const label = unit.name || `${tr("common.unit")} ${unit.id}`;
    return `<div class="gmp-unit" title="${esc(label)}">
      <span class="gmp-unit-name">${esc(label)}</span>
      <span class="gmp-unit-meta">${esc(String(unit.eid ?? unit.type ?? "—"))} · ${esc(position)}</span>
    </div>`;
  }).join("");
  container.innerHTML = `<div class="gmp-preview">
    <div class="gmp-head">
      <div class="gmp-title">
        <b>${esc(data.map_name || tr("gmp.mapName"))}</b>
        <span>${tr("gmp.unitSummary", { units: Number(data.unit_count || 0).toLocaleString(), chunks: Number((data.chunks || []).length).toLocaleString() })}</span>
      </div>
      <button class="btn primary" id="gmp-open-map" type="button">${icon("map")}${tr("gmp.openCanvas")}</button>
    </div>
    <div class="bank-chips">${chips}</div>
    ${units.length
      ? `<div class="section-title">${tr("gmp.unitList", { count: `${Math.min(units.length, 200)}${units.length > 200 ? " / " + units.length : ""}` })}</div><div class="gmp-units">${unitRows}</div>`
      : `<div class="note">${tr("gmp.noUnits")}</div>`}
    ${refs.length ? `<div class="section-title">${tr("gmp.refs")}</div><div class="inspect-refs">${refs.slice(0, 80).map((ref) => `<span class="inspect-ref" title="${esc(ref)}">${esc(ref)}</span>`).join("")}</div>` : ""}
  </div>`;
  const open = container.querySelector("#gmp-open-map");
  if (open) {
    open.addEventListener("click", () => {
      const file = state.files.find((item) => item.path === data.path);
      if (file) openViewer(file);
    });
  }
  $("preview-note").textContent = units.length
    ? tr("gmp.note", { count: Number(data.unit_count || 0).toLocaleString() })
    : tr("gmp.noteNoUnits");
}

function xnPreviewText(data) {
  const p = data.preview || {};
  const lines = [
    `# module: ${data.module || ""}`,
    `# signature: ${data.signature}`,
    `# container: XN v2 (HMAC-SHA256 + NeoX AES-256 + zlib)`,
    `# compressed: ${Number(data.compressed || 0).toLocaleString()} bytes`,
    `# original: ${Number(data.original || 0).toLocaleString()} bytes`,
    `# code_name: ${p.code_name || "<module>"}`,
    `# filename: ${p.filename || (data.module ? data.module.replace(/\./g, "/") + ".nxs" : "")}`,
    `# code_objects: ${Number(p.code_objects || 0).toLocaleString()}`,
    "",
    "## functions",
    (p.functions || []).slice(0, 300).map((fn) => `${fn.name}(${(fn.args || []).join(", ")})  # line ${fn.line}`).join("\n"),
    "",
    "## names",
    (p.names || []).slice(0, 500).join("\n"),
    "",
    "## strings",
    (p.strings || []).slice(0, 300).join("\n"),
  ];
  return lines.join("\n");
}

function renderXnPreview(data, container, file) {
  const p = data.preview || {};
  if (!data.decrypted) {
    container.innerHTML = `<div class="xn-fail">
      <div class="xn-fail-title">${icon("code")}${tr("xn.title")}</div>
      <p>${esc(backendText(data.reason, data.detail) || tr("xn.cannotDecrypt"))}</p>
      <p class="note">${tr("xn.needModule")}</p>
    </div>`;
    $("preview-note").textContent = tr("xn.notIndexed");
    return;
  }
  const names = (p.names || []).slice(0, 80);
  const strings = (p.strings || []).slice(0, 60);
  const functions = (p.functions || []).slice(0, 60);
  container.innerHTML = `<div class="xn-panel">
    <div class="xn-head">
      <div>
        <b>${esc(data.module || tr("xn.unknownModule"))}</b>
        <span>${esc(p.filename || (data.module ? data.module.replace(/\./g, "/") + ".nxs" : ""))}</span>
      </div>
      <button class="btn primary" id="xn-open-viewer" type="button">${icon("code")}${tr("xn.openPreview")}</button>
    </div>
    <div class="bank-chips">
      <span class="bank-chip">${tr("xn.codeObjects")} <b>${Number(p.code_objects || 0).toLocaleString()}</b></span>
      <span class="bank-chip">${tr("xn.functions")} <b>${Number(p.function_count || 0).toLocaleString()}</b></span>
      <span class="bank-chip">${tr("xn.names")} <b>${Number(p.name_count || 0).toLocaleString()}</b></span>
      <span class="bank-chip">${tr("xn.strings")} <b>${Number(p.string_count || 0).toLocaleString()}</b></span>
      <span class="bank-chip">${tr("xn.unpacked")} <b>${formatBytes(data.original || 0)}</b></span>
    </div>
    <div class="section-title">${tr("xn.functionsClasses")}</div>
    <div class="xn-list">${functions.map((fn) => `<span class="xn-item" title="${esc(fn.name)}">${esc(fn.name)}(${esc((fn.args || []).join(", "))})</span>`).join("")}</div>
    <div class="section-title">${tr("xn.namesTable")}</div>
    <div class="xn-list">${names.map((name) => `<span class="xn-item" title="${esc(name)}">${esc(name)}</span>`).join("")}</div>
    <div class="section-title">${tr("xn.stringsTable")}</div>
    <div class="xn-list">${strings.map((text) => `<span class="xn-item" title="${esc(text)}">${esc(text)}</span>`).join("")}</div>
  </div>`;
  const open = container.querySelector("#xn-open-viewer");
  if (open) open.addEventListener("click", () => openScriptViewer(file));
  $("preview-note").textContent = tr("xn.structure");
}

function lockViewerScroll() {
  document.documentElement.classList.add("viewer-open");
  document.body.classList.add("viewer-open");
}

function unlockViewerScroll() {
  document.documentElement.classList.remove("viewer-open");
  document.body.classList.remove("viewer-open");
}

async function openScriptViewer(file) {
  closeDrawer();
  if (viewerCloseTimer) {
    clearTimeout(viewerCloseTimer);
    viewerCloseTimer = 0;
  }
  const node = $("viewer");
  node.classList.remove("hidden", "closing", "open");
  node.setAttribute("aria-hidden", "false");
  viewer.token += 1;
  const token = viewer.token;
  viewer.file = file;
  viewer.path = file.path;
  viewer.mode = "script";
  $("viewer-name").textContent = file.name;
  $("viewer-stage").innerHTML = `<div class="viewer-loading"><span class="spinner"></span>${tr("drawer.note.script")}</div>`;
  $("viewer-foot").innerHTML = "";
  void node.offsetWidth;
  node.classList.add("open");
  lockViewerScroll();
  try {
    const data = await getJson(apiUrl("/api/xn", { path: file.path }));
    if (token !== viewer.token) return;
    if (!data.decrypted) {
      $("viewer-stage").innerHTML = `<div class="errorbox">${esc(backendText(data.reason, data.detail) || tr("xn.cannotDecrypt"))}</div>`;
      return;
    }
    const text = xnPreviewText(data);
    $("viewer-stage").innerHTML = `<div class="script-viewer"><pre class="script-preview">${esc(text)}</pre></div>`;
    $("viewer-foot").innerHTML = `<button class="mini-btn" id="xn-decompile" type="button">${tr("xn.decompile")}</button><button class="mini-btn" id="xn-disasm" type="button">${tr("xn.disasm")}</button><button class="mini-btn" id="xn-copy" type="button">${tr("xn.copyPreview")}</button>`;
    const decompile = $("xn-decompile");
    if (decompile) {
      decompile.addEventListener("click", async () => {
        if (decompile.dataset.loaded === "1") return;
        decompile.disabled = true;
        decompile.textContent = tr("xn.decompiling");
        try {
          const result = await getJson(apiUrl("/api/xn", { path: file.path, decompile: 1 }));
          if (token !== viewer.token) return;
          if (result.error) throw new Error(backendText(result.error, result.detail));
          const block = document.createElement("pre");
          block.className = "script-preview";
          block.textContent = `\n\n## decompiled source (${result.module})\n${result.source}`;
          $("viewer-stage").querySelector(".script-viewer")?.appendChild(block);
          decompile.dataset.loaded = "1";
          decompile.textContent = tr("xn.sourceLoaded");
          block.scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (error) {
          decompile.textContent = tr("xn.decompileFailed");
        } finally {
          decompile.disabled = false;
        }
      });
    }
    const disasm = $("xn-disasm");
    if (disasm) {
      disasm.addEventListener("click", async () => {
        if (disasm.dataset.loaded === "1") return;
        disasm.disabled = true;
        disasm.textContent = tr("xn.disassembling");
        try {
          const result = await getJson(apiUrl("/api/xn", { path: file.path, disasm: 1 }));
          if (token !== viewer.token) return;
          if (result.error) throw new Error(backendText(result.error, result.detail));
          const block = document.createElement("pre");
          block.className = "script-preview";
          block.textContent = `\n\n## disassembly (${result.function})\n${result.disassembly}`;
          $("viewer-stage").querySelector(".script-viewer")?.appendChild(block);
          disasm.dataset.loaded = "1";
          disasm.textContent = tr("xn.disasmLoaded");
        } catch (error) {
          disasm.textContent = tr("xn.disasmFailed");
        } finally {
          disasm.disabled = false;
        }
      });
    }
    const copy = $("xn-copy");
    if (copy) {
      copy.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(text); copy.textContent = tr("xn.copied"); }
        catch (_) { copy.textContent = tr("xn.copyFailed"); }
        setTimeout(() => { copy.textContent = tr("xn.copyPreview"); }, 1200);
      });
    }
  } catch (error) {
    if (token !== viewer.token) return;
    $("viewer-stage").innerHTML = `<div class="errorbox">${esc(tr("error.script", { message: error.message }))}</div>`;
  }
}

function metadataRows(info) {
  const rows = [
    [tr("drawer.meta.path"), info.path],
    [tr("drawer.meta.package"), info.package],
    [tr("drawer.meta.type"), `${KIND_LABEL[displayKind(info.kind, info.ext)] || info.kind} ${info.ext || ""}`],
    [tr("drawer.meta.size"), formatBytes(info.unpacked)],
    [tr("drawer.meta.storedSize"), formatBytes(info.packed)],
    [tr("drawer.meta.storage"), info.compression === 3 ? tr("drawer.storage.compressed") : tr("drawer.storage.raw")],
    [tr("drawer.meta.flags"), info.flags],
    [tr("drawer.meta.signature"), info.signature],
    [tr("drawer.meta.offset"), info.offset],
    [tr("drawer.meta.compressedHash"), info.packed_hash],
    [tr("drawer.meta.originalHash"), info.unpacked_hash],
  ];
  return rows.map(([key, value]) => `<div class="k">${esc(key)}</div><div class="v">${esc(value)}</div>`).join("");
}

function quickOpenActions() {
  const actions = [];
  if (state.platform === "win32") actions.push(["explorer", tr("drawer.open.explorer")]);
  if (state.platform === "darwin") actions.push(["finder", tr("drawer.open.finder")]);
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
  if (note) note.textContent = tr("common.opening");
  try {
    const response = await fetch("/api/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, app }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(backendText(result.error, result.detail) || tr("error.open"));
    if (note) note.textContent = result.converted
      ? tr("common.converted", { message: backendText(result.message) })
      : backendText(result.message);
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
let viewerCloseTimer = 0;

const mapView = {
  data: null, file: null, canvas: null, ctx: null, stage: null,
  width: 1, height: 1, dpr: 1,
  centerX: 0, centerZ: 0, scale: 1, fitScale: 1,
  dotSize: Math.min(16, Math.max(3, Number(localStorage.getItem("npk-map-dot-size")) || 6)),
  dragging: false, dragX: 0, dragY: 0,
  minimap: null, minimapCtx: null, minimapBox: null,
  minimapBase: null,
  icons: {},
  frame: 0,
};

const MAP_EID_LABEL = {};
const MAP_EID_COLOR = {
  100001: "#ff8d8d", 100007: "#6ee7d8", 100008: "#ffd166",
  100024: "#8fc7ff", 100029: "#ffa0d2", 100060: "#ff9f43", 900001: "#5f7186",
};
const MAP_FALLBACK_COLORS = ["#8fc7ff", "#6ee7d8", "#ffd166", "#ff9f43", "#b7a8ff", "#ff8d8d"];
const MAP_EID_ICON = {
  100007: "gui2/main/img_spawn_qipao.kta",
  100029: "gui2/editor_egg/editor_right/img_gear.kta",
  100060: "gui2/editor_egg/editor_right/item_box_ban.kta",
  900001: "gui2/editor_egg/editor_right/img_diban_007.kta",
};

function mapIconPath(eid) {
  return MAP_EID_ICON[Number(eid)] || "";
}

function mapUnitColor(unit) {
  const color = unit.color;
  if (Array.isArray(color) && color.length >= 3) {
    return `rgb(${color[0]}, ${color[1]}, ${color[2]})`;
  }
  return mapColor(unit.eid ?? unit.type ?? 0);
}

function mapIconUrl(eid) {
  const path = mapIconPath(eid);
  return path ? apiUrl("/api/thumb", { path, w: 64, v: 3 }) : "";
}

function ensureMapIcons() {
  const eids = new Set((mapView.data?.units || []).map((unit) => unit.eid).filter((eid) => eid != null));
  for (const eid of eids) {
    const url = mapIconUrl(eid);
    if (!url || mapView.icons[eid]) continue;
    const img = new Image();
    img.onload = () => {
      mapView.icons[eid] = img;
      requestMapDraw();
    };
    img.src = url;
  }
}

function mapColor(eid) {
  const key = Number(eid);
  if (MAP_EID_COLOR[key]) return MAP_EID_COLOR[key];
  const index = Math.abs(Number.isFinite(key) ? key : 0) % MAP_FALLBACK_COLORS.length;
  return MAP_FALLBACK_COLORS[index];
}

function viewerIsOpen() {
  return !$("viewer").classList.contains("hidden");
}

function openViewer(file) {
  closeDrawer();
  if (viewerCloseTimer) {
    clearTimeout(viewerCloseTimer);
    viewerCloseTimer = 0;
  }
  const node = $("viewer");
  node.classList.remove("hidden", "closing", "open");
  node.setAttribute("aria-hidden", "false");
  viewer.token += 1;
  const token = viewer.token;
  viewer.file = file;
  viewer.path = file.path;
  const ext = (file.ext || "").toLowerCase();
  viewer.mode = ext === ".gmp" ? "map" : isTextView(file) ? "text" : "image";
  $("viewer-name").textContent = file.name;
  $("viewer-stage").innerHTML = "";
  $("viewer-foot").innerHTML = "";
  void node.offsetWidth;
  node.classList.add("open");
  lockViewerScroll();
  if (viewer.mode === "map") {
    openMapViewer(file, token);
  } else if (viewer.mode === "image") {
    openImageViewer(
      file, token,
      apiUrl("/api/image", { path: file.path }),
      apiUrl("/api/thumb", { path: file.path, w: 360, v: 3 })
    );
  } else {
    openTextViewer(file, token);
  }
}

function clearViewerContent() {
  if (mapView.frame) cancelAnimationFrame(mapView.frame);
  mapView.frame = 0;
  mapView.data = null;
  mapView.file = null;
  mapView.canvas = null;
  mapView.ctx = null;
  mapView.minimap = null;
  mapView.minimapCtx = null;
  mapView.minimapBox = null;
  mapView.minimapBase = null;
  mapView.icons = {};
  mapView.dragging = false;
  $("viewer-name").textContent = "";
  $("viewer-stage").innerHTML = "";
  $("viewer-foot").innerHTML = "";
}

function closeViewer() {
  viewer.token += 1;
  viewer.path = null;
  viewer.file = null;
  viewer.mode = "";
  viewer.dragging = false;
  viewer.mapDragging = false;
  const node = $("viewer");
  node.setAttribute("aria-hidden", "true");
  if (node.classList.contains("hidden")) {
    clearViewerContent();
    unlockViewerScroll();
    return;
  }
  node.classList.remove("open");
  node.classList.add("closing");
  if (viewerCloseTimer) clearTimeout(viewerCloseTimer);
  viewerCloseTimer = setTimeout(() => {
    viewerCloseTimer = 0;
    if (node.classList.contains("open")) return;
    node.classList.add("hidden");
    node.classList.remove("closing");
    clearViewerContent();
    unlockViewerScroll();
  }, 340);
}

function imageViewerFoot(file, mapUrl) {
  const mapSource = mapUrl || apiUrl("/api/thumb", { path: file.path, w: 360, v: 3 });
  return `<div class="viewer-zoom">
    <button class="icon-btn" id="viewer-zoom-out" title="${tr("viewer.zoomOut")}" aria-label="${tr("viewer.zoomOut")}">${icon("minus")}</button>
    <label class="viewer-zoom-value"><input id="viewer-zoom-input" type="number" min="5" max="800" step="1" value="100"><span>%</span></label>
    <button class="icon-btn" id="viewer-zoom-in" title="${tr("viewer.zoomIn")}" aria-label="${tr("viewer.zoomIn")}">${icon("plus")}</button>
    <button class="mini-btn" id="viewer-zoom-fit" title="${tr("viewer.fit")}">${icon("fit")}${tr("viewer.fitShort")}</button>
  </div>
  <div class="viewer-map" id="viewer-map">
    <img id="viewer-map-img" alt="" src="${esc(mapSource)}">
    <div class="viewer-map-view" id="viewer-map-view"></div>
  </div>`;
}

function openImageViewer(file, token, sourceUrl, mapUrl) {
  const stage = $("viewer-stage");
  const isMap = (file.ext || "").toLowerCase() === ".gmp";
  $("viewer-foot").innerHTML = imageViewerFoot(file, mapUrl);
  stage.innerHTML = `<div class="viewer-loading"><span class="spinner"></span>${isMap ? tr("viewer.loadingMap") : tr("viewer.loadingImage")}</div>`;
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
    stage.innerHTML = `<div class="errorbox">${isMap ? tr("error.map") : tr("error.image")}</div>`;
  }, { once: true });
  img.src = sourceUrl;
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
    $("viewer").classList.add("map-dragging");
    map.setPointerCapture(event.pointerId);
    moveTo(event);
    event.preventDefault();
  });
  map.addEventListener("pointermove", (event) => {
    if (viewer.mapDragging) moveTo(event);
  });
  const end = (event) => {
    viewer.mapDragging = false;
    $("viewer").classList.remove("map-dragging");
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
  stage.innerHTML = `<div class="viewer-loading"><span class="spinner"></span>${tr("text.loading")}</div>`;
  try {
    const response = await fetch(apiUrl("/api/text", { path: file.path }));
    if (token !== viewer.token) return;
    if (!response.ok) {
      stage.innerHTML = `<div class="errorbox">${tr("text.cannotPreview")}</div>`;
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
    stage.innerHTML = `<div class="errorbox">${esc(tr("error.text", { message: error.message }))}</div>`;
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

function mapAxis() {
  return mapView.data?.bounds?.axis === "xy" ? "xy" : "xz";
}

function mapPoint(unit) {
  const pos = unit.position || [];
  return mapAxis() === "xy" ? [pos[0], pos[1]] : [pos[0], pos[2]];
}

function mapUnits() {
  const data = mapView.data;
  if (!data) return [];
  const axis = mapAxis();
  return (data.units || []).filter((unit) => {
    const pos = unit.position;
    if (!Array.isArray(pos) || pos.length < 3) return false;
    const x = pos[0];
    const z = axis === "xy" ? pos[1] : pos[2];
    return Number.isFinite(x) && Number.isFinite(z) && Math.abs(x) < 50000 && Math.abs(z) < 50000;
  });
}

function mapNiceStep(raw) {
  if (!Number.isFinite(raw) || raw <= 0) return 1;
  const power = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / power;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * power;
}

function mapFormat(value, step) {
  const rounded = Math.abs(value) < 1e-9 ? 0 : value;
  if (step >= 1) return String(Math.round(rounded));
  if (step >= 0.1) return rounded.toFixed(1);
  return rounded.toFixed(2);
}

async function openMapViewer(file, token) {
  mapView.file = file;
  mapView.data = null;
  const stage = $("viewer-stage");
  $("viewer-foot").innerHTML = "";
  stage.innerHTML = `<div class="viewer-loading"><span class="spinner"></span>${tr("gmp.loading")}</div>`;
  try {
    const data = await getJson(apiUrl("/api/gmp", { path: file.path }));
    if (token !== viewer.token) return;
    mapView.data = data;
    renderMapStage(file, data);
  } catch (error) {
    if (token !== viewer.token) return;
    stage.innerHTML = `<div class="errorbox">${esc(tr("error.mapParse", { message: error.message }))}</div>`;
  }
}

function renderMapStage(file, data) {
  const stage = $("viewer-stage");
  stage.innerHTML = `<canvas id="map-canvas" class="map-canvas" aria-label="${tr("map.canvas")}"></canvas>
    <div class="map-hud" id="map-hud"></div>
    <div class="map-legend" id="map-legend"></div>
    ${!data.unit_count || !data.bounds ? `<div class="map-empty">${tr("gmp.empty")}</div>` : ""}`;
  $("viewer-foot").innerHTML = mapFoot();
  mapView.stage = stage;
  mapView.canvas = $("map-canvas");
  mapView.ctx = mapView.canvas.getContext("2d");
  mapView.minimap = $("map-minimap-canvas");
  mapView.minimapCtx = mapView.minimap ? mapView.minimap.getContext("2d") : null;
  mapView.dragging = false;
  fitMapView();
  resizeMapCanvas();
  bindMapControls(file);
  buildMapLegend();
  ensureMapIcons();
  drawMap();
}

function mapFoot() {
  return `<div class="viewer-zoom">
    <button class="icon-btn" id="map-zoom-out" title="${tr("map.zoomOut")}" aria-label="${tr("map.zoomOut")}">${icon("minus")}</button>
    <label class="viewer-zoom-value"><input id="map-zoom-input" type="number" min="5" max="2000" step="1" value="100"><span>%</span></label>
    <button class="icon-btn" id="map-zoom-in" title="${tr("map.zoomIn")}" aria-label="${tr("map.zoomIn")}">${icon("plus")}</button>
    <button class="mini-btn" id="map-zoom-fit" title="${tr("viewer.fit")}">${icon("fit")}${tr("map.fit")}</button>
    <label class="map-dot-control">${tr("map.dot")} <input id="map-dot-size" type="range" min="3" max="16" step="1" value="${mapView.dotSize}"></label>
  </div>
  <div class="viewer-map">
    <canvas id="map-minimap-canvas" class="viewer-map-canvas"></canvas>
  </div>`;
}

function fitMapView() {
  if (!mapView.stage) return;
  const w = Math.max(1, mapView.stage.clientWidth);
  const h = Math.max(1, mapView.stage.clientHeight);
  mapView.width = w;
  mapView.height = h;
  const bounds = mapView.data?.bounds;
  if (!bounds) {
    mapView.centerX = 0;
    mapView.centerZ = 0;
    mapView.fitScale = 24;
    mapView.scale = 24;
  } else {
    const spanX = Math.max(1, bounds.max_x - bounds.min_x);
    const spanZ = Math.max(1, bounds.max_y - bounds.min_y);
    mapView.centerX = (bounds.min_x + bounds.max_x) / 2;
    mapView.centerZ = (bounds.min_y + bounds.max_y) / 2;
    mapView.fitScale = Math.max(0.02, Math.min(80, Math.min(Math.max(80, w - 120) / spanX, Math.max(80, h - 120) / spanZ)));
    mapView.scale = mapView.fitScale;
  }
  updateMapZoomInput();
  updateMapHud();
}

function resizeMapCanvas() {
  const canvas = mapView.canvas;
  const stage = mapView.stage;
  if (!canvas || !stage) return;
  const w = Math.max(1, stage.clientWidth);
  const h = Math.max(1, stage.clientHeight);
  const dpr = window.devicePixelRatio || 1;
  mapView.width = w;
  mapView.height = h;
  mapView.dpr = dpr;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  canvas.style.width = "100%";
  canvas.style.height = "100%";
  mapView.minimapBase = null;
  if (mapView.ctx) mapView.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  drawMap();
}

function updateMapHud() {
  const hud = $("map-hud");
  if (!hud) return;
  const second = mapAxis() === "xz" ? "Z" : "Y";
  hud.textContent = `X ${mapView.centerX.toFixed(1)} · ${second} ${mapView.centerZ.toFixed(1)}`;
}

function updateMapZoomInput() {
  const input = $("map-zoom-input");
  if (!input || document.activeElement === input) return;
  input.value = Math.round((mapView.scale / (mapView.fitScale || 1)) * 100);
}

function setMapScale(next, ax, ay) {
  if (!mapView.canvas) return;
  const fit = mapView.fitScale || 1;
  const scale = Math.max(fit * 0.05, Math.min(fit * 20, next));
  const w = mapView.width;
  const h = mapView.height;
  const anchorX = ax === undefined ? w / 2 : ax;
  const anchorY = ay === undefined ? h / 2 : ay;
  const old = mapView.scale || 1;
  const worldX = mapView.centerX + (anchorX - w / 2) / old;
  const worldZ = mapView.centerZ - (anchorY - h / 2) / old;
  mapView.scale = scale;
  mapView.centerX = worldX - (anchorX - w / 2) / scale;
  mapView.centerZ = worldZ + (anchorY - h / 2) / scale;
  updateMapZoomInput();
  updateMapHud();
  requestMapDraw();
}

function requestMapDraw() {
  if (mapView.frame) return;
  mapView.frame = requestAnimationFrame(() => {
    mapView.frame = 0;
    drawMap();
  });
}

function drawMap() {
  const ctx = mapView.ctx;
  if (!ctx || !mapView.canvas) return;
  const w = mapView.width;
  const h = mapView.height;
  const scale = mapView.scale || 1;
  ctx.setTransform(mapView.dpr, 0, 0, mapView.dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#080e15";
  ctx.fillRect(0, 0, w, h);

  const step = mapNiceStep(80 / scale);
  const halfW = w / (2 * scale);
  const halfH = h / (2 * scale);
  const minX = mapView.centerX - halfW;
  const maxX = mapView.centerX + halfW;
  const minZ = mapView.centerZ - halfH;
  const maxZ = mapView.centerZ + halfH;

  ctx.strokeStyle = "#111c27";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = Math.ceil(minX / step) * step; x <= maxX; x += step) {
    const sx = w / 2 + (x - mapView.centerX) * scale;
    ctx.moveTo(sx, 0);
    ctx.lineTo(sx, h);
  }
  for (let z = Math.ceil(minZ / step) * step; z <= maxZ; z += step) {
    const sy = h / 2 - (z - mapView.centerZ) * scale;
    ctx.moveTo(0, sy);
    ctx.lineTo(w, sy);
  }
  ctx.stroke();

  drawMapUnits(ctx, w, h, scale);

  ctx.strokeStyle = "rgba(232, 238, 247, .22)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(w / 2 - 8, h / 2);
  ctx.lineTo(w / 2 + 8, h / 2);
  ctx.moveTo(w / 2, h / 2 - 8);
  ctx.lineTo(w / 2, h / 2 + 8);
  ctx.stroke();

  drawMapRulers(ctx, w, h, scale, step, minX, maxX, minZ, maxZ);
  drawMapMinimap();
}

function drawMapUnits(ctx, w, h, scale) {
  const units = mapUnits();
  if (!units.length) return;
  const radius = Math.max(3, Math.min(16, mapView.dotSize));
  const boxes = [];
  let labels = 0;
  ctx.font = '11px "Segoe UI", "Microsoft YaHei", sans-serif';
  ctx.textBaseline = "middle";
  for (const unit of units) {
    const [x, z] = mapPoint(unit);
    const sx = w / 2 + (x - mapView.centerX) * scale;
    const sy = h / 2 - (z - mapView.centerZ) * scale;
    if (sx < -40 || sx > w + 40 || sy < -40 || sy > h + 40) continue;
    const icon = mapView.icons[unit.eid];
    const iconSize = Math.max(18, radius * 2.6);
    if (icon) {
      ctx.beginPath();
      ctx.arc(sx, sy, iconSize / 2 + 1.5, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(8, 14, 21, .88)";
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = mapColor(unit.eid ?? unit.type ?? 0);
      ctx.stroke();
      ctx.drawImage(icon, sx - iconSize / 2, sy - iconSize / 2, iconSize, iconSize);
    } else {
      ctx.beginPath();
      ctx.arc(sx, sy, radius, 0, Math.PI * 2);
      ctx.fillStyle = mapUnitColor(unit);
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = mapColor(unit.eid ?? unit.type ?? 0);
      ctx.stroke();
    }
    const marker = icon ? iconSize / 2 : radius;
    const name = unit.name || "";
    if (!name || labels >= 160) continue;
    const width = ctx.measureText(name).width;
    const lx = sx + marker + 3;
    const ly = sy;
    if (lx + width > w - 4 || ly < 28 || ly > h - 6) continue;
    const box = [lx, ly - 7, lx + width, ly + 7];
    if (boxes.some((other) => box[0] < other[2] && box[2] > other[0] && box[1] < other[3] && box[3] > other[1])) continue;
    ctx.strokeStyle = "#0b1219";
    ctx.lineWidth = 3;
    ctx.strokeText(name, lx, ly);
    ctx.fillStyle = "#dae5f0";
    ctx.fillText(name, lx, ly);
    boxes.push(box);
    labels += 1;
  }
}

function drawMapRulers(ctx, w, h, scale, step, minX, maxX, minZ, maxZ) {
  const top = 24;
  const left = 44;
  ctx.fillStyle = "rgba(10, 17, 25, .94)";
  ctx.fillRect(0, 0, w, top);
  ctx.fillRect(0, 0, left, h);
  ctx.strokeStyle = "#223043";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, top + 0.5);
  ctx.lineTo(w, top + 0.5);
  ctx.moveTo(left + 0.5, 0);
  ctx.lineTo(left + 0.5, h);
  ctx.stroke();
  ctx.font = '10px "Segoe UI", "Microsoft YaHei", sans-serif';
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.fillStyle = "#c7d4e4";
  ctx.fillText(`X / ${mapAxis() === "xz" ? "Z" : "Y"}`, 6, top / 2);
  ctx.fillStyle = "#8495ab";
  for (let x = Math.ceil(minX / step) * step; x <= maxX; x += step) {
    const sx = w / 2 + (x - mapView.centerX) * scale;
    if (sx < left) continue;
    ctx.strokeStyle = "#3a4e66";
    ctx.beginPath();
    ctx.moveTo(sx, 0);
    ctx.lineTo(sx, 6);
    ctx.stroke();
    ctx.fillText(mapFormat(x, step), sx + 3, top / 2 + 2);
  }
  for (let z = Math.ceil(minZ / step) * step; z <= maxZ; z += step) {
    const sy = h / 2 - (z - mapView.centerZ) * scale;
    if (sy < top) continue;
    ctx.strokeStyle = "#3a4e66";
    ctx.beginPath();
    ctx.moveTo(0, sy);
    ctx.lineTo(4, sy);
    ctx.stroke();
    ctx.fillText(mapFormat(z, step), 12, sy);
  }
  ctx.textAlign = "left";
}

function buildMapMinimapBase() {
  const canvas = mapView.minimap;
  if (!canvas || !mapView.data) {
    mapView.minimapBase = null;
    return;
  }
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (!w || !h) {
    mapView.minimapBase = null;
    return;
  }
  const dpr = window.devicePixelRatio || 1;
  const base = document.createElement("canvas");
  base.width = Math.round(w * dpr);
  base.height = Math.round(h * dpr);
  const ctx = base.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#0a1119";
  ctx.fillRect(0, 0, w, h);
  const bounds = mapView.data.bounds;
  let minX, maxX, minZ, maxZ;
  if (bounds) {
    const padX = Math.max(1, (bounds.max_x - bounds.min_x) * 0.05);
    const padZ = Math.max(1, (bounds.max_y - bounds.min_y) * 0.05);
    minX = bounds.min_x - padX;
    maxX = bounds.max_x + padX;
    minZ = bounds.min_y - padZ;
    maxZ = bounds.max_y + padZ;
  } else {
    minX = mapView.centerX - 100;
    maxX = mapView.centerX + 100;
    minZ = mapView.centerZ - 100;
    maxZ = mapView.centerZ + 100;
  }
  const spanX = Math.max(1, maxX - minX);
  const spanZ = Math.max(1, maxZ - minZ);
  const ms = Math.min((w - 8) / spanX, (h - 8) / spanZ);
  const left = (w - spanX * ms) / 2;
  const top = (h - spanZ * ms) / 2;
  const box = { left, top, ms, minX, maxZ };
  ctx.fillStyle = "rgba(143, 199, 255, .55)";
  for (const unit of mapUnits()) {
    const [x, z] = mapPoint(unit);
    const mx = left + (x - minX) * ms;
    const my = top + (maxZ - z) * ms;
    ctx.beginPath();
    ctx.arc(mx, my, 1.4, 0, Math.PI * 2);
    ctx.fill();
  }
  mapView.minimapBase = { canvas: base, box };
  mapView.minimapBox = box;
}

function drawMapMinimap() {
  const canvas = mapView.minimap;
  const ctx = mapView.minimapCtx;
  if (!canvas || !ctx) return;
  if (!mapView.minimapBase) buildMapMinimapBase();
  const base = mapView.minimapBase;
  if (!base) return;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (!w || !h) return;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.drawImage(base.canvas, 0, 0, w, h);
  const box = base.box;
  mapView.minimapBox = box;
  const viewW = mapView.width / (mapView.scale || 1);
  const viewH = mapView.height / (mapView.scale || 1);
  const vx = box.left + (mapView.centerX - viewW / 2 - box.minX) * box.ms;
  const vy = box.top + (box.maxZ - (mapView.centerZ + viewH / 2)) * box.ms;
  ctx.fillStyle = "rgba(110, 231, 216, .1)";
  ctx.strokeStyle = "#6ee7d8";
  ctx.lineWidth = 1;
  ctx.fillRect(vx, vy, viewW * box.ms, viewH * box.ms);
  ctx.strokeRect(vx + 0.5, vy + 0.5, Math.max(1, viewW * box.ms - 1), Math.max(1, viewH * box.ms - 1));
}

function bindMapControls(file) {
  const canvas = mapView.canvas;
  if (!canvas) return;
  $("map-zoom-in").addEventListener("click", () => setMapScale(mapView.scale * 1.25));
  $("map-zoom-out").addEventListener("click", () => setMapScale(mapView.scale / 1.25));
  $("map-zoom-fit").addEventListener("click", () => {
    fitMapView();
    resizeMapCanvas();
  });
  const zoomInput = $("map-zoom-input");
  zoomInput.addEventListener("change", () => {
    const value = Number(zoomInput.value);
    if (Number.isFinite(value) && value > 0) setMapScale(mapView.fitScale * value / 100);
    else updateMapZoomInput();
  });
  zoomInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      zoomInput.blur();
    }
  });
  const dot = $("map-dot-size");
  dot.value = String(mapView.dotSize);
  dot.addEventListener("input", () => {
    mapView.dotSize = Math.max(3, Math.min(16, Number(dot.value) || 6));
    localStorage.setItem("npk-map-dot-size", String(mapView.dotSize));
    updateMapLegendDots();
    requestMapDraw();
  });
  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    mapView.dragging = true;
    mapView.dragX = event.clientX;
    mapView.dragY = event.clientY;
    canvas.classList.add("dragging");
    try { canvas.setPointerCapture(event.pointerId); } catch (_) {}
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!mapView.dragging) return;
    const scale = mapView.scale || 1;
    mapView.centerX -= (event.clientX - mapView.dragX) / scale;
    mapView.centerZ += (event.clientY - mapView.dragY) / scale;
    mapView.dragX = event.clientX;
    mapView.dragY = event.clientY;
    updateMapHud();
    requestMapDraw();
  });
  const endDrag = (event) => {
    if (!mapView.dragging) return;
    mapView.dragging = false;
    canvas.classList.remove("dragging");
    try { canvas.releasePointerCapture(event.pointerId); } catch (_) {}
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
    setMapScale(mapView.scale * factor, event.clientX - rect.left, event.clientY - rect.top);
  }, { passive: false });
  bindMapMinimap();
}

function bindMapMinimap() {
  const canvas = mapView.minimap;
  if (!canvas) return;
  let dragging = false;
  const centerFrom = (event) => {
    const box = mapView.minimapBox;
    if (!box) return;
    const rect = canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    mapView.centerX = box.minX + (mx - box.left) / box.ms;
    mapView.centerZ = box.maxZ - (my - box.top) / box.ms;
    updateMapHud();
    requestMapDraw();
  };
  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    dragging = true;
    try { canvas.setPointerCapture(event.pointerId); } catch (_) {}
    centerFrom(event);
    event.preventDefault();
  });
  canvas.addEventListener("pointermove", (event) => {
    if (dragging) centerFrom(event);
  });
  const end = (event) => {
    dragging = false;
    try { canvas.releasePointerCapture(event.pointerId); } catch (_) {}
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
}

function buildMapLegend() {
  const data = mapView.data;
  const panel = $("map-legend");
  if (!data || !panel) return;
  const labels = {};
  const tints = {};
  for (const unit of data.units || []) {
    const eid = unit.eid;
    if (eid == null) continue;
    if (!tints[eid] && Array.isArray(unit.color) && unit.color.length >= 3) {
      tints[eid] = `rgb(${unit.color[0]}, ${unit.color[1]}, ${unit.color[2]})`;
    }
    if (labels[eid]) continue;
    const base = String(unit.name || "").replace(/\d+$/, "").trim();
    if (base) labels[eid] = base;
  }
  const rows = Object.entries(data.eids || {})
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 10)
    .map(([eidText, count]) => {
      const eid = Number(eidText);
      const color = tints[eid] || mapColor(eid);
      const typeColor = mapColor(eid);
      const label = labels[eid] || MAP_EID_LABEL[eid] || tr("map.unit", { eid: eidText });
      const icon = mapIconUrl(eid);
      const swatch = icon
        ? `<span class="map-legend-icon" title="${esc(label)} ${tr("map.legend.kind")}" style="background-image:url('${icon}')"></span>`
        : `<span class="map-legend-swatch" title="${esc(label)} ${tr("map.legend.paint")}" style="width:${mapView.dotSize}px;height:${mapView.dotSize}px;background:${color};border-color:${typeColor}"></span>`;
      return `<div class="map-legend-row">
        ${swatch}
        <span class="map-legend-name">${esc(label)}</span>
        <span class="map-legend-count">${Number(count).toLocaleString()}</span>
      </div>`;
    })
    .join("");
  panel.innerHTML = `<div class="map-legend-head"><b>${esc(data.map_name || tr("gmp.mapName"))}</b><span>${tr("map.units", { count: Number(data.unit_count || 0).toLocaleString() })}</span></div>${rows || `<div class="map-legend-empty">${tr("map.noUnits")}</div>`}`;
}

function updateMapLegendDots() {
  document.querySelectorAll(".map-legend-swatch").forEach((dot) => {
    dot.style.width = `${mapView.dotSize}px`;
    dot.style.height = `${mapView.dotSize}px`;
  });
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
  $("preview-media").classList.remove("swap-in", "gmp-mode");
  $("preview-media").innerHTML = `<div class="preview-skeleton skeleton" aria-hidden="true"></div>`;
  $("preview-meta").innerHTML = metaSkeleton();
  $("preview-note").textContent = tr("drawer.note.info");
  $("open-image").classList.add("hidden");
  renderOpenActions();
  openDrawerPanel();
  $("download-link").textContent = tr("drawer.download");
  $("download-link").href = apiUrl("/api/download", { path: file.path });
  $("download-link").setAttribute("download", file.name);

  let info;
  try {
    info = await getJson(apiUrl("/api/info", { path: file.path }));
  } catch (error) {
    if (token !== previewToken) return;
    $("preview-meta").innerHTML = "";
    $("preview-note").textContent = "";
    $("preview-media").innerHTML = `<div class="errorbox">${esc(tr("error.readInfo", { message: error.message }))}</div>`;
    return;
  }
  if (token !== previewToken) return;
  $("preview-meta").innerHTML = metadataRows(info);
  const media = $("preview-media");
  const kind = info.kind;
  try {
    if ((info.ext || "").toLowerCase() === ".gmp") {
      media.innerHTML = `<div class="preview-skeleton skeleton" aria-hidden="true"></div>`;
      $("preview-note").textContent = tr("gmp.loading");
      const data = await getJson(apiUrl("/api/gmp", { path: file.path }));
      if (token !== previewToken) return;
      renderGmpPreview(data, media);
    } else if (kind === "script") {
      media.innerHTML = `<div class="preview-skeleton skeleton" aria-hidden="true"></div>`;
      $("preview-note").textContent = tr("drawer.note.script");
      const data = await getJson(apiUrl("/api/xn", { path: file.path }));
      if (token !== previewToken) return;
      renderXnPreview(data, media, file);
    } else if (kind === "image") {
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
        media.innerHTML = `<div class="errorbox">${tr("error.image")}</div>`;
      }, { once: true });
      if (img.complete && img.naturalWidth > 0) ready();
      $("open-image").href = url;
      $("open-image").classList.remove("hidden");
      $("preview-note").textContent = tr("drawer.note.image");
    } else if (kind === "bank") {
      $("download-link").textContent = tr("drawer.downloadBank");
      $("preview-note").textContent = tr("drawer.note.bank");
      await showBankPreview(file, media, token);
    } else if (kind === "audio") {
      const url = apiUrl("/api/audio", { path: file.path });
      media.innerHTML = `<audio controls autoplay preload="auto" src="${url}"></audio>`;
      $("download-link").textContent = tr("drawer.downloadFlac");
      $("download-link").href = apiUrl("/api/audio", { path: file.path, download: 1 });
      $("preview-note").textContent = /^\d+\.wem$/i.test(file.name)
        ? tr("audio.wemNote")
        : tr("drawer.note.audio");
    } else if (kind === "video") {
      media.innerHTML = `<div class="preview-video">
        <span class="preview-video-skeleton skeleton" aria-hidden="true"></span>
        <video controls preload="metadata" src="${apiUrl("/api/video", { path: file.path })}"></video>
      </div>`;
      const wrap = media.querySelector(".preview-video");
      const video = media.querySelector("video");
      video.addEventListener("loadedmetadata", () => requestAnimationFrame(() => wrap && wrap.classList.add("ready")), { once: true });
      video.addEventListener("error", () => {
        media.innerHTML = `<div class="errorbox">${tr("error.video")}</div>`;
      }, { once: true });
      $("preview-note").textContent = tr("drawer.note.video");
    } else {
      media.innerHTML = `<div class="preview-skeleton skeleton" aria-hidden="true"></div>`;
      $("preview-note").textContent = tr("drawer.note.inspect");
      const data = await getJson(apiUrl("/api/inspect", { path: file.path }));
      if (token !== previewToken) return;
      renderInspect(data, media);
    }
  } catch (error) {
    if (token !== previewToken) return;
    $("preview-note").textContent = "";
    media.innerHTML = `<div class="errorbox">${esc(tr("error.preview", { message: error.message }))}<br>${tr("error.previewTip")}</div>`;
  }
}

function openDrawerPanel() {
  const drawer = $("drawer");
  const overlay = $("overlay");
  if (drawerCloseTimer) {
    clearTimeout(drawerCloseTimer);
    drawerCloseTimer = 0;
  }
  drawer.classList.remove("hidden");
  overlay.classList.remove("hidden");
  drawer.setAttribute("aria-hidden", "false");
  const body = drawer.querySelector(".drawer-body");
  if (body) body.scrollTop = 0;
  void drawer.offsetWidth;
  void overlay.offsetWidth;
  drawer.classList.add("open");
  overlay.classList.add("open");
}

function closeDrawer() {
  previewToken += 1;
  state.previewPath = null;
  setActiveFile(null);
  updatePreviewNav();
  const drawer = $("drawer");
  const overlay = $("overlay");
  drawer.setAttribute("aria-hidden", "true");
  drawer.classList.remove("open");
  overlay.classList.remove("open");
  if (drawer.classList.contains("hidden")) {
    overlay.classList.add("hidden");
    $("preview-media").innerHTML = "";
    $("open-actions").innerHTML = "";
    $("preview-note").textContent = "";
    return;
  }
  if (drawerCloseTimer) clearTimeout(drawerCloseTimer);
  drawerCloseTimer = setTimeout(() => {
    drawerCloseTimer = 0;
    if (drawer.classList.contains("open")) return;
    drawer.classList.add("hidden");
    overlay.classList.add("hidden");
    $("preview-media").innerHTML = "";
    $("open-actions").innerHTML = "";
    $("preview-note").textContent = "";
  }, 320);
}

async function copyText(text, button, label) {
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = tr("common.copied");
    setTimeout(() => { button.textContent = label; }, 1200);
  } catch (_) {
    button.textContent = tr("common.copyFailed");
  }
}

async function copyPath() {
  if (!state.previewPath) return;
  await copyText(state.previewPath, $("copy-path"), tr("drawer.copyPath"));
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
        else if (action.dataset.action === "script") openScriptViewer(match);
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
      copyText(search ? ($("search").value.trim()) : state.path, $("copy-index"), search ? tr("list.copyKeyword") : tr("index.copyPath"));
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
    const button = $("refresh");
    button.classList.add("spinning");
    try {
      await loadStats();
      if (state.searchMode) {
        const query = $("search").value.trim();
        if (query.length >= 2) await search(query); else await loadList(state.path, false);
      } else {
        await loadList(state.path, false, true);
      }
    } finally {
      button.classList.remove("spinning");
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
  $("update-toast-close").addEventListener("click", hideUpdateToast);
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
    } else if (viewer.mode === "map") {
      resizeMapCanvas();
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
  loadUpdate();
  await loadList(path, false);
  await statsPromise;
}

async function boot() {
  await initI18n();
  await init();
}

boot();
