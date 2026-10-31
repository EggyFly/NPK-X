(() => {
  "use strict";

  const STORAGE_KEY = "npk-field-icons-v1";
  const MAX_RULES = 200;
  const MAX_CONFIG_BYTES = 3500000;
  const TARGETS = { all: "全部位置", field: "字段键名", object: "资源对象", component: "GMP 组件" };
  const DATA_IMAGE = /^data:image\/(png|jpeg|webp|gif);base64,[a-zA-Z0-9+/]+={0,2}$/;
  const IMAGE_FILE = /\.(kta|ktx|dds|png|jpe?g|webp|gif|tga|bmp)$/i;
  const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char]);

  let config = { version: 1, enabled: true, rules: [] };
  let storageWarning = "";
  let dialog = null;
  let form = null;
  let editingId = null;
  let uploadedImage = "";
  let uploadToken = 0;
  let refreshQueued = false;

  function cleanSource(source) {
    if (typeof source !== "string") throw new Error("请选择贴图或填写资源路径。");
    source = source.trim();
    if (DATA_IMAGE.test(source) && source.length <= 240000) return source;
    const path = source.replace(/\\/g, "/").replace(/^\/+/, "");
    if (!path || path.length > 1000 || /[\x00-\x1f:?#]/.test(path) || path.split("/").includes("..")) {
      throw new Error("贴图只能是上传图片或资源库内的相对路径。");
    }
    if (!IMAGE_FILE.test(path)) throw new Error("资源路径要指向图片或纹理文件。");
    return path;
  }

  function loadConfig(value) {
    if (!value || value.version !== 1 || !Array.isArray(value.rules) || value.rules.length > MAX_RULES) {
      throw new Error("配置格式不正确，最多 200 条规则。");
    }
    const rules = value.rules.map((rule, index) => {
      if (!rule || typeof rule !== "object" || !Object.hasOwn(TARGETS, rule.target) || !["exact", "contains"].includes(rule.match)) {
        throw new Error(`第 ${index + 1} 条规则的匹配设置不正确。`);
      }
      const text = typeof rule.text === "string" ? rule.text.trim() : "";
      if (!text || text.length > 300) throw new Error(`第 ${index + 1} 条规则要填写 1–300 字。`);
      return {
        id: `rule-${index}`,
        target: rule.target,
        match: rule.match,
        text,
        source: cleanSource(rule.source),
        enabled: rule.enabled !== false,
        size: [16, 20, 24, 32].includes(Number(rule.size)) ? Number(rule.size) : 20,
      };
    });
    const result = { version: 1, enabled: value.enabled !== false, rules };
    if (JSON.stringify(result).length > MAX_CONFIG_BYTES) throw new Error("贴图配置太大，请减少图片或规则。");
    return result;
  }

  function findRule(kind, values) {
    if (!config.enabled) return null;
    const names = values.map((value) => String(value ?? "").trim().toLowerCase()).filter(Boolean);
    // 规则从上到下匹配，命中第一条就停。
    return config.rules.find((rule) => rule.enabled && (rule.target === "all" || rule.target === kind)
      && names.some((name) => rule.match === "contains"
        ? name.includes(rule.text.toLowerCase())
        : name === rule.text.toLowerCase())) || null;
  }

  function imageUrl(source) {
    return source.startsWith("data:") ? source : `/api/image?path=${encodeURIComponent(source)}`;
  }

  function label(kind, text, aliases = []) {
    const values = [text, ...aliases].filter((value) => value !== null && value !== undefined && value !== "");
    return `<span class="field-icon-label" data-field-icon-kind="${esc(kind)}" data-field-icon-values="${esc(JSON.stringify(values))}"><span class="field-icon-text">${esc(text)}</span></span>`;
  }

  function decorate(node) {
    let values;
    try { values = JSON.parse(node.dataset.fieldIconValues || "[]"); } catch (_) { values = []; }
    if (!Array.isArray(values)) return;
    const rule = findRule(node.dataset.fieldIconKind, values);
    const current = node.querySelector(":scope > .field-icon-image");
    if (!rule) {
      if (current) current.remove();
      return;
    }
    const src = imageUrl(rule.source);
    if (current && current.getAttribute("src") === src && current.width === rule.size) return;
    if (current) current.remove();
    const image = document.createElement("img");
    image.className = "field-icon-image";
    image.alt = "";
    image.width = rule.size;
    image.height = rule.size;
    image.title = rule.text;
    image.addEventListener("error", () => { image.hidden = true; }, { once: true });
    image.src = src;
    node.prepend(image);
  }

  function refresh() {
    refreshQueued = false;
    document.querySelectorAll("[data-field-icon-kind]").forEach(decorate);
  }

  function scheduleRefresh() {
    if (!refreshQueued) {
      refreshQueued = true;
      requestAnimationFrame(refresh);
    }
  }

  function save(next) {
    const validated = loadConfig(next);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(validated));
    } catch (_) {
      throw new Error("保存失败，请检查浏览器存储权限或减少上传贴图。");
    }
    config = validated;
    refresh();
    renderRules();
  }

  function status(text, error = false) {
    const output = dialog.querySelector("#field-icons-status");
    output.textContent = text;
    output.classList.toggle("field-icons-error", error);
  }

  function ruleRow(rule, index) {
    const meta = `${TARGETS[rule.target]} · ${rule.match === "exact" ? "精确" : "包含"} · ${rule.size}px${rule.enabled ? "" : " · 已停用"}`;
    return `<div class="field-icons-rule" data-rule-index="${index}">
      <img src="${esc(imageUrl(rule.source))}" width="32" height="32" alt="">
      <div class="field-icons-rule-copy"><b>${esc(rule.text)}</b><small>${esc(meta)}</small></div>
      <button type="button" class="mini-btn" data-rule-action="toggle">${rule.enabled ? "停用" : "启用"}</button>
      <button type="button" class="mini-btn" data-rule-action="edit">编辑</button>
      <button type="button" class="mini-btn" data-rule-action="delete">删除</button>
    </div>`;
  }

  function renderRules() {
    if (!dialog) return;
    const list = dialog.querySelector("#field-icons-rules");
    dialog.querySelector("#field-icons-enabled").checked = config.enabled;
    list.innerHTML = config.rules.length
      ? config.rules.map(ruleRow).join("")
      : '<p class="field-icons-empty">还没有规则，添加后会显示在匹配的名称前。</p>';
  }

  function resetForm() {
    editingId = null;
    uploadedImage = "";
    uploadToken += 1;
    form.reset();
    form.elements.size.value = "20";
    dialog.querySelector("#field-icons-save").textContent = "添加规则";
    dialog.querySelector("#field-icons-cancel-edit").hidden = true;
    updatePreview();
  }

  function updatePreview() {
    const image = dialog.querySelector("#field-icons-preview");
    const source = uploadedImage || form.elements.resource.value.trim();
    image.hidden = !source;
    image.removeAttribute("src");
    if (source) {
      try { image.src = imageUrl(cleanSource(source)); } catch (_) { image.hidden = true; }
    }
    dialog.querySelector("#field-icons-preview-label").textContent = form.elements.text.value || "字段 / 对象 / 组件名称";
    image.width = image.height = Number(form.elements.size.value) || 20;
  }

  async function readUpload(file) {
    if (!file || !["image/png", "image/jpeg", "image/webp", "image/gif"].includes(file.type)) {
      throw new Error("请选择 PNG、JPG、WebP 或 GIF 图片。");
    }
    if (file.size > 8 * 1024 * 1024) throw new Error("图片不能超过 8 MB。");
    const url = URL.createObjectURL(file);
    try {
      const image = new Image();
      image.src = url;
      await image.decode();
      const ratio = Math.min(1, 128 / Math.max(image.naturalWidth, image.naturalHeight));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.naturalWidth * ratio));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * ratio));
      canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
      // 图片压到 128px 内直接存进配置，不经过服务器。
      return canvas.toDataURL("image/png");
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  function createDialog() {
    dialog = document.createElement("dialog");
    dialog.id = "field-icons-dialog";
    dialog.setAttribute("aria-labelledby", "field-icons-title");
    dialog.innerHTML = `
      <div class="field-icons-head"><h2 id="field-icons-title">字段贴图</h2><button id="field-icons-close" class="icon-btn" type="button" aria-label="关闭">×</button></div>
      <div class="field-icons-body">
        <p class="field-icons-hint">在字段、对象和 GMP 组件名称前显示贴图。规则从上到下匹配，命中第一条生效，不区分大小写。</p>
        <label class="field-icons-enable"><input id="field-icons-enabled" type="checkbox">启用字段贴图</label>
        <form id="field-icons-form">
          <div class="field-icons-form-grid">
            <label>作用位置<select name="target">${Object.entries(TARGETS).map(([key, text]) => `<option value="${key}">${text}</option>`).join("")}</select></label>
            <label>匹配方式<select name="match"><option value="exact">精确匹配</option><option value="contains">包含文字</option></select></label>
            <label class="field-icons-wide">匹配内容<input name="text" maxlength="300" required placeholder="例如 Mesh、小地板、100001 或 props/…/model.gim"></label>
            <label class="field-icons-wide">资源贴图路径<input name="resource" placeholder="例如 gui2/main/img_spawn_qipao.kta"></label>
            <label>或上传本地图片<input name="upload" type="file" accept="image/png,image/jpeg,image/webp,image/gif"></label>
            <label>显示尺寸<select name="size"><option value="16">16 px</option><option value="20" selected>20 px</option><option value="24">24 px</option><option value="32">32 px</option></select></label>
          </div>
          <div class="field-icons-preview"><img id="field-icons-preview" alt="" hidden><span id="field-icons-preview-label">字段 / 对象 / 组件名称</span></div>
          <div class="field-icons-actions"><button id="field-icons-save" class="btn primary" type="submit">添加规则</button><button id="field-icons-cancel-edit" class="btn" type="button" hidden>取消编辑</button></div>
        </form>
        <p id="field-icons-status" role="status" aria-live="polite"></p>
        <div class="field-icons-list-head"><h3>已保存的规则</h3><div class="field-icons-actions"><button id="field-icons-import" class="mini-btn" type="button">导入配置</button><button id="field-icons-export" class="mini-btn" type="button">导出配置</button><input id="field-icons-import-file" type="file" accept="application/json,.json" hidden></div></div>
        <div id="field-icons-rules"></div>
        <p class="field-icons-hint">配置存在当前浏览器，刷新后保留。组件可按名称、eid、组件 ID 或模型路径匹配；对象可按名称或索引匹配。</p>
      </div>`;
    document.body.appendChild(dialog);
    form = dialog.querySelector("form");

    const q = (selector) => dialog.querySelector(selector);
    const preview = q("#field-icons-preview");
    preview.addEventListener("error", () => {
      if (!preview.getAttribute("src")) return;
      preview.hidden = true;
      status("贴图加载失败，请检查资源路径，或改用上传图片。", true);
    });

    q("#field-icons-close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) {
        dialog.close();
      }
    });
    dialog.addEventListener("keydown", (event) => event.stopPropagation());

    q("#field-icons-cancel-edit").addEventListener("click", resetForm);
    q("#field-icons-enabled").addEventListener("change", (event) => {
      try {
        save({ ...config, enabled: event.target.checked });
        status(event.target.checked ? "字段贴图已启用。" : "字段贴图已停用。");
      } catch (error) {
        event.target.checked = config.enabled;
        status(error.message, true);
      }
    });

    form.elements.text.addEventListener("input", updatePreview);
    form.elements.size.addEventListener("change", updatePreview);
    form.elements.resource.addEventListener("input", () => {
      uploadedImage = "";
      uploadToken += 1;
      form.elements.upload.value = "";
      updatePreview();
    });
    form.elements.upload.addEventListener("change", async (event) => {
      const token = ++uploadToken;
      const file = event.target.files[0];
      if (!file) return;
      try {
        const image = await readUpload(file);
        if (token !== uploadToken) return;
        uploadedImage = image;
        form.elements.resource.value = "";
        updatePreview();
        status("图片已就绪，保存后生效。");
      } catch (error) {
        if (token === uploadToken) status(error.message, true);
      }
    });

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      try {
        const rule = {
          id: editingId,
          target: form.elements.target.value,
          match: form.elements.match.value,
          text: form.elements.text.value,
          source: uploadedImage || form.elements.resource.value,
          enabled: true,
          size: Number(form.elements.size.value),
        };
        const rules = [...config.rules];
        const index = editingId === null ? -1 : rules.findIndex((item) => item.id === editingId);
        if (index < 0) rules.push(rule);
        else {
          rule.enabled = rules[index].enabled;
          rules[index] = rule;
        }
        save({ ...config, rules });
        resetForm();
        status("规则已保存。");
      } catch (error) {
        status(error.message, true);
      }
    });

    q("#field-icons-rules").addEventListener("click", (event) => {
      const button = event.target.closest("[data-rule-action]");
      if (!button) return;
      const index = Number(button.closest("[data-rule-index]").dataset.ruleIndex);
      const rule = config.rules[index];
      if (!rule) return;
      if (button.dataset.ruleAction === "edit") {
        resetForm();
        editingId = rule.id;
        for (const key of ["target", "match", "text", "size"]) form.elements[key].value = rule[key];
        if (rule.source.startsWith("data:")) uploadedImage = rule.source;
        else form.elements.resource.value = rule.source;
        q("#field-icons-save").textContent = "保存修改";
        q("#field-icons-cancel-edit").hidden = false;
        updatePreview();
        form.elements.text.focus();
        return;
      }
      try {
        const rules = config.rules.map((item) => ({ ...item }));
        if (button.dataset.ruleAction === "toggle") rules[index].enabled = !rule.enabled;
        else rules.splice(index, 1);
        save({ ...config, rules });
        resetForm();
        status("规则已更新。");
      } catch (error) {
        status(error.message, true);
      }
    });

    q("#field-icons-import").addEventListener("click", () => q("#field-icons-import-file").click());
    q("#field-icons-import-file").addEventListener("change", async (event) => {
      const file = event.target.files[0];
      if (!file) return;
      try {
        if (file.size > MAX_CONFIG_BYTES) throw new Error("配置文件太大。");
        const imported = loadConfig(JSON.parse(await file.text()));
        // 导入只追加规则，保留当前规则和启用状态。
        save({ ...config, rules: [...config.rules, ...imported.rules] });
        resetForm();
        status(`已导入 ${imported.rules.length} 条规则。`);
      } catch (error) {
        status(`导入失败：${error.message}`, true);
      } finally {
        event.target.value = "";
      }
    });
    q("#field-icons-export").addEventListener("click", () => {
      const blob = new Blob([JSON.stringify(config, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "npk-field-icons.json";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      status("已导出配置。");
    });

    renderRules();
  }

  function open() {
    if (!dialog) createDialog();
    renderRules();
    status(storageWarning);
    if (!dialog.open) dialog.showModal();
  }

  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) config = loadConfig(JSON.parse(stored));
  } catch (_) {
    storageWarning = "已保存的配置无法读取，请重新导入。";
  }

  window.FieldIcons = { label, open, refresh };

  function start() {
    document.getElementById("field-icons-open")?.addEventListener("click", open);
    // 地图刷新会重建大量标签，只处理新增节点，避免每帧全页扫描。
    new MutationObserver((records) => {
      if (records.some((record) => [...record.addedNodes].some((node) => node.nodeType === 1
        && (node.matches("[data-field-icon-kind]") || node.querySelector("[data-field-icon-kind]"))))) {
        scheduleRefresh();
      }
    }).observe(document.body, { childList: true, subtree: true });
    window.addEventListener("storage", (event) => {
      if (event.key !== STORAGE_KEY) return;
      try {
        config = event.newValue ? loadConfig(JSON.parse(event.newValue)) : { version: 1, enabled: true, rules: [] };
        refresh();
        renderRules();
      } catch (_) {}
    });
    refresh();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
