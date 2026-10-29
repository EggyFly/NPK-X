const $ = (id) => document.getElementById(id);
const NL = String.fromCharCode(10);
const PAGE_SIZE = 1500;
const BATCH_RAW_MAX = 2000;
const BATCH_CONVERT_MAX = 200;
const IMAGE_FORMATS = ["png", "jpg", "ico"];
const AUDIO_FORMATS = ["wav", "flac", "mp3"];
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
  selectMode: false,
  selected: new Set(),
  roots: [],
  names: new Map(),
};

const batchPick = { img: "png", aud: "wav" };
let batchMenuOpen = false;
let batchMenuPage = "main";
let batchToastTimer = 0;

let previewToken = 0;
let drawerCloseTimer = 0;
let listToken = 0;
let pendingRestoreScroll = null;
let dropdownOpen = "";
let marqueeResizeTimer = 0;
let moreObserver = null;

const I18N_CODES = ["zh_cn", "zh_tw", "en_us"];
const I18N_STORAGE = "npk-language";
const THEME_STORAGE = "npk-theme";
const SIDEBAR_WIDTH_STORAGE = "npk-sidebar-width";
const INDEX_COLLAPSE_STORAGE = "npk-index-collapsed";
const THEME_CHOICES = ["auto", "light", "dark"];
const themeState = { choice: "auto", resolved: "dark", media: null, menuOpen: false };
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
  syncDropdown("sort");
}

let canvasPaletteCache = null;

function cssVar(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function canvasFont(size) {
  const family = getComputedStyle(document.body).fontFamily || '"Segoe UI", sans-serif';
  return `${size}px ${family}`;
}

function canvasPalette() {
  if (canvasPaletteCache) return canvasPaletteCache;
  canvasPaletteCache = {
    bg: cssVar("--md-canvas-bg", "#0a0e10"),
    grid: cssVar("--md-canvas-grid", "rgba(191,200,203,.1)"),
    line: cssVar("--md-canvas-line", "#5c6669"),
    text: cssVar("--md-canvas-text", "#dfe3e6"),
    muted: cssVar("--md-canvas-muted", "#bfc8cb"),
    accent: cssVar("--md-canvas-accent", "#7bdbe6"),
    accentSoft: cssVar("--md-canvas-accent-soft", "rgba(123,219,230,.55)"),
    accentFill: cssVar("--md-canvas-accent-fill", "rgba(123,219,230,.12)"),
    rulerBg: cssVar("--md-canvas-ruler-bg", "rgba(15,19,22,.94)"),
    rulerLine: cssVar("--md-canvas-ruler-line", "#3f484b"),
    labelShadow: cssVar("--md-canvas-label-shadow", "#0a0e10"),
    textMap: cssVar("--md-canvas-textmap", "rgba(123,219,230,.45)"),
    crosshair: cssVar("--md-canvas-crosshair", "rgba(223,227,230,.24)"),
    points: cssVar("--md-canvas-points", "rgba(123,219,230,.55)"),
    chip: cssVar("--md-canvas-chip-bg", "rgba(10,14,16,.9)"),
  };
  return canvasPaletteCache;
}

function colorToRgb(value, fallback) {
  const hex = /^#([0-9a-f]{6})$/i.exec(value);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }
  const rgba = /^rgba?\(([^)]+)\)$/i.exec(value);
  if (rgba) {
    const parts = rgba[1].split(",").slice(0, 3).map((part) => Number(part.trim()));
    if (parts.length === 3 && parts.every(Number.isFinite)) {
      return parts.map((part) => Math.max(0, Math.min(255, part)) / 255);
    }
  }
  return fallback;
}

function invalidateCanvasPalette() {
  canvasPaletteCache = null;
}

function resolveTheme(choice) {
  if (choice === "light" || choice === "dark") return choice;
  return themeState.media && themeState.media.matches ? "light" : "dark";
}

function renderThemeMenu() {
  const menu = $("theme-menu");
  if (!menu) return;
  menu.innerHTML = THEME_CHOICES.map((choice) => {
    const active = themeState.choice === choice;
    const iconName = choice === "auto" ? "theme-auto" : choice === "light" ? "theme-light" : "theme-dark";
    return `<button type="button" class="theme-item" role="menuitemradio" aria-checked="${active}" data-theme-choice="${choice}">${icon(iconName)}<span>${tr(`theme.${choice}`)}</span>${active ? `<svg class="icon theme-check" aria-hidden="true"><use href="#i-check"></use></svg>` : ""}</button>`;
  }).join("");
}

function updateThemeUi() {
  const toggle = $("theme-toggle");
  if (toggle) {
    const iconName = themeState.choice === "auto" ? "theme-auto" : themeState.choice === "light" ? "theme-light" : "theme-dark";
    toggle.innerHTML = icon(iconName);
    const label = `${tr("theme.title")}: ${tr(`theme.${themeState.choice}`)}`;
    toggle.title = label;
    toggle.setAttribute("aria-label", label);
  }
  renderThemeMenu();
}

function refreshThemeSurfaces() {
  if (mapView.data) {
    mapView.minimapBase = null;
    requestMapDraw();
  }
  if (viewerIsOpen() && viewer.mode === "text") {
    drawTextViewerMap($("viewer-text")?.textContent || "");
    updateTextViewerMap();
  }
  if (gim3d.gl) {
    const clear = colorToRgb(canvasPalette().bg, [0.039, 0.055, 0.063]);
    gim3d.gl.clearColor(clear[0], clear[1], clear[2], 1);
  }
}

function applyTheme(choice, persist = true) {
  themeState.choice = THEME_CHOICES.includes(choice) ? choice : "auto";
  themeState.resolved = resolveTheme(themeState.choice);
  document.documentElement.dataset.theme = themeState.resolved;
  document.documentElement.style.colorScheme = themeState.resolved;
  if (persist) {
    try { localStorage.setItem(THEME_STORAGE, themeState.choice); } catch (_) {}
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", cssVar("--md-surface", themeState.resolved === "light" ? "#f7fafb" : "#0f1316"));
  updateThemeUi();
  invalidateCanvasPalette();
  refreshThemeSurfaces();
}

function openThemeMenu() {
  if (batchMenuOpen) closeBatchMenu();
  closeDropdown();
  themeState.menuOpen = true;
  renderThemeMenu();
  $("theme-menu").classList.remove("hidden");
  $("theme-toggle").setAttribute("aria-expanded", "true");
  const checked = $("theme-menu").querySelector('[aria-checked="true"]');
  if (checked) checked.focus({ preventScroll: true });
}

function closeThemeMenu() {
  themeState.menuOpen = false;
  const menu = $("theme-menu");
  const toggle = $("theme-toggle");
  if (menu && menu.contains(document.activeElement)) toggle.focus({ preventScroll: true });
  menu.classList.add("hidden");
  toggle.setAttribute("aria-expanded", "false");
}

function toggleThemeMenu() {
  if (themeState.menuOpen) closeThemeMenu();
  else openThemeMenu();
}

function initTheme() {
  themeState.media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
  if (themeState.media) {
    const onChange = () => { if (themeState.choice === "auto") applyTheme("auto", false); };
    if (themeState.media.addEventListener) themeState.media.addEventListener("change", onChange);
    else if (themeState.media.addListener) themeState.media.addListener(onChange);
  }
  let stored = "auto";
  try { stored = localStorage.getItem(THEME_STORAGE) || "auto"; } catch (_) {}
  applyTheme(stored, false);
  $("theme-toggle").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleThemeMenu();
  });
  $("theme-menu").addEventListener("click", (event) => {
    event.stopPropagation();
    const item = event.target.closest("[data-theme-choice]");
    if (!item) return;
    closeThemeMenu();
    applyTheme(item.dataset.themeChoice);
  });
  document.addEventListener("click", (event) => {
    if (!themeState.menuOpen) return;
    const path = event.composedPath ? event.composedPath() : [];
    if (!path.some((node) => node instanceof Element && node.matches(".theme-wrap"))) closeThemeMenu();
  });
}


function syncDropdown(name) {
  const select = $(name);
  const menu = $(name + "-menu");
  const label = $(name + "-label");
  if (!select || !menu || !label) return;
  const value = select.value;
  const selected = select.options[select.selectedIndex];
  label.textContent = selected ? selected.textContent : "";
  label.title = label.textContent;
  menu.innerHTML = Array.from(select.options).map((option) => {
    const on = option.value === value;
    return `<button type="button" class="menu-option${on ? " active" : ""}" role="option" aria-selected="${on}" data-value="${esc(option.value)}">${esc(option.textContent)}${on ? `<svg class="icon menu-check" aria-hidden="true"><use href="#i-check"></use></svg>` : ""}</button>`;
  }).join("");
}

function dropdownBox(name) {
  return document.getElementById(name + "-select");
}

function closeDropdown() {
  if (!dropdownOpen) return;
  const name = dropdownOpen;
  dropdownOpen = "";
  const box = dropdownBox(name);
  if (!box) return;
  const trigger = box.querySelector(".menu-trigger");
  const menu = box.querySelector(".menu-popup");
  if (menu && menu.contains(document.activeElement)) trigger.focus({ preventScroll: true });
  menu.classList.add("hidden");
  trigger.setAttribute("aria-expanded", "false");
  box.classList.remove("open");
}

function openDropdown(name) {
  if (dropdownOpen === name) return closeDropdown();
  closeDropdown();
  if (batchMenuOpen) closeBatchMenu();
  if (themeState.menuOpen) closeThemeMenu();
  const box = dropdownBox(name);
  if (!box) return;
  const trigger = box.querySelector(".menu-trigger");
  const menu = box.querySelector(".menu-popup");
  syncDropdown(name);
  dropdownOpen = name;
  menu.classList.remove("hidden");
  trigger.setAttribute("aria-expanded", "true");
  box.classList.add("open");
  const active = menu.querySelector('[aria-selected="true"]');
  if (active) active.focus({ preventScroll: true });
}

function initDropdowns() {
  ["sort", "language"].forEach((name) => {
    const box = dropdownBox(name);
    if (!box) return;
    const trigger = box.querySelector(".menu-trigger");
    const menu = box.querySelector(".menu-popup");
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      if (dropdownOpen === name) closeDropdown();
      else openDropdown(name);
    });
    menu.addEventListener("click", (event) => {
      event.stopPropagation();
      const option = event.target.closest(".menu-option");
      if (!option) return;
      const select = $(name);
      select.value = option.dataset.value;
      closeDropdown();
      syncDropdown(name);
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    menu.addEventListener("keydown", (event) => {
      const options = Array.from(menu.querySelectorAll(".menu-option"));
      const index = options.indexOf(document.activeElement);
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const next = event.key === "ArrowDown" ? Math.min(options.length - 1, index + 1) : Math.max(0, index - 1);
        options[next].focus();
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        options[event.key === "Home" ? 0 : options.length - 1].focus();
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeDropdown();
        trigger.focus({ preventScroll: true });
      }
    });
  });
  document.addEventListener("click", (event) => {
    if (!dropdownOpen) return;
    if (!event.target.closest(".menu-select")) closeDropdown();
  });
  syncDropdown("sort");
  syncDropdown("language");
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
  syncDropdown("language");
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
  rerenderRoots();
  applyStaticI18n();
  updateThemeUi();
  if (lastUpdateInfo) renderUpdate(lastUpdateInfo, false);
  renderLanguageOptions();
  if (!rerender) return;
  const preview = state.previewPath;
  const previewFile = preview ? state.files.find((item) => item.path === preview) : null;
  if (viewerIsOpen()) closeViewer();
  if (state.searchMode) await search(state.searchQuery, true);
  else await loadList(state.path, false, true, true);
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

function neoxParams(file) {
  const pose = new URLSearchParams(location.search).get("pose");
  return pose === "0" ? { path: file.path, pose: "0" } : { path: file.path };
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
const NAME_EXT = new Set([".gmp", ".xn", ".wem"]);

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

function badgeIcon(kind, name) {
  return `<span class="badge ${esc(kind)} badge-icon" aria-hidden="true">${icon(name)}</span>`;
}

function badgeShort(file) {
  const kind = displayKind(file.kind, file.ext);
  const ext = (file.ext || "").toLowerCase();
  if (ext === ".mesh") return badgeIcon("model", "mesh");
  if (kind === "binary") {
    const label = (file.ext || "BIN").replace(".", "").slice(0, 4).toUpperCase() || "BIN";
    if (label === "BIN") return badgeIcon("binary", "bin");
  }
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
  if (ext === ".gim") return { name: "gim", label: tr("action.view"), icon: "map" };
  if (ext === ".mesh") return { name: "gim", label: tr("action.view"), icon: "map" };
  if (ext === ".xn") return { name: "script", label: tr("action.script"), icon: "code" };
  if (kind === "audio") return { name: "play", label: tr("action.play"), icon: "play" };
  if (kind === "image" || isTextView(file)) return { name: "view", label: tr("action.view"), icon: "zoom" };
  return { name: "preview", label: tr("action.view"), icon: "zoom" };
}

function thumbMarkup(file, small = false) {
  const kind = displayKind(file.kind, file.ext);
  const ext = (file.ext || "").toLowerCase();
  const width = small ? 96 : 360;
  const thumbUrl = apiUrl("/api/thumb", { path: file.path, w: width, v: 6 });
  const fallback = `<span class="thumb-fallback">${badgeShort(file)}</span>`;
  if (ext === ".mtg") {
    return `<div class="thumb thumb-mtg" data-mtg="${esc(apiUrl("/api/mtg", { path: file.path }))}">
      <span class="thumb-skeleton skeleton" aria-hidden="true"></span>
      <div class="mtg-grid"></div>
      ${fallback}
    </div>`;
  }
  if (ext === ".gim" || ext === ".gmp") {
    return `<div class="thumb thumb-media thumb-render">
      <span class="thumb-skeleton skeleton" aria-hidden="true"></span>
      <img decoding="async" draggable="false" data-thumb-src="${thumbUrl}" alt="">
      ${fallback}
    </div>`;
  }
  if (ext === ".mesh") return `<div class="thumb thumb-icon">${icon("mesh")}</div>`;
  if (ext === ".bin") return `<div class="thumb thumb-icon">${icon("bin")}</div>`;
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
  delete thumb.dataset.mtg;
}

function unpreviewThumb(thumb) {
  if (thumbPrefetchObserver) thumbPrefetchObserver.unobserve(thumb);
  if (thumbRevealObserver) thumbRevealObserver.unobserve(thumb);
}

const THUMB_FETCH_LIMIT = 6;
const THUMB_FETCH_TIMEOUT = 30000;
const THUMB_RETRY_LIMIT = 240;
let thumbEpoch = 0;
let thumbRunning = 0;
const thumbJobs = new Map();
const thumbQueue = [[], []];

function beginThumbEpoch() {
  thumbEpoch += 1;
  thumbQueue[0].length = 0;
  thumbQueue[1].length = 0;
  for (const job of Array.from(thumbJobs.values())) cancelThumbJob(job);
}

function cancelThumbJob(job) {
  if (job.cancelled) return;
  job.cancelled = true;
  thumbJobs.delete(job.key);
  if (job.controller) job.controller.abort();
  for (const waiter of job.waiters) waiter.reject(new Error("thumbnail cancelled"));
  job.waiters.length = 0;
}

function enqueueThumbJob(job) {
  if (job.cancelled || job.epoch !== thumbEpoch) return;
  thumbQueue[job.priority === 0 ? 0 : 1].push(job);
  pumpThumbQueue();
}

function raiseThumbPriority(job) {
  if (job.priority === 0) return;
  job.priority = 0;
  const index = thumbQueue[1].indexOf(job);
  if (index >= 0) {
    thumbQueue[1].splice(index, 1);
    thumbQueue[0].push(job);
  }
}

function pumpThumbQueue() {
  while (thumbRunning < THUMB_FETCH_LIMIT) {
    const job = thumbQueue[0].shift() || thumbQueue[1].shift();
    if (!job) return;
    if (job.cancelled || job.epoch !== thumbEpoch) continue;
    thumbRunning += 1;
    void runThumbJob(job);
  }
}

async function runThumbJob(job) {
  job.controller = new AbortController();
  const timer = setTimeout(() => job.controller.abort(), THUMB_FETCH_TIMEOUT);
  let retryDelay = 0;
  try {
    const response = await fetch(job.url, { cache: "default", signal: job.controller.signal });
    if (job.cancelled || job.epoch !== thumbEpoch) return;
    if (response.status === 202) {
      job.retries += 1;
      if (job.retries > THUMB_RETRY_LIMIT) throw new Error("thumbnail timeout");
      retryDelay = 450;
      try {
        const data = await response.json();
        const retry = Number(data && data.retry_after);
        if (Number.isFinite(retry) && retry > 0) retryDelay = Math.max(200, retry * 1000);
      } catch (_) {}
      return;
    }
    if (!response.ok) throw new Error(`thumbnail ${response.status}`);
    const blob = await response.blob();
    if (job.cancelled || job.epoch !== thumbEpoch) return;
    thumbJobs.delete(job.key);
    for (const waiter of job.waiters) waiter.resolve(URL.createObjectURL(blob));
    job.waiters.length = 0;
  } catch (error) {
    if (job.cancelled) return;
    const failure = error && error.name === "AbortError" ? new Error("thumbnail timeout") : error;
    thumbJobs.delete(job.key);
    for (const waiter of job.waiters) waiter.reject(failure);
    job.waiters.length = 0;
  } finally {
    clearTimeout(timer);
    thumbRunning = Math.max(0, thumbRunning - 1);
    if (retryDelay) {
      setTimeout(() => {
        if (!job.cancelled && job.epoch === thumbEpoch) enqueueThumbJob(job);
      }, retryDelay * (0.85 + Math.random() * 0.3));
    }
    pumpThumbQueue();
  }
}

function scheduleThumb(url, priority = 1) {
  const key = `${thumbEpoch}|${url}`;
  let job = thumbJobs.get(key);
  if (!job) {
    job = {
      key, url, epoch: thumbEpoch, priority: priority === 0 ? 0 : 1,
      retries: 0, waiters: [], controller: null, cancelled: false,
    };
    thumbJobs.set(key, job);
    enqueueThumbJob(job);
  } else if (priority === 0) {
    raiseThumbPriority(job);
  }
  return new Promise((resolve, reject) => job.waiters.push({ resolve, reject }));
}

async function loadThumbImage(img, thumb, src, soft = false, priority = null) {
  const rank = priority === null ? (thumb && thumb.dataset.visible === "1" ? 0 : 1) : priority;
  try {
    const blobUrl = await scheduleThumb(src, rank);
    if (!img.isConnected) {
      URL.revokeObjectURL(blobUrl);
      return;
    }
    const revoke = () => URL.revokeObjectURL(blobUrl);
    img.addEventListener("load", revoke, { once: true });
    img.addEventListener("error", revoke, { once: true });
    img.src = blobUrl;
  } catch (_) {
    if (soft) {
      img.style.display = "none";
      return;
    }
    if (!thumb.isConnected) return;
    img.style.display = "none";
    clearThumbSource(thumb);
    thumb.classList.add("failed");
    unpreviewThumb(thumb);
  }
}

function revealThumb(thumb) {
  if (thumb.dataset.visible !== "1" || thumb.dataset.ready !== "1") return;
  if (thumb.classList.contains("loaded") || thumb.classList.contains("failed")) return;
  clearThumbSource(thumb);
  let shown = false;
  const show = () => {
    if (shown) return;
    shown = true;
    thumb.classList.add("loaded");
  };
  setTimeout(show, 48);
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(show);
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
    loadThumbImage(img, thumb, src);
    return;
  }
  if (thumb.dataset.mtg) {
    loadThumbMtg(thumb);
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
  if (!visible) return;
  revealThumb(thumb);
  const img = thumb.querySelector("img[data-thumb-src]");
  const job = img && img.dataset.thumbSrc ? thumbJobs.get(`${thumbEpoch}|${img.dataset.thumbSrc}`) : null;
  if (job) raiseThumbPriority(job);
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
  const nodes = root.querySelectorAll("[data-thumb-src], [data-snippet], [data-video-src], [data-mtg]");
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

let nameObserver = null;
let nameQueue = new Set();
let nameTimer = 0;
let nameEpoch = 0;
let nameBusy = false;
let fileNodeMap = new Map();

function indexFileNodes(root) {
  if (!root) return;
  root.querySelectorAll("[data-file]").forEach((node) => fileNodeMap.set(node.dataset.file, node));
}

function displayNameFor(file) {
  const name = state.names.get(file.path) || "";
  return name && name !== file.name ? name : "";
}

function namePending(file) {
  return NAME_EXT.has((file.ext || "").toLowerCase()) && !state.names.has(file.path);
}

function absorbNames(files) {
  for (const file of files || []) {
    if (typeof file.display_name === "string" && file.display_name) {
      state.names.set(file.path, file.display_name);
    }
  }
}

function beginNameEpoch() {
  nameEpoch += 1;
  nameQueue.clear();
  if (nameTimer) {
    clearTimeout(nameTimer);
    nameTimer = 0;
  }
  if (nameObserver) {
    nameObserver.disconnect();
    nameObserver = null;
  }
}

function queueName(path) {
  if (!path || state.names.has(path) || nameQueue.has(path)) return;
  nameQueue.add(path);
  if (!nameTimer) nameTimer = setTimeout(flushNames, 80);
}

function queueFileNames(file) {
  if (file && NAME_EXT.has((file.ext || "").toLowerCase())) queueName(file.path);
}

async function flushNames() {
  if (nameBusy) return;
  nameTimer = 0;
  const batch = Array.from(nameQueue).slice(0, 40);
  batch.forEach((path) => nameQueue.delete(path));
  if (!batch.length) return;
  nameBusy = true;
  try {
    const epoch = nameEpoch;
    const query = new URLSearchParams();
    batch.forEach((path) => query.append("path", path));
    let data = null;
    let ok = false;
    try {
      data = await getJson(`/api/names?${query.toString()}`);
      ok = true;
    } catch (_) {}
    if (epoch !== nameEpoch || !ok) return;
    const names = (data && data.names) || {};
    for (const path of batch) {
      if (!state.names.has(path)) state.names.set(path, "");
      const node = fileNodeMap.get(path);
      if (node) delete node.dataset.nameFile;
    }
    const applied = [];
    for (const [path, name] of Object.entries(names)) {
      if (typeof name === "string" && name) {
        state.names.set(path, name);
        applied.push([path, name]);
      }
    }
    if (applied.length) applyNames(applied);
  } finally {
    nameBusy = false;
    if (nameQueue.size) nameTimer = setTimeout(flushNames, 40);
  }
}

function takePendingName(node) {
  queueName(node.dataset.file);
  delete node.dataset.nameFile;
  if (nameObserver) nameObserver.unobserve(node);
}

function ensureNameObserver() {
  if (nameObserver || !window.IntersectionObserver) return;
  nameObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) takePendingName(entry.target);
    }
  }, { rootMargin: "800px 0px" });
}

function watchNameNodes(nodes) {
  if (!nodes.length) return;
  if (!window.IntersectionObserver) {
    nodes.forEach(takePendingName);
    return;
  }
  ensureNameObserver();
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
  nodes.forEach((node) => {
    const rect = node.getBoundingClientRect();
    if (rect.bottom >= -800 && rect.top <= viewportHeight + 800) takePendingName(node);
    else nameObserver.observe(node);
  });
}

function hydrateNames(root) {
  if (!root) return;
  if (nameObserver) {
    nameObserver.disconnect();
    nameObserver = null;
  }
  watchNameNodes(Array.from(root.querySelectorAll("[data-name-file]")));
}

let nameScrollTimer = 0;
function queueVisibleNames() {
  if (nameScrollTimer) clearTimeout(nameScrollTimer);
  nameScrollTimer = setTimeout(() => {
    nameScrollTimer = 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
    document.querySelectorAll("[data-name-file]").forEach((node) => {
      const rect = node.getBoundingClientRect();
      if (rect.bottom >= -800 && rect.top <= viewportHeight + 800) takePendingName(node);
    });
  }, 220);
}

function initNameHydration() {
  window.addEventListener("scroll", queueVisibleNames, { passive: true });
}

function updateNameNode(node, name) {
  const filename = node.dataset.filename || "";
  if (name === filename) {
    delete node.dataset.nameFile;
    return;
  }
  if (node.classList.contains("file-row")) {
    const nameEl = node.querySelector(".file-row-name");
    const label = node.querySelector(".file-row-label");
    if (nameEl) nameEl.textContent = name;
    if (label && !label.querySelector(".file-row-sub") && filename) {
      const sub = document.createElement("span");
      sub.className = "file-row-sub";
      sub.textContent = filename;
      label.appendChild(sub);
    }
  } else {
    const nameEl = node.querySelector(".file-name");
    const text = node.querySelector(".file-text");
    if (nameEl) nameEl.textContent = name;
    if (text && !text.querySelector(".file-filename") && filename) {
      const line = document.createElement("div");
      line.className = "file-filename";
      line.textContent = filename;
      const meta = text.querySelector(".file-meta");
      if (meta) text.insertBefore(line, meta);
    }
  }
  delete node.dataset.nameFile;
  refreshMarqueeTargets(node);
}

function applyNames(entries) {
  for (const [path, name] of entries) {
    const node = fileNodeMap.get(path);
    if (node && node.isConnected) updateNameNode(node, name);
    if (state.previewPath === path) {
      const file = state.files.find((item) => item.path === path);
      if (file) setPreviewTitle(file);
    }
    if (viewer.path === path && viewer.file) setViewerTitle(viewer.file);
  }
}

function mtgCellMarkup(item) {
  const ext = (item.ext || "").toLowerCase();
  const label = ext ? ext.slice(1, 5).toUpperCase() : "FILE";
  if (item.kind === "image") {
    return `<span class="mtg-cell mtg-cell-image"><img data-thumb-src="${esc(apiUrl("/api/thumb", { path: item.path, w: 96, v: 6 }))}" alt="" draggable="false"><b>${esc(label)}</b></span>`;
  }
  const name = ext === ".mesh" || ext === ".gim" ? "mesh" : ext === ".xn" ? "code" : "bin";
  return `<span class="mtg-cell"><span class="mtg-cell-icon">${icon(name)}</span><b>${esc(label)}</b></span>`;
}

async function loadThumbMtg(thumb) {
  const url = thumb.dataset.mtg;
  try {
    const data = await getJson(url);
    if (!thumb.isConnected) return;
    const list = Array.isArray(data.resources) ? data.resources : [];
    const total = Number(data.total ?? list.length);
    if (!list.length) throw new Error("no resources");
    const grid = thumb.querySelector(".mtg-grid");
    if (!grid) throw new Error("no grid");
    const limit = total > 6 ? 5 : 6;
    grid.innerHTML = list.slice(0, limit).map(mtgCellMarkup).join("");
    if (total > 6) grid.insertAdjacentHTML("beforeend", `<span class="mtg-cell mtg-more"><b>${total - 5}+</b></span>`);
    grid.querySelectorAll("img[data-thumb-src]").forEach((img) => {
      const src = img.dataset.thumbSrc;
      delete img.dataset.thumbSrc;
      loadThumbImage(img, thumb, src, true);
    });
    thumb.dataset.ready = "1";
    revealThumb(thumb);
  } catch (_) {
    clearThumbSource(thumb);
    thumb.classList.add("failed");
    unpreviewThumb(thumb);
  }
}

async function loadThumbSnippet(thumb) {
  const url = thumb.dataset.snippet;
  try {
    const data = await getJson(url);
    if (!thumb.isConnected) return;
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
  if (!thumb.isConnected) return;
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
  if (!crumbs || !crumbs.length) {
    node.innerHTML = "";
    node.classList.add("hidden");
    return;
  }
  node.classList.remove("hidden");
  const parts = [`<button data-dir="">${tr("index.all")}</button>`];
  for (const crumb of crumbs) {
    parts.push('<span class="sep">/</span>');
    parts.push(`<button data-dir="${esc(crumb.path)}">${esc(crumb.name)}</button>`);
  }
  node.innerHTML = parts.join("");
}

function rerenderRoots() {
  const node = $("roots");
  if (!node || !state.roots.length) return;
  const scrollTop = node.scrollTop;
  const focused = document.activeElement && document.activeElement.closest ? document.activeElement.closest(".root") : null;
  const focusDir = focused ? focused.dataset.dir : "";
  renderRoots(state.roots);
  node.scrollTop = scrollTop;
  if (focusDir) {
    const next = Array.from(node.querySelectorAll(".root")).find((item) => item.dataset.dir === focusDir);
    if (next) next.focus({ preventScroll: true });
  }
}

function renderRoots(roots) {
  $("roots").innerHTML = (roots || []).map((root) => {
    const active = state.path === root.path || state.path.startsWith(root.path + "/");
    return `<button class="root ${active ? "active" : ""}" data-dir="${esc(root.path)}" title="${esc(root.path)}">
      <span class="root-name">${esc(ROOT_LABEL[root.path] || root.path)}</span>
      <span class="root-count">${Number(root.file_count).toLocaleString()}</span>
    </button>`;
  }).join("");
  setupMarquee($("roots"));
}

function folderMeta(dir) {
  return `${Number(dir.count).toLocaleString()} ${tr("stats.files")} · ${formatBytes(dir.bytes)}`;
}

function folderCardMarkup(dir) {
  const meta = folderMeta(dir);
  return `<button class="folder" data-dir="${esc(dir.path)}">
      <span class="folder-ico">${brandIcon("explorer")}</span>
      <span class="folder-text">
        <span class="folder-name">${esc(dir.name)}</span>
        <span class="folder-meta">${esc(meta)}</span>
      </span>
    </button>`;
}

function folderRowMarkup(dir) {
  const meta = folderMeta(dir);
  return `<button class="folder-row" data-dir="${esc(dir.path)}">
      <span class="folder-row-ico">${brandIcon("explorer")}</span>
      <span class="folder-row-name">${esc(dir.name)}</span>
      <span class="folder-row-meta">${esc(meta)}</span>
    </button>`;
}

function renderFolders(dirs) {
  if (!dirs.length) return "";
  if (state.view === "list") {
    return `<div class="folder-list">${dirs.map(folderRowMarkup).join("")}</div>`;
  }
  return `<div class="folder-grid">${dirs.map(folderCardMarkup).join("")}</div>`;
}

function pickBoxMarkup(file) {
  if (!state.selectMode) return "";
  return `<span class="pick-box${state.selected.has(file.path) ? " on" : ""}" data-pick="${esc(file.path)}">${state.selected.has(file.path) ? icon("check") : ""}</span>`;
}

function fileRowMarkup(file) {
  const act = itemAction(file);
  const display = displayNameFor(file);
  const sub = display ? `<span class="file-row-sub">${esc(file.name)}</span>` : "";
  const pending = namePending(file) ? ` data-name-file="1"` : "";
  return `<div class="file-row${state.selectMode && state.selected.has(file.path) ? " picked" : ""}" data-file="${esc(file.path)}" data-filename="${esc(file.name)}"${pending}>
    ${pickBoxMarkup(file)}
    <div class="file-row-thumb">${thumbMarkup(file, true)}</div>
    <div class="file-row-label" title="${esc(file.path)}">
      <span class="file-row-name">${esc(display || file.name)}</span>${sub}
    </div>
    <div class="file-row-size">${formatBytes(file.size)}</div>
    <div class="file-row-pkg">${esc(file.package)}</div>
    <div class="file-row-action">
      <button class="zoom-btn ${act.name === "play" ? "play-btn" : ""}" type="button" data-action="${act.name}" title="${act.label}" aria-label="${act.label}">${icon(act.icon)}</button>
    </div>
  </div>`;
}

function fileCardMarkup(file) {
  const act = itemAction(file);
  const display = displayNameFor(file);
  const filenameLine = display ? `<div class="file-filename">${esc(file.name)}</div>` : "";
  const pending = namePending(file) ? ` data-name-file="1"` : "";
  return `<div class="file${state.selectMode && state.selected.has(file.path) ? " picked" : ""}" data-file="${esc(file.path)}" data-filename="${esc(file.name)}"${pending} title="${esc(file.path)}">
    ${thumbMarkup(file)}
    ${pickBoxMarkup(file)}
    <div class="file-info">
      <div class="file-text">
        <div class="file-name">${esc(display || file.name)}</div>
        ${filenameLine}
        <div class="file-meta">${esc(KIND_LABEL[displayKind(file.kind, file.ext)] || file.kind)} · ${formatBytes(file.size)}</div>
      </div>
      <button class="zoom-btn ${act.name === "play" ? "play-btn" : ""}" type="button" data-action="${act.name}" title="${act.label}" aria-label="${act.label}">${icon(act.icon)}</button>
    </div>
  </div>`;
}

function renderFiles(files) {
  if (!files.length) return "";
  if (state.view === "list") {
    return `<div class="file-list${state.selectMode ? " picking" : ""}">${files.map(fileRowMarkup).join("")}</div>`;
  }
  return `<div class="file-grid">${files.map(fileCardMarkup).join("")}</div>`;
}

function appendFiles(files) {
  if (!files.length) return;
  const container = document.querySelector("#content .file-list, #content .file-grid");
  if (!container) return;
  const markup = files.map(state.view === "list" ? fileRowMarkup : fileCardMarkup).join("");
  container.insertAdjacentHTML("beforeend", markup);
  const added = Array.from(container.querySelectorAll("[data-file]")).slice(-files.length);
  added.forEach((node) => fileNodeMap.set(node.dataset.file, node));
  watchNameNodes(added.filter((node) => node.hasAttribute("data-name-file")));
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
    absorbNames(more);
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
    setupMarquee($("content"));
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
  setupMarquee($("index-pane"));
}

async function loadStats() {
  try {
    const stats = await getJson("/api/stats");
    state.platform = stats.platform || "win32";
    if (stats.version) $("project-version").textContent = `v${stats.version}`;
    $("stat-files").textContent = Number(stats.files).toLocaleString();
    $("stat-size").textContent = formatBytes(stats.bytes);
    state.roots = stats.roots || [];
    renderRoots(state.roots);
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
  $("view-grid").setAttribute("aria-pressed", String(state.view === "grid"));
  $("view-list").setAttribute("aria-pressed", String(state.view === "list"));
  $("sort").value = state.sort;
  syncDropdown("sort");
}

async function loadList(path, push = true, keepKind = false, keepScroll = false) {
  const token = ++listToken;
  beginThumbEpoch();
  beginNameEpoch();
  if (push) pendingRestoreScroll = null;
  const previousPath = state.path;
  const previousScroll = window.scrollY;
  const pathChanged = (path || "") !== state.path;
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
  if (pathChanged && !keepScroll) window.scrollTo(0, 0);
  if (!keepKind) state.kind = "";
  state.previewPath = null;
  closeDrawer();
  if (push) {
    const url = new URL(location.href);
    if (state.path) url.searchParams.set("path", state.path);
    else url.searchParams.delete("path");
    history.replaceState({ path: previousPath, scroll: previousScroll }, "", location.href);
    history.pushState({ path: state.path, scroll: 0 }, "", url);
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
    absorbNames(state.files);
    state.dirs = data.dirs || [];
    state.dirSummary = data.dir_summary || null;
    state.kindCounts = data.kind_counts || {};
    state.fileTotal = Number(data.file_total ?? state.files.length);
    if (state.selectMode) {
      state.selected = new Set();
      closeBatchMenu();
      updateBatchBar();
    }
    renderCrumbs(data.breadcrumbs);
    $("where").textContent = state.path ? state.path.split("/").pop() : tr("index.all");
    updateCounts();
    fileNodeMap = new Map();
    $("content").innerHTML = renderFolders(state.dirs) + renderFiles(state.files) || `<div class="empty">${tr("list.empty")}</div>`;
    indexFileNodes($("content"));
    hydrateThumbs($("content"));
    hydrateNames($("content"));
    setupMarquee($("content"));
    setupMarquee($("content-topbar"));
    renderIndex(data);
    ensureSentinel();
    if (pendingRestoreScroll !== null) {
      const target = pendingRestoreScroll;
      pendingRestoreScroll = null;
      restoreScrollPosition(target);
    }
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

async function search(query, keepScroll = false) {
  const token = ++listToken;
  beginThumbEpoch();
  beginNameEpoch();
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
  if (!keepScroll) window.scrollTo(0, 0);
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
    absorbNames(state.files);
    state.dirs = [];
    state.kindCounts = data.kind_counts || {};
    state.searchTotal = Number(data.total ?? state.files.length);
    if (state.selectMode) {
      state.selected = new Set();
      closeBatchMenu();
      updateBatchBar();
    }
    renderCrumbs([]);
    updateCounts();
    fileNodeMap = new Map();
    $("content").innerHTML = renderFiles(state.files) || `<div class="empty">${tr("list.noMatches")}</div>`;
    indexFileNodes($("content"));
    hydrateThumbs($("content"));
    hydrateNames($("content"));
    setupMarquee($("content"));
    setupMarquee($("content-topbar"));
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

async function tryXnModule(file, container) {
  const input = container.querySelector("#xn-module");
  const note = container.querySelector("#xn-module-note");
  const button = container.querySelector("#xn-module-try");
  const module = input ? input.value.trim() : "";
  if (!module) {
    if (note) note.textContent = tr("xn.moduleRequired");
    if (input) input.focus();
    return;
  }
  if (button) button.disabled = true;
  if (note) note.textContent = tr("drawer.note.script");
  try {
    const data = await getJson(apiUrl("/api/xn", { path: file.path, module }));
    if (state.previewPath !== file.path) return;
    if (!data.decrypted) {
      if (note) note.textContent = backendText(data.reason, data.detail) || tr("xn.cannotDecrypt");
      return;
    }
    renderXnPreview(data, container, file);
  } catch (error) {
    if (note) note.textContent = error.message;
  } finally {
    if (button) button.disabled = false;
  }
}

function renderXnPreview(data, container, file) {
  const p = data.preview || {};
  if (!data.decrypted) {
    container.innerHTML = `<div class="xn-fail">
      <div class="xn-fail-title">${icon("code")}${tr("xn.title")}</div>
      <p>${esc(backendText(data.reason, data.detail) || tr("xn.cannotDecrypt"))}</p>
      <p class="note">${tr("xn.needModule")}</p>
      <div class="xn-module-row">
        <input id="xn-module" class="xn-module-input" type="text" placeholder="${tr("xn.modulePlaceholder")}" autocomplete="off" spellcheck="false">
        <button id="xn-module-try" class="mini-btn" type="button">${tr("xn.tryModule")}</button>
      </div>
      <p class="note" id="xn-module-note"></p>
    </div>`;
    const input = container.querySelector("#xn-module");
    const button = container.querySelector("#xn-module-try");
    if (button) button.addEventListener("click", () => tryXnModule(file, container));
    if (input) {
      input.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        tryXnModule(file, container);
      });
    }
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
  setupMarquee(container);
  startXnMarquee();
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
  setViewerTitle(file);
  $("viewer-stage").innerHTML = `<div class="viewer-loading"><span class="spinner"></span>${tr("drawer.note.script")}</div>`;
  $("viewer-foot").innerHTML = "";
  void node.offsetWidth;
  node.classList.add("open");
  setupMarquee($("viewer"));
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
  sprites: localStorage.getItem("npk-map-sprites") !== "0",
  is3d: localStorage.getItem("npk-map-3d") === "1",
  ignoreErrors: localStorage.getItem("npk-map-ignore-errors") !== "0",
  dragging: false, dragX: 0, dragY: 0, dragGrab: null,
  minimap: null, minimapCtx: null, minimapBox: null,
  minimapBase: null,
  icons: {}, modelIcons: {}, modelColors: {}, modelNames: {}, modelNameCount: 0, orderUnits: null,
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
    scheduleThumb(url, 0).then((blobUrl) => {
      if (!mapView.data) {
        URL.revokeObjectURL(blobUrl);
        return;
      }
      img.addEventListener("load", () => {
        URL.revokeObjectURL(blobUrl);
        mapView.icons[eid] = img;
        requestMapDraw();
      }, { once: true });
      img.addEventListener("error", () => URL.revokeObjectURL(blobUrl), { once: true });
      img.src = blobUrl;
    }).catch(() => {});
  }
}

function ensureMapModelIcons() {
  if (!mapView.data || (!mapView.sprites && !mapView.is3d)) return;
  const counts = new Map();
  for (const unit of mapView.data.units || []) {
    const model = unit.model;
    if (!model || !/\.(gim|mesh)$/i.test(model)) continue;
    counts.set(model, (counts.get(model) || 0) + 1);
  }
  const models = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 260).map(([model]) => model);
  for (const model of models) {
    if (mapView.modelIcons[model] !== undefined) continue;
    mapView.modelIcons[model] = null;
    const url = apiUrl("/api/thumb", { path: model, w: 96, v: 3 });
    scheduleThumb(url, 1).then((blobUrl) => {
      if (!mapView.data) {
        URL.revokeObjectURL(blobUrl);
        return;
      }
      const img = new Image();
      img.addEventListener("load", () => {
        URL.revokeObjectURL(blobUrl);
        mapView.modelIcons[model] = img;
        delete mapView.modelColors[model];
        map3DRefreshColors(model);
        requestMapDraw();
      }, { once: true });
      img.addEventListener("error", () => URL.revokeObjectURL(blobUrl), { once: true });
      img.src = blobUrl;
    }).catch(() => {});
  }
}

function ensureMapModelNames() {
  const data = mapView.data;
  if (!data) return;
  const models = [...new Set((data.units || []).map((unit) => unit.model).filter((model) => model && /\.(gim|mesh)$/i.test(model)))];
  if (models.length === mapView.modelNameCount) return;
  mapView.modelNameCount = models.length;
  mapView.modelNames = {};
  let cursor = 0;
  const worker = async () => {
    while (cursor < models.length) {
      const batch = models.slice(cursor, cursor + 64);
      cursor += 64;
      try {
        const query = batch.map((model) => `path=${encodeURIComponent(model)}`).join("&");
        const payload = await getJson(`/api/names?${query}`);
        if (mapView.data !== data) return;
        Object.assign(mapView.modelNames, payload.names || {});
      } catch (_) {}
    }
    if (mapView.data === data) buildMapLegend();
  };
  void Promise.all([worker(), worker()]);
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
  setViewerTitle(file);
  $("viewer-stage").innerHTML = "";
  $("viewer-foot").innerHTML = "";
  void node.offsetWidth;
  node.classList.add("open");
  setupMarquee($("viewer"));
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
  disposeMap3D();
  const viewerNode = $("viewer");
  if (viewerNode) viewerNode.classList.remove("map-3d");
  mapView.data = null;
  mapView.file = null;
  mapView.canvas = null;
  mapView.ctx = null;
  mapView.minimap = null;
  mapView.minimapCtx = null;
  mapView.minimapBox = null;
  mapView.minimapBase = null;
  mapView.icons = {};
  if (Object.keys(mapView.modelIcons).length > 800) {
    mapView.modelIcons = {};
    mapView.modelColors = {};
  }
  mapView.orderUnits = null;
  mapView.dragGrab = null;
  mapView.dragging = false;
  setViewerTitle(null);
  $("viewer-stage").innerHTML = "";
  $("viewer-foot").innerHTML = "";
  unlockViewerScroll();
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
    disposeGim3D();
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
    disposeGim3D();
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
    <img id="viewer-map-img" alt="" data-viewer-thumb="${esc(mapSource)}">
    <div class="viewer-map-view" id="viewer-map-view"></div>
  </div>`;
}

function openImageViewer(file, token, sourceUrl, mapUrl) {
  const stage = $("viewer-stage");
  const isMap = (file.ext || "").toLowerCase() === ".gmp";
  $("viewer-foot").innerHTML = imageViewerFoot(file, mapUrl);
  const mapThumb = $("viewer-map-img");
  if (mapThumb && mapThumb.dataset.viewerThumb) loadThumbImage(mapThumb, null, mapThumb.dataset.viewerThumb, true, 0);
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
  </div>`;
}

function textViewerMap() {
  return `<div class="viewer-map" id="viewer-map">
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
    stage.innerHTML = `<div class="text-viewer-layout"><div class="viewer-text-wrap" id="viewer-text-wrap"><pre class="viewer-text" id="viewer-text"></pre></div>${textViewerMap()}</div>`;
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
  const pal = canvasPalette();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = pal.textMap;
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

function mapHeight(unit) {
  const pos = unit.position || [];
  return Number(mapAxis() === "xy" ? pos[2] : pos[1]) || 0;
}

function unitFootprint(unit) {
  if (typeof unit._footprint === "number") return unit._footprint;
  const scale = unit.scale || [];
  const footprint = Math.sqrt(Math.abs((Number(scale[0]) || 1) * (Number(scale[2]) || 1))) || 1;
  unit._footprint = footprint;
  return footprint;
}

const MAP_ISO_COS = Math.cos(Math.PI / 6);
const MAP_SPRITE_BUDGET = 1600;

function mapProject(x, z, y = 0) {
  const scale = mapView.scale || 1;
  const cx = mapView.centerX;
  const cz = mapView.centerZ;
  let px;
  let py;
  if (mapView.is3d) {
    px = ((x - z) - (cx - cz)) * MAP_ISO_COS * scale;
    py = -(((x + z) - (cx + cz)) * 0.5 - y * 0.55) * scale;
  } else {
    px = (x - cx) * scale;
    py = -(z - cz) * scale;
  }
  return [mapView.width / 2 + px, mapView.height / 2 + py];
}

function mapUnproject(sx, sy) {
  const scale = mapView.scale || 1;
  const w = mapView.width;
  const h = mapView.height;
  const cx = mapView.centerX;
  const cz = mapView.centerZ;
  if (!mapView.is3d) {
    return [cx + (sx - w / 2) / scale, cz - (sy - h / 2) / scale];
  }
  const isoX = (cx - cz) * MAP_ISO_COS + (sx - w / 2) / scale;
  const isoY = (cx + cz) * 0.5 - (sy - h / 2) / scale;
  return [(isoX / MAP_ISO_COS + 2 * isoY) / 2, (2 * isoY - isoX / MAP_ISO_COS) / 2];
}

function focusMapPoint(x, z, sx, sy, scale) {
  const w = mapView.width;
  const h = mapView.height;
  if (!mapView.is3d) {
    mapView.centerX = x - (sx - w / 2) / scale;
    mapView.centerZ = z + (sy - h / 2) / scale;
    return;
  }
  const isoX = (x - z) * MAP_ISO_COS - (sx - w / 2) / scale;
  const isoY = (x + z) * 0.5 - (h / 2 - sy) / scale;
  mapView.centerX = (isoX / MAP_ISO_COS + 2 * isoY) / 2;
  mapView.centerZ = (2 * isoY - isoX / MAP_ISO_COS) / 2;
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
  mapView.modelNames = {};
  mapView.modelNameCount = 0;
  mapMessages.items = [];
  mapMessages.unread = 0;
  mapMessages.open = false;
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
    mapMsgAdd("error", tr("map.msg.read"), file.path, String(error.message || error));
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
  ensureMapModelIcons();
  ensureMapModelNames();
  updateMapMsgBadge();
  if (data.skipped_chunks) {
    mapMsgAdd("warn", tr("map.msg.decrypt"), tr("map.msg.chunks", { count: data.skipped_chunks }));
  }
  if (data.binary_chunks) {
    mapMsgAdd("warn", tr("map.msg.warning"), tr("map.msg.binary", { count: data.binary_chunks }));
  }
  updateMapMode();
  if (mapView.is3d) {
    drawMap();
    void openMap3D(file, data).then((ok) => {
      if (!ok) {
        updateMapMode();
        drawMap();
      }
    });
  } else {
    drawMap();
  }
}

function mapFoot() {
  return `<div class="viewer-zoom">
    <button class="icon-btn" id="map-zoom-out" title="${tr("map.zoomOut")}" aria-label="${tr("map.zoomOut")}">${icon("minus")}</button>
    <label class="viewer-zoom-value"><input id="map-zoom-input" type="number" min="5" max="2000" step="1" value="100"><span>%</span></label>
    <button class="icon-btn" id="map-zoom-in" title="${tr("map.zoomIn")}" aria-label="${tr("map.zoomIn")}">${icon("plus")}</button>
    <button class="mini-btn" id="map-zoom-fit" title="${tr("viewer.fit")}">${icon("fit")}${tr("map.fit")}</button>
    <label class="map-dot-control map-2d-only">${tr("map.dot")} <input id="map-dot-size" type="range" min="3" max="16" step="1" value="${mapView.dotSize}"></label>
    <label class="map-dot-control map-2d-only"><input id="map-sprites" type="checkbox" autocomplete="off" ${mapView.sprites ? "checked" : ""}>${tr("map.sprites")}</label>
    <label class="map-dot-control"><input id="map-ignore-errors" type="checkbox" autocomplete="off" ${mapView.ignoreErrors ? "checked" : ""}>${tr("map.ignoreErrors")}</label>
    <span class="view-switch map-view-switch" role="group" aria-label="2D/3D"><button class="icon-btn ${mapView.is3d ? "" : "active"}" id="map-mode-2d" type="button" aria-pressed="${mapView.is3d ? "false" : "true"}" title="${tr("map.mode2d")}">2D</button><button class="icon-btn ${mapView.is3d ? "active" : ""}" id="map-mode-3d" type="button" aria-pressed="${mapView.is3d ? "true" : "false"}" title="${tr("map.mode3d")}">3D</button></span>
    <span class="map-msg-wrap"><button class="map-msg-btn" id="map-msg-btn" type="button" title="${tr("map.messages")}" aria-label="${tr("map.messages")}">${icon("bell")}</button><span class="map-msg-badge hidden" id="map-msg-badge">0</span><div class="map-msg-panel hidden" id="map-msg-panel"></div></span>
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
    const projX = mapView.is3d ? (spanX + spanZ) * MAP_ISO_COS : spanX;
    const projZ = mapView.is3d ? (spanX + spanZ) * 0.5 : spanZ;
    mapView.fitScale = Math.max(0.02, Math.min(80, Math.min(Math.max(80, w - 120) / projX, Math.max(80, h - 120) / projZ)));
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
  if (map3d.gl) requestMap3DDraw();
  drawMap();
}

function updateMapHud() {
  const hud = $("map-hud");
  if (!hud) return;
  if (mapView.is3d) {
    const note = updateMap3DNote();
    const camera = map3d.camera;
    hud.textContent = note || `X ${camera.target[0].toFixed(1)} · Z ${camera.target[2].toFixed(1)}`;
    return;
  }
  const second = mapAxis() === "xz" ? "Z" : "Y";
  hud.textContent = `X ${mapView.centerX.toFixed(1)} · ${second} ${mapView.centerZ.toFixed(1)}`;
}

function updateMapZoomInput() {
  const input = $("map-zoom-input");
  if (!input || document.activeElement === input) return;
  if (mapView.is3d) {
    input.value = Math.round(((map3d.camera.fit || 1) / Math.max(0.5, map3d.camera.distance)) * 100);
    return;
  }
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
  const [worldX, worldZ] = mapUnproject(anchorX, anchorY);
  mapView.scale = scale;
  focusMapPoint(worldX, worldZ, anchorX, anchorY, scale);
  mapView.minimapBase = null;
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
  const pal = canvasPalette();
  ctx.setTransform(mapView.dpr, 0, 0, mapView.dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = pal.bg;
  ctx.fillRect(0, 0, w, h);

  const step = mapNiceStep(80 / scale);
  let minX;
  let maxX;
  let minZ;
  let maxZ;
  if (mapView.is3d) {
    const corners = [[0, 0], [w, 0], [0, h], [w, h]].map(([sx, sy]) => mapUnproject(sx, sy));
    const xs = corners.map((point) => point[0]);
    const zs = corners.map((point) => point[1]);
    minX = Math.min(...xs);
    maxX = Math.max(...xs);
    minZ = Math.min(...zs);
    maxZ = Math.max(...zs);
  } else {
    const halfW = w / (2 * scale);
    const halfH = h / (2 * scale);
    minX = mapView.centerX - halfW;
    maxX = mapView.centerX + halfW;
    minZ = mapView.centerZ - halfH;
    maxZ = mapView.centerZ + halfH;
  }

  ctx.strokeStyle = pal.grid;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = Math.ceil(minX / step) * step; x <= maxX; x += step) {
    const [ax, ay] = mapProject(x, minZ);
    const [bx, by] = mapProject(x, maxZ);
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
  }
  for (let z = Math.ceil(minZ / step) * step; z <= maxZ; z += step) {
    const [ax, ay] = mapProject(minX, z);
    const [bx, by] = mapProject(maxX, z);
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
  }
  ctx.stroke();

  drawMapUnits(ctx, w, h, scale);

  ctx.strokeStyle = pal.crosshair;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(w / 2 - 8, h / 2);
  ctx.lineTo(w / 2 + 8, h / 2);
  ctx.moveTo(w / 2, h / 2 - 8);
  ctx.lineTo(w / 2, h / 2 + 8);
  ctx.stroke();

  if (!mapView.is3d) {
    drawMapRulers(ctx, w, h, scale, step, minX, maxX, minZ, maxZ);
  }
  drawMapMinimap();
}

function drawMapUnits(ctx, w, h, scale) {
  let units = mapUnits();
  if (!units.length) return;
  if (mapView.is3d) {
    if (!mapView.orderUnits || mapView.orderUnits.length !== units.length) {
      mapView.orderUnits = units.slice().sort((a, b) => {
        const [ax, az] = mapPoint(a);
        const [bx, bz] = mapPoint(b);
        return (ax + az) - (bx + bz);
      });
    }
    units = mapView.orderUnits;
  }
  const pal = canvasPalette();
  const radius = Math.max(3, Math.min(16, mapView.dotSize));
  const zoomK = Math.max(0.7, Math.min(2, scale / (mapView.fitScale || scale)));
  const visible = [];
  for (const unit of units) {
    const [x, z] = mapPoint(unit);
    const [sx, sy] = mapView.is3d ? mapProject(x, z, mapHeight(unit)) : mapProject(x, z);
    if (sx < -60 || sx > w + 60 || sy < -60 || sy > h + 60) continue;
    visible.push([unit, sx, sy]);
  }
  let spriteIds = null;
  if (mapView.sprites) {
    const budget = Math.round(MAP_SPRITE_BUDGET * Math.min(2.5, Math.max(1, zoomK)));
    if (visible.length <= budget) {
      spriteIds = null;
    } else {
      spriteIds = new Set(
        visible
          .slice()
          .sort((a, b) => unitFootprint(b[0]) - unitFootprint(a[0]))
          .slice(0, budget)
          .map(([unit]) => unit.id),
      );
    }
  }
  const boxes = [];
  let labels = 0;
  ctx.font = canvasFont(11);
  ctx.textBaseline = "middle";
  for (const [unit, sx, sy] of visible) {
    const icon = mapView.icons[unit.eid];
    const iconSize = Math.max(18, radius * 2.6);
    const sprite = mapView.sprites && (!spriteIds || spriteIds.has(unit.id)) ? mapView.modelIcons[unit.model] : null;
    let marker = radius;
    if (sprite) {
      const footprint = unitFootprint(unit);
      const size = Math.max(10, Math.min(56, radius * 2.6 * Math.max(0.6, Math.min(2.2, footprint)) * Math.sqrt(zoomK)));
      marker = size / 2;
      ctx.drawImage(sprite, sx - size / 2, sy - size / 2, size, size);
    } else if (icon) {
      ctx.beginPath();
      ctx.arc(sx, sy, iconSize / 2 + 1.5, 0, Math.PI * 2);
      ctx.fillStyle = pal.chip;
      ctx.fill();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = mapColor(unit.eid ?? unit.type ?? 0);
      ctx.stroke();
      ctx.drawImage(icon, sx - iconSize / 2, sy - iconSize / 2, iconSize, iconSize);
      marker = iconSize / 2;
    } else {
      const dotRadius = mapView.sprites ? Math.max(2, radius * 0.55) : radius;
      ctx.beginPath();
      ctx.arc(sx, sy, dotRadius, 0, Math.PI * 2);
      ctx.fillStyle = mapUnitColor(unit);
      ctx.fill();
      if (!mapView.sprites) {
        ctx.lineWidth = 2;
        ctx.strokeStyle = mapColor(unit.eid ?? unit.type ?? 0);
        ctx.stroke();
      }
      marker = dotRadius;
    }
    const name = unit.name || "";
    if (!name || labels >= 120) continue;
    if (zoomK < 1.2 && units.length > 800) continue;
    const width = ctx.measureText(name).width;
    const lx = sx + marker + 3;
    const ly = sy;
    if (lx + width > w - 4 || ly < 28 || ly > h - 6) continue;
    const box = [lx, ly - 7, lx + width, ly + 7];
    if (boxes.some((other) => box[0] < other[2] && box[2] > other[0] && box[1] < other[3] && box[3] > other[1])) continue;
    ctx.strokeStyle = pal.labelShadow;
    ctx.lineWidth = 3;
    ctx.strokeText(name, lx, ly);
    ctx.fillStyle = pal.text;
    ctx.fillText(name, lx, ly);
    boxes.push(box);
    labels += 1;
  }
}

function drawMapRulers(ctx, w, h, scale, step, minX, maxX, minZ, maxZ) {
  const top = 24;
  const left = 44;
  const pal = canvasPalette();
  ctx.fillStyle = pal.rulerBg;
  ctx.fillRect(0, 0, w, top);
  ctx.fillRect(0, 0, left, h);
  ctx.strokeStyle = pal.rulerLine;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, top + 0.5);
  ctx.lineTo(w, top + 0.5);
  ctx.moveTo(left + 0.5, 0);
  ctx.lineTo(left + 0.5, h);
  ctx.stroke();
  ctx.font = canvasFont(10);
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.fillStyle = pal.text;
  ctx.fillText(`X / ${mapAxis() === "xz" ? "Z" : "Y"}`, 6, top / 2);
  ctx.fillStyle = pal.muted;
  for (let x = Math.ceil(minX / step) * step; x <= maxX; x += step) {
    const sx = w / 2 + (x - mapView.centerX) * scale;
    if (sx < left) continue;
    ctx.strokeStyle = pal.line;
    ctx.beginPath();
    ctx.moveTo(sx, 0);
    ctx.lineTo(sx, 6);
    ctx.stroke();
    ctx.fillText(mapFormat(x, step), sx + 3, top / 2 + 2);
  }
  for (let z = Math.ceil(minZ / step) * step; z <= maxZ; z += step) {
    const sy = h / 2 - (z - mapView.centerZ) * scale;
    if (sy < top) continue;
    ctx.strokeStyle = pal.line;
    ctx.beginPath();
    ctx.moveTo(0, sy);
    ctx.lineTo(4, sy);
    ctx.stroke();
    ctx.fillText(mapFormat(z, step), 12, sy);
  }
  ctx.textAlign = "left";
}

function buildMapMinimapBase() {
  const pal = canvasPalette();
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
  ctx.fillStyle = pal.bg;
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
  ctx.fillStyle = pal.points;
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
  const pal = canvasPalette();
  const box = base.box;
  mapView.minimapBox = box;
  const viewW = mapView.width / (mapView.scale || 1);
  const viewH = mapView.height / (mapView.scale || 1);
  const vx = box.left + (mapView.centerX - viewW / 2 - box.minX) * box.ms;
  const vy = box.top + (box.maxZ - (mapView.centerZ + viewH / 2)) * box.ms;
  ctx.fillStyle = pal.accentFill;
  ctx.strokeStyle = pal.accent;
  ctx.lineWidth = 1;
  ctx.fillRect(vx, vy, viewW * box.ms, viewH * box.ms);
  ctx.strokeRect(vx + 0.5, vy + 0.5, Math.max(1, viewW * box.ms - 1), Math.max(1, viewH * box.ms - 1));
}

function bindMapControls(file) {
  const canvas = mapView.canvas;
  if (!canvas) return;
  const zoomBy = (factor) => {
    if (mapView.is3d && map3d.ready) {
      map3d.camera.distance = Math.max(0.5, Math.min(map3d.camera.fit * 40, map3d.camera.distance / factor));
      updateMapZoomInput();
      updateMapHud();
      requestMap3DDraw();
      return;
    }
    setMapScale(mapView.scale * factor);
  };
  $("map-zoom-in").addEventListener("click", () => zoomBy(1.25));
  $("map-zoom-out").addEventListener("click", () => zoomBy(1 / 1.25));
  $("map-zoom-fit").addEventListener("click", () => {
    if (mapView.is3d && map3d.ready) {
      fitMap3D(mapView.data);
      return;
    }
    fitMapView();
    resizeMapCanvas();
  });
  const zoomInput = $("map-zoom-input");
  zoomInput.addEventListener("change", () => {
    const value = Number(zoomInput.value);
    if (!(Number.isFinite(value) && value > 0)) {
      updateMapZoomInput();
      return;
    }
    if (mapView.is3d && map3d.ready) {
      map3d.camera.distance = (map3d.camera.fit || 1) * 100 / value;
      updateMapZoomInput();
      updateMapHud();
      requestMap3DDraw();
      return;
    }
    setMapScale(mapView.fitScale * value / 100);
  });
  zoomInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      zoomInput.blur();
    }
  });
  const dot = $("map-dot-size");
  if (dot) {
    dot.value = String(mapView.dotSize);
    dot.addEventListener("input", () => {
      mapView.dotSize = Math.max(3, Math.min(16, Number(dot.value) || 6));
      localStorage.setItem("npk-map-dot-size", String(mapView.dotSize));
      updateMapLegendDots();
      requestMapDraw();
    });
  }
  const sprites = $("map-sprites");
  if (sprites) {
    sprites.addEventListener("change", () => {
      mapView.sprites = sprites.checked;
      localStorage.setItem("npk-map-sprites", mapView.sprites ? "1" : "0");
      if (mapView.sprites) ensureMapModelIcons();
      requestMapDraw();
    });
  }
  const ignore = $("map-ignore-errors");
  if (ignore) {
    ignore.addEventListener("change", () => {
      mapView.ignoreErrors = ignore.checked;
      localStorage.setItem("npk-map-ignore-errors", mapView.ignoreErrors ? "1" : "0");
      if (mapView.is3d) {
        rebuildMapPoints();
        requestMap3DDraw();
      }
    });
  }
  const mode2d = $("map-mode-2d");
  if (mode2d) mode2d.addEventListener("click", () => void setMapMode(false));
  const mode3d = $("map-mode-3d");
  if (mode3d) mode3d.addEventListener("click", () => void setMapMode(true));
  const messages = $("map-msg-btn");
  if (messages) messages.addEventListener("click", toggleMapMsgPanel);
  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const rect = canvas.getBoundingClientRect();
    mapView.dragging = true;
    mapView.dragGrab = mapUnproject(event.clientX - rect.left, event.clientY - rect.top);
    mapView.dragX = event.clientX;
    mapView.dragY = event.clientY;
    canvas.classList.add("dragging");
    try { canvas.setPointerCapture(event.pointerId); } catch (_) {}
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!mapView.dragging || !mapView.dragGrab) return;
    const rect = canvas.getBoundingClientRect();
    focusMapPoint(mapView.dragGrab[0], mapView.dragGrab[1], event.clientX - rect.left, event.clientY - rect.top, mapView.scale || 1);
    updateMapHud();
    requestMapDraw();
  });
  const endDrag = (event) => {
    if (!mapView.dragging) return;
    mapView.dragging = false;
    mapView.dragGrab = null;
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

const mapMessages = { items: [], unread: 0, open: false };

function updateMapMsgBadge() {
  const badge = $("map-msg-badge");
  if (!badge) return;
  badge.textContent = String(mapMessages.unread);
  badge.classList.toggle("hidden", mapMessages.unread <= 0);
}

function renderMapMsgPanel() {
  const panel = $("map-msg-panel");
  if (!panel) return;
  const rows = mapMessages.items.slice().reverse().map((item) => (
    `<div class="map-msg-row" data-level="${item.level}"><span class="map-msg-tag">${esc(item.tag)}</span><span class="map-msg-text"><b>${esc(item.title)}</b>${item.detail ? `<br>${esc(item.detail)}` : ""}</span></div>`
  )).join("");
  panel.innerHTML = `<div class="map-msg-head"><span>${tr("map.messages")}</span><button type="button" id="map-msg-clear">${tr("map.messages.clear")}</button></div>${rows || `<div class="map-msg-empty">${tr("map.messages.empty")}</div>`}`;
  const clear = panel.querySelector("#map-msg-clear");
  if (clear) {
    clear.addEventListener("click", () => {
      mapMessages.items = [];
      mapMessages.unread = 0;
      updateMapMsgBadge();
      renderMapMsgPanel();
    });
  }
}

function mapMsgAdd(level, tag, title, detail = "") {
  if (mapMessages.items.some((item) => item.tag === tag && item.title === title && item.detail === detail)) return;
  mapMessages.items.push({ level, tag, title, detail });
  if (mapMessages.items.length > 200) mapMessages.items.shift();
  mapMessages.unread += 1;
  updateMapMsgBadge();
  if (mapMessages.open) renderMapMsgPanel();
}

function toggleMapMsgPanel() {
  const panel = $("map-msg-panel");
  if (!panel) return;
  mapMessages.open = !mapMessages.open;
  panel.classList.toggle("hidden", !mapMessages.open);
  if (mapMessages.open) {
    mapMessages.unread = 0;
    updateMapMsgBadge();
    renderMapMsgPanel();
  }
}

const MAP_MESH_LIMIT = 240;
const map3d = {
  canvas: null, gl: null, program: null, pointProgram: null, lineProgram: null,
  uViewProj: null, uPointView: null, uPointSize: null, uLineView: null, uLineColor: null,
  uniforms: {}, textureCache: new Map(), texturePending: new Map(),
  models: new Map(), units: [], points: null, pointCount: 0, grid: null, gridCount: 0, hiddenModels: new Set(),
  camera: { yaw: 0.9, pitch: 0.9, distance: 100, fit: 100, target: [0, 0, 0] },
  dragging: false, panning: false, dragX: 0, dragY: 0,
  frame: 0, token: 0, loading: 0, total: 0, loadErrors: 0, note: "", ready: false, data: null,
};

const MAP3D_MESH_VS = `#version 300 es
layout(location = 0) in vec3 a_pos;
layout(location = 1) in vec3 a_norm;
layout(location = 2) in mat4 a_model;
layout(location = 6) in vec4 a_paint1;
layout(location = 7) in vec2 a_uv;
layout(location = 8) in vec4 a_paint2;
layout(location = 9) in vec4 a_paint3;
layout(location = 10) in vec4 a_tangent;
uniform mat4 u_view_proj;
out vec3 v_norm;
out vec3 v_local;
out vec2 v_uv;
out vec4 v_paint1;
out vec4 v_paint2;
out vec4 v_paint3;
out vec3 v_tangent;
out vec3 v_bitangent;
out float v_light;
void main() {
  mat3 rot = mat3(a_model);
  mat3 nmat = transpose(inverse(rot));
  v_norm = normalize(nmat * a_norm);
  vec3 tangent = normalize(nmat * a_tangent.xyz);
  v_tangent = tangent;
  v_bitangent = cross(v_norm, tangent) * a_tangent.w;
  v_local = a_pos;
  v_uv = vec2(a_uv.x, 1.0 - a_uv.y);
  v_paint1 = a_paint1;
  v_paint2 = a_paint2;
  v_paint3 = a_paint3;
  v_light = 0.55 + 0.5 * abs(dot(v_norm, normalize(vec3(0.45, 0.8, 0.35))));
  gl_Position = u_view_proj * a_model * vec4(a_pos, 1.0);
}`;

const MAP3D_MESH_FS = `#version 300 es
precision highp float;
in vec3 v_norm;
in vec3 v_local;
in vec2 v_uv;
in vec4 v_paint1;
in vec4 v_paint2;
in vec4 v_paint3;
in vec3 v_tangent;
in vec3 v_bitangent;
in float v_light;
uniform sampler2D u_tex0;
uniform sampler2D u_tex1;
uniform sampler2D u_tex2;
uniform sampler2D u_tex3;
uniform float u_has_tex0;
uniform float u_has_tex1;
uniform float u_has_tex2;
uniform float u_has_tex3;
uniform vec4 u_pattern_color;
uniform float u_pattern_mode;
uniform float u_color_mode;
uniform float u_axis;
uniform float u_lerp_center;
uniform float u_lerp_contrast;
uniform vec4 u_mask0;
uniform vec4 u_mask1;
uniform vec4 u_mask2;
uniform vec4 u_mask3;
uniform vec4 u_base_color;
uniform float u_tint_value;
uniform float u_normal_intensity;
uniform float u_opacity_tex;
uniform float u_alpha_ref;
uniform float u_unlit;
uniform vec4 u_texture_para;
uniform float u_rotation_angle;
uniform float u_tiling;
uniform float u_repeat_scale;
uniform vec3 u_bound_min;
uniform vec3 u_bound_half;
uniform vec3 u_fallback;
uniform float u_use_paint;
uniform float u_lerp_linear;
uniform float u_emissive_kind;
uniform vec4 u_emissive_color;
uniform vec4 u_ei;
uniform float u_intensity_a;
uniform float u_glass;
uniform float u_glass_opacity;
out vec4 out_color;
vec3 mapSrgb2Lin(vec3 c) {
  return pow(max(c, vec3(0.0)), vec3(2.2));
}
vec3 mapLin2Srgb(vec3 c) {
  return pow(max(c, vec3(0.0)), vec3(0.4545));
}
void main() {
  vec2 uv = v_uv;
  uv -= 0.5;
  uv *= u_texture_para.xy;
  float texture_angle = radians(u_rotation_angle);
  if (abs(texture_angle) > 0.0001) {
    float s = sin(texture_angle), c = cos(texture_angle);
    uv = vec2(uv.x * c - uv.y * s, uv.x * s + uv.y * c);
  }
  uv += 0.5;
  uv += u_texture_para.zw;
  if (u_tiling > 0.5) {
    vec3 an = abs(normalize(v_norm));
    vec3 p = v_local / 2.64 * u_repeat_scale;
    if (an.x >= an.y && an.x >= an.z) uv = p.yz;
    else if (an.y >= an.z) uv = p.xz;
    else uv = p.yx;
  }
  vec4 tex0 = u_has_tex0 > 0.5 ? texture(u_tex0, uv) : vec4(1.0);
  vec4 tex1 = u_has_tex1 > 0.5 ? texture(u_tex1, uv) : vec4(0.5, 0.5, 1.0, 1.0);
  vec4 tex2 = u_has_tex2 > 0.5 ? texture(u_tex2, uv) : vec4(0.0, 0.5, 0.0, 0.0);
  vec4 tex3s = u_has_tex3 > 0.5 ? texture(u_tex3, uv) : vec4(0.0);
  vec4 m0 = u_use_paint > 0.5 && v_paint1.r >= 0.0 ? vec4(v_paint1.rgb, 1.0) : u_mask0;
  vec4 m1 = u_use_paint > 0.5 && v_paint2.r >= 0.0 ? vec4(v_paint2.rgb, 1.0) : u_mask1;
  vec4 m2 = u_use_paint > 0.5 && v_paint3.r >= 0.0 ? vec4(v_paint3.rgb, 1.0) : u_mask2;
  vec3 color;
  bool color_linear = false;
  float alpha;
  if (u_glass > 0.5) {
    color = u_base_color.rgb * u_tint_value;
    alpha = u_base_color.a * v_paint1.a * u_glass_opacity;
  } else {
    vec3 painted = u_use_paint > 0.5 && v_paint1.r >= 0.0 ? v_paint1.rgb : u_fallback;
    color = painted;
    if (u_color_mode > 3.5) {
      color = u_has_tex0 > 0.5 ? mix(m0.rgb, m1.rgb, tex0.g) : painted;
      color = mix(color, m2.rgb, tex0.b);
      color = mix(color, u_mask3.rgb, tex0.a);
    } else if (u_color_mode > 2.5) {
      color = u_has_tex0 > 0.5 ? tex0.r * m0.rgb + tex0.g * m1.rgb + tex0.b * m2.rgb + tex0.a * u_mask3.rgb : painted;
    } else if (u_color_mode > 1.5) {
      vec3 uvw = abs((v_local - u_bound_min) / max(u_bound_half * 2.0, vec3(0.0001)));
      float axis_value = u_axis < 0.5 ? uvw.x : (u_axis < 1.5 ? uvw.y : uvw.z);
      float final_axis = smoothstep(-0.5 + u_lerp_contrast, 1.5001 - u_lerp_contrast, axis_value - u_lerp_center * 0.5);
      final_axis *= final_axis;
      color = mix(mapSrgb2Lin(m0.rgb), mapSrgb2Lin(m1.rgb), final_axis * (tex0.r * 0.5 + 0.5));
      color = mix(color, mapSrgb2Lin(m2.rgb), (1.0 - final_axis) * tex0.g);
      color = mix(color, mapSrgb2Lin(u_mask3.rgb), tex0.b);
      color_linear = true;
    } else if (u_color_mode > 0.5) {
      if (u_lerp_linear > 0.5) {
        color = mix(mapSrgb2Lin(m0.rgb), mapSrgb2Lin(m1.rgb), tex0.r);
        color = mix(color, mapSrgb2Lin(m2.rgb), tex0.g);
        color = mix(color, mapSrgb2Lin(u_mask3.rgb), tex0.b);
        color_linear = true;
      } else {
        color = mix(m0.rgb, m1.rgb, tex0.r);
        color = mix(color, m2.rgb, tex0.g);
        color = mix(color, u_mask3.rgb, tex0.b);
      }
    } else if (u_has_tex0 > 0.5) {
      color = tex0.rgb;
    }
    color *= u_base_color.rgb * u_tint_value;
    alpha = u_has_tex0 > 0.5 ? tex0.a : 1.0;
    if (u_opacity_tex > 1.5) alpha = 1.0;
    else if (u_opacity_tex > 0.5) alpha = tex1.a;
    alpha *= u_base_color.a * v_paint1.a;
  }
  vec3 albedo_lin = color_linear ? max(color, vec3(0.0)) : mapSrgb2Lin(color);
  if (u_pattern_mode > 0.5 && u_has_tex3 > 0.5) {
    vec3 an = abs(normalize(v_norm));
    vec3 p = v_local * u_repeat_scale;
    vec2 puv;
    if (an.x >= an.y && an.x >= an.z) puv = p.yz;
    else if (an.y >= an.z) puv = p.xz;
    else puv = p.yx;
    puv *= u_texture_para.xy;
    vec4 pattern = texture(u_tex3, puv);
    vec3 tiling_col = mapSrgb2Lin(pattern.r * u_pattern_color.rgb);
    albedo_lin = mix(albedo_lin, tiling_col, pattern.r * u_pattern_color.a);
  }
  vec3 emissive = vec3(0.0);
  if (u_emissive_kind > 0.5 && u_emissive_kind < 1.5) {
    emissive = mapSrgb2Lin(tex2.a * u_emissive_color.rgb * u_intensity_a);
  } else if (u_emissive_kind > 1.5 && u_emissive_kind < 2.5) {
    emissive = mapSrgb2Lin(tex2.a * u_emissive_color.rgb) * u_intensity_a;
  } else if (u_emissive_kind > 2.5 && u_emissive_kind < 3.5) {
    vec4 chain = mix(u_mask0 * u_ei.r, u_mask1 * u_ei.g, tex0.r);
    chain = mix(chain, u_mask2 * u_ei.b, tex0.g);
    chain = mix(chain, u_mask3 * u_ei.a, tex0.b);
    emissive = tex2.a * chain.rgb + u_emissive_color.rgb * u_intensity_a;
  } else if (u_emissive_kind > 3.5 && u_emissive_kind < 4.5) {
    vec3 chain = mix(vec3(u_ei.r), vec3(u_ei.g), tex0.r);
    chain = mix(chain, vec3(u_ei.b), tex0.g);
    chain = mix(chain, vec3(u_ei.a), tex0.b);
    emissive = tex2.a * chain + u_emissive_color.rgb * u_intensity_a;
  } else if (u_emissive_kind > 4.5 && u_emissive_kind < 5.5) {
    vec3 chain = mix(vec3(u_ei.r), vec3(u_ei.g), tex0.r);
    chain = mix(chain, vec3(u_ei.b), tex0.g);
    chain = mix(chain, vec3(u_ei.a), tex0.b);
    emissive = tex2.a * albedo_lin * chain + u_emissive_color.rgb * u_intensity_a;
  } else if (u_emissive_kind > 5.5 && u_emissive_kind < 6.5) {
    vec3 chain = tex0.r * m0.rgb * u_ei.r + tex0.g * m1.rgb * u_ei.g + tex0.b * m2.rgb * u_ei.b + tex0.a * u_mask3.rgb * u_ei.a;
    emissive = tex2.a * chain + u_emissive_color.rgb * u_intensity_a;
  } else if (u_emissive_kind > 7.5 && u_emissive_kind < 8.5) {
    emissive = u_emissive_color.rgb * (tex2.a * u_intensity_a * u_tint_value);
  } else if (u_emissive_kind > 8.5 && u_emissive_kind < 9.5) {
    vec3 e = mapSrgb2Lin(tex3s.rgb * tex3s.a * u_intensity_a);
    emissive = e;
    albedo_lin += e;
  }
  if (alpha < u_alpha_ref) discard;
  vec3 normal = normalize(v_norm);
  float light = u_unlit > 0.5 ? 1.0 : v_light;
  if (u_has_tex1 > 0.5 && u_unlit < 0.5) {
    vec3 n = tex1.xyz * 2.0 - 1.0;
    if (dot(n, n) < 0.001) n = vec3(0.0, 0.0, 1.0);
    n.xy *= u_normal_intensity;
    normal = normalize(v_tangent * n.x + v_bitangent * n.y + v_norm * n.z);
    light = 0.55 + 0.5 * abs(dot(normal, normalize(vec3(0.45, 0.8, 0.35))));
  }
  vec3 final_lin = albedo_lin * light + emissive;
  out_color = vec4(mapLin2Srgb(final_lin), clamp(alpha, 0.0, 1.0));
}`;

const MAP3D_POINT_VS = `#version 300 es
layout(location = 0) in vec3 a_pos;
layout(location = 1) in vec4 a_color;
uniform mat4 u_view_proj;
uniform float u_point_size;
out vec4 v_color;
void main() {
  gl_Position = u_view_proj * vec4(a_pos, 1.0);
  gl_PointSize = u_point_size;
  v_color = a_color;
}`;

const MAP3D_POINT_FS = `#version 300 es
precision mediump float;
in vec4 v_color;
out vec4 out_color;
void main() {
  vec2 d = gl_PointCoord - vec2(0.5);
  if (dot(d, d) > 0.25) discard;
  out_color = v_color;
}`;

const MAP3D_LINE_VS = `#version 300 es
layout(location = 0) in vec3 a_pos;
uniform mat4 u_view_proj;
uniform vec4 u_color;
out vec4 v_color;
void main() {
  gl_Position = u_view_proj * vec4(a_pos, 1.0);
  v_color = u_color;
}`;

const MAP3D_LINE_FS = `#version 300 es
precision mediump float;
in vec4 v_color;
out vec4 out_color;
void main() {
  out_color = v_color;
}`;

function map3DCompile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader) || "shader compile failed";
    gl.deleteShader(shader);
    throw new Error(log);
  }
  return shader;
}

function map3DProgram(gl, vsSource, fsSource) {
  const vs = map3DCompile(gl, gl.VERTEX_SHADER, vsSource);
  const fs = map3DCompile(gl, gl.FRAGMENT_SHADER, fsSource);
  const program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const log = gl.getProgramInfoLog(program) || "program link failed";
    gl.deleteProgram(program);
    throw new Error(log);
  }
  return program;
}

function map3DEulerQuat(rx, ry, rz) {
  const x = rx * Math.PI / 180;
  const y = ry * Math.PI / 180;
  const z = rz * Math.PI / 180;
  const c1 = Math.cos(x / 2), s1 = Math.sin(x / 2);
  const c2 = Math.cos(y / 2), s2 = Math.sin(y / 2);
  const c3 = Math.cos(z / 2), s3 = Math.sin(z / 2);
  return [
    s1 * c2 * c3 - c1 * s2 * s3,
    c1 * s2 * c3 + s1 * c2 * s3,
    c1 * c2 * s3 - s1 * s2 * c3,
    c1 * c2 * c3 + s1 * s2 * s3,
  ];
}

function map3DUnitMatrix(unit) {
  const position = unit.position || [0, 0, 0];
  const scale = unit.scale || [1, 1, 1];
  const angle = unit.angle || [0, 0, 0];
  const quat = map3DEulerQuat(Number(angle[0]) || 0, Number(angle[1]) || 0, Number(angle[2]) || 0);
  return gimTrsMatrix([
    Number(position[0]) || 0, Number(position[1]) || 0, Number(position[2]) || 0,
    quat[0], quat[1], quat[2], quat[3],
    Number(scale[0]) || 1, Number(scale[1]) || 1, Number(scale[2]) || 1,
  ]);
}

function map3DModelColor(model) {
  if (mapView.modelColors[model]) return mapView.modelColors[model];
  const image = mapView.modelIcons[model];
  if (!image) return [0.62, 0.7, 0.74, 1];
  try {
    const canvas = document.createElement("canvas");
    canvas.width = 8;
    canvas.height = 8;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(image, 0, 0, 8, 8);
    const data = ctx.getImageData(0, 0, 8, 8).data;
    let r = 0, g = 0, b = 0, n = 0;
    for (let index = 0; index < data.length; index += 4) {
      if (data[index + 3] < 32) continue;
      r += data[index];
      g += data[index + 1];
      b += data[index + 2];
      n += 1;
    }
    if (n) {
      mapView.modelColors[model] = [r / n / 255, g / n / 255, b / n / 255, 1];
      return mapView.modelColors[model];
    }
  } catch (_) {}
  return [0.62, 0.7, 0.74, 1];
}

function map3DUnitColor(unit) {
  const color = unit.color;
  if (Array.isArray(color) && color.length >= 3) {
    const r = Number(color[0]) || 0;
    const g = Number(color[1]) || 0;
    const b = Number(color[2]) || 0;
    if (r + g + b > 30) return [r / 255, g / 255, b / 255, 1];
  }
  const fallback = map3DModelColor(unit.model);
  return [fallback[0], fallback[1], fallback[2], 0.5];
}

function map3DUnitPaint(unit) {
  const raw = unit.alpha === null || unit.alpha === undefined ? NaN : Number(unit.alpha);
  const alpha = Number.isFinite(raw) ? Math.max(0, Math.min(1, raw)) : 1;
  const paints = Array.isArray(unit.paint) ? unit.paint : [unit.color, null, null];
  const out = [];
  for (let index = 0; index < 3; index += 1) {
    const color = paints[index];
    const r = Array.isArray(color) ? Number(color[0]) || 0 : 0;
    const g = Array.isArray(color) ? Number(color[1]) || 0 : 0;
    const b = Array.isArray(color) ? Number(color[2]) || 0 : 0;
    out.push(r + g + b > 30 ? [r / 255, g / 255, b / 255, alpha] : [-1, -1, -1, alpha]);
  }
  return out;
}

function map3DCameraEye() {
  const camera = map3d.camera;
  return [
    camera.target[0] + camera.distance * Math.cos(camera.pitch) * Math.sin(camera.yaw),
    camera.target[1] + camera.distance * Math.sin(camera.pitch),
    camera.target[2] + camera.distance * Math.cos(camera.pitch) * Math.cos(camera.yaw),
  ];
}

function map3DViewProj() {
  const camera = map3d.camera;
  const eye = map3DCameraEye();
  const view = gimLookAt(eye, camera.target, [0, 1, 0]);
  const proj = gimPerspective(Math.PI / 4, (map3d.canvas.width || 1) / (map3d.canvas.height || 1), Math.max(0.5, camera.distance * 0.02), camera.distance * 8);
  return gimMultiply(proj, view);
}

function resizeMap3D() {
  const canvas = map3d.canvas;
  if (!canvas || !mapView.stage) return;
  const width = Math.max(1, mapView.stage.clientWidth);
  const height = Math.max(1, mapView.stage.clientHeight);
  const dpr = Math.min(2, Math.max(1.5, window.devicePixelRatio || 1));
  const scaledWidth = Math.round(width * dpr);
  const scaledHeight = Math.round(height * dpr);
  if (canvas.width === scaledWidth && canvas.height === scaledHeight) return;
  canvas.width = scaledWidth;
  canvas.height = scaledHeight;
}

function fitMap3D(data) {
  const bounds = data?.bounds;
  const camera = map3d.camera;
  if (bounds) {
    const cx = (bounds.min_x + bounds.max_x) / 2;
    const cz = (bounds.min_y + bounds.max_y) / 2;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const unit of map3d.units) {
      const position = unit.position || [];
      const x = Number(position[0]);
      const y = Number(position[1]);
      const z = Number(position[2]);
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue;
      if (x < bounds.min_x - 4 || x > bounds.max_x + 4 || z < bounds.min_y - 4 || z > bounds.max_y + 4) continue;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
    if (!Number.isFinite(minY) || !Number.isFinite(maxY)) {
      minY = 0;
      maxY = 0;
    }
    const spanXZ = Math.max(1, bounds.max_x - bounds.min_x, bounds.max_y - bounds.min_y);
    const spanY = Math.max(1, maxY - minY);
    const span = Math.max(spanXZ, spanY * 2);
    camera.target = [cx, (minY + maxY) / 2, cz];
    camera.distance = span * 1.8;
  } else {
    camera.target = [0, 0, 0];
    camera.distance = 100;
  }
  camera.fit = camera.distance;
  camera.yaw = 0.9;
  camera.pitch = 0.92;
  updateMapZoomInput();
  updateMapHud();
  requestMap3DDraw();
}

function map3DBuildModel(model, mesh, units, materials, texturePaths) {
  const gl = map3d.gl;
  const vertices = mesh.vertices || [];
  const faces = mesh.indices || [];
  if (!vertices.length || !faces.length) throw new Error("empty mesh");
  const positions = new Float32Array(vertices.length * 3);
  const normals = new Float32Array(vertices.length * 3);
  for (let index = 0; index < vertices.length; index += 1) {
    const vertex = vertices[index] || [0, 0, 0];
    positions[index * 3] = Number(vertex[0]) || 0;
    positions[index * 3 + 1] = Number(vertex[1]) || 0;
    positions[index * 3 + 2] = Number(vertex[2]) || 0;
    const normal = (mesh.normals && mesh.normals[index]) || [0, 1, 0];
    normals[index * 3] = Number(normal[0]) || 0;
    normals[index * 3 + 1] = Number(normal[1]) || 1;
    normals[index * 3 + 2] = Number(normal[2]) || 0;
  }
  const uvSource = mesh.uvs || [];
  const uvs = new Float32Array(vertices.length * 2);
  for (let index = 0; index < vertices.length; index += 1) {
    const uv = uvSource[index] || [0, 0];
    uvs[index * 2] = Number(uv[0]) || 0;
    uvs[index * 2 + 1] = Number(uv[1]) || 0;
  }
  const tangents = new Float32Array(vertices.length * 4);
  for (let index = 0; index < vertices.length; index += 1) {
    tangents[index * 4] = 1;
    tangents[index * 4 + 3] = 1;
  }
  const tangentSum = new Float32Array(vertices.length * 3);
  const bitangentSum = new Float32Array(vertices.length * 3);
  for (let index = 0; index + 2 < faces.length; index += 3) {
    const ia = faces[index], ib = faces[index + 1], ic = faces[index + 2];
    const pa = vertices[ia], pb = vertices[ib], pc = vertices[ic];
    if (!pa || !pb || !pc) continue;
    const ua = uvSource[ia] || [0, 0], ub = uvSource[ib] || [0, 0], uc = uvSource[ic] || [0, 0];
    const e1x = pb[0] - pa[0], e1y = pb[1] - pa[1], e1z = pb[2] - pa[2];
    const e2x = pc[0] - pa[0], e2y = pc[1] - pa[1], e2z = pc[2] - pa[2];
    const du1 = ub[0] - ua[0], dv1 = ub[1] - ua[1];
    const du2 = uc[0] - ua[0], dv2 = uc[1] - ua[1];
    const det = du1 * dv2 - du2 * dv1;
    if (Math.abs(det) < 1e-9) continue;
    const r = 1 / det;
    const tx = (e1x * dv2 - e2x * dv1) * r, ty = (e1y * dv2 - e2y * dv1) * r, tz = (e1z * dv2 - e2z * dv1) * r;
    const bx = (e2x * du1 - e1x * du2) * r, by = (e2y * du1 - e1y * du2) * r, bz = (e2z * du1 - e1z * du2) * r;
    for (const target of [ia, ib, ic]) {
      tangentSum[target * 3] += tx; tangentSum[target * 3 + 1] += ty; tangentSum[target * 3 + 2] += tz;
      bitangentSum[target * 3] += bx; bitangentSum[target * 3 + 1] += by; bitangentSum[target * 3 + 2] += bz;
    }
  }
  for (let index = 0; index < vertices.length; index += 1) {
    const nx = normals[index * 3], ny = normals[index * 3 + 1], nz = normals[index * 3 + 2];
    let tx = tangentSum[index * 3], ty = tangentSum[index * 3 + 1], tz = tangentSum[index * 3 + 2];
    const dot = nx * tx + ny * ty + nz * tz;
    tx -= nx * dot; ty -= ny * dot; tz -= nz * dot;
    const length = Math.hypot(tx, ty, tz);
    if (length > 1e-6) { tx /= length; ty /= length; tz /= length; } else { tx = 1; ty = 0; tz = 0; }
    const bx = bitangentSum[index * 3], by = bitangentSum[index * 3 + 1], bz = bitangentSum[index * 3 + 2];
    const cx = ny * tz - nz * ty, cy = nz * tx - nx * tz, cz = nx * ty - ny * tx;
    tangents[index * 4] = tx; tangents[index * 4 + 1] = ty; tangents[index * 4 + 2] = tz;
    tangents[index * 4 + 3] = cx * bx + cy * by + cz * bz < 0 ? -1 : 1;
  }
  const use32 = vertices.length > 65535;
  const indices = use32 ? new Uint32Array(faces) : new Uint16Array(faces);
  const instances = new Float32Array(units.length * 28);
  for (let index = 0; index < units.length; index += 1) {
    const base = index * 28;
    instances.set(map3DUnitMatrix(units[index]), base);
    const paint = map3DUnitPaint(units[index]);
    for (let part = 0; part < 3; part += 1) {
      instances[base + 16 + part * 4] = paint[part][0];
      instances[base + 17 + part * 4] = paint[part][1];
      instances[base + 18 + part * 4] = paint[part][2];
      instances[base + 19 + part * 4] = paint[part][3];
    }
  }
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const positionBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, positions, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
  const normalBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, normalBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, normals, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(1);
  gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 0, 0);
  const instanceBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, instanceBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, instances, gl.STATIC_DRAW);
  for (let part = 0; part < 4; part += 1) {
    gl.enableVertexAttribArray(2 + part);
    gl.vertexAttribPointer(2 + part, 4, gl.FLOAT, false, 112, part * 16);
    gl.vertexAttribDivisor(2 + part, 1);
  }
  for (let part = 0; part < 3; part += 1) {
    const location = part === 0 ? 6 : (part === 1 ? 8 : 9);
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, 4, gl.FLOAT, false, 112, 64 + part * 16);
    gl.vertexAttribDivisor(location, 1);
  }
  const uvBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, uvBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, uvs, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(7);
  gl.vertexAttribPointer(7, 2, gl.FLOAT, false, 0, 0);
  const tangentBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, tangentBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, tangents, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(10);
  gl.vertexAttribPointer(10, 4, gl.FLOAT, false, 0, 0);
  const indexBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuffer);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
  gl.bindVertexArray(null);
  const previous = map3d.models.get(model);
  if (previous) {
    gl.deleteVertexArray(previous.vao);
    gl.deleteBuffer(previous.positionBuffer);
    gl.deleteBuffer(previous.normalBuffer);
    gl.deleteBuffer(previous.instanceBuffer);
    gl.deleteBuffer(previous.indexBuffer);
    gl.deleteBuffer(previous.uvBuffer);
    gl.deleteBuffer(previous.tangentBuffer);
  }
  const materialList = (Array.isArray(materials) ? materials : []).map((item) => (item && typeof item === "object" ? item : {}));
  const submeshes = Array.isArray(mesh.submeshes) ? mesh.submeshes : [];
  const groups = [];
  for (const sub of submeshes) {
    const count = Math.max(0, (Number(sub.face_count) || 0) * 3);
    if (!count) continue;
    groups.push({
      material: Math.max(0, Number(sub.material_index) || 0),
      offset: Math.max(0, (Number(sub.face_offset) || 0) * 3),
      count,
    });
  }
  if (!groups.length) groups.push({ material: 0, offset: 0, count: indices.length });
  const bounds = mesh.bounds || {};
  const min = Array.isArray(bounds.min) ? bounds.min : [0, 0, 0];
  const max = Array.isArray(bounds.max) ? bounds.max : [0, 0, 0];
  const center = [0, 0, 0];
  for (const unit of units) {
    const position = unit.position || [0, 0, 0];
    center[0] += Number(position[0]) || 0;
    center[1] += Number(position[1]) || 0;
    center[2] += Number(position[2]) || 0;
  }
  const unitTotal = Math.max(1, units.length);
  center[0] /= unitTotal;
  center[1] /= unitTotal;
  center[2] /= unitTotal;
  map3d.models.set(model, {
    vao,
    positionBuffer,
    normalBuffer,
    instanceBuffer,
    indexBuffer,
    uvBuffer,
    tangentBuffer,
    units,
    instances,
    materials: materialList,
    groups,
    textures: [],
    count: units.length,
    indexCount: indices.length,
    indexType: use32 ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT,
    fallback: map3DModelColor(model),
    center,
    boundMin: [Number(min[0]) || 0, Number(min[1]) || 0, Number(min[2]) || 0],
    boundHalf: [
      Math.max(0.0001, ((Number(max[0]) || 0) - (Number(min[0]) || 0)) / 2),
      Math.max(0.0001, ((Number(max[1]) || 0) - (Number(min[1]) || 0)) / 2),
      Math.max(0.0001, ((Number(max[2]) || 0) - (Number(min[2]) || 0)) / 2),
    ],
  });
  map3DLoadMaterialTextures(model, materialList, texturePaths);
}

function map3DLoadTexture(path, done) {
  const gl = map3d.gl;
  if (!gl || !path) {
    done(null);
    return;
  }
  if (map3d.textureCache.has(path)) {
    done(map3d.textureCache.get(path));
    return;
  }
  if (map3d.textureCache.size + map3d.texturePending.size >= 300) {
    mapMsgAdd("info", tr("map.msg.info"), tr("map.3d.textureLimit", { count: 300 }));
    done(null);
    return;
  }
  const pending = map3d.texturePending.get(path);
  if (pending) {
    pending.push(done);
    return;
  }
  map3d.texturePending.set(path, [done]);
  const finish = (texture) => {
    const waiters = map3d.texturePending.get(path) || [];
    map3d.texturePending.delete(path);
    if (texture) map3d.textureCache.set(path, texture);
    for (const waiter of waiters) waiter(texture);
  };
  scheduleThumb(apiUrl("/api/image", { path }), 1).then((blobUrl) => {
    const image = new Image();
    image.addEventListener("load", () => {
      URL.revokeObjectURL(blobUrl);
      if (map3d.gl !== gl) {
        finish(null);
        return;
      }
      let source = image;
      const maxSize = 1024;
      if (image.width > maxSize || image.height > maxSize) {
        const scale = maxSize / Math.max(image.width, image.height);
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(image.width * scale));
        canvas.height = Math.max(1, Math.round(image.height * scale));
        canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
        source = canvas;
      }
      const texture = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      const small = source.width <= 32 && source.height <= 32;
      if (small) {
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
      } else {
        gl.generateMipmap(gl.TEXTURE_2D);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      }
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, small ? gl.NEAREST : gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
      if (!small) {
        const aniso = gl.getExtension("EXT_texture_filter_anisotropic");
        if (aniso) {
          const max = gl.getParameter(aniso.MAX_TEXTURE_MAX_ANISOTROPY_EXT) || 1;
          gl.texParameterf(gl.TEXTURE_2D, aniso.TEXTURE_MAX_ANISOTROPY_EXT, Math.min(16, max));
        }
      }
      texture.__small = small;
      gl.bindTexture(gl.TEXTURE_2D, null);
      finish(texture);
      requestMap3DDraw();
    }, { once: true });
    image.addEventListener("error", () => {
      URL.revokeObjectURL(blobUrl);
      finish(null);
    }, { once: true });
    image.src = blobUrl;
  }).catch(() => finish(null));
}

function map3DLoadMaterialTextures(model, materials, texturePaths) {
  const entry = map3d.models.get(model);
  if (!entry) return;
  const fallback = (texturePaths || []).filter((item) => typeof item === "string" && item);
  const apply = (index, slot, texture) => {
    const current = map3d.models.get(model);
    if (!current || current !== entry) return;
    if (!current.textures[index]) current.textures[index] = [];
    current.textures[index][slot] = texture;
    requestMap3DDraw();
  };
  materials.forEach((material, index) => {
    const slots = [material.tex0, material.tex1, material.tex2, material.tex3];
    if (index === 0 && fallback[0]) slots[0] = slots[0] || fallback[0];
    slots.forEach((path, slot) => {
      if (!path) return;
      map3DLoadTexture(path, (texture) => apply(index, slot, texture));
    });
  });
}

function map3DApplyMaterial(entry, group) {
  const gl = map3d.gl;
  const u = map3d.uniforms;
  const mat = entry.materials[group.material] || {};
  const colors = Array.isArray(mat.colors) ? mat.colors : [];
  for (let index = 0; index < 4; index += 1) {
    const value = Array.isArray(colors[index]) ? colors[index] : [1, 1, 1, 1];
    gl.uniform4f(u["mask" + index], Number(value[0]) || 0, Number(value[1]) || 0, Number(value[2]) || 0, Number(value[3]) || 0);
  }
  const tex = entry.textures[group.material] || [];
  for (let index = 0; index < 4; index += 1) {
    const texture = (index === 1 && tex[index] && tex[index].__small) ? null : (tex[index] || null);
    gl.activeTexture(gl.TEXTURE0 + index);
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.uniform1f(u["hasTex" + index], texture ? 1 : 0);
  }
  const surface = String(mat.surface || "").toLowerCase();
  const changeColor = surface.includes("change_color");
  const weighted = changeColor && (surface.includes("object") || surface.includes("tiling"));
  gl.uniform1f(u.colorMode, weighted ? 3 : (Number(mat.color_mode) || 0));
  gl.uniform1f(u.usePaint, changeColor ? 1 : 0);
  gl.uniform1f(u.axis, Number(mat.axis) || 0);
  gl.uniform1f(u.lerpCenter, Number(mat.lerp_center) || 0);
  const contrast = Number(mat.lerp_contrast);
  gl.uniform1f(u.lerpContrast, Number.isFinite(contrast) ? contrast : 0.5);
  const base = Array.isArray(mat.base_color) ? mat.base_color : [1, 1, 1, 1];
  gl.uniform4f(u.baseColor, Number(base[0]) || 0, Number(base[1]) || 0, Number(base[2]) || 0, Number(base[3]) || 0);
  const tintValue = Number(mat.tint_value);
  gl.uniform1f(u.tintValue, Number.isFinite(tintValue) ? tintValue : 1);
  const intensity = Array.isArray(mat.intensity) ? mat.intensity : [1, 1, 1, 0];
  const emissive = Array.isArray(mat.emissive_color) ? mat.emissive_color : [0, 0, 0, 0];
  gl.uniform4f(u.emissiveColor, Number(emissive[0]) || 0, Number(emissive[1]) || 0, Number(emissive[2]) || 0, Number(emissive[3]) || 0);
  const ei = Array.isArray(mat.emissive_intensity) ? mat.emissive_intensity : [0, 0, 0, 0];
  gl.uniform4f(u.ei, Number(ei[0]) || 0, Number(ei[1]) || 0, Number(ei[2]) || 0, Number(ei[3]) || 0);
  gl.uniform1f(u.intensityA, Number(intensity[3]) || 0);
  gl.uniform1f(u.emissiveKind, Number(mat.emissive_kind) || 0);
  gl.uniform1f(u.lerpLinear, Number(mat.lerp_linear) || 0);
  const glassOpacity = Number(mat.glass_opacity);
  gl.uniform1f(u.glassOpacity, Number.isFinite(glassOpacity) && glassOpacity > 0 ? glassOpacity : 0.6);
  gl.uniform1f(u.glass, surface.includes("glass") ? 1 : 0);
  gl.uniform1f(u.patternMode, surface.includes("tiling") ? 1 : 0);
  const patternColor = Array.isArray(mat.pattern_color) ? mat.pattern_color : [1, 1, 1, 1];
  gl.uniform4f(u.patternColor, Number(patternColor[0]) || 0, Number(patternColor[1]) || 0, Number(patternColor[2]) || 0, Number(patternColor[3]) || 0);
  const normalIntensity = Number(intensity[0]);
  gl.uniform1f(u.normalIntensity, Number.isFinite(normalIntensity) ? normalIntensity : 1);
  const opacityTex = mat.opacity_tex === "tex1" ? 1 : (mat.opacity_tex === "none" ? 2 : 0);
  gl.uniform1f(u.opacityTex, surface.includes("tiling") ? 2 : opacityTex);
  gl.uniform1f(u.alphaRef, Number(mat.alpha_ref) || 0);
  gl.uniform1f(u.unlit, mat.lighting === false ? 1 : 0);
  const para = Array.isArray(mat.texture_para) ? mat.texture_para : [1, 1, 0, 0];
  gl.uniform4f(u.texturePara, Number(para[0]) || 1, Number(para[1]) || 1, Number(para[2]) || 0, Number(para[3]) || 0);
  gl.uniform1f(u.rotationAngle, Number(mat.rotation_angle) || 0);
  gl.uniform1f(u.tiling, mat.tiling ? 1 : 0);
  const repeatScale = Number(mat.repeat_scale);
  gl.uniform1f(u.repeatScale, Number.isFinite(repeatScale) ? repeatScale : 1);
  gl.uniform3f(u.boundMin, entry.boundMin[0], entry.boundMin[1], entry.boundMin[2]);
  gl.uniform3f(u.boundHalf, entry.boundHalf[0], entry.boundHalf[1], entry.boundHalf[2]);
  gl.uniform3f(u.fallback, entry.fallback[0], entry.fallback[1], entry.fallback[2]);
}

function buildMap3DGrid(data) {
  const gl = map3d.gl;
  const bounds = data?.bounds;
  if (!gl || !bounds) return;
  const span = Math.max(bounds.max_x - bounds.min_x, bounds.max_y - bounds.min_y, 1);
  const step = mapNiceStep(span / 24);
  const x0 = Math.floor(bounds.min_x / step) * step;
  const x1 = Math.ceil(bounds.max_x / step) * step;
  const z0 = Math.floor(bounds.min_y / step) * step;
  const z1 = Math.ceil(bounds.max_y / step) * step;
  const values = [];
  for (let x = x0; x <= x1 + step * 0.5; x += step) values.push(x, 0, z0, x, 0, z1);
  for (let z = z0; z <= z1 + step * 0.5; z += step) values.push(x0, 0, z, x1, 0, z);
  if (!map3d.grid) map3d.grid = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, map3d.grid);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(values), gl.STATIC_DRAW);
  gl.bindBuffer(gl.ARRAY_BUFFER, null);
  map3d.gridCount = values.length / 3;
}

function map3DRefreshColors(model) {
  const entry = map3d.models.get(model);
  const gl = map3d.gl;
  if (!entry || !gl || !entry.instances || !entry.units) return;
  for (let index = 0; index < entry.units.length; index += 1) {
    const base = index * 28;
    const paint = map3DUnitPaint(entry.units[index]);
    for (let part = 0; part < 3; part += 1) {
      entry.instances[base + 16 + part * 4] = paint[part][0];
      entry.instances[base + 17 + part * 4] = paint[part][1];
      entry.instances[base + 18 + part * 4] = paint[part][2];
      entry.instances[base + 19 + part * 4] = paint[part][3];
    }
  }
  gl.bindBuffer(gl.ARRAY_BUFFER, entry.instanceBuffer);
  gl.bufferSubData(gl.ARRAY_BUFFER, 0, entry.instances);
  gl.bindBuffer(gl.ARRAY_BUFFER, null);
  requestMap3DDraw();
}

function rebuildMapPoints(forceAll = false) {
  const gl = map3d.gl;
  if (!gl) return;
  const strict = !mapView.ignoreErrors && map3d.loadErrors > 0;
  const skip = strict || forceAll ? new Set() : map3d.models;
  const values = [];
  for (const unit of map3d.units) {
    if (skip.has(unit.model)) continue;
    if (map3d.hiddenModels.has(unit.model)) continue;
    const position = unit.position || [0, 0, 0];
    const color = map3DUnitColor(unit);
    values.push(Number(position[0]) || 0, Number(position[1]) || 0, Number(position[2]) || 0, color[0], color[1], color[2], color[3]);
  }
  map3d.pointCount = values.length / 7;
  if (!map3d.points) map3d.points = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, map3d.points);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(values), gl.DYNAMIC_DRAW);
  gl.bindBuffer(gl.ARRAY_BUFFER, null);
}

function drawMap3D() {
  const gl = map3d.gl;
  const canvas = map3d.canvas;
  if (!gl || !canvas) return;
  resizeMap3D();
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clearColor(0.055, 0.07, 0.08, 1);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.enable(gl.DEPTH_TEST);
  gl.disable(gl.CULL_FACE);
  const vp = map3DViewProj();
  const strict = !mapView.ignoreErrors && map3d.loadErrors > 0;
  if (map3d.lineProgram && map3d.gridCount) {
    gl.useProgram(map3d.lineProgram);
    gl.uniformMatrix4fv(map3d.uLineView, false, vp);
    gl.uniform4f(map3d.uLineColor, 0.3, 0.36, 0.4, 0.6);
    gl.bindBuffer(gl.ARRAY_BUFFER, map3d.grid);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.LINES, 0, map3d.gridCount);
  }
  if (!strict && map3d.models.size) {
    gl.useProgram(map3d.program);
    gl.uniformMatrix4fv(map3d.uViewProj, false, vp);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    const eye = map3DCameraEye();
    const draws = [];
    for (const entry of map3d.models.values()) {
      const center = entry.center || [0, 0, 0];
      const dist = (center[0] - eye[0]) ** 2 + (center[1] - eye[1]) ** 2 + (center[2] - eye[2]) ** 2;
      for (const group of entry.groups) {
        const mat = entry.materials[group.material] || {};
        draws.push({ entry, group, mat, dist, transparent: mat.transparent === true });
      }
    }
    draws.sort((a, b) => {
      if (a.transparent !== b.transparent) return a.transparent ? 1 : -1;
      return b.dist - a.dist;
    });
    for (const draw of draws) {
      const entry = draw.entry;
      gl.bindVertexArray(entry.vao);
      const bytes = entry.indexType === gl.UNSIGNED_INT ? 4 : 2;
      map3DApplyMaterial(entry, draw.group);
      if (draw.transparent) {
        gl.enable(gl.BLEND);
        gl.depthMask(false);
      } else {
        gl.disable(gl.BLEND);
        gl.depthMask(true);
      }
      gl.drawElementsInstanced(gl.TRIANGLES, draw.group.count, entry.indexType, draw.group.offset * bytes, entry.count);
    }
    gl.depthMask(true);
    gl.disable(gl.BLEND);
    gl.bindVertexArray(null);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, null);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, null);
    gl.activeTexture(gl.TEXTURE2);
    gl.bindTexture(gl.TEXTURE_2D, null);
    gl.activeTexture(gl.TEXTURE3);
    gl.bindTexture(gl.TEXTURE_2D, null);
  }
  if (map3d.pointCount) {
    gl.useProgram(map3d.pointProgram);
    gl.uniformMatrix4fv(map3d.uPointView, false, vp);
    gl.uniform1f(map3d.uPointSize, Math.max(3, Math.min(14, 900 / Math.max(1, map3d.camera.distance))));
    gl.bindBuffer(gl.ARRAY_BUFFER, map3d.points);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 28, 0);
    gl.enableVertexAttribArray(1);
    gl.vertexAttribPointer(1, 4, gl.FLOAT, false, 28, 12);
    gl.drawArrays(gl.POINTS, 0, map3d.pointCount);
  }
}

function requestMap3DDraw() {
  if (map3d.frame) return;
  map3d.frame = requestAnimationFrame(() => {
    map3d.frame = 0;
    drawMap3D();
  });
}

function disposeMap3D() {
  const gl = map3d.gl;
  if (gl) {
    for (const entry of map3d.models.values()) {
      gl.deleteVertexArray(entry.vao);
      gl.deleteBuffer(entry.positionBuffer);
      gl.deleteBuffer(entry.normalBuffer);
      gl.deleteBuffer(entry.instanceBuffer);
      gl.deleteBuffer(entry.indexBuffer);
      gl.deleteBuffer(entry.uvBuffer);
      gl.deleteBuffer(entry.tangentBuffer);
    }
    for (const texture of map3d.textureCache.values()) gl.deleteTexture(texture);
    if (map3d.points) gl.deleteBuffer(map3d.points);
    if (map3d.grid) gl.deleteBuffer(map3d.grid);
    if (map3d.program) gl.deleteProgram(map3d.program);
    if (map3d.pointProgram) gl.deleteProgram(map3d.pointProgram);
    if (map3d.lineProgram) gl.deleteProgram(map3d.lineProgram);
  }
  if (map3d.frame) cancelAnimationFrame(map3d.frame);
  map3d.frame = 0;
  map3d.textureCache.clear();
  map3d.texturePending.clear();
  map3d.uniforms = {};
  map3d.models.clear();
  map3d.hiddenModels.clear();
  map3d.points = null;
  map3d.pointCount = 0;
  map3d.grid = null;
  map3d.gridCount = 0;
  map3d.ready = false;
  map3d.data = null;
  map3d.program = null;
  map3d.pointProgram = null;
  map3d.lineProgram = null;
  map3d.gl = null;
  map3d.token += 1;
  if (map3d.canvas) {
    map3d.canvas.remove();
    map3d.canvas = null;
  }
}

async function openMap3D(file, data) {
  disposeMap3D();
  const token = map3d.token;
  const canvas = document.createElement("canvas");
  canvas.id = "map-gl";
  canvas.className = "map-gl";
  mapView.stage.insertBefore(canvas, mapView.canvas);
  const gl = canvas.getContext("webgl2", { antialias: true, alpha: false });
  if (!gl) {
    canvas.remove();
    mapView.is3d = false;
    localStorage.setItem("npk-map-3d", "0");
    mapMsgAdd("warn", tr("map.msg.warning"), tr("map.3d.webgl"));
    return false;
  }
  map3d.canvas = canvas;
  map3d.gl = gl;
  canvas.addEventListener("webglcontextlost", (event) => {
    event.preventDefault();
    mapMsgAdd("error", tr("map.msg.render"), "WebGL context lost");
    mapView.is3d = false;
    localStorage.setItem("npk-map-3d", "0");
    updateMapMode();
    drawMap();
  });
  try {
    map3d.program = map3DProgram(gl, MAP3D_MESH_VS, MAP3D_MESH_FS);
    map3d.pointProgram = map3DProgram(gl, MAP3D_POINT_VS, MAP3D_POINT_FS);
    map3d.lineProgram = map3DProgram(gl, MAP3D_LINE_VS, MAP3D_LINE_FS);
  } catch (error) {
    mapMsgAdd("error", tr("map.msg.render"), "WebGL", String(error.message || error));
    disposeMap3D();
    mapView.is3d = false;
    localStorage.setItem("npk-map-3d", "0");
    return false;
  }
  map3d.uViewProj = gl.getUniformLocation(map3d.program, "u_view_proj");
  map3d.uPointView = gl.getUniformLocation(map3d.pointProgram, "u_view_proj");
  map3d.uPointSize = gl.getUniformLocation(map3d.pointProgram, "u_point_size");
  map3d.uLineView = gl.getUniformLocation(map3d.lineProgram, "u_view_proj");
  map3d.uLineColor = gl.getUniformLocation(map3d.lineProgram, "u_color");
  const uniform = (name) => gl.getUniformLocation(map3d.program, name);
  map3d.uniforms = {
    tex0: uniform("u_tex0"), tex1: uniform("u_tex1"), tex2: uniform("u_tex2"), tex3: uniform("u_tex3"),
    hasTex0: uniform("u_has_tex0"), hasTex1: uniform("u_has_tex1"), hasTex2: uniform("u_has_tex2"), hasTex3: uniform("u_has_tex3"),
    patternColor: uniform("u_pattern_color"), patternMode: uniform("u_pattern_mode"),
    colorMode: uniform("u_color_mode"), axis: uniform("u_axis"),
    lerpCenter: uniform("u_lerp_center"), lerpContrast: uniform("u_lerp_contrast"),
    mask0: uniform("u_mask0"), mask1: uniform("u_mask1"), mask2: uniform("u_mask2"), mask3: uniform("u_mask3"),
    baseColor: uniform("u_base_color"), tintValue: uniform("u_tint_value"),
    emissiveColor: uniform("u_emissive_color"), normalIntensity: uniform("u_normal_intensity"),
    opacityTex: uniform("u_opacity_tex"), alphaRef: uniform("u_alpha_ref"), unlit: uniform("u_unlit"),
    texturePara: uniform("u_texture_para"), rotationAngle: uniform("u_rotation_angle"),
    tiling: uniform("u_tiling"), repeatScale: uniform("u_repeat_scale"),
    boundMin: uniform("u_bound_min"), boundHalf: uniform("u_bound_half"), fallback: uniform("u_fallback"),
    usePaint: uniform("u_use_paint"), emissiveKind: uniform("u_emissive_kind"),
    ei: uniform("u_ei"), intensityA: uniform("u_intensity_a"),
    lerpLinear: uniform("u_lerp_linear"), glass: uniform("u_glass"),
    glassOpacity: uniform("u_glass_opacity"),
  };
  gl.useProgram(map3d.program);
  gl.uniform1i(map3d.uniforms.tex0, 0);
  gl.uniform1i(map3d.uniforms.tex1, 1);
  gl.uniform1i(map3d.uniforms.tex2, 2);
  gl.uniform1i(map3d.uniforms.tex3, 3);
  map3d.data = data;
  map3d.ready = false;
  map3d.units = (data.units || []).filter((unit) => Array.isArray(unit.position));
  map3d.loadErrors = 0;
  map3d.loading = 0;
  map3d.total = 0;
  map3d.note = "";
  fitMap3D(data);
  buildMap3DGrid(data);
  rebuildMapPoints(true);
  map3d.ready = true;
  bindMap3DControls();
  requestMap3DDraw();
  void loadMap3DMeshes(data, token);
  return true;
}

async function setMapMode(next) {
  if (mapView.is3d === next) return;
  mapView.is3d = next;
  localStorage.setItem("npk-map-3d", next ? "1" : "0");
  mapView.orderUnits = null;
  mapView.minimapBase = null;
  if (next) {
    if (map3d.gl && map3d.data === mapView.data) {
      resizeMap3D();
      updateMapMode();
      requestMap3DDraw();
      return;
    }
    const ok = await openMap3D(mapView.file, mapView.data);
    if (!ok) {
      mapView.is3d = false;
      localStorage.setItem("npk-map-3d", "0");
    }
  } else if (map3d.frame) {
    cancelAnimationFrame(map3d.frame);
    map3d.frame = 0;
  }
  updateMapMode();
  if (mapView.is3d) requestMap3DDraw();
  else drawMap();
}

function updateMapMode() {
  const viewer = $("viewer");
  const glVisible = mapView.is3d && map3d.ready;
  if (viewer) viewer.classList.toggle("map-3d", glVisible);
  if (mapView.canvas) mapView.canvas.classList.toggle("hidden", glVisible);
  if (map3d.canvas) map3d.canvas.classList.toggle("hidden", !glVisible);
  const two = $("map-mode-2d");
  const three = $("map-mode-3d");
  if (two) {
    two.classList.toggle("active", !mapView.is3d);
    two.setAttribute("aria-pressed", mapView.is3d ? "false" : "true");
  }
  if (three) {
    three.classList.toggle("active", mapView.is3d);
    three.setAttribute("aria-pressed", mapView.is3d ? "true" : "false");
  }
  updateMapZoomInput();
  updateMapHud();
}

async function loadMap3DMeshes(data, token) {
  const groups = new Map();
  let unresolved = 0;
  let collisions = 0;
  map3d.hiddenModels.clear();
  for (const unit of data.units || []) {
    const model = unit.model;
    if (!model || !/\.(gim|mesh)$/i.test(model)) {
      unresolved += 1;
      continue;
    }
    const base = model.split(/[\\/]/).pop();
    if (/^collision/i.test(base)) {
      map3d.hiddenModels.add(model);
      collisions += 1;
      continue;
    }
    if (!groups.has(model)) groups.set(model, []);
    groups.get(model).push(unit);
  }
  if (unresolved) {
    mapMsgAdd("warn", tr("map.msg.unresolved"), tr("map.msg.unresolvedDetail", { count: unresolved }));
  }
  if (collisions) {
    mapMsgAdd("info", tr("map.msg.info"), tr("map.3d.collision", { count: collisions }));
  }
  const queue = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
  if (queue.length > MAP_MESH_LIMIT) {
    mapMsgAdd("warn", tr("map.msg.warning"), tr("map.3d.budget", { count: queue.length - MAP_MESH_LIMIT }));
  }
  const work = queue.slice(0, MAP_MESH_LIMIT);
  map3d.total = work.length;
  map3d.loading = 0;
  updateMapHud();
  let cursor = 0;
  let sinceRebuild = 0;
  const worker = async () => {
    while (cursor < work.length) {
      const [model, units] = work[cursor];
      cursor += 1;
      try {
        const response = await getJson(apiUrl("/api/neox", { path: model }));
        if (token !== map3d.token) return;
        const mesh = response.mesh_data;
        if (!mesh || !Array.isArray(mesh.vertices) || !mesh.indices || !mesh.vertices.length) {
          throw new Error(response.mesh_error || "no mesh");
        }
        map3DBuildModel(model, mesh, units, response.materials || [], response.texture_images || response.textures || []);
      } catch (error) {
        if (token !== map3d.token) return;
        map3d.loadErrors += 1;
        mapMsgAdd("error", tr("map.msg.render"), model, `${error.message || error} · ${units.length}`);
        rebuildMapPoints();
        requestMap3DDraw();
      }
      map3d.loading += 1;
      sinceRebuild += 1;
      if (sinceRebuild >= 8) {
        sinceRebuild = 0;
        rebuildMapPoints();
      }
      updateMapHud();
      requestMap3DDraw();
    }
  };
  await Promise.all([worker(), worker(), worker()]);
  if (token !== map3d.token) return;
  rebuildMapPoints();
  map3d.ready = true;
  updateMapMode();
  updateMapHud();
  requestMap3DDraw();
}

function bindMap3DControls() {
  const canvas = map3d.canvas;
  if (!canvas) return;
  canvas.addEventListener("pointerdown", (event) => {
    map3d.dragging = true;
    map3d.panning = event.button === 2 || event.shiftKey;
    map3d.dragX = event.clientX;
    map3d.dragY = event.clientY;
    canvas.classList.add("dragging");
    try { canvas.setPointerCapture(event.pointerId); } catch (_) {}
    event.preventDefault();
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!map3d.dragging) return;
    const dx = event.clientX - map3d.dragX;
    const dy = event.clientY - map3d.dragY;
    map3d.dragX = event.clientX;
    map3d.dragY = event.clientY;
    const camera = map3d.camera;
    if (map3d.panning) {
      const unit = camera.distance * 0.0016;
      camera.target[0] -= dx * unit;
      camera.target[2] += dy * unit;
    } else {
      camera.yaw -= dx * 0.008;
      camera.pitch = Math.max(0.08, Math.min(1.45, camera.pitch + dy * 0.008));
    }
    updateMapHud();
    requestMap3DDraw();
  });
  const end = (event) => {
    if (!map3d.dragging) return;
    map3d.dragging = false;
    canvas.classList.remove("dragging");
    try { canvas.releasePointerCapture(event.pointerId); } catch (_) {}
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    map3d.camera.distance = Math.max(0.5, Math.min(map3d.camera.fit * 40, map3d.camera.distance * (event.deltaY < 0 ? 0.88 : 1.14)));
    updateMapZoomInput();
    updateMapHud();
    requestMap3DDraw();
  }, { passive: false });
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
}

function updateMap3DNote() {
  if (!mapView.is3d) return "";
  if (map3d.loading < map3d.total) {
    return tr("map.3d.loading", { done: map3d.loading, total: map3d.total });
  }
  return "";
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
    const modelName = unit.model ? mapView.modelNames[unit.model] : "";
    const base = String(modelName || unit.name || "").replace(/\d+$/, "").trim();
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

function renderNeoXPreview(data, container, file) {
  container.classList.add("gmp-mode");
  const objects = data.objects || [];
  const refs = [];
  const seen = new Set();
  for (const obj of objects) {
    for (const value of Object.values(obj)) {
      if (typeof value === "string" && value.includes("\\") && value.length < 240 && !seen.has(value)) {
        seen.add(value);
        refs.push(value);
      }
    }
  }
  const meshData = data.mesh_data || null;
  const textures = data.textures || [];
  const chips = [
    `<span class="bank-chip">${tr("gmp.units")} <b>${Number(objects.length).toLocaleString()}</b></span>`,
    `<span class="bank-chip">${tr("gmp.refs")} <b>${refs.length}</b></span>`,
  ];
  if (meshData) {
    chips.push(`<span class="bank-chip">${tr("gmp.verts")} <b>${Number(meshData.vertex_count).toLocaleString()}</b></span>`);
  }
  if (textures.length) {
    chips.push(`<span class="bank-chip">${tr("mtg.textures")} <b>${textures.length}</b></span>`);
  }
  chips.join("");
  const textureCards = textures.map((tex) => {
    const url = tex.resolved ? apiUrl("/api/image", { path: tex.resolved }) : "";
    return `<button class="neox-tex" data-mesh-path="${esc(tex.resolved || "")}" ${url ? "" : "disabled"}>
      ${url ? `<img src="${esc(url)}" alt="${esc(tex.path)}" loading="lazy">` : `<span class="thumb-fallback">${esc(tex.path.split("/").pop())}</span>`}
      <span class="neox-tex-name">${esc(tex.path.split("/").pop())}</span>
    </button>`;
  }).join("");
  const objRows = objects.slice(0, 60).map((obj, index) => {
    const entries = Object.entries(obj).slice(0, 6);
    const detail = entries.map(([key, value]) => `${esc(key)}=${esc(String(value))}`).join(" · ") || "—";
    return `<div class="gmp-unit" title="${esc(detail)}">
      <span class="gmp-unit-name">${esc(tr("common.unit"))} ${index}</span>
      <span class="gmp-unit-meta">${esc(detail.slice(0, 110))}</span>
    </div>`;
  }).join("");
  const refRows = refs.slice(0, 60).map((ref) => `<span class="inspect-ref" title="${esc(ref)}">${esc(ref)}</span>`).join("");
  container.innerHTML = `<div class="gmp-preview">
    <div class="gmp-head">
      <div class="gmp-title">
        <b>${esc(file.name)}</b>
        <span>${tr("gmp.unitSummary", { units: Number(objects.length).toLocaleString(), chunks: refs.length })}</span>
      </div>
      ${meshData
        ? `<button class="btn primary" id="neox-open-3d" type="button">${icon("map")}${tr("gmp.open3d")}</button>`
        : ""}
    </div>
    <div class="bank-chips">${chips.join("")}</div>
    ${textures.length ? `<div class="section-title">${tr("mtg.textures")}</div><div class="neox-tex-grid">${textureCards}</div>` : ""}
    ${objects.length
      ? `<div class="section-title">${tr("gmp.unitList", { count: Math.min(objects.length, 60) })}</div><div class="gmp-units">${objRows}</div>`
      : `<div class="note">${tr("gmp.noUnits")}</div>`}
    ${refs.length ? `<div class="section-title">${tr("gmp.refs")}</div><div class="inspect-refs">${refRows}</div>` : ""}
  </div>`;
  const open = container.querySelector("#neox-open-3d");
  if (open) {
    open.addEventListener("click", () => openGimViewer(file, data));
  }
  container.querySelectorAll(".neox-tex[data-mesh-path]").forEach((card) => {
    card.addEventListener("click", () => {
      const path = card.dataset.meshPath;
      const texFile = state.files.find((item) => item.path === path);
      if (texFile) showPreview(texFile);
    });
  });
  $("preview-note").textContent = data.mesh_error
    ? data.mesh_error
    : meshData
      ? tr("gmp.note3d", { count: Number(meshData.vertex_count).toLocaleString() })
      : textures.length
        ? tr("mtg.note", { count: textures.length })
        : tr("gmp.noteNoUnits");
}

async function openGimDirect(file) {
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
  viewer.mode = "image";
  setViewerTitle(file);
  $("viewer-foot").innerHTML = "";
  $("viewer-stage").innerHTML = `<div class="viewer-loading"><span class="spinner"></span>${tr("gmp.loading")}</div>`;
  void node.offsetWidth;
  node.classList.add("open");
  setupMarquee($("viewer"));
  lockViewerScroll();
  try {
    const data = await getJson(apiUrl("/api/neox", neoxParams(file)));
    if (token !== viewer.token) return;
    const meshData = data.mesh_data || ((file.ext || "").toLowerCase() === ".mesh" ? data : null);
    if (!meshData || !(meshData.vertices || []).length) throw new Error(data.mesh_error || tr("gmp.noUnits"));
    if (data.animation) meshData.animation = data.animation;
    const textureList = data.texture_images || [];
    $("viewer-foot").innerHTML = gimFoot();
    bindGimFoot();
    const material = data.material || {};
    if (Array.isArray(data.materials)) material.materials = data.materials;
    renderGim3D($("viewer-stage"), meshData, token, textureList[0] ? apiUrl("/api/image", { path: textureList[0] }) : "", material);
  } catch (error) {
    if (token !== viewer.token) return;
    $("viewer-stage").innerHTML = `<div class="errorbox">${esc(tr("error.preview", { message: error.message }))}</div>`;
  }
}

function gimFoot() {
  return `<div class="viewer-zoom">
    <button class="icon-btn" id="gim-zoom-out" title="${tr("map.zoomOut")}" aria-label="${tr("map.zoomOut")}">${icon("minus")}</button>
    <label class="viewer-zoom-value"><input id="gim-zoom-input" type="number" min="5" max="2000" step="1" value="100"><span>%</span></label>
    <button class="icon-btn" id="gim-zoom-in" title="${tr("map.zoomIn")}" aria-label="${tr("map.zoomIn")}">${icon("plus")}</button>
    <button class="mini-btn" id="gim-reset" title="${tr("viewer.fit")}">${icon("fit")}${tr("map.fit")}</button>
    <label class="map-dot-control"><input id="gim-points" type="checkbox" autocomplete="off">${tr("gmp.pointsToggle")}</label>
  </div>
  <div class="viewer-map" id="gim-map">
    <canvas id="gim-map-canvas" class="viewer-map-canvas" width="178" height="118"></canvas>
    <div class="viewer-map-view" id="gim-map-view"></div>
  </div>`;
}

function bindGimFoot() {
  const zoomIn = $("gim-zoom-in");
  const zoomOut = $("gim-zoom-out");
  const zoomInput = $("gim-zoom-input");
  const reset = $("gim-reset");
  const pointsToggle = $("gim-points");
  if (zoomIn) zoomIn.addEventListener("click", () => { gim3d.scale = Math.min(20, gim3d.scale * 1.2); });
  if (zoomOut) zoomOut.addEventListener("click", () => { gim3d.scale = Math.max(0.05, gim3d.scale / 1.2); });
  if (zoomInput) {
    zoomInput.addEventListener("change", () => {
      gim3d.scale = Math.max(0.05, Math.min(20, Number(zoomInput.value) / 100 || 1));
    });
    gim3d.zoomInput = zoomInput;
  }
  if (reset) reset.addEventListener("click", () => { gim3d.yaw = 0.6; gim3d.pitch = 0.35; gim3d.scale = 1; });
  if (pointsToggle) {
    pointsToggle.checked = true;
    gim3d.points = true;
    pointsToggle.addEventListener("change", () => { gim3d.points = pointsToggle.checked; });
  }
}

function openGimViewer(file, data) {
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
  viewer.mode = "image";
  setViewerTitle(file);
  $("viewer-foot").innerHTML = gimFoot();
  bindGimFoot();
  const stage = $("viewer-stage");
  stage.innerHTML = `<div class="viewer-loading"><span class="spinner"></span>${tr("gmp.loading")}</div>`;
  void node.offsetWidth;
  node.classList.add("open");
  setupMarquee($("viewer"));
  lockViewerScroll();
  try {
    const textureList = data.texture_images || [];
    const material = data.material || {};
    if (Array.isArray(data.materials)) material.materials = data.materials;
    if (data.animation && data.mesh_data) data.mesh_data.animation = data.animation;
    renderGim3D(stage, data.mesh_data, token, textureList[0] ? apiUrl("/api/image", { path: textureList[0] }) : "", material);
  } catch (error) {
    stage.innerHTML = `<div class="errorbox">${esc(tr("error.preview", { message: error.message }))}</div>`;
  }
}

const gim3d = { canvas: null, gl: null, raf: 0, drag: false, px: 0, py: 0, yaw: 0.6, pitch: 0.35, scale: 1, points: false, zoomInput: null, minimap: null, mapView: null, minimapPoints: null };

function gimCompile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader) || "shader compile failed";
    gl.deleteShader(shader);
    throw new Error(log);
  }
  return shader;
}

function gimProgram(gl, vsSource, fsSource) {
  const vs = gimCompile(gl, gl.VERTEX_SHADER, vsSource);
  const fs = gimCompile(gl, gl.FRAGMENT_SHADER, fsSource);
  if (!vs || !fs) return null;
  const program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const log = gl.getProgramInfoLog(program) || "shader link failed";
    gl.deleteProgram(program);
    throw new Error(log);
  }
  return program;
}

function gimMultiply(a, b) {
  const out = new Float32Array(16);
  for (let col = 0; col < 4; col += 1) {
    for (let row = 0; row < 4; row += 1) {
      out[col * 4 + row] = a[row] * b[col * 4] + a[4 + row] * b[col * 4 + 1] + a[8 + row] * b[col * 4 + 2] + a[12 + row] * b[col * 4 + 3];
    }
  }
  return out;
}

function gimInvertAffine(m) {
  const a00 = m[0], a01 = m[1], a02 = m[2];
  const a10 = m[4], a11 = m[5], a12 = m[6];
  const a20 = m[8], a21 = m[9], a22 = m[10];
  const b01 = a22 * a11 - a12 * a21;
  const b11 = -a22 * a10 + a12 * a20;
  const b21 = a21 * a10 - a11 * a20;
  const det = a00 * b01 + a01 * b11 + a02 * b21;
  if (Math.abs(det) < 1e-12) return null;
  const inv = 1 / det;
  const out = new Float32Array(16);
  out[0] = b01 * inv;
  out[1] = (-a22 * a01 + a02 * a21) * inv;
  out[2] = (a12 * a01 - a02 * a11) * inv;
  out[4] = b11 * inv;
  out[5] = (a22 * a00 - a02 * a20) * inv;
  out[6] = (-a12 * a00 + a02 * a10) * inv;
  out[8] = b21 * inv;
  out[9] = (-a21 * a00 + a01 * a20) * inv;
  out[10] = (a11 * a00 - a01 * a10) * inv;
  const tx = m[12], ty = m[13], tz = m[14];
  out[12] = -(out[0] * tx + out[4] * ty + out[8] * tz);
  out[13] = -(out[1] * tx + out[5] * ty + out[9] * tz);
  out[14] = -(out[2] * tx + out[6] * ty + out[10] * tz);
  out[15] = 1;
  return out;
}

function gimTrsMatrix(values) {
  const tx = values[0], ty = values[1], tz = values[2];
  let x = values[3], y = values[4], z = values[5], w = values[6];
  const length = Math.hypot(x, y, z, w) || 1;
  x /= length; y /= length; z /= length; w /= length;
  const sx = values[7], sy = values[8], sz = values[9];
  const x2 = x + x, y2 = y + y, z2 = z + z;
  const xx = x * x2, xy = x * y2, xz = x * z2;
  const yy = y * y2, yz = y * z2, zz = z * z2;
  const wx = w * x2, wy = w * y2, wz = w * z2;
  return new Float32Array([
    (1 - (yy + zz)) * sx, (xy + wz) * sx, (xz - wy) * sx, 0,
    (xy - wz) * sy, (1 - (xx + zz)) * sy, (yz + wx) * sy, 0,
    (xz + wy) * sz, (yz - wx) * sz, (1 - (xx + yy)) * sz, 0,
    tx, ty, tz, 1,
  ]);
}

function gimBase64Float32(value) {
  const binary = atob(value || "");
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return new Float32Array(bytes.buffer);
}

function gimPerspective(fovy, aspect, near, far) {
  const f = 1 / Math.tan(fovy / 2);
  const nf = 1 / (near - far);
  return new Float32Array([
    f / aspect, 0, 0, 0,
    0, f, 0, 0,
    0, 0, (far + near) * nf, -1,
    0, 0, 2 * far * near * nf, 0,
  ]);
}

function gimNormalize(v) {
  const len = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / len, v[1] / len, v[2] / len];
}

function gimCross(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function gimLookAt(eye, center, up) {
  const z = gimNormalize([eye[0] - center[0], eye[1] - center[1], eye[2] - center[2]]);
  const x = gimNormalize(gimCross(up, z));
  const y = gimCross(z, x);
  return new Float32Array([
    x[0], y[0], z[0], 0,
    x[1], y[1], z[1], 0,
    x[2], y[2], z[2], 0,
    -(x[0] * eye[0] + x[1] * eye[1] + x[2] * eye[2]),
    -(y[0] * eye[0] + y[1] * eye[1] + y[2] * eye[2]),
    -(z[0] * eye[0] + z[1] * eye[1] + z[2] * eye[2]),
    1,
  ]);
}

function gimProject(mvp, vertex, w, h) {
  const x = mvp[0] * vertex[0] + mvp[4] * vertex[1] + mvp[8] * vertex[2] + mvp[12];
  const y = mvp[1] * vertex[0] + mvp[5] * vertex[1] + mvp[9] * vertex[2] + mvp[13];
  const wc = mvp[3] * vertex[0] + mvp[7] * vertex[1] + mvp[11] * vertex[2] + mvp[15];
  if (wc <= 0) return null;
  return [(x / wc * 0.5 + 0.5) * w, (1 - (y / wc * 0.5 + 0.5)) * h];
}

function disposeGim3D() {
  if (gim3d.animKeyHandler) { document.removeEventListener("keydown", gim3d.animKeyHandler); gim3d.animKeyHandler = null; }
  const foot = $("viewer-foot");
  if (foot) { foot.classList.remove("has-timeline"); const timeline = foot.querySelector(".gim-timeline"); if (timeline) timeline.remove(); }
  const stats = $("viewer-stats");
  if (stats) stats.textContent = "";
  if (gim3d.raf) { cancelAnimationFrame(gim3d.raf); gim3d.raf = 0; }
  const gl = gim3d.gl;
  if (gl) {
    const lose = gl.getExtension("WEBGL_lose_context");
    if (lose) lose.loseContext();
    gim3d.gl = null;
  }
}

function renderGim3D(stage, meshData, token, textureUrl, material) {
  disposeGim3D();
  const verts = meshData.vertices || [];
  const indices = meshData.indices || [];
  if (!verts.length || !indices.length) throw new Error("no vertices");
  const uvs = meshData.uvs || [];
  const normals = meshData.normals || [];
  const animData = meshData.animation && meshData.animation.tracks ? meshData.animation : null;
  let animState = null;
  if (animData && Array.isArray(meshData.joints) && meshData.joints.length) {
    const boneCount = animData.parents.length;
    const bindMatrices = [];
    const inverseBind = [];
    const bindLocal = [];
    for (let index = 0; index < boneCount; index += 1) {
      const matrix = new Float32Array(animData.bind.slice(index * 16, index * 16 + 16));
      bindMatrices.push(matrix);
      const inverse = gimInvertAffine(matrix);
      inverseBind.push(inverse);
      const parent = animData.parents[index];
      bindLocal.push(parent >= 0 && parent < boneCount && inverseBind[parent]
        ? gimMultiply(inverseBind[parent], matrix)
        : matrix);
    }
    const trackForBone = new Int32Array(boneCount).fill(-1);
    (animData.track_bones || []).forEach((bone, track) => {
      if (bone >= 0 && bone < boneCount) trackForBone[bone] = track;
    });
    const trackData = gimBase64Float32(animData.tracks);
    const jointMap = Array.isArray(animData.joint_map) ? animData.joint_map : null;
    const frameCount = Math.max(1, Number(animData.frame_count) || 1);
    const trackCount = Math.max(1, Number(animData.track_count) || 1);
    if (trackData.length >= frameCount * trackCount * 10) {
      animState = {
        data: animData, boneCount, bindLocal, inverseBind, trackForBone, trackData, jointMap,
        vertexBones: Array.isArray(animData.vertex_bones) ? animData.vertex_bones : null,
        trackCount, frameCount,
        fps: Number(animData.fps) || 30,
        duration: Number(animData.duration) || frameCount / (Number(animData.fps) || 30),
        frame: -1, playing: true, elapsed: 0, last: 0,
      };
    }
  }
  const treeMaterials = material && Array.isArray(material.materials) ? material.materials : [];
  const firstTreeMaterial = treeMaterials[0] || null;
  const submeshList = Array.isArray(meshData.submeshes) ? meshData.submeshes : [];
  const materialList = treeMaterials;
  const useSubmeshMaterials = submeshList.length > 1 && materialList.length > 0 && submeshList.every((sub) => Number.isInteger(sub.material_index));
  const unlit = firstTreeMaterial ? firstTreeMaterial.lighting === false : (!!material && material.lighting === false);
  const transparent = firstTreeMaterial ? firstTreeMaterial.transparent === true : (!!material && material.transparent === true);
  const cullBack = firstTreeMaterial ? firstTreeMaterial.cull_back !== false : !!(material && material.cull_back === true);
  const tint = material && Array.isArray(material.tint) && material.tint.length === 4 ? material.tint : [1, 1, 1, 1];
  const sprite = !!(material && typeof material.tech === "string" && material.tech.includes("sfx_spr"));
  const textureFiles = material && Array.isArray(material.textures) ? material.textures : [];
  const normalFile = textureFiles.find((path) => /(^|[_.])(n|normal)([_.]|$)/i.test((path.split("/").pop() || ""))) || "";
  const normalUrl = firstTreeMaterial && firstTreeMaterial.tex1
    ? apiUrl("/api/image", { path: firstTreeMaterial.tex1 })
    : (normalFile ? apiUrl("/api/image", { path: normalFile }) : "");
  const diffuseUrl = firstTreeMaterial && firstTreeMaterial.tex0
    ? apiUrl("/api/image", { path: firstTreeMaterial.tex0 })
    : (textureUrl || (textureFiles[0] ? apiUrl("/api/image", { path: textureFiles[0] }) : ""));
  const mrsUrl = firstTreeMaterial && firstTreeMaterial.tex2
    ? apiUrl("/api/image", { path: firstTreeMaterial.tex2 })
    : "";
  const tex3Url = firstTreeMaterial && firstTreeMaterial.tex3
    ? apiUrl("/api/image", { path: firstTreeMaterial.tex3 })
    : "";
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (const vertex of verts) {
    for (let axis = 0; axis < 3; axis += 1) {
      if (vertex[axis] < min[axis]) min[axis] = vertex[axis];
      if (vertex[axis] > max[axis]) max[axis] = vertex[axis];
    }
  }
  const center = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
  let radius = 0;
  for (const vertex of verts) {
    const distance = Math.hypot(vertex[0] - center[0], vertex[1] - center[1], vertex[2] - center[2]);
    if (distance > radius) radius = distance;
  }
  radius = radius || 1;
  stage.innerHTML = `<canvas id="gim-canvas" class="gim-canvas"></canvas>`;
  const statsNode = $("viewer-stats");
  if (statsNode) {
    statsNode.textContent = `${Number(verts.length).toLocaleString()} verts · ${Number(indices.length / 3).toLocaleString()} tris`;
  }
  const foot = $("viewer-foot");
  if (foot) {
    foot.classList.toggle("has-timeline", !!animState);
    const oldTimeline = foot.querySelector(".gim-timeline");
    if (oldTimeline) oldTimeline.remove();
    if (animState) {
      foot.insertAdjacentHTML("afterbegin", `<div class="gim-timeline" id="gim-timeline">
        <button class="gim-timeline-btn" id="gim-play" type="button" title="${tr("gim.pause")}" aria-label="${tr("gim.pause")}">${icon("pause")}</button>
        <button class="gim-timeline-btn" id="gim-prev" type="button" title="${tr("gim.prevSecond")}" aria-label="${tr("gim.prevSecond")}">-1s</button>
        <div class="gim-timeline-track" id="gim-track"><div class="gim-timeline-fill" id="gim-fill"></div><div class="gim-timeline-head" id="gim-head"></div></div>
        <span class="gim-timeline-time" id="gim-time">0.00 / ${animState.duration.toFixed(2)}</span>
        <span class="gim-timeline-frame" id="gim-frame">1 / ${animState.frameCount}</span>
        <button class="gim-timeline-btn" id="gim-next" type="button" title="${tr("gim.nextSecond")}" aria-label="${tr("gim.nextSecond")}">+1s</button>
        <button class="gim-timeline-btn" id="gim-reset-anim" type="button" title="${tr("gim.reset")}" aria-label="${tr("gim.reset")}">${tr("gim.reset")}</button>
      </div>`);
    }
  }
  const canvas = $("gim-canvas");
  canvas.tabIndex = 0;
  canvas.focus({ preventScroll: true });
  const gl = canvas.getContext("webgl2", { antialias: true, alpha: false })
    || canvas.getContext("webgl", { antialias: true, alpha: false })
    || canvas.getContext("experimental-webgl");
  if (!gl) throw new Error("WebGL unavailable");
  const isWebGL2 = typeof WebGL2RenderingContext !== "undefined" && gl instanceof WebGL2RenderingContext;
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
  gim3d.canvas = canvas;
  gim3d.gl = gl;
  gim3d.zoomInput = $("gim-zoom-input");
  gim3d.minimap = $("gim-map-canvas");
  gim3d.mapView = $("gim-map-view");
  gim3d.yaw = 0.6; gim3d.pitch = 0.35; gim3d.scale = 1;
  gim3d.minimapPoints = [];
  const sample = Math.max(1, Math.floor(verts.length / 400));
  for (let index = 0; index < verts.length; index += sample) gim3d.minimapPoints.push(verts[index]);
  const program = gimProgram(gl, `
    attribute vec3 a_pos;
    attribute vec3 a_norm;
    attribute vec2 a_uv;
    attribute vec4 a_tangent;
    uniform mat4 u_mvp;
    uniform mediump float u_flat;
    varying vec2 v_uv;
    varying vec3 v_local;
    varying float v_light;
    varying vec3 v_normal;
    varying vec3 v_tangent;
    varying vec3 v_bitangent;
    void main() {
      vec3 normal = normalize(a_norm);
      vec3 tangent = a_tangent.xyz;
      float tangent_length = length(tangent);
      tangent = tangent_length > 0.0 ? tangent / tangent_length : vec3(1.0, 0.0, 0.0);
      v_normal = normal;
      v_tangent = tangent;
      v_bitangent = cross(normal, tangent) * a_tangent.w;
      v_light = 0.35 + 0.65 * abs(dot(normal, normalize(vec3(0.45, 0.8, 0.35))));
      v_uv = vec2(a_uv.x, 1.0 - a_uv.y);
      v_local = a_pos;
      gl_Position = u_mvp * vec4(a_pos, 1.0);
      gl_PointSize = u_flat > 0.5 ? 2.0 : 1.0;
    }`, `
    precision mediump float;
    varying vec2 v_uv;
    varying vec3 v_local;
    varying float v_light;
    varying vec3 v_normal;
    varying vec3 v_tangent;
    varying vec3 v_bitangent;
    uniform sampler2D u_tex;
    uniform sampler2D u_normal_tex;
    uniform sampler2D u_base_normal_tex;
    uniform sampler2D u_tex2;
    uniform float u_has_tex;
    uniform float u_has_normal;
    uniform float u_has_base_normal;
    uniform float u_has_tex2;
    uniform float u_normal_mode;
    uniform float u_flat;
    uniform float u_unlit;
    uniform float u_sprite;
    uniform vec4 u_tint;
    uniform vec3 u_color;
    uniform float u_color_mode;
    uniform vec4 u_mask0;
    uniform vec4 u_mask1;
    uniform vec4 u_mask2;
    uniform vec4 u_mask3;
    uniform vec4 u_design;
    uniform float u_axis;
    uniform float u_lerp_center;
    uniform float u_lerp_contrast;
    uniform vec3 u_bound_min;
    uniform vec3 u_bound_half;
    uniform float u_tiling;
    uniform float u_repeat_scale;
    uniform float u_repeat_always;
    uniform vec4 u_texture_para;
    uniform float u_rotation_angle;
    uniform vec4 u_base_color;
    uniform float u_tint_value;
    uniform vec4 u_intensity;
    uniform float u_alpha_ref;
    uniform sampler2D u_pattern_tex;
    uniform float u_has_pattern;
    uniform vec2 u_pattern_scale;
    uniform vec2 u_pattern_offset;
    uniform float u_pattern_rotation;
    uniform float u_pattern_color_key;
    uniform float u_opacity_tex;
    uniform float u_glass;
    uniform float u_glass_opacity;
    uniform float u_lerp_linear;
    uniform float u_emissive_kind;
    uniform vec4 u_emissive_color;
    uniform vec4 u_ei;
    uniform sampler2D u_tex3;
    uniform float u_has_tex3;
    vec3 srgb2lin(vec3 c) {
      return pow(max(c, vec3(0.0)), vec3(2.2));
    }
    vec3 lin2srgb(vec3 c) {
      return pow(max(c, vec3(0.0)), vec3(0.4545));
    }
    void main() {
      if (u_flat > 0.5) {
        gl_FragColor = vec4(u_color, 1.0);
        return;
      }
      vec2 sample_uv = v_uv;
      sample_uv -= 0.5;
      sample_uv *= u_texture_para.xy;
      float texture_angle = radians(u_rotation_angle);
      if (abs(texture_angle) > 0.0001) {
        float texture_sin = sin(texture_angle), texture_cos = cos(texture_angle);
        sample_uv = vec2(sample_uv.x * texture_cos - sample_uv.y * texture_sin, sample_uv.x * texture_sin + sample_uv.y * texture_cos);
      }
      sample_uv += 0.5;
      sample_uv += u_texture_para.zw;
      if (u_repeat_always > 0.5) sample_uv *= u_repeat_scale;
      if (u_tiling > 0.5) {
        vec3 an = abs(normalize(v_normal));
        vec3 p = v_local / 2.64 * u_repeat_scale;
        if (an.x >= an.y && an.x >= an.z) sample_uv = p.yz;
        else if (an.y >= an.z) sample_uv = p.xz;
        else sample_uv = p.yx;
      }
      vec4 texel = u_has_tex > 0.5 ? texture2D(u_tex, sample_uv) : vec4(1.0);
      vec4 normal_texel = u_has_normal > 0.5 ? texture2D(u_normal_tex, sample_uv) : vec4(0.5, 0.5, 1.0, 1.0);
      vec4 mrs_texel = u_has_tex2 > 0.5 ? texture2D(u_tex2, sample_uv) : vec4(0.0, 0.5, 0.0, 0.0);
      vec4 tex3s = u_has_tex3 > 0.5 ? texture2D(u_tex3, sample_uv) : vec4(0.0);
      vec3 color;
      bool color_linear = false;
      float alpha;
      if (u_glass > 0.5) {
        color = u_base_color.rgb;
        alpha = u_base_color.a * u_glass_opacity;
      } else {
        color = u_has_tex > 0.5 ? texel.rgb : u_base_color.rgb;
        if (u_color_mode > 3.5) {
          color = mix(u_mask0.rgb, u_mask1.rgb, texel.g);
          color = mix(color, u_mask2.rgb, texel.b);
          color = mix(color, u_mask3.rgb, texel.a);
        } else if (u_color_mode > 2.5) {
          color = texel.r * u_mask0.rgb + texel.g * u_mask1.rgb + texel.b * u_mask2.rgb + texel.a * u_mask3.rgb;
        } else if (u_color_mode > 1.5) {
          vec3 uvw = abs((v_local - u_bound_min) / max(u_bound_half * 2.0, vec3(0.0001)));
          float axis_value = u_axis < 0.5 ? uvw.x : (u_axis < 1.5 ? uvw.y : uvw.z);
          float final_axis = smoothstep(-0.5 + u_lerp_contrast, 1.5001 - u_lerp_contrast, axis_value - u_lerp_center * 0.5);
          final_axis *= final_axis;
          float mask1 = final_axis * (texel.r * 0.5 + 0.5);
          float mask2 = (1.0 - final_axis) * texel.g;
          color = mix(srgb2lin(u_mask0.rgb), srgb2lin(u_mask1.rgb), mask1);
          color = mix(color, srgb2lin(u_mask2.rgb), mask2);
          color = mix(color, srgb2lin(u_mask3.rgb), texel.b);
          color_linear = true;
        } else if (u_color_mode > 0.5) {
          if (u_lerp_linear > 0.5) {
            color = mix(srgb2lin(u_mask0.rgb), srgb2lin(u_mask1.rgb), texel.r);
            color = mix(color, srgb2lin(u_mask2.rgb), texel.g);
            color = mix(color, srgb2lin(u_mask3.rgb), texel.b);
            color_linear = true;
          } else {
            color = mix(u_mask0.rgb, u_mask1.rgb, texel.r);
            color = mix(color, u_mask2.rgb, texel.g);
            color = mix(color, u_mask3.rgb, texel.b);
          }
          if (u_has_pattern > 0.5) {
            vec2 pattern_uv = sample_uv * u_pattern_scale;
            if (abs(u_pattern_rotation) > 0.001) {
              float angle = radians(u_pattern_rotation);
              float s = sin(angle), c = cos(angle);
              pattern_uv = vec2(pattern_uv.x * c - pattern_uv.y * s, pattern_uv.x * s + pattern_uv.y * c);
            }
            pattern_uv -= u_pattern_offset;
            vec4 pattern = texture2D(u_pattern_tex, pattern_uv);
            float pattern_mask = pattern.a * normal_texel.a * u_pattern_color_key;
            vec3 design_col = u_lerp_linear > 0.5 ? srgb2lin(u_design.rgb) : u_design.rgb;
            color = mix(color, design_col, clamp(pattern_mask, 0.0, 1.0));
          }
        }
        color *= u_base_color.rgb * u_tint_value;
        alpha = u_has_tex > 0.5 ? texel.a : 1.0;
        if (u_opacity_tex > 1.5) alpha = 1.0;
        else if (u_opacity_tex > 0.5) alpha = normal_texel.a;
        alpha *= u_base_color.a;
        if (u_emissive_kind > 8.5 && u_has_tex < 0.5) alpha = 0.0;
      }
      if (u_sprite > 0.5) {
        color *= u_tint.rgb * 2.0;
        alpha *= u_tint.a * 2.0;
      } else if (u_unlit > 0.5) {
        color *= u_tint.rgb;
        alpha *= u_tint.a;
      }
      vec3 albedo_lin = color_linear ? max(color, vec3(0.0)) : srgb2lin(color);
      vec3 emissive = vec3(0.0);
      if (u_emissive_kind > 0.5 && u_emissive_kind < 1.5) {
        emissive = srgb2lin(mrs_texel.a * u_emissive_color.rgb * u_intensity.a);
      } else if (u_emissive_kind > 1.5 && u_emissive_kind < 2.5) {
        emissive = srgb2lin(mrs_texel.a * u_emissive_color.rgb) * u_intensity.a;
      } else if (u_emissive_kind > 2.5 && u_emissive_kind < 3.5) {
        vec4 chain = mix(u_mask0 * u_ei.r, u_mask1 * u_ei.g, texel.r);
        chain = mix(chain, u_mask2 * u_ei.b, texel.g);
        chain = mix(chain, u_mask3 * u_ei.a, texel.b);
        emissive = mrs_texel.a * chain.rgb + u_emissive_color.rgb * u_intensity.a;
      } else if (u_emissive_kind > 3.5 && u_emissive_kind < 4.5) {
        vec3 chain = mix(vec3(u_ei.r), vec3(u_ei.g), texel.r);
        chain = mix(chain, vec3(u_ei.b), texel.g);
        chain = mix(chain, vec3(u_ei.a), texel.b);
        emissive = mrs_texel.a * chain + u_emissive_color.rgb * u_intensity.a;
      } else if (u_emissive_kind > 4.5 && u_emissive_kind < 5.5) {
        vec3 chain = mix(vec3(u_ei.r), vec3(u_ei.g), texel.r);
        chain = mix(chain, vec3(u_ei.b), texel.g);
        chain = mix(chain, vec3(u_ei.a), texel.b);
        emissive = mrs_texel.a * albedo_lin * chain + u_emissive_color.rgb * u_intensity.a;
      } else if (u_emissive_kind > 5.5 && u_emissive_kind < 6.5) {
        vec3 chain = texel.r * u_mask0.rgb * u_ei.r + texel.g * u_mask1.rgb * u_ei.g + texel.b * u_mask2.rgb * u_ei.b + texel.a * u_mask3.rgb * u_ei.a;
        emissive = mrs_texel.a * chain + u_emissive_color.rgb * u_intensity.a;
      } else if (u_emissive_kind > 7.5 && u_emissive_kind < 8.5) {
        emissive = u_emissive_color.rgb * (mrs_texel.a * u_intensity.a * u_tint_value);
      } else if (u_emissive_kind > 8.5 && u_emissive_kind < 9.5) {
        vec3 e = srgb2lin(tex3s.rgb * tex3s.a * u_intensity.a);
        emissive = e;
        albedo_lin += e;
      }
      vec3 normal = normalize(v_normal);
      float light = u_unlit > 0.5 ? 1.0 : v_light;
      if (u_has_normal > 0.5 && u_unlit < 0.5) {
        vec3 tex_normal = normal_texel.xyz;
        vec3 n;
        if (u_normal_mode > 0.5) {
          vec3 t = tex_normal * 2.0 - vec3(1.0, 1.0, 0.0);
          n = t;
          if (u_has_base_normal > 0.5) {
            vec3 base = texture2D(u_base_normal_tex, v_uv).xyz;
            vec3 u = base * vec3(-2.0, -2.0, 2.0) + vec3(1.0, 1.0, -1.0);
            n = t * dot(t, u) - u * t.z;
          }
        } else {
          n = tex_normal * 2.0 - 1.0;
        }
        if (dot(n, n) < 0.001) n = vec3(0.0, 0.0, 1.0);
        n.xy *= u_intensity.r;
        normal = normalize(v_tangent * n.x + v_bitangent * n.y + v_normal * n.z);
        light = 0.35 + 0.65 * abs(dot(normal, normalize(vec3(0.45, 0.8, 0.35))));
      }
      if (alpha < u_alpha_ref) discard;
      vec3 final_lin = albedo_lin * light + emissive;
      gl_FragColor = vec4(lin2srgb(final_lin), clamp(alpha, 0.0, 1.0));
    }`);
  if (!program) throw new Error("shader failed");
  gl.useProgram(program);
  const positionData = new Float32Array(verts.length * 3);
  const normalData = new Float32Array(verts.length * 3);
  const uvData = new Float32Array(verts.length * 2);
  for (let index = 0; index < verts.length; index += 1) {
    const vertex = verts[index];
    positionData[index * 3] = vertex[0];
    positionData[index * 3 + 1] = vertex[1];
    positionData[index * 3 + 2] = vertex[2];
    const normal = normals[index] || [0, 1, 0];
    normalData[index * 3] = normal[0];
    normalData[index * 3 + 1] = normal[1];
    normalData[index * 3 + 2] = normal[2];
    const uv = uvs[index] || [0, 0];
    uvData[index * 2] = uv[0];
    uvData[index * 2 + 1] = uv[1];
  }
  const tangentData = new Float32Array(verts.length * 4);
  for (let index = 0; index < verts.length; index += 1) {
    tangentData[index * 4] = 1;
    tangentData[index * 4 + 3] = 1;
  }
  if (normalUrl) {
    const tangentSum = new Float32Array(verts.length * 3);
    const bitangentSum = new Float32Array(verts.length * 3);
    for (let index = 0; index + 2 < indices.length; index += 3) {
      const ia = indices[index], ib = indices[index + 1], ic = indices[index + 2];
      const pa = verts[ia], pb = verts[ib], pc = verts[ic];
      const ua = uvs[ia] || [0, 0], ub = uvs[ib] || [0, 0], uc = uvs[ic] || [0, 0];
      const e1x = pb[0] - pa[0], e1y = pb[1] - pa[1], e1z = pb[2] - pa[2];
      const e2x = pc[0] - pa[0], e2y = pc[1] - pa[1], e2z = pc[2] - pa[2];
      const du1 = ub[0] - ua[0], dv1 = ub[1] - ua[1];
      const du2 = uc[0] - ua[0], dv2 = uc[1] - ua[1];
      const det = du1 * dv2 - du2 * dv1;
      if (Math.abs(det) < 1e-9) continue;
      const r = 1 / det;
      const tx = (e1x * dv2 - e2x * dv1) * r, ty = (e1y * dv2 - e2y * dv1) * r, tz = (e1z * dv2 - e2z * dv1) * r;
      const bx = (e2x * du1 - e1x * du2) * r, by = (e2y * du1 - e1y * du2) * r, bz = (e2z * du1 - e1z * du2) * r;
      for (const target of [ia, ib, ic]) {
        tangentSum[target * 3] += tx; tangentSum[target * 3 + 1] += ty; tangentSum[target * 3 + 2] += tz;
        bitangentSum[target * 3] += bx; bitangentSum[target * 3 + 1] += by; bitangentSum[target * 3 + 2] += bz;
      }
    }
    for (let index = 0; index < verts.length; index += 1) {
      const nx = normalData[index * 3], ny = normalData[index * 3 + 1], nz = normalData[index * 3 + 2];
      let tx = tangentSum[index * 3], ty = tangentSum[index * 3 + 1], tz = tangentSum[index * 3 + 2];
      const dot = nx * tx + ny * ty + nz * tz;
      tx -= nx * dot; ty -= ny * dot; tz -= nz * dot;
      const length = Math.hypot(tx, ty, tz);
      if (length > 1e-6) { tx /= length; ty /= length; tz /= length; } else { tx = 1; ty = 0; tz = 0; }
      const bx = bitangentSum[index * 3], by = bitangentSum[index * 3 + 1], bz = bitangentSum[index * 3 + 2];
      const cx = ny * tz - nz * ty, cy = nz * tx - nx * tz, cz = nx * ty - ny * tx;
      tangentData[index * 4] = tx; tangentData[index * 4 + 1] = ty; tangentData[index * 4 + 2] = tz;
      tangentData[index * 4 + 3] = cx * bx + cy * by + cz * bz < 0 ? -1 : 1;
    }
  }
  let maxIndex = 0;
  for (const value of indices) if (value > maxIndex) maxIndex = value;
  const useUint32 = maxIndex > 65535;
  if (useUint32 && !isWebGL2 && !gl.getExtension("OES_element_index_uint")) throw new Error("index buffer too large");
  const indexData = useUint32 ? new Uint32Array(indices) : new Uint16Array(indices);
  const attributeBuffers = {};
  const bindAttribute = (name, size, data) => {
    const location = gl.getAttribLocation(program, name);
    if (location < 0) return;
    const buffer = gl.createBuffer();
    attributeBuffers[name] = buffer;
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, size, gl.FLOAT, false, 0, 0);
  };
  bindAttribute("a_pos", 3, positionData);
  bindAttribute("a_norm", 3, normalData);
  bindAttribute("a_uv", 2, uvData);
  bindAttribute("a_tangent", 4, tangentData);
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indexData, gl.STATIC_DRAW);
  if (animState) {
    animState.posBuffer = attributeBuffers.a_pos;
    animState.normalBuffer = attributeBuffers.a_norm;
    const timelineFill = $("gim-fill");
    const timelineHead = $("gim-head");
    const timelineTime = $("gim-time");
    const timelineFrame = $("gim-frame");
    const playButton = $("gim-play");
    const updateTimeline = () => {
      const progress = animState.duration > 0 ? Math.min(1, animState.elapsed / animState.duration) : 0;
      if (timelineFill) timelineFill.style.width = `${(progress * 100).toFixed(2)}%`;
      if (timelineHead) timelineHead.style.left = `${(progress * 100).toFixed(2)}%`;
      if (timelineTime) timelineTime.textContent = `${animState.elapsed.toFixed(2)} / ${animState.duration.toFixed(2)}`;
      if (timelineFrame) timelineFrame.textContent = `${animState.frame + 1} / ${animState.frameCount}`;
      if (playButton) {
        playButton.innerHTML = animState.playing ? icon("pause") : icon("play");
        const label = animState.playing ? tr("gim.pause") : tr("gim.play");
        playButton.title = label;
        playButton.setAttribute("aria-label", label);
      }
    };
    const applyFrame = (frameIndex) => {
      const frame = Math.max(0, Math.min(animState.frameCount - 1, frameIndex));
      const locals = new Array(animState.boneCount);
      for (let bone = 0; bone < animState.boneCount; bone += 1) {
        const track = animState.trackForBone[bone];
        if (track >= 0) {
          const offset = (frame * animState.trackCount + track) * 10;
          locals[bone] = gimTrsMatrix(animState.trackData.subarray(offset, offset + 10));
        } else {
          locals[bone] = animState.bindLocal[bone];
        }
      }
      const globals = new Array(animState.boneCount);
      const resolve = (index) => {
        if (globals[index]) return globals[index];
        const parent = animState.data.parents[index];
        globals[index] = parent >= 0 && parent < animState.boneCount
          ? gimMultiply(resolve(parent), locals[index])
          : locals[index];
        return globals[index];
      };
      for (let bone = 0; bone < animState.boneCount; bone += 1) resolve(bone);
      const skin = new Array(animState.boneCount);
      for (let bone = 0; bone < animState.boneCount; bone += 1) {
        skin[bone] = animState.inverseBind[bone] ? gimMultiply(globals[bone], animState.inverseBind[bone]) : globals[bone];
      }
      for (let index = 0; index < verts.length; index += 1) {
        const rigidBone = animState.vertexBones ? animState.vertexBones[index] : -1;
        const jointRow = rigidBone >= 0 ? [rigidBone, 255, 255, 255] : (meshData.joints[index] || []);
        const weightRow = rigidBone >= 0 ? [1, 0, 0, 0] : (meshData.weights[index] || []);
        let px = 0, py = 0, pz = 0, nx = 0, ny = 0, nz = 0, total = 0;
        const sourceNormal = normals[index] || [0, 1, 0];
        for (let slot = 0; slot < 4; slot += 1) {
          let joint = jointRow[slot];
          const weight = weightRow[slot];
          if (joint === 255) continue;
          if (animState.jointMap) joint = animState.jointMap[joint];
          if (!(joint >= 0) || joint >= animState.boneCount || !(weight > 1e-7)) continue;
          const matrix = skin[joint];
          const x = verts[index][0], y = verts[index][1], z = verts[index][2];
          px += weight * (matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12]);
          py += weight * (matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13]);
          pz += weight * (matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]);
          nx += weight * (matrix[0] * sourceNormal[0] + matrix[4] * sourceNormal[1] + matrix[8] * sourceNormal[2]);
          ny += weight * (matrix[1] * sourceNormal[0] + matrix[5] * sourceNormal[1] + matrix[9] * sourceNormal[2]);
          nz += weight * (matrix[2] * sourceNormal[0] + matrix[6] * sourceNormal[1] + matrix[10] * sourceNormal[2]);
          total += weight;
        }
        if (total > 1e-8) {
          px /= total; py /= total; pz /= total;
          nx /= total; ny /= total; nz /= total;
          const length = Math.hypot(nx, ny, nz) || 1;
          positionData[index * 3] = px;
          positionData[index * 3 + 1] = py;
          positionData[index * 3 + 2] = pz;
          normalData[index * 3] = nx / length;
          normalData[index * 3 + 1] = ny / length;
          normalData[index * 3 + 2] = nz / length;
        } else {
          positionData[index * 3] = verts[index][0];
          positionData[index * 3 + 1] = verts[index][1];
          positionData[index * 3 + 2] = verts[index][2];
          normalData[index * 3] = sourceNormal[0];
          normalData[index * 3 + 1] = sourceNormal[1];
          normalData[index * 3 + 2] = sourceNormal[2];
        }
      }
      gl.bindBuffer(gl.ARRAY_BUFFER, animState.posBuffer);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, positionData);
      gl.bindBuffer(gl.ARRAY_BUFFER, animState.normalBuffer);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, normalData);
      animState.frame = frame;
      updateTimeline();
    };
    animState.applyFrame = applyFrame;
    animState.updateTimeline = updateTimeline;
    const seek = (time, wrap = false) => {
      const duration = animState.duration || 1;
      animState.elapsed = wrap
        ? ((time % duration) + duration) % duration
        : Math.max(0, Math.min(duration, time));
      animState.last = performance.now();
      animState.applyFrame(Math.floor(animState.elapsed * animState.fps));
    };
    if (playButton) {
      playButton.addEventListener("click", () => {
        animState.playing = !animState.playing;
        animState.last = performance.now();
        updateTimeline();
      });
    }
    const previousButton = $("gim-prev");
    const nextButton = $("gim-next");
    if (previousButton) previousButton.addEventListener("click", () => seek(animState.elapsed - 1));
    if (nextButton) nextButton.addEventListener("click", () => seek(animState.elapsed + 1));
    const resetAnimationButton = $("gim-reset-anim");
    if (resetAnimationButton) resetAnimationButton.addEventListener("click", () => seek(0));
    const animationKeyHandler = (event) => {
      if ((event.code !== "Space" && event.key !== " ") || event.repeat) return;
      const viewerNode = $("viewer");
      if (!viewerNode || !viewerNode.classList.contains("open")) return;
      const active = document.activeElement;
      if (active && ["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName)) return;
      event.preventDefault();
      animState.playing = !animState.playing;
      animState.last = performance.now();
      updateTimeline();
    };
    document.addEventListener("keydown", animationKeyHandler);
    gim3d.animKeyHandler = animationKeyHandler;
    const timelineTrack = $("gim-track");
    if (timelineTrack) {
      timelineTrack.addEventListener("pointerdown", (event) => {
        event.stopPropagation();
        const rect = timelineTrack.getBoundingClientRect();
        seek(Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width))) * animState.duration);
      });
    }
    animState.playing = true;
    animState.last = performance.now();
    animState.applyFrame(0);
  }
  const uMvp = gl.getUniformLocation(program, "u_mvp");
  const uTex = gl.getUniformLocation(program, "u_tex");
  const uTex2 = gl.getUniformLocation(program, "u_tex2");
  const uHasTex2 = gl.getUniformLocation(program, "u_has_tex2");
  const uHasTex = gl.getUniformLocation(program, "u_has_tex");
  const uFlat = gl.getUniformLocation(program, "u_flat");
  const uUnlit = gl.getUniformLocation(program, "u_unlit");
  const uSprite = gl.getUniformLocation(program, "u_sprite");
  const uTint = gl.getUniformLocation(program, "u_tint");
  const uColor = gl.getUniformLocation(program, "u_color");
  const uNormalTex = gl.getUniformLocation(program, "u_normal_tex");
  const uHasNormal = gl.getUniformLocation(program, "u_has_normal");
  const uBaseNormalTex = gl.getUniformLocation(program, "u_base_normal_tex");
  const uHasBaseNormal = gl.getUniformLocation(program, "u_has_base_normal");
  const uNormalMode = gl.getUniformLocation(program, "u_normal_mode");
  const uColorMode = gl.getUniformLocation(program, "u_color_mode");
  const uMask0 = gl.getUniformLocation(program, "u_mask0");
  const uMask1 = gl.getUniformLocation(program, "u_mask1");
  const uMask2 = gl.getUniformLocation(program, "u_mask2");
  const uMask3 = gl.getUniformLocation(program, "u_mask3");
  const uDesign = gl.getUniformLocation(program, "u_design");
  const uAxis = gl.getUniformLocation(program, "u_axis");
  const uLerpCenter = gl.getUniformLocation(program, "u_lerp_center");
  const uLerpContrast = gl.getUniformLocation(program, "u_lerp_contrast");
  const uBoundMin = gl.getUniformLocation(program, "u_bound_min");
  const uBoundHalf = gl.getUniformLocation(program, "u_bound_half");
  const uTiling = gl.getUniformLocation(program, "u_tiling");
  const uRepeatScale = gl.getUniformLocation(program, "u_repeat_scale");
  const uRepeatAlways = gl.getUniformLocation(program, "u_repeat_always");
  const uTexturePara = gl.getUniformLocation(program, "u_texture_para");
  const uRotationAngle = gl.getUniformLocation(program, "u_rotation_angle");
  const uBaseColor = gl.getUniformLocation(program, "u_base_color");
  const uTintValue = gl.getUniformLocation(program, "u_tint_value");
  const uIntensity = gl.getUniformLocation(program, "u_intensity");
  const uAlphaRef = gl.getUniformLocation(program, "u_alpha_ref");
  const repeatAlwaysFor = (group) => {
    const surface = String(group && group.surface || "").toLowerCase();
    return surface.includes("change_color") && !surface.includes("change_color_object");
  };
  const uPatternTex = gl.getUniformLocation(program, "u_pattern_tex");
  const uHasPattern = gl.getUniformLocation(program, "u_has_pattern");
  const uPatternScale = gl.getUniformLocation(program, "u_pattern_scale");
  const uPatternOffset = gl.getUniformLocation(program, "u_pattern_offset");
  const uPatternRotation = gl.getUniformLocation(program, "u_pattern_rotation");
  const uPatternColorKey = gl.getUniformLocation(program, "u_pattern_color_key");
  const uOpacityTex = gl.getUniformLocation(program, "u_opacity_tex");
  const uGlass = gl.getUniformLocation(program, "u_glass");
  const uGlassOpacity = gl.getUniformLocation(program, "u_glass_opacity");
  const uLerpLinear = gl.getUniformLocation(program, "u_lerp_linear");
  const uEmissiveKind = gl.getUniformLocation(program, "u_emissive_kind");
  const uEmissiveColor = gl.getUniformLocation(program, "u_emissive_color");
  const uEi = gl.getUniformLocation(program, "u_ei");
  const uTex3 = gl.getUniformLocation(program, "u_tex3");
  const uHasTex3 = gl.getUniformLocation(program, "u_has_tex3");
  const potImage = (image) => {
    if (isWebGL2) return image;
    const next = (value) => 1 << Math.ceil(Math.log2(Math.max(1, value)));
    const width = next(image.width), height = next(image.height);
    if (width === image.width && height === image.height) return image;
    const canvas = document.createElement("canvas");
    canvas.width = width; canvas.height = height;
    canvas.getContext("2d").drawImage(image, 0, 0, width, height);
    return canvas;
  };
  const texture = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([255, 255, 255, 255]));
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.uniform1i(uTex, 0);
  const normalTexture = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, normalTexture);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([128, 128, 255, 255]));
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.uniform1i(uNormalTex, 1);
  gl.uniform1i(uBaseNormalTex, 2);
  gl.uniform1i(uTex2, 4);
  gl.uniform1i(uTex3, 5);
  const mrsTexture = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, mrsTexture);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 0]));
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  const tex3Texture = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex3Texture);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 0]));
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.uniform1f(uHasBaseNormal, 0);
  gl.uniform1f(uNormalMode, 0);
  gl.uniform1f(uColorMode, 0);
  gl.uniform4f(uMask0, 1, 1, 1, 1);
  gl.uniform4f(uMask1, 1, 1, 1, 1);
  gl.uniform4f(uMask2, 1, 1, 1, 1);
  gl.uniform4f(uMask3, 1, 1, 1, 1);
  gl.uniform4f(uDesign, 1, 1, 1, 1);
  gl.uniform1f(uAxis, 0);
  gl.uniform1f(uLerpCenter, 0);
  gl.uniform1f(uLerpContrast, 0.5);
  gl.uniform3f(uBoundMin, 0, 0, 0);
  gl.uniform3f(uBoundHalf, 1, 1, 1);
  if (!useSubmeshMaterials) {
    gl.uniform3f(uBoundMin, min[0], min[1], min[2]);
    gl.uniform3f(
      uBoundHalf,
      Math.max((max[0] - min[0]) / 2, 0.0001),
      Math.max((max[1] - min[1]) / 2, 0.0001),
      Math.max((max[2] - min[2]) / 2, 0.0001),
    );
  }
  gl.uniform1f(uTiling, 0);
  gl.uniform1f(uRepeatScale, 1);
  gl.uniform1f(uRepeatAlways, 0);
  gl.uniform4f(uTexturePara, 1, 1, 0, 0);
  gl.uniform1f(uRotationAngle, 0);
  gl.uniform4f(uBaseColor, 1, 1, 1, 1);
  gl.uniform1f(uTintValue, 1);
  gl.uniform4f(uIntensity, 1, 1, 1, 1);
  gl.uniform1f(uAlphaRef, 0);
  gl.uniform1i(uPatternTex, 3);
  gl.uniform1f(uHasPattern, 0);
  gl.uniform2f(uPatternScale, 1, 1);
  gl.uniform2f(uPatternOffset, 0, 0);
  gl.uniform1f(uPatternRotation, 0);
  gl.uniform1f(uPatternColorKey, 0);
  gl.uniform1f(uOpacityTex, 0);
  gl.uniform1f(uGlass, 0);
  gl.uniform1f(uGlassOpacity, 0.6);
  gl.uniform1f(uHasTex3, 0);
  gl.uniform1f(uLerpLinear, 0);
  gl.uniform1f(uEmissiveKind, 0);
  gl.uniform4f(uEmissiveColor, 0, 0, 0, 0);
  gl.uniform4f(uEi, 0, 0, 0, 0);
  if (firstTreeMaterial) {
    const colors = Array.isArray(firstTreeMaterial.colors) ? firstTreeMaterial.colors : [];
    [uMask0, uMask1, uMask2, uMask3].forEach((location, index) => {
      const value = Array.isArray(colors[index]) ? colors[index] : [1, 1, 1, 1];
      gl.uniform4f(location, Number(value[0]) || 0, Number(value[1]) || 0, Number(value[2]) || 0, Number(value[3]) || 0);
    });
    const design = Array.isArray(firstTreeMaterial.design_color) ? firstTreeMaterial.design_color : [1, 1, 1, 1];
    gl.uniform4f(uDesign, Number(design[0]) || 0, Number(design[1]) || 0, Number(design[2]) || 0, Number(design[3]) || 0);
    gl.uniform1f(uColorMode, Number(firstTreeMaterial.color_mode) || 0);
    gl.uniform1f(uAxis, Number(firstTreeMaterial.axis) || 0);
    gl.uniform1f(uLerpCenter, Number(firstTreeMaterial.lerp_center) || 0);
    const contrast = Number(firstTreeMaterial.lerp_contrast);
    gl.uniform1f(uLerpContrast, Number.isFinite(contrast) ? contrast : 0.5);
    gl.uniform1f(uTiling, firstTreeMaterial.tiling ? 1 : 0);
    const repeatScale = Number(firstTreeMaterial.repeat_scale);
    gl.uniform1f(uRepeatScale, Number.isFinite(repeatScale) ? repeatScale : 1);
    gl.uniform1f(uRepeatAlways, repeatAlwaysFor(firstTreeMaterial) ? 1 : 0);
    const texturePara = Array.isArray(firstTreeMaterial.texture_para) ? firstTreeMaterial.texture_para : [1, 1, 0, 0];
    gl.uniform4f(uTexturePara, Number(texturePara[0]) || 1, Number(texturePara[1]) || 1, Number(texturePara[2]) || 0, Number(texturePara[3]) || 0);
    gl.uniform1f(uRotationAngle, Number(firstTreeMaterial.rotation_angle) || 0);
    const baseColor = Array.isArray(firstTreeMaterial.base_color) ? firstTreeMaterial.base_color : [1, 1, 1, 1];
    gl.uniform4f(uBaseColor, Number(baseColor[0]) || 0, Number(baseColor[1]) || 0, Number(baseColor[2]) || 0, Number(baseColor[3]) || 0);
    gl.uniform1f(uTintValue, Number.isFinite(Number(firstTreeMaterial.tint_value)) ? Number(firstTreeMaterial.tint_value) : 1);
    const intensity = Array.isArray(firstTreeMaterial.intensity) ? firstTreeMaterial.intensity : [1, 1, 1, 1];
    gl.uniform4f(uIntensity, Number(intensity[0]) || 0, Number(intensity[1]) || 0, Number(intensity[2]) || 0, Number(intensity[3]) || 0);
    gl.uniform1f(uAlphaRef, Number(firstTreeMaterial.alpha_ref) || 0);
    gl.uniform1f(uOpacityTex, firstTreeMaterial.opacity_tex === "tex1" ? 1 : (firstTreeMaterial.opacity_tex === "none" ? 2 : 0));
    const patternPara = Array.isArray(firstTreeMaterial.pattern_para) ? firstTreeMaterial.pattern_para : [1, 1, 0, 0];
    gl.uniform2f(uPatternScale, Number(patternPara[0]) || 1, Number(patternPara[1]) || 1);
    gl.uniform2f(uPatternOffset, Number(patternPara[2]) || 0, Number(patternPara[3]) || 0);
    gl.uniform1f(uPatternRotation, Number(firstTreeMaterial.pattern_rotation) || 0);
    gl.uniform1f(uPatternColorKey, Number(firstTreeMaterial.pattern_color_key) || 0);
    gl.uniform1f(uNormalMode, Number.isInteger(firstTreeMaterial.normal_mode) ? firstTreeMaterial.normal_mode : 0);
    gl.uniform1f(uUnlit, firstTreeMaterial.lighting === false ? 1 : 0);
    gl.uniform1f(uGlass, String(firstTreeMaterial.surface || "").toLowerCase().includes("standard_glass") ? 1 : 0);
    const glassOpacityValue = Number(firstTreeMaterial.glass_opacity);
    gl.uniform1f(uGlassOpacity, Number.isFinite(glassOpacityValue) && glassOpacityValue > 0 ? glassOpacityValue : 0.6);
    gl.uniform1f(uLerpLinear, Number(firstTreeMaterial.lerp_linear) || 0);
    gl.uniform1f(uEmissiveKind, Number(firstTreeMaterial.emissive_kind) || 0);
    const emissiveColor = Array.isArray(firstTreeMaterial.emissive_color) ? firstTreeMaterial.emissive_color : [0, 0, 0, 0];
    gl.uniform4f(uEmissiveColor, Number(emissiveColor[0]) || 0, Number(emissiveColor[1]) || 0, Number(emissiveColor[2]) || 0, Number(emissiveColor[3]) || 0);
    const eiValue = Array.isArray(firstTreeMaterial.emissive_intensity) ? firstTreeMaterial.emissive_intensity : [0, 0, 0, 0];
    gl.uniform4f(uEi, Number(eiValue[0]) || 0, Number(eiValue[1]) || 0, Number(eiValue[2]) || 0, Number(eiValue[3]) || 0);
  }
  let hasTexture = false;
  if (diffuseUrl) {
    const image = new Image();
    image.onload = () => {
      if (token !== viewer.token) return;
      const source = potImage(image);
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      hasTexture = true;
    };
    image.onerror = () => { hasTexture = false; };
    image.src = diffuseUrl;
  }
  let hasNormal = false;
  if (normalUrl) {
    const normalImage = new Image();
    normalImage.onload = () => {
      if (token !== viewer.token) return;
      const source = potImage(normalImage);
      gl.bindTexture(gl.TEXTURE_2D, normalTexture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      hasNormal = true;
    };
    normalImage.onerror = () => { hasNormal = false; };
    normalImage.src = normalUrl;
  }
  let hasTex2 = false;
  if (mrsUrl) {
    const mrsImage = new Image();
    mrsImage.onload = () => {
      if (token !== viewer.token) return;
      const source = potImage(mrsImage);
      gl.bindTexture(gl.TEXTURE_2D, mrsTexture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      hasTex2 = true;
    };
    mrsImage.onerror = () => { hasTex2 = false; };
    mrsImage.src = mrsUrl;
  }
  let hasTex3 = false;
  if (tex3Url) {
    const tex3Image = new Image();
    tex3Image.onload = () => {
      if (token !== viewer.token) return;
      const source = potImage(tex3Image);
      gl.bindTexture(gl.TEXTURE_2D, tex3Texture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      hasTex3 = true;
    };
    tex3Image.onerror = () => { hasTex3 = false; };
    tex3Image.src = tex3Url;
  }
  const materialTextures = [];
  const textureCache = new Map();
  const initGimTexture = (tex, pixel) => {
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(pixel));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  };
  const loadGimTexture = (url, target, onReady) => {
    const image = new Image();
    image.onload = () => {
      if (token !== viewer.token) return;
      const source = potImage(image);
      gl.bindTexture(gl.TEXTURE_2D, target);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      onReady();
    };
    image.src = url;
  };
  const cachedGimTexture = (url, normal) => {
    const key = (normal ? "n:" : "d:") + url;
    if (textureCache.has(key)) return textureCache.get(key);
    const entry = { texture: gl.createTexture(), ready: false };
    initGimTexture(entry.texture, normal ? [128, 128, 255, 255] : [255, 255, 255, 255]);
    loadGimTexture(url, entry.texture, () => { entry.ready = true; });
    textureCache.set(key, entry);
    return entry;
  };
  const globalBaseNormal = firstTreeMaterial && firstTreeMaterial.base_normal
    ? cachedGimTexture(apiUrl("/api/image", { path: firstTreeMaterial.base_normal }), true) : null;
  const globalPattern = firstTreeMaterial && firstTreeMaterial.pattern_tex
    ? cachedGimTexture(apiUrl("/api/image", { path: firstTreeMaterial.pattern_tex }), false) : null;
  if (useSubmeshMaterials) {
    materialList.forEach((group) => {
      const files = Array.isArray(group.textures) ? group.textures : [];
      const normalFile = files.find((path) => /(^|[_.])(n|normal)([_.]|$)/i.test((path.split("/").pop() || ""))) || "";
      const diffusePath = group.tex0 || files[0] || "";
      const normalPath = group.tex1 || normalFile;
      materialTextures.push({
        data: group,
        diffuse: diffusePath ? cachedGimTexture(apiUrl("/api/image", { path: diffusePath }), false) : null,
        normal: normalPath ? cachedGimTexture(apiUrl("/api/image", { path: normalPath }), true) : null,
        mrs: group.tex2 ? cachedGimTexture(apiUrl("/api/image", { path: group.tex2 }), false) : null,
        tex3: group.tex3 ? cachedGimTexture(apiUrl("/api/image", { path: group.tex3 }), false) : null,
        baseNormal: group.base_normal ? cachedGimTexture(apiUrl("/api/image", { path: group.base_normal }), true) : null,
        pattern: group.pattern_tex ? cachedGimTexture(apiUrl("/api/image", { path: group.pattern_tex }), false) : null,
      });
    });
  }
  canvas.addEventListener("pointerdown", (event) => {
    canvas.focus({ preventScroll: true });
    gim3d.drag = true; gim3d.px = event.clientX; gim3d.py = event.clientY;
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!gim3d.drag) return;
    gim3d.yaw -= (event.clientX - gim3d.px) * 0.008;
    gim3d.pitch += (event.clientY - gim3d.py) * 0.008;
    gim3d.pitch = Math.max(-1.55, Math.min(1.55, gim3d.pitch));
    gim3d.px = event.clientX; gim3d.py = event.clientY;
  });
  const stopDrag = () => { gim3d.drag = false; };
  canvas.addEventListener("pointerup", stopDrag);
  canvas.addEventListener("pointercancel", stopDrag);
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    gim3d.scale = Math.max(0.05, Math.min(20, gim3d.scale * (event.deltaY < 0 ? 1.1 : 1 / 1.1)));
  }, { passive: false });
  const draw = () => {
    if (token !== viewer.token) return;
    if (animState && animState.playing) {
      const now = performance.now();
      if (!animState.last) animState.last = now;
      animState.elapsed += (now - animState.last) / 1000;
      animState.last = now;
      if (animState.elapsed >= animState.duration) animState.elapsed %= animState.duration;
      const frame = Math.min(animState.frameCount - 1, Math.floor(animState.elapsed * animState.fps));
      if (frame !== animState.frame) animState.applyFrame(frame);
      else if (animState.updateTimeline) animState.updateTimeline();
    }
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = canvas.clientWidth || 800;
    const height = canvas.clientHeight || 500;
    const pixelWidth = Math.max(1, Math.floor(width * dpr));
    const pixelHeight = Math.max(1, Math.floor(height * dpr));
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }
    gl.viewport(0, 0, pixelWidth, pixelHeight);
    gl.enable(gl.DEPTH_TEST);
    if (cullBack) {
      gl.enable(gl.CULL_FACE);
      gl.cullFace(gl.BACK);
    } else {
      gl.disable(gl.CULL_FACE);
    }
    const clear = colorToRgb(canvasPalette().bg, [0.039, 0.055, 0.063]);
    gl.clearColor(clear[0], clear[1], clear[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    const pitch = Math.max(-1.55, Math.min(1.55, gim3d.pitch));
    const distance = radius * 2.8 / Math.max(0.05, Math.min(20, gim3d.scale));
    const eye = [
      center[0] + Math.sin(gim3d.yaw) * Math.cos(pitch) * distance,
      center[1] + Math.sin(pitch) * distance,
      center[2] + Math.cos(gim3d.yaw) * Math.cos(pitch) * distance,
    ];
    const projection = gimPerspective(Math.PI / 4, width / height, Math.max(0.01, radius * 0.01), radius * 20 + 1);
    const viewMatrix = gimLookAt(eye, center, [0, 1, 0]);
    const mvp = gimMultiply(projection, viewMatrix);
    gl.useProgram(program);
    gl.uniformMatrix4fv(uMvp, false, mvp);
    gl.uniform1f(uHasTex, hasTexture ? 1 : 0);
    gl.uniform1f(uFlat, 0);
    gl.uniform3f(uColor, 0.35, 0.78, 0.72);
    gl.uniform1f(uUnlit, unlit ? 1 : 0);
    gl.uniform1f(uSprite, sprite ? 1 : 0);
    gl.uniform4f(uTint, tint[0], tint[1], tint[2], tint[3]);
    gl.uniform1f(uHasNormal, hasNormal ? 1 : 0);
    if (hasNormal) {
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, normalTexture);
    }
    if (transparent) {
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.depthMask(false);
    } else {
      gl.disable(gl.BLEND);
      gl.depthMask(true);
    }
    if (useSubmeshMaterials) {
      const indexSize = useUint32 ? 4 : 2;
      const submeshOrder = submeshList.map((sub, order) => ({ sub, order })).sort((left, right) => {
        const leftMat = materialList[Number.isInteger(left.sub.material_index) ? left.sub.material_index : 0] || {};
        const rightMat = materialList[Number.isInteger(right.sub.material_index) ? right.sub.material_index : 0] || {};
        return (leftMat.transparent === true ? 1 : 0) - (rightMat.transparent === true ? 1 : 0) || left.order - right.order;
      });
      submeshOrder.forEach(({ sub }) => {
        const materialIndex = Number.isInteger(sub.material_index) ? sub.material_index : 0;
        const entry = materialTextures[materialIndex] || materialTextures[0];
        if (entry) {
          const diffuse = entry.diffuse;
          const normal = entry.normal;
          if (diffuse) {
            gl.activeTexture(gl.TEXTURE0);
            gl.bindTexture(gl.TEXTURE_2D, diffuse.texture);
            gl.uniform1f(uHasTex, diffuse.ready ? 1 : 0);
          } else {
            gl.uniform1f(uHasTex, 0);
          }
          if (normal) {
            gl.activeTexture(gl.TEXTURE1);
            gl.bindTexture(gl.TEXTURE_2D, normal.texture);
            gl.uniform1f(uHasNormal, normal.ready ? 1 : 0);
          } else {
            gl.uniform1f(uHasNormal, 0);
          }
          if (entry.mrs) {
            gl.activeTexture(gl.TEXTURE4);
            gl.bindTexture(gl.TEXTURE_2D, entry.mrs.texture);
            gl.uniform1f(uHasTex2, entry.mrs.ready ? 1 : 0);
          } else {
            gl.uniform1f(uHasTex2, 0);
          }
          if (entry.tex3) {
            gl.activeTexture(gl.TEXTURE5);
            gl.bindTexture(gl.TEXTURE_2D, entry.tex3.texture);
            gl.uniform1f(uHasTex3, entry.tex3.ready ? 1 : 0);
          } else {
            gl.uniform1f(uHasTex3, 0);
          }
        } else {
          gl.uniform1f(uHasTex, hasTexture ? 1 : 0);
          gl.uniform1f(uHasNormal, hasNormal ? 1 : 0);
          gl.uniform1f(uHasTex2, hasTex2 ? 1 : 0);
          if (hasTexture) {
            gl.activeTexture(gl.TEXTURE0);
            gl.bindTexture(gl.TEXTURE_2D, texture);
          }
          if (hasTex2) {
            gl.activeTexture(gl.TEXTURE4);
            gl.bindTexture(gl.TEXTURE_2D, mrsTexture);
          }
          if (hasTex3) {
            gl.activeTexture(gl.TEXTURE5);
            gl.bindTexture(gl.TEXTURE_2D, tex3Texture);
          }
          gl.uniform1f(uHasTex3, hasTex3 ? 1 : 0);
        }
        const mat = materialList[materialIndex] || materialList[0] || {};
        const colors = Array.isArray(mat.colors) ? mat.colors : [];
        const setColor = (location, colorIndex) => {
          const value = Array.isArray(colors[colorIndex]) ? colors[colorIndex] : [1, 1, 1, 1];
          const r = Number(value[0]), g = Number(value[1]), b = Number(value[2]), a = Number(value[3]);
          gl.uniform4f(location, Number.isFinite(r) ? r : 1, Number.isFinite(g) ? g : 1, Number.isFinite(b) ? b : 1, Number.isFinite(a) ? a : 1);
        };
        setColor(uMask0, 0);
        setColor(uMask1, 1);
        setColor(uMask2, 2);
        setColor(uMask3, 3);
        const design = Array.isArray(mat.design_color) ? mat.design_color : [1, 1, 1, 1];
        gl.uniform4f(uDesign, Number(design[0]) || 0, Number(design[1]) || 0, Number(design[2]) || 0, Number(design[3]) || 0);
        gl.uniform1f(uColorMode, Number(mat.color_mode) || 0);
        gl.uniform1f(uAxis, Number(mat.axis) || 0);
        gl.uniform1f(uLerpCenter, Number(mat.lerp_center) || 0);
        const contrast = Number(mat.lerp_contrast);
        gl.uniform1f(uLerpContrast, Number.isFinite(contrast) ? contrast : 0.5);
        gl.uniform1f(uTiling, mat.tiling ? 1 : 0);
        const repeatScale = Number(mat.repeat_scale);
        gl.uniform1f(uRepeatScale, Number.isFinite(repeatScale) ? repeatScale : 1);
        gl.uniform1f(uRepeatAlways, repeatAlwaysFor(mat) ? 1 : 0);
        const texturePara = Array.isArray(mat.texture_para) ? mat.texture_para : [1, 1, 0, 0];
        gl.uniform4f(uTexturePara, Number(texturePara[0]) || 1, Number(texturePara[1]) || 1, Number(texturePara[2]) || 0, Number(texturePara[3]) || 0);
        gl.uniform1f(uRotationAngle, Number(mat.rotation_angle) || 0);
        const baseColor = Array.isArray(mat.base_color) ? mat.base_color : [1, 1, 1, 1];
        gl.uniform4f(uBaseColor, Number(baseColor[0]) || 0, Number(baseColor[1]) || 0, Number(baseColor[2]) || 0, Number(baseColor[3]) || 0);
        gl.uniform1f(uTintValue, Number.isFinite(Number(mat.tint_value)) ? Number(mat.tint_value) : 1);
        const intensity = Array.isArray(mat.intensity) ? mat.intensity : [1, 1, 1, 1];
        gl.uniform4f(uIntensity, Number(intensity[0]) || 0, Number(intensity[1]) || 0, Number(intensity[2]) || 0, Number(intensity[3]) || 0);
        gl.uniform1f(uAlphaRef, Number(mat.alpha_ref) || 0);
        gl.uniform1f(uUnlit, mat.lighting === false ? 1 : 0);
        gl.uniform1f(uOpacityTex, mat.opacity_tex === "tex1" ? 1 : (mat.opacity_tex === "none" ? 2 : 0));
        gl.uniform1f(uGlass, String(mat.surface || "").toLowerCase().includes("standard_glass") ? 1 : 0);
        const glassOpacity = Number(mat.glass_opacity);
        gl.uniform1f(uGlassOpacity, Number.isFinite(glassOpacity) && glassOpacity > 0 ? glassOpacity : 0.6);
        gl.uniform1f(uLerpLinear, Number(mat.lerp_linear) || 0);
        gl.uniform1f(uEmissiveKind, Number(mat.emissive_kind) || 0);
        const emissiveColor = Array.isArray(mat.emissive_color) ? mat.emissive_color : [0, 0, 0, 0];
        gl.uniform4f(uEmissiveColor, Number(emissiveColor[0]) || 0, Number(emissiveColor[1]) || 0, Number(emissiveColor[2]) || 0, Number(emissiveColor[3]) || 0);
        const eiValue = Array.isArray(mat.emissive_intensity) ? mat.emissive_intensity : [0, 0, 0, 0];
        gl.uniform4f(uEi, Number(eiValue[0]) || 0, Number(eiValue[1]) || 0, Number(eiValue[2]) || 0, Number(eiValue[3]) || 0);
        const patternPara = Array.isArray(mat.pattern_para) ? mat.pattern_para : [1, 1, 0, 0];
        gl.uniform2f(uPatternScale, Number(patternPara[0]) || 1, Number(patternPara[1]) || 1);
        gl.uniform2f(uPatternOffset, Number(patternPara[2]) || 0, Number(patternPara[3]) || 0);
        gl.uniform1f(uPatternRotation, Number(mat.pattern_rotation) || 0);
        gl.uniform1f(uPatternColorKey, Number(mat.pattern_color_key) || 0);
        if (entry && entry.pattern) {
          gl.activeTexture(gl.TEXTURE3);
          gl.bindTexture(gl.TEXTURE_2D, entry.pattern.texture);
          gl.uniform1f(uHasPattern, entry.pattern.ready ? 1 : 0);
        } else {
          gl.uniform1f(uHasPattern, 0);
        }
        const bmin = sub.bounds && Array.isArray(sub.bounds.min) ? sub.bounds.min : [0, 0, 0];
        const bmax = sub.bounds && Array.isArray(sub.bounds.max) ? sub.bounds.max : [0, 0, 0];
        const bhx = Math.max((Number(bmax[0]) - Number(bmin[0])) / 2, 0.0001);
        const bhy = Math.max((Number(bmax[1]) - Number(bmin[1])) / 2, 0.0001);
        const bhz = Math.max((Number(bmax[2]) - Number(bmin[2])) / 2, 0.0001);
        gl.uniform3f(uBoundMin, Number(bmin[0]) || 0, Number(bmin[1]) || 0, Number(bmin[2]) || 0);
        gl.uniform3f(uBoundHalf, bhx, bhy, bhz);
        if (entry && entry.baseNormal) {
          gl.activeTexture(gl.TEXTURE2);
          gl.bindTexture(gl.TEXTURE_2D, entry.baseNormal.texture);
          gl.uniform1f(uHasBaseNormal, entry.baseNormal.ready ? 1 : 0);
        } else {
          gl.uniform1f(uHasBaseNormal, 0);
        }
        gl.uniform1f(uNormalMode, Number.isInteger(mat.normal_mode) ? mat.normal_mode : (Number(mat.color_mode) === 2 && entry && entry.baseNormal ? 1 : 0));
        if (mat.transparent === true) {
          gl.enable(gl.BLEND);
          gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
          gl.depthMask(false);
        } else {
          gl.disable(gl.BLEND);
          gl.depthMask(true);
        }
        if (mat.cull_back === false) gl.disable(gl.CULL_FACE);
        else gl.enable(gl.CULL_FACE);
        const offset = Math.max(0, Number(sub.face_offset) || 0) * 3 * indexSize;
        const count = Math.max(0, Number(sub.face_count) || 0) * 3;
        if (count) gl.drawElements(gl.TRIANGLES, count, useUint32 ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT, offset);
      });
    } else {
      if (hasTexture) {
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, texture);
      }
      if (hasTex2) {
        gl.activeTexture(gl.TEXTURE4);
        gl.bindTexture(gl.TEXTURE_2D, mrsTexture);
      }
      gl.uniform1f(uHasTex2, hasTex2 ? 1 : 0);
      if (globalBaseNormal) {
        gl.activeTexture(gl.TEXTURE2);
        gl.bindTexture(gl.TEXTURE_2D, globalBaseNormal.texture);
        gl.uniform1f(uHasBaseNormal, globalBaseNormal.ready ? 1 : 0);
      } else {
        gl.uniform1f(uHasBaseNormal, 0);
      }
      if (globalPattern) {
        gl.activeTexture(gl.TEXTURE3);
        gl.bindTexture(gl.TEXTURE_2D, globalPattern.texture);
        gl.uniform1f(uHasPattern, globalPattern.ready ? 1 : 0);
      } else {
        gl.uniform1f(uHasPattern, 0);
      }
      gl.drawElements(gl.TRIANGLES, indexData.length, useUint32 ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT, 0);
    }
    if (gim3d.points) {
      gl.uniform1f(uFlat, 1);
      gl.uniform1f(uHasTex, 0);
      gl.uniform1f(uHasNormal, 0);
      gl.uniform3f(uColor, 1.0, 0.82, 0.4);
      gl.drawElements(gl.POINTS, indexData.length, useUint32 ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT, 0);
    }
    if (gim3d.zoomInput && Number(gim3d.zoomInput.value) !== Math.round(gim3d.scale * 100)) {
      gim3d.zoomInput.value = Math.round(gim3d.scale * 100);
    }
    drawGimMinimap(mvp, width, height);
    gim3d.raf = requestAnimationFrame(draw);
  };
  draw();
}

function drawGimMinimap(mvp, width, height) {
  const mini = gim3d.minimap;
  const view = gim3d.mapView;
  if (!mini || !view) return;
  const pal = canvasPalette();
  const ctx = mini.getContext("2d");
  const mw = mini.width, mh = mini.height;
  ctx.fillStyle = pal.bg;
  ctx.fillRect(0, 0, mw, mh);
  ctx.fillStyle = pal.points;
  for (const vertex of gim3d.minimapPoints || []) {
    const point = gimProject(mvp, vertex, width, height);
    if (point) ctx.fillRect((point[0] / width) * mw - 1, (point[1] / height) * mh - 1, 2, 2);
  }
  view.style.left = `${Math.max(0, (width / 2 - width / (2 * gim3d.scale)) / width * mw)}px`;
  view.style.top = `${Math.max(0, (height / 2 - height / (2 * gim3d.scale)) / height * mh)}px`;
  view.style.width = `${Math.min(mw, mw / gim3d.scale)}px`;
  view.style.height = `${Math.min(mh, mh / gim3d.scale)}px`;
}

function setPreviewTitle(file) {
  const name = (file && displayNameFor(file)) || "";
  $("preview-name").textContent = name || (file ? file.name : "");
  const sub = $("preview-sub");
  if (sub) {
    sub.textContent = name && file ? file.name : "";
    sub.classList.toggle("hidden", !name);
  }
  if (file) queueFileNames(file);
  const head = document.querySelector("#drawer .drawer-head");
  if (head) refreshMarqueeTargets(head);
}

function setViewerTitle(file) {
  const name = (file && displayNameFor(file)) || "";
  $("viewer-name").textContent = name || (file ? file.name : "");
  const sub = $("viewer-sub");
  if (sub) {
    sub.textContent = name && file ? file.name : "";
    sub.classList.toggle("hidden", !name);
  }
  if (file) queueFileNames(file);
  const head = document.querySelector("#viewer .viewer-head");
  if (head) refreshMarqueeTargets(head);
}

async function showPreview(file) {
  const token = ++previewToken;
  stopXnMarquee();
  state.previewPath = file.path;
  setActiveFile(file.path);
  updatePreviewNav();
  setPreviewTitle(file);
  const previewExt = (file.ext || "").toLowerCase();
  const previewBadge = $("preview-badge");
  if (previewExt === ".bin" || previewExt === ".mesh") {
    previewBadge.className = `badge ${previewExt === ".mesh" ? "model" : "binary"} badge-icon`;
    previewBadge.innerHTML = icon(previewExt === ".mesh" ? "mesh" : "bin");
  } else {
    previewBadge.textContent = (file.ext || file.kind || "FILE").replace(".", "").slice(0, 5).toUpperCase();
    previewBadge.className = `badge ${displayKind(file.kind, file.ext)}`;
  }
  $("preview-media").classList.remove("swap-in", "gmp-mode");
  $("preview-media").innerHTML = `<div class="preview-skeleton skeleton" aria-hidden="true"></div>`;
  $("preview-meta").innerHTML = metaSkeleton();
  $("preview-note").textContent = tr("drawer.note.info");
  $("open-image").classList.add("hidden");
  renderOpenActions();
  openDrawerPanel();
  setupMarquee($("drawer"));
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
    const ext = (info.ext || "").toLowerCase();
    if (ext === ".gmp") {
      media.innerHTML = `<div class="preview-skeleton skeleton" aria-hidden="true"></div>`;
      $("preview-note").textContent = tr("gmp.loading");
      const data = await getJson(apiUrl("/api/gmp", { path: file.path }));
      if (token !== previewToken) return;
      renderGmpPreview(data, media);
    } else if (ext === ".gim" || ext === ".mtg") {
      media.innerHTML = `<div class="preview-skeleton skeleton" aria-hidden="true"></div>`;
      $("preview-note").textContent = tr("gmp.loading");
      const data = await getJson(apiUrl("/api/neox", neoxParams(file)));
      if (token !== previewToken) return;
      renderNeoXPreview(data, media, file);
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
  stopXnMarquee();
  state.previewPath = null;
  const sub = $("preview-sub");
  if (sub) {
    sub.textContent = "";
    sub.classList.add("hidden");
  }
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

function restoreScrollPosition(scroll) {
  let tries = 0;
  let cancelled = false;
  const events = ["wheel", "touchstart", "pointerdown", "keydown"];
  const cleanup = () => events.forEach((name) => window.removeEventListener(name, cancel));
  const cancel = () => {
    cancelled = true;
    cleanup();
  };
  events.forEach((name) => window.addEventListener(name, cancel, { passive: true }));
  const apply = () => {
    if (cancelled) return;
    window.scrollTo(0, scroll);
    if (Math.abs(window.scrollY - scroll) > 2 && tries < 20) {
      tries += 1;
      setTimeout(apply, 50);
    } else {
      cleanup();
    }
  };
  apply();
}


function setSidebarWidth(width, persist = true) {
  const next = Math.max(220, Math.min(420, Math.round(Number(width) || 264)));
  document.documentElement.style.setProperty("--sidebar", `${next}px`);
  if (persist) {
    try { localStorage.setItem(SIDEBAR_WIDTH_STORAGE, String(next)); } catch (_) {}
    setTimeout(() => {
      setupMarquee($("content"));
      setupMarquee($("content-topbar"));
      setupMarquee($("roots"));
      setupMarquee($("index-pane"));
    }, 60);
  }
  return next;
}

function initSidebarResize() {
  let stored = 264;
  try { stored = Number(localStorage.getItem(SIDEBAR_WIDTH_STORAGE)) || 264; } catch (_) {}
  setSidebarWidth(stored, false);
  const handle = $("sidebar-resizer");
  if (!handle) return;
  let drag = null;
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const sidebar = document.querySelector(".sidebar");
    drag = { x: event.clientX, width: sidebar ? sidebar.getBoundingClientRect().width : 264 };
    document.documentElement.classList.add("resizing-sidebar");
    try { handle.setPointerCapture(event.pointerId); } catch (_) {}
    event.preventDefault();
  });
  handle.addEventListener("pointermove", (event) => {
    if (!drag) return;
    setSidebarWidth(drag.width + event.clientX - drag.x, false);
  });
  const end = (event) => {
    if (!drag) return;
    const sidebar = document.querySelector(".sidebar");
    const width = sidebar ? sidebar.getBoundingClientRect().width : drag.width;
    drag = null;
    document.documentElement.classList.remove("resizing-sidebar");
    setSidebarWidth(width, true);
    try { handle.releasePointerCapture(event.pointerId); } catch (_) {}
  };
  handle.addEventListener("pointerup", end);
  handle.addEventListener("pointercancel", end);
  handle.addEventListener("keydown", (event) => {
    const sidebar = document.querySelector(".sidebar");
    const current = sidebar ? sidebar.getBoundingClientRect().width : 264;
    let next = current;
    if (event.key === "ArrowLeft") next = current - 16;
    else if (event.key === "ArrowRight") next = current + 16;
    else if (event.key === "Home") next = 220;
    else if (event.key === "End") next = 420;
    else return;
    event.preventDefault();
    setSidebarWidth(next, true);
  });
  handle.addEventListener("dblclick", () => setSidebarWidth(264, true));
}

function setIndexCollapsed(collapsed, persist = true) {
  const workspace = document.querySelector(".workspace");
  if (!workspace) return;
  workspace.classList.toggle("index-collapsed", !!collapsed);
  if (persist) {
    try { localStorage.setItem(INDEX_COLLAPSE_STORAGE, collapsed ? "1" : "0"); } catch (_) {}
  }
  if (!collapsed) setTimeout(() => setupMarquee($("index-pane")), 340);
}

function initIndexPane() {
  let stored = false;
  try { stored = localStorage.getItem(INDEX_COLLAPSE_STORAGE) === "1"; } catch (_) {}
  setIndexCollapsed(stored, false);
  const collapse = $("index-collapse");
  const expand = $("index-expand");
  if (collapse) collapse.addEventListener("click", () => {
    setIndexCollapsed(true);
    if (expand) expand.focus({ preventScroll: true });
  });
  if (expand) expand.addEventListener("click", () => {
    setIndexCollapsed(false);
    if (collapse) collapse.focus({ preventScroll: true });
  });
}


const MARQUEE_TARGETS = ".folder-name, .folder-meta, .folder-row-name, .folder-row-meta, .file-name, .file-filename, .file-meta, .file-row-name, .file-row-sub, .file-row-size, .file-row-pkg, .root-name, .index-dir span:first-child, .drawer-name, .drawer-sub, .viewer-name, .viewer-sub, .where, .xn-head b, .xn-head > div > span";
const MARQUEE_HOSTS = ".folder, .folder-row, .file, .file-row, .root, .index-dir, .drawer-head, .viewer-head, .toolbar";
const MARQUEE_HOVER_HOSTS = MARQUEE_HOSTS.split(",").map((item) => item.trim() + ":hover").join(", ");

let marqueeTicker = null;
const marqueeActive = new Set();

function stopMarqueeTicker() {
  if (!marqueeTicker) return;
  if (marqueeTicker.worker) marqueeTicker.worker.terminate();
  if (marqueeTicker.timer) clearTimeout(marqueeTicker.timer);
  marqueeTicker = null;
}

function startFallbackMarqueeTicker() {
  marqueeTicker = { worker: null, timer: 0 };
  const tick = () => {
    if (!marqueeTicker) return;
    if (!marqueeActive.size) {
      marqueeTicker = null;
      return;
    }
    marqueeActive.forEach((el) => updateMarquee(el, performance.now()));
    marqueeTicker.timer = setTimeout(tick, 16);
  };
  tick();
}

function ensureMarqueeTicker() {
  if (marqueeTicker) return;
  try {
    const source = "const id=setInterval(()=>postMessage(0),16);self.onmessage=()=>{clearInterval(id);self.close();};";
    const url = URL.createObjectURL(new Blob([source], { type: "application/javascript" }));
    const worker = new Worker(url);
    URL.revokeObjectURL(url);
    marqueeTicker = { worker, timer: 0 };
    worker.onmessage = () => {
      if (!marqueeTicker || marqueeTicker.worker !== worker) return;
      marqueeActive.forEach((el) => updateMarquee(el, performance.now()));
    };
    worker.onerror = () => {
      if (!marqueeTicker || marqueeTicker.worker !== worker) return;
      worker.terminate();
      marqueeTicker = null;
      if (marqueeActive.size) startFallbackMarqueeTicker();
    };
  } catch (_) {
    startFallbackMarqueeTicker();
  }
}

function updateMarquee(el, now) {
  if (!el.__marqueeRun || !el.isConnected) {
    marqueeActive.delete(el);
    if (!marqueeActive.size) stopMarqueeTicker();
    return;
  }
  if (el.__marqueePausedUntil) {
    if (now < el.__marqueePausedUntil) return;
    el.__marqueePausedUntil = 0;
    el.__marqueeStart = now;
    el.__marqueePos = 0;
    el.classList.remove("marquee-pause");
    el.classList.add("marquee-moving");
    el.style.setProperty("--marquee-shift", "0px");
    return;
  }
  const raw = Math.max(0, now - el.__marqueeStart);
  el.__marqueeStart = now;
  const next = el.__marqueePos + Math.min(raw, 64);
  if (next >= el.__marqueeTravel) {
    el.__marqueePos = 0;
    el.classList.remove("marquee-moving");
    el.classList.add("marquee-pause");
    el.__marqueePausedUntil = now + 2000;
    el.style.setProperty("--marquee-shift", "0px");
    return;
  }
  el.__marqueePos = next;
  el.style.setProperty("--marquee-shift", `${-el.__marqueeLoop * (next / el.__marqueeTravel)}px`);
}

function stopMarquee(el) {
  marqueeActive.delete(el);
  el.__marqueeRun = false;
  el.__marqueeStart = 0;
  el.__marqueeTravel = 0;
  el.__marqueeOverflow = 0;
  el.__marqueePos = 0;
  el.classList.remove("marquee-moving", "marquee-pause");
  el.style.removeProperty("--marquee-shift");
  if (!marqueeActive.size) stopMarqueeTicker();
}

function startMarquee(el) {
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  if (!el.classList.contains("marquee") || el.__marqueeRun) return;
  let inner = el.querySelector(":scope > .marquee-inner");
  if (!inner) {
    inner = document.createElement("span");
    inner.className = "marquee-inner";
    while (el.firstChild) inner.appendChild(el.firstChild);
    el.appendChild(inner);
  }
  if (!inner.querySelector(":scope > .marquee-copy")) {
    const copy = document.createElement("span");
    copy.className = "marquee-copy";
    copy.innerHTML = inner.innerHTML;
    inner.appendChild(copy);
  }
  const first = inner.firstElementChild;
  const unit = first ? first.getBoundingClientRect().width : 0;
  const overflow = el.scrollWidth - el.clientWidth;
  if (overflow < 2 || unit < 8) {
    el.classList.remove("marquee");
    return;
  }
  el.__marqueeRun = true;
  el.__marqueeStart = performance.now();
  el.__marqueeLoop = unit;
  el.__marqueeTravel = Math.max(525, Math.min(6000, unit * 25));
  el.__marqueePos = 0;
  el.classList.add("marquee-moving");
  el.classList.remove("marquee-pause");
  el.style.setProperty("--marquee-shift", "0px");
  marqueeActive.add(el);
  ensureMarqueeTicker();
}

function startHoveredMarquees() {
  document.querySelectorAll(MARQUEE_HOVER_HOSTS).forEach((host) => {
    host.querySelectorAll(".marquee").forEach(startMarquee);
  });
}

function resetMarqueeTarget(el) {
  if (el.__marqueeRun) return false;
  stopMarquee(el);
  el.classList.remove("marquee");
  const inner = el.querySelector(":scope > .marquee-inner");
  if (inner) {
    el.innerHTML = "";
    while (inner.firstChild) {
      const node = inner.firstChild;
      if (node.classList && node.classList.contains("marquee-copy")) break;
      el.appendChild(node);
    }
    el.style.removeProperty("--marquee-shift");
  }
  return true;
}

function measureMarqueeTarget(el) {
  if (el.__marqueeRun) return;
  if (el.clientWidth > 0 && el.scrollWidth - el.clientWidth > 1) el.classList.add("marquee");
}

function setupMarquee(root) {
  if (!root) return;
  const targets = root.querySelectorAll(MARQUEE_TARGETS);
  targets.forEach(resetMarqueeTarget);
  targets.forEach(measureMarqueeTarget);
  startHoveredMarquees();
}

function refreshMarqueeTargets(root) {
  if (!root) return;
  root.querySelectorAll(MARQUEE_TARGETS).forEach((el) => {
    if (resetMarqueeTarget(el)) measureMarqueeTarget(el);
  });
}

function startXnMarquee() {
  document.querySelectorAll("#preview-media .xn-head .marquee").forEach(startMarquee);
}

function stopXnMarquee() {
  document.querySelectorAll("#preview-media .xn-head .marquee").forEach(stopMarquee);
}

function initMarquee() {
  document.addEventListener("mouseover", (event) => {
    if (event.relatedTarget && event.target.contains && event.target.contains(event.relatedTarget)) return;
    const host = event.target && event.target.closest ? event.target.closest(MARQUEE_HOSTS) : null;
    if (!host) return;
    host.querySelectorAll(".marquee").forEach(startMarquee);
  });
  document.addEventListener("mouseout", (event) => {
    const host = event.target && event.target.closest ? event.target.closest(MARQUEE_HOSTS) : null;
    if (!host || (event.relatedTarget && host.contains(event.relatedTarget))) return;
    host.querySelectorAll(".marquee").forEach(stopMarquee);
  });
  let lastHoverScroll = 0;
  window.addEventListener("scroll", () => {
    const now = Date.now();
    if (now - lastHoverScroll < 120) return;
    lastHoverScroll = now;
    startHoveredMarquees();
  }, { passive: true });
}

function syncTopbarHeight() {
  const bar = $("content-topbar");
  if (!bar) return;
  document.documentElement.style.setProperty("--content-topbar-height", `${Math.ceil(bar.getBoundingClientRect().height)}px`);
}

function rerenderFilesOnly() {
  const wrap = document.querySelector("#content .file-list, #content .file-grid");
  if (!wrap) return;
  beginThumbEpoch();
  const body = renderFiles(state.files) || `<div class="empty">${tr(state.searchMode ? "list.noMatches" : "list.empty")}</div>`;
  fileNodeMap = new Map();
  $("content").innerHTML = (state.searchMode ? "" : renderFolders(state.dirs)) + body;
  indexFileNodes($("content"));
  hydrateThumbs($("content"));
  hydrateNames($("content"));
  setupMarquee($("content"));
  setupMarquee($("content-topbar"));
  ensureSentinel();
  syncPickStates();
}

function applyPickBoxes() {
  const list = document.querySelector("#content .file-list");
  if (list) list.classList.toggle("picking", state.selectMode);
  document.querySelectorAll("#content [data-file]").forEach((node) => {
    let box = node.querySelector(".pick-box");
    if (state.selectMode) {
      if (!box) {
        box = document.createElement("span");
        box.className = "pick-box";
        box.dataset.pick = node.dataset.file;
        if (node.classList.contains("file-row")) node.prepend(box);
        else node.insertBefore(box, node.querySelector(".file-info"));
      }
    } else if (box) {
      box.remove();
    }
    node.classList.remove("picked");
  });
}

function setSelectMode(on) {
  state.selectMode = on;
  state.selected = new Set();
  $("select-mode").classList.toggle("active", on);
  $("batch-bar").classList.toggle("hidden", !on);
  closeBatchMenu();
  applyPickBoxes();
  updateBatchBar();
}

function togglePick(path) {
  if (state.selected.has(path)) state.selected.delete(path);
  else state.selected.add(path);
  syncPickStates();
}

function syncPickStates() {
  document.querySelectorAll("#content [data-file]").forEach((node) => {
    const on = state.selected.has(node.dataset.file);
    node.classList.toggle("picked", on);
    const box = node.querySelector(".pick-box");
    if (box) {
      box.classList.toggle("on", on);
      box.innerHTML = on ? icon("check") : "";
    }
  });
  updateBatchBar();
}

function updateBatchBar() {
  const count = state.selected.size;
  $("batch-count").textContent = count.toLocaleString();
  $("batch-clear").disabled = count === 0;
  $("batch-download").disabled = count === 0;
  const allLoaded = state.files.length > 0 && state.files.every((item) => state.selected.has(item.path));
  $("batch-all").disabled = allLoaded;
  if (batchMenuOpen) renderBatchMenu();
}

function renderBatchMenu() {
  const menu = $("batch-menu");
  const count = state.selected.size;
  const rawOk = count > 0 && count <= BATCH_RAW_MAX;
  const convOk = count > 0 && count <= BATCH_CONVERT_MAX;
  const limitNote = (ok, max) => (ok ? "" : `<span class="batch-limit">${tr("batch.overLimit", { max: max.toLocaleString() })}</span>`);
  if (batchMenuPage === "convert") {
    menu.innerHTML = `
      <button class="batch-item batch-back" data-batch-page="main">${icon("chevron-left")}<span>${tr("batch.back")}</span></button>
      <div class="batch-group">
        <div class="batch-group-title">${tr("batch.imageFormat")}</div>
        <div class="batch-chips">${IMAGE_FORMATS.map((fmt) => `<button type="button" class="kind-chip batch-chip${batchPick.img === fmt ? " active" : ""}" data-batch-img="${fmt}">${fmt.toUpperCase()}</button>`).join("")}</div>
      </div>
      <div class="batch-group">
        <div class="batch-group-title">${tr("batch.audioFormat")}</div>
        <div class="batch-chips">${AUDIO_FORMATS.map((fmt) => `<button type="button" class="kind-chip batch-chip${batchPick.aud === fmt ? " active" : ""}" data-batch-aud="${fmt}">${fmt.toUpperCase()}</button>`).join("")}</div>
      </div>
      <button type="button" class="batch-item primary${convOk ? "" : " disabled"}" data-batch-run="each">${icon("download")}<span>${tr("batch.convertEach")}</span>${limitNote(convOk, BATCH_CONVERT_MAX)}</button>
      <button type="button" class="batch-item primary${convOk ? "" : " disabled"}" data-batch-run="zip">${icon("package")}<span>${tr("batch.convertZip")}</span>${limitNote(convOk, BATCH_CONVERT_MAX)}</button>`;
    return;
  }
  menu.innerHTML = `
    <button type="button" class="batch-item primary${rawOk ? "" : " disabled"}" data-batch-run="zip-raw">${icon("package")}<span>${tr("batch.zipRaw")}</span>${limitNote(rawOk, BATCH_RAW_MAX)}</button>
    <button type="button" class="batch-item primary${rawOk ? "" : " disabled"}" data-batch-run="each-raw">${icon("download")}<span>${tr("batch.eachRaw")}</span>${limitNote(rawOk, BATCH_RAW_MAX)}</button>
    <button type="button" class="batch-item" data-batch-page="convert">${icon("sync")}<span>${tr("batch.convertEach")}</span><svg class="icon chev"><use href="#i-chevron-right"></use></svg></button>
    <button type="button" class="batch-item" data-batch-page="convert">${icon("sync")}<span>${tr("batch.convertZip")}</span><svg class="icon chev"><use href="#i-chevron-right"></use></svg></button>`;
}

function openBatchMenu() {
  if (themeState.menuOpen) closeThemeMenu();
  closeDropdown();
  batchMenuOpen = true;
  batchMenuPage = "main";
  renderBatchMenu();
  $("batch-menu").classList.remove("hidden");
}

function closeBatchMenu() {
  batchMenuOpen = false;
  $("batch-menu").classList.add("hidden");
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

async function downloadBatchZip(mode) {
  const count = state.selected.size;
  showBatchToast(tr("batch.zipping"), tr("batch.zippingHint", { count: count.toLocaleString() }), true);
  try {
    const response = await fetch("/api/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: Array.from(state.selected), mode, img: batchPick.img, aud: batchPick.aud }),
    });
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try { message = (await response.json()).error || message; } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    saveBlob(blob, `npkx_batch_${Date.now()}.zip`);
    showBatchToast(tr("batch.zipDone"), tr("batch.zipDoneHint", { size: formatBytes(blob.size) }), false);
  } catch (error) {
    showBatchToast(tr("batch.failed"), error.message, false);
  }
}

async function downloadEachItem(convert) {
  const paths = Array.from(state.selected);
  showBatchToast(tr("batch.downloading"), tr("batch.eachHint", { count: paths.length.toLocaleString() }), true);
  for (const path of paths) {
    const url = convert
      ? apiUrl("/api/convert", { path, img: batchPick.img, aud: batchPick.aud })
      : apiUrl("/api/download", { path });
    const link = document.createElement("a");
    link.href = url;
    link.download = "";
    document.body.appendChild(link);
    link.click();
    link.remove();
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  showBatchToast(tr("batch.done"), tr("batch.eachDoneHint", { count: paths.length.toLocaleString() }), false);
}

function runBatch(kind) {
  if (state.selected.size === 0) return;
  if (kind === "zip-raw") return downloadBatchZip("raw");
  if (kind === "each-raw") return downloadEachItem(false);
  if (kind === "zip") return downloadBatchZip("convert");
  if (kind === "each") return downloadEachItem(true);
}

function showBatchToast(title, text, spinning) {
  const toast = $("batch-toast");
  clearTimeout(batchToastTimer);
  $("batch-toast-title").textContent = title;
  $("batch-toast-text").textContent = text || "";
  $("batch-toast-spin").classList.toggle("hidden", !spinning);
  toast.classList.remove("hidden", "closing");
  void toast.offsetWidth;
  if (!spinning) batchToastTimer = setTimeout(hideBatchToast, 5200);
}

function hideBatchToast() {
  clearTimeout(batchToastTimer);
  const toast = $("batch-toast");
  if (!toast || toast.classList.contains("hidden")) return;
  toast.classList.add("closing");
  batchToastTimer = setTimeout(() => {
    toast.classList.add("hidden");
    toast.classList.remove("closing");
  }, 240);
}

function setView(view) {
  state.view = view;
  localStorage.setItem("npk-view", view);
  updateViewButtons();
  if (state.selectMode) {
    rerenderFilesOnly();
    return;
  }
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
    window.scrollTo(0, 0);
    loadList(state.path, false, false, true);
  }
}

function bind() {
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  initDropdowns();
  initSidebarResize();
  initIndexPane();
  initMarquee();
  initNameHydration();
  $("content").addEventListener("click", (event) => {
    if (event.target.closest("#retry-more")) {
      loadMore();
      return;
    }
    if (state.selectMode) {
      const pick = event.target.closest("[data-file]");
      if (pick) togglePick(pick.dataset.file);
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
        else if (action.dataset.action === "gim") openGimDirect(match);
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
    syncDropdown("sort");
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
  $("select-mode").addEventListener("click", () => setSelectMode(!state.selectMode));
  $("batch-exit").addEventListener("click", () => setSelectMode(false));
  $("batch-all").addEventListener("click", () => {
    state.files.forEach((item) => state.selected.add(item.path));
    syncPickStates();
  });
  $("batch-clear").addEventListener("click", () => {
    state.selected.clear();
    syncPickStates();
  });
  $("batch-download").addEventListener("click", () => {
    if (batchMenuOpen) closeBatchMenu();
    else openBatchMenu();
  });
  $("batch-menu").addEventListener("click", (event) => {
    event.stopPropagation();
    const page = event.target.closest("[data-batch-page]");
    if (page) {
      batchMenuPage = page.dataset.batchPage;
      renderBatchMenu();
      return;
    }
    const img = event.target.closest("[data-batch-img]");
    if (img) {
      batchPick.img = img.dataset.batchImg;
      renderBatchMenu();
      return;
    }
    const aud = event.target.closest("[data-batch-aud]");
    if (aud) {
      batchPick.aud = aud.dataset.batchAud;
      renderBatchMenu();
      return;
    }
    const run = event.target.closest("[data-batch-run]");
    if (run && !run.classList.contains("disabled")) {
      closeBatchMenu();
      runBatch(run.dataset.batchRun);
    }
  });
  document.addEventListener("click", (event) => {
    if (!batchMenuOpen) return;
    const path = event.composedPath ? event.composedPath() : [];
    const inside = path.some((node) => node instanceof Element && node.matches(".batch-download-wrap, #batch-menu"))
      || !!(event.target.closest && event.target.closest(".batch-download-wrap, #batch-menu"));
    if (!inside) closeBatchMenu();
  });
  $("batch-toast-close").addEventListener("click", hideBatchToast);
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
  window.addEventListener("popstate", async (event) => {
    const path = new URLSearchParams(location.search).get("path") || "";
    const scroll = event.state && Number.isFinite(event.state.scroll) ? event.state.scroll : 0;
    pendingRestoreScroll = scroll;
    await loadList(path, false, false, true);
  });
  window.addEventListener("resize", syncTopbarHeight);
  window.addEventListener("resize", () => {
    clearTimeout(marqueeResizeTimer);
    marqueeResizeTimer = setTimeout(() => {
      setupMarquee($("content"));
      setupMarquee($("content-topbar"));
      setupMarquee($("roots"));
      setupMarquee($("index-pane"));
      if (!$("drawer").classList.contains("hidden")) {
        setupMarquee($("drawer"));
        startXnMarquee();
      }
    }, 160);
  });
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
      if (dropdownOpen) closeDropdown();
      else if (themeState.menuOpen) closeThemeMenu();
      else if (batchMenuOpen) closeBatchMenu();
      else if (viewerIsOpen()) closeViewer();
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
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => {
      setupMarquee($("content"));
      setupMarquee($("content-topbar"));
      setupMarquee($("roots"));
      setupMarquee($("index-pane"));
      if (!$("drawer").classList.contains("hidden")) {
        setupMarquee($("drawer"));
        startXnMarquee();
      }
    });
  }
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
  initTheme();
  await initI18n();
  await init();
}

boot();
