(function () {
  "use strict";

  if (typeof window === "undefined") return;
  if (window.renderStructPreview) return;
  window.STRUCT_PREVIEW_EXT = new Set([
    ".bin", ".eggimonbin", ".meta", ".efx", ".scn", ".trk", ".cache",
  ]);

  var STYLE_ID = "struct-preview-style";
  var CSS = [
    ".struct-preview{display:flex;flex-direction:column;gap:10px;width:100%;padding:12px;border:1px solid var(--md-outline-variant,#3f484b);border-radius:var(--md-shape-lg,16px);background:var(--md-surface-container-low,#171c1f);color:var(--md-on-surface,#dfe3e6);box-sizing:border-box}",
    ".struct-preview *{box-sizing:border-box}",
    ".struct-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap}",
    ".struct-title{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;letter-spacing:.2px}",
    ".struct-title .icon{width:16px;height:16px;color:var(--md-primary,#7bdbe6)}",
    ".struct-preview .bank-chips{display:flex;flex-wrap:wrap;gap:5px}",
    ".struct-preview .bank-chip{display:inline-flex;align-items:center;gap:5px;border:0;border-radius:var(--md-shape-sm,8px);padding:4px 8px;background:var(--md-surface-container-high,#252b2e);color:var(--md-on-surface-variant,#bfc8cb);font:10px/1.4 var(--md-font-mono,Consolas,monospace);white-space:nowrap}",
    ".struct-preview .bank-chip b{color:var(--md-on-surface,#dfe3e6);font-weight:600;word-break:break-all;white-space:normal}",
    ".struct-warn{border:1px dashed var(--md-error,#ffb4ab);border-radius:var(--md-shape-sm,8px);padding:6px 8px;color:var(--md-error,#ffb4ab);font-size:10px;line-height:1.5}",
    ".struct-section-title{color:var(--md-on-surface-variant,#bfc8cb);font-size:10px;font-weight:600;letter-spacing:.3px;margin:0 0 6px}",
    ".struct-toolbar{display:flex;gap:4px;margin-left:auto}",
    ".struct-mini{border:0;border-radius:var(--md-shape-sm,8px);background:var(--md-surface-container-high,#252b2e);color:var(--md-on-surface-variant,#bfc8cb);font:9px/1.4 var(--md-font-mono,Consolas,monospace);padding:3px 6px;cursor:pointer}",
    ".struct-mini:hover{background:var(--md-surface-container-highest,#303639);color:var(--md-on-surface,#dfe3e6)}",
    ".struct-tree{overflow:auto;max-height:420px;padding:6px 8px;border-radius:var(--md-shape-sm,8px);background:var(--md-surface-container-lowest,#0a0e10);font:11px/1.55 var(--md-font-mono,Consolas,monospace)}",
    ".struct-node{min-width:0}",
    ".struct-row{display:flex;align-items:baseline;gap:4px;min-width:0}",
    ".struct-toggle{flex:none;width:14px;height:18px;display:inline-flex;align-items:center;justify-content:center;border:0;background:transparent;color:var(--md-on-surface-variant,#bfc8cb);padding:0;cursor:pointer;visibility:hidden}",
    ".struct-toggle.has-children{visibility:visible}",
    ".struct-toggle svg{width:10px;height:10px;transition:transform .14s ease}",
    ".struct-node.is-open>.struct-row .struct-toggle svg{transform:rotate(90deg)}",
    ".struct-key{color:var(--md-primary,#7bdbe6);word-break:break-all}",
    ".struct-val{color:var(--md-on-surface,#dfe3e6);word-break:break-all;white-space:pre-wrap}",
    ".struct-val.t-str{color:#9cdcaa}.struct-val.t-num{color:#7bdbe6}.struct-val.t-bool{color:#d3b7ff}.struct-val.t-null{color:var(--md-on-surface-variant,#bfc8cb)}.struct-val.t-bin{color:var(--md-on-surface-variant,#bfc8cb);font-style:italic}.struct-val.t-more{color:var(--md-on-surface-variant,#bfc8cb);font-style:italic}",
    ".struct-children{display:none;margin-left:9px;padding-left:8px;border-left:1px solid var(--md-outline-variant,#3f484b)}",
    ".struct-node.is-open>.struct-children{display:block}",
    ".struct-pairs{display:grid;grid-template-columns:minmax(90px,auto) minmax(0,1fr);gap:2px 10px;overflow:auto;max-height:340px;padding:6px 8px;border-radius:var(--md-shape-sm,8px);background:var(--md-surface-container-lowest,#0a0e10);font:11px/1.6 var(--md-font-mono,Consolas,monospace)}",
    ".struct-pairs .k{color:var(--md-primary,#7bdbe6);word-break:break-all}",
    ".struct-pairs .v{color:var(--md-on-surface,#dfe3e6);word-break:break-all}",
    ".struct-canvas{display:block;width:100%;border-radius:var(--md-shape-sm,8px);background:var(--md-canvas-bg,#0a0e10)}",
    ".struct-plot{display:block;width:100%;height:260px;border-radius:var(--md-shape-sm,8px);background:var(--md-canvas-bg,#0a0e10)}",
    ".struct-refs{display:flex;flex-wrap:wrap;gap:5px}",
    ".struct-refs .inspect-ref{max-width:100%}",
    ".struct-pre{overflow:auto;max-height:420px;margin:0;padding:8px 10px;border-radius:var(--md-shape-sm,8px);background:var(--md-surface-container-lowest,#0a0e10);font:11px/1.55 var(--md-font-mono,Consolas,monospace);color:var(--md-on-surface,#dfe3e6)}",
    ".struct-details summary{cursor:pointer;color:var(--md-on-surface-variant,#bfc8cb);font-size:10px;font-weight:600;letter-spacing:.3px}",
    ".struct-details[open] summary{margin-bottom:6px}",
    "@media (max-width:720px){.struct-tree,.struct-pairs{max-height:280px}.struct-plot{height:200px}}"
  ].join("");

  var CHEVRON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  var DEFAULTS = {
    "struct.title.bin": ".bin 结构",
    "struct.title.eggimonbin": "蛋仔实例数据",
    "struct.title.meta": "NXSC 元数据",
    "struct.title.efx": "NeoX 特效属性",
    "struct.title.scn": "NeoX 场景对象",
    "struct.title.trk": "相机轨道",
    "struct.title.cache": ".cache 着色器",
    "struct.tree": "结构树",
    "struct.expandAll": "展开",
    "struct.collapseAll": "收起",
    "struct.layers": "解码层",
    "struct.mpNodes": "节点",
    "struct.mpDepth": "深度",
    "struct.hmSize": "尺寸",
    "struct.hmMin": "最小高程",
    "struct.hmMax": "最大高程",
    "struct.hmTooBig": "高程网格过大，仅显示统计信息",
    "struct.trkName": "轨道名",
    "struct.trkKeys": "关键帧",
    "struct.trkDuration": "时长",
    "struct.trkFps": "估算帧率",
    "struct.trkChannels": "通道标志",
    "struct.trkQuats": "旋转关键帧",
    "struct.trkQuatSample": "首帧四元数",
    "struct.efxVersion": "版本",
    "struct.efxStrings": "明文属性",
    "struct.efxCoverage": "明文覆盖",
    "struct.efxObjects": "对象",
    "struct.efxProps": "属性 (名 → 值)",
    "struct.metaHash": "头哈希",
    "struct.metaSchema": "Schema ID",
    "struct.metaCompressed": "压缩",
    "struct.metaRaw": "未压缩",
    "struct.metaRecords": "哈希记录",
    "struct.metaOff12": "批次标签",
    "struct.shaderFrames": "帧数",
    "struct.shaderGlsl": "GLSL 段",
    "struct.shaderUniforms": "Uniform 表帧",
    "struct.shaderDict": "字典帧",
    "struct.shaderUniformTable": "Uniform 表",
    "struct.shaderVertex": "顶点着色器 (VS)",
    "struct.shaderFragment": "片段着色器 (PS)",
    "struct.shaderVariant": "GLSL 变体",
    "struct.shaderMore": "其余帧",
    "struct.shaderLayoutHex": "布局帧 Hex (压缩字典)",
    "struct.shaderRowsCut": "Uniform 行数过多，仅展示前一部分。",
    "struct.shaderTextCut": "源码过长，仅展示前一部分。",
    "struct.cacheHead": "缓存头",
    "struct.scnObjects": "对象",
    "struct.scnNames": "名称池",
    "struct.scnFields": "字段",
    "struct.bdRows": "行",
    "struct.bdStrings": "字符串",
    "struct.bdUnits": "单位",
    "struct.bdStringPool": "字符串池",
    "struct.unknown": "这个格式返回了未知结构，请查看 Hex。",
    "inspect.references": "引用资源",
    "inspect.hex": "原始 Hex（前 4 KB）",
    "text.tooLarge": "文件过大，仅显示前一部分。",
    "inspect.binEmpty": "全零占位文件，没有实际内容。",
    "inspect.binZstdOnly": "Zstd 解压成功，但内容不是 MessagePack 结构。",
    "inspect.binZlib": "Zlib 解压成功，内容为二进制数据块。",
    "inspect.binUnknown": "未能识别这个 .bin 的内部结构，请查看 Hex。",
    "inspect.metaNoRecords": "未识别出哈希记录表，请查看解压后内容的 Hex。",
    "inspect.efxHashRecords": "属性流中混有哈希化的属性记录，仅能展示明文部分。",
    "inspect.trkMultiChannel": "多通道轨道，仅解析出位置/旋转等已确认的通道。",
    "inspect.trkNoTimes": "未找到单调时间轴。",
    "inspect.trkChannelsSkip": "通道数据未能按已确认的布局解析。",
    "inspect.scnStopEarly": "遇到未支持的对象结构，提前停止解析。",
    "inspect.scnObjectCap": "对象数过多，仅展示前一部分。",
  };

  var langCache = null;
  var langPromise = null;
  var langPromiseCode = null;

  function currentCode() {
    var stored = null;
    try { stored = localStorage.getItem("npk-language"); } catch (_) {}
    if (stored && stored !== "auto") return stored;
    var tag = String((navigator.languages && navigator.languages[0]) || navigator.language || "en-US").toLowerCase();
    if (tag.indexOf("zh") === 0) return /(tw|hk|mo)/.test(tag) ? "zh_tw" : "zh_cn";
    return "en_us";
  }

  function loadLang() {
    var code = currentCode();
    if (langCache && langCache.code === code) return Promise.resolve(langCache.map);
    if (!langPromise || langPromiseCode !== code) {
      langPromiseCode = code;
      langPromise = fetch("/language/" + code + ".json", { cache: "no-store" })
        .then(function (response) { return response.ok ? response.json() : null; })
        .then(function (json) { langCache = { code: code, map: (json && json.i18n) || {} }; return langCache.map; })
        .catch(function () { langCache = { code: code, map: {} }; return langCache.map; });
    }
    return langPromise;
  }
  loadLang();

  function tr(key, vars) {
    var map = (langCache && langCache.map) || {};
    var text = map[key] !== undefined ? map[key] : DEFAULTS[key] !== undefined ? DEFAULTS[key] : key;
    return String(text).replace(/\{(\w+)\}/g, function (_, name) {
      return vars && vars[name] !== undefined ? String(vars[name]) : "{" + name + "}";
    });
  }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function toElement(target) {
    if (typeof target === "string") return document.querySelector(target);
    return target && target.nodeType === 1 ? target : null;
  }

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  function chips(items) {
    var box = document.createElement("div");
    box.className = "bank-chips";
    box.innerHTML = items.filter(Boolean).map(function (item) {
      return `<span class="bank-chip">${item[0]} <b>${esc(item[1])}</b></span>`;
    }).join("");
    return box;
  }

  function sectionTitle(key) {
    return `<div class="struct-section-title">${esc(tr(key))}</div>`;
  }

  function fmtNumber(value) {
    var num = Number(value);
    if (!Number.isFinite(num)) return esc(value);
    if (Math.abs(num) >= 1e6 || (Math.abs(num) > 0 && Math.abs(num) < 1e-4)) return num.toExponential(3);
    return String(Math.round(num * 10000) / 10000);
  }

  function valueClass(value) {
    if (value === null || value === undefined) return "t-null";
    var type = typeof value;
    if (type === "string") return "t-str";
    if (type === "number") return "t-num";
    if (type === "boolean") return "t-bool";
    return "t-bin";
  }

  function valueText(value) {
    if (value === null || value === undefined) return "null";
    if (typeof value === "string") return `"${value}"`;
    if (typeof value === "number" || typeof value === "boolean") return fmtNumber(value);
    return "{}";
  }

  function hasChildren(value) {
    return value !== null && typeof value === "object";
  }

  function childEntries(value) {
    if (Array.isArray(value)) return value.map(function (item, index) { return [String(index), item]; });
    return Object.keys(value).map(function (key) { return [key, value[key]]; });
  }

  function buildTree(key, value, depth) {
    var node = document.createElement("div");
    node.className = "struct-node";
    var row = document.createElement("div");
    row.className = "struct-row";
    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "struct-toggle";
    toggle.innerHTML = CHEVRON;
    row.appendChild(toggle);
    if (key !== null) {
      var keyNode = document.createElement("span");
      keyNode.className = "struct-key";
      keyNode.textContent = key;
      row.appendChild(keyNode);
    }
    var more = value && typeof value === "object" && !Array.isArray(value) && value.__more__ !== undefined ? value.__more__ : null;
    var cut = value && typeof value === "object" && value.__cut__ !== undefined;
    if (hasChildren(value) && !cut) {
      var entries = childEntries(value).filter(function (entry) { return entry[0] !== "__more__" && entry[0] !== "__cut__"; });
      var bracket = Array.isArray(value) ? "[" : "{";
      var close = Array.isArray(value) ? "]" : "}";
      var summary = document.createElement("span");
      summary.className = "struct-val t-bin";
      summary.textContent = `${bracket} ${entries.length}${more !== null ? " +" + more : ""} ${close}`;
      row.appendChild(summary);
      node.appendChild(row);
      var children = document.createElement("div");
      children.className = "struct-children";
      entries.forEach(function (entry) {
        children.appendChild(buildTree(entry[0], entry[1], depth + 1));
      });
      if (more !== null) {
        var moreNode = document.createElement("div");
        moreNode.className = "struct-row";
        moreNode.innerHTML = `<span class="struct-val t-more">… +${more}</span>`;
        children.appendChild(moreNode);
      }
      node.appendChild(children);
      if (depth < 2) node.classList.add("is-open");
      toggle.classList.add("has-children");
      toggle.addEventListener("click", function () {
        node.classList.toggle("is-open");
      });
    } else {
      var val = document.createElement("span");
      val.className = "struct-val " + (cut ? "t-more" : valueClass(value));
      val.textContent = cut ? "…" : valueText(value);
      row.appendChild(val);
      node.appendChild(row);
    }
    return node;
  }

  function treeSection(value) {
    var wrap = document.createElement("div");
    var head = document.createElement("div");
    head.className = "struct-row";
    head.innerHTML = sectionTitle("struct.tree");
    var actions = document.createElement("div");
    actions.className = "struct-toolbar";
    var expand = document.createElement("button");
    expand.type = "button";
    expand.className = "struct-mini";
    expand.textContent = tr("struct.expandAll");
    var collapse = document.createElement("button");
    collapse.type = "button";
    collapse.className = "struct-mini";
    collapse.textContent = tr("struct.collapseAll");
    actions.appendChild(expand);
    actions.appendChild(collapse);
    head.appendChild(actions);
    wrap.appendChild(head);
    var tree = document.createElement("div");
    tree.className = "struct-tree";
    tree.appendChild(buildTree(null, value, 0));
    expand.addEventListener("click", function () {
      tree.querySelectorAll(".struct-toggle.has-children").forEach(function (button) {
        button.closest(".struct-node").classList.add("is-open");
      });
    });
    collapse.addEventListener("click", function () {
      tree.querySelectorAll(".struct-node.is-open").forEach(function (node) {
        if (node.parentElement === tree) return;
        node.classList.remove("is-open");
      });
    });
    wrap.appendChild(tree);
    return wrap;
  }

  function refsSection(refs) {
    var wrap = document.createElement("div");
    wrap.innerHTML = sectionTitle("inspect.references");
    var box = document.createElement("div");
    box.className = "struct-refs";
    refs.slice(0, 80).forEach(function (ref) {
      var span = document.createElement("span");
      span.className = "inspect-ref";
      span.title = ref;
      span.textContent = ref;
      box.appendChild(span);
    });
    wrap.appendChild(box);
    return wrap;
  }

  function hexDetails(hexLines) {
    var details = document.createElement("details");
    details.className = "struct-details";
    details.innerHTML = `<summary>${esc(tr("inspect.hex"))}</summary>`;
    var pre = document.createElement("pre");
    pre.className = "struct-pre";
    pre.textContent = (hexLines || []).join("\n");
    details.appendChild(pre);
    return details;
  }

  function renderHeightmap(root, data) {
    root.appendChild(chips([
      [tr("struct.hmSize"), `${data.width} × ${data.height}`],
      [tr("struct.hmMin"), fmtNumber(data.min)],
      [tr("struct.hmMax"), fmtNumber(data.max)],
    ]));
    if (data.values_truncated || !data.values || data.values.length < data.width * data.height) {
      var tooBig = document.createElement("div");
      tooBig.className = "struct-warn";
      tooBig.textContent = tr("struct.hmTooBig");
      root.appendChild(tooBig);
      return;
    }
    var canvas = document.createElement("canvas");
    canvas.className = "struct-canvas";
    canvas.width = Math.min(data.width, 1024);
    canvas.height = Math.max(1, Math.round(canvas.width * data.height / data.width));
    var ctx = canvas.getContext("2d");
    var image = ctx.createImageData(canvas.width, canvas.height);
    var span = Math.max(1, data.max - data.min);
    for (var y = 0; y < canvas.height; y += 1) {
      for (var x = 0; x < canvas.width; x += 1) {
        var sx = Math.min(data.width - 1, Math.floor(x * data.width / canvas.width));
        var sy = Math.min(data.height - 1, Math.floor(y * data.height / canvas.height));
        var value = data.values[sy * data.width + sx];
        var t = (value - data.min) / span;
        var offset = (y * canvas.width + x) * 4;
        image.data[offset] = Math.round(38 + t * 80);
        image.data[offset + 1] = Math.round(64 + t * 130);
        image.data[offset + 2] = Math.round(52 + t * 90);
        image.data[offset + 3] = 255;
      }
    }
    ctx.putImageData(image, 0, 0);
    root.appendChild(canvas);
  }

  function renderTrk(root, data) {
    var fps = data.duration > 0 ? Math.round((data.key_count - 1) / (data.duration / 1000)) : 0;
    root.appendChild(chips([
      [tr("struct.trkName"), data.name || "—"],
      [tr("struct.trkKeys"), data.key_count],
      [tr("struct.trkDuration"), `${fmtNumber(data.duration)} ms`],
      fps > 0 ? [tr("struct.trkFps"), fps] : null,
      [tr("struct.trkChannels"), `A=${data.flags[0]} pos=${data.flags[1]} rot=${data.flags[2]}`],
    ]));
    var positions = data.positions;
    if (positions && positions.length >= 6) {
      var count = Math.floor(positions.length / 3);
      var xs = [];
      var zs = [];
      for (var index = 0; index < count; index += 1) {
        xs.push(positions[index * 3]);
        zs.push(positions[index * 3 + 2]);
      }
      var minX = Math.min.apply(null, xs);
      var maxX = Math.max.apply(null, xs);
      var minZ = Math.min.apply(null, zs);
      var maxZ = Math.max.apply(null, zs);
      var width = Math.max(1e-6, maxX - minX);
      var depth = Math.max(1e-6, maxZ - minZ);
      var svgNs = "http://www.w3.org/2000/svg";
      var svg = document.createElementNS(svgNs, "svg");
      svg.setAttribute("class", "struct-plot");
      svg.setAttribute("viewBox", "0 0 600 260");
      svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
      var pad = 18;
      var scale = Math.min((600 - pad * 2) / width, (260 - pad * 2) / depth);
      var ox = pad + ((600 - pad * 2) - width * scale) / 2;
      var oz = pad + ((260 - pad * 2) - depth * scale) / 2;
      var points = [];
      for (var step = 0; step < count; step += 1) {
        var px = ox + (xs[step] - minX) * scale;
        var pz = oz + (zs[step] - minZ) * scale;
        points.push(`${px.toFixed(1)},${pz.toFixed(1)}`);
      }
      var style = document.createElementNS(svgNs, "style");
      style.textContent = ".struct-plot polyline{fill:none;stroke:var(--md-primary,#7bdbe6);stroke-width:1.6;stroke-linejoin:round}.struct-plot circle{fill:var(--md-primary,#7bdbe6)}.struct-plot circle.end{fill:var(--md-error,#ffb4ab)}.struct-plot text{fill:var(--md-on-surface-variant,#bfc8cb);font:10px Consolas,monospace}";
      svg.appendChild(style);
      var poly = document.createElementNS(svgNs, "polyline");
      poly.setAttribute("points", points.join(" "));
      svg.appendChild(poly);
      [0, count - 1].forEach(function (marker, slot) {
        var coords = points[marker].split(",");
        var dot = document.createElementNS(svgNs, "circle");
        dot.setAttribute("cx", coords[0]);
        dot.setAttribute("cy", coords[1]);
        dot.setAttribute("r", slot ? 4 : 3.4);
        if (slot) dot.setAttribute("class", "end");
        svg.appendChild(dot);
      });
      var label = document.createElementNS(svgNs, "text");
      label.setAttribute("x", 10);
      label.setAttribute("y", 248);
      label.textContent = `x-z ${count} pts`;
      svg.appendChild(label);
      root.appendChild(svg);
    }
    if (data.quats && data.quats.length >= 4) {
      var quatCount = Math.floor(data.quats.length / 4);
      var sample = data.quats.slice(0, 4).map(function (value) { return fmtNumber(value); }).join(", ");
      root.appendChild(chips([
        [tr("struct.trkQuats"), quatCount],
        [tr("struct.trkQuatSample"), `[${sample}]`],
      ]));
    }
  }

  function renderEfx(root, data) {
    root.appendChild(chips([
      [tr("struct.efxVersion"), data.version],
      [tr("struct.efxStrings"), data.string_count],
      [tr("struct.efxCoverage"), `${data.coverage}%`],
      data.objects && data.objects.length ? [tr("struct.efxObjects"), data.objects.length] : null,
    ]));
    if (data.objects && data.objects.length) {
      root.appendChild(chips(data.objects.slice(0, 12).map(function (name) { return ["◦", name]; })));
    }
    if (data.pairs && data.pairs.length) {
      var box = document.createElement("div");
      box.innerHTML = sectionTitle("struct.efxProps");
      var pairs = document.createElement("div");
      pairs.className = "struct-pairs";
      data.pairs.slice(0, 400).forEach(function (pair) {
        var key = document.createElement("div");
        key.className = "k";
        key.textContent = pair[0];
        var value = document.createElement("div");
        value.className = "v";
        value.textContent = pair[1];
        pairs.appendChild(key);
        pairs.appendChild(value);
      });
      box.appendChild(pairs);
      root.appendChild(box);
    } else if (data.hex_head) {
      root.appendChild(hexDetails(data.hex_head));
    }
  }

  function renderMeta(root, data) {
    root.appendChild(chips([
      [tr("struct.metaHash"), data.hash],
      [tr("struct.metaSchema"), data.schema_id],
      [tr("struct.metaCompressed"), data.compressed ? `${data.body_size} → ${data.inner_size}` : tr("struct.metaRaw")],
      data.off12 ? [tr("struct.metaOff12"), data.off12] : null,
    ]));
    if (data.record_count) {
      root.appendChild(chips([
        [tr("struct.metaRecords"), `${data.record_count} × ${data.record_stride}B`],
      ]));
      var box = document.createElement("div");
      box.innerHTML = sectionTitle("struct.metaRecords");
      var pairs = document.createElement("div");
      pairs.className = "struct-pairs";
      (data.records || []).slice(0, 300).forEach(function (record) {
        var key = document.createElement("div");
        key.className = "k";
        key.textContent = record.hash;
        var value = document.createElement("div");
        value.className = "v";
        value.textContent = record.flags + (record.tail ? " · " + record.tail.slice(0, 64) : "");
        pairs.appendChild(key);
        pairs.appendChild(value);
      });
      box.appendChild(pairs);
      root.appendChild(box);
    } else if (data.hex_head) {
      root.appendChild(hexDetails(data.hex_head));
    }
  }

  function glslBlock(frame, open) {
    var label = frame.role === "vs" ? tr("struct.shaderVertex")
      : frame.role === "ps" ? tr("struct.shaderFragment") : tr("struct.shaderVariant");
    var details = document.createElement("details");
    details.className = "struct-details";
    if (open) details.setAttribute("open", "");
    details.innerHTML = `<summary>${esc(label)} · #${frame.index} · ${frame.size} B</summary>`;
    var pre = document.createElement("pre");
    pre.className = "struct-pre";
    if (window.SyntaxHighlight) {
      pre.innerHTML = window.SyntaxHighlight.render(frame.text, "hlsl") || esc(frame.text);
    } else {
      pre.textContent = frame.text;
    }
    details.appendChild(pre);
    if (frame.truncated) {
      var warn = document.createElement("div");
      warn.className = "struct-warn";
      warn.textContent = tr("struct.shaderTextCut");
      details.appendChild(warn);
    }
    return details;
  }

  function glslSection(root, frames) {
    var seen = { vs: 0, ps: 0 };
    frames.forEach(function (frame) {
      var open = false;
      if (frame.role === "vs" || frame.role === "ps") {
        open = seen[frame.role] === 0;
        seen[frame.role] += 1;
      } else if (frames.length === 1) {
        open = true;
      }
      root.appendChild(glslBlock(frame, open));
    });
  }

  function moreFramesDetails(total, frames) {
    var details = document.createElement("details");
    details.className = "struct-details";
    details.innerHTML = `<summary>${esc(tr("struct.shaderMore"))} (+${total})</summary>`;
    var pairs = document.createElement("div");
    pairs.className = "struct-pairs";
    frames.forEach(function (frame) {
      var key = document.createElement("div");
      key.className = "k";
      key.textContent = `#${frame.index}`;
      var value = document.createElement("div");
      value.className = "v";
      value.textContent = `${frame.role} · ${frame.size} B`;
      pairs.appendChild(key);
      pairs.appendChild(value);
    });
    details.appendChild(pairs);
    return details;
  }

  function uniformSection(root, uniform) {
    var box = document.createElement("div");
    box.innerHTML = sectionTitle("struct.shaderUniformTable");
    var pairs = document.createElement("div");
    pairs.className = "struct-pairs";
    (uniform.rows || []).forEach(function (row) {
      var key = document.createElement("div");
      key.className = "k";
      key.textContent = row.name;
      var value = document.createElement("div");
      value.className = "v";
      value.textContent = [row.type, row.sampler, row.props].filter(Boolean).join(" · ");
      pairs.appendChild(key);
      pairs.appendChild(value);
    });
    box.appendChild(pairs);
    if (uniform.truncated) {
      var warn = document.createElement("div");
      warn.className = "struct-warn";
      warn.textContent = tr("struct.shaderRowsCut");
      box.appendChild(warn);
    }
    root.appendChild(box);
  }

  function renderShaderCache(root, data) {
    root.appendChild(chips([
      [tr("struct.shaderFrames"), data.frame_count],
      [tr("struct.shaderGlsl"), data.glsl_frames],
      [tr("struct.shaderUniforms"), data.uniform_frames],
      [tr("struct.shaderDict"), `${data.dict_size} B`],
    ]));
    if (data.uniform && data.uniform.rows && data.uniform.rows.length) {
      uniformSection(root, data.uniform);
    }
    glslSection(root, data.glsl || []);
    if (data.hex_head) {
      var details = hexDetails(data.hex_head);
      details.querySelector("summary").textContent = tr("struct.shaderLayoutHex");
      root.appendChild(details);
    }
    if (data.more_total > 0) {
      root.appendChild(moreFramesDetails(data.more_total, data.more_frames || []));
    }
  }

  function renderCachePlain(root, data) {
    root.appendChild(chips([
      [tr("struct.shaderGlsl"), data.glsl_frames],
      [tr("struct.cacheHead"), `${data.head_size} B`],
    ]));
    glslSection(root, data.glsl || []);
    if (data.hex_head) {
      root.appendChild(hexDetails(data.hex_head));
    }
    if (data.more_total > 0) {
      root.appendChild(moreFramesDetails(data.more_total, data.more_frames || []));
    }
  }

  function renderScn(root, data) {
    root.appendChild(chips([
      [tr("struct.scnObjects"), data.object_count ? `${data.parsed_count} / ${data.object_count}` : data.parsed_count],
      [tr("struct.scnNames"), data.table_count],
      [tr("struct.scnFields"), data.field_count],
    ]));
    if (data.objects && data.objects.length) {
      root.appendChild(treeSection(data.objects));
    }
  }

  function renderMsgpack(root, data) {
    var layers = (data.layers || []).join(" → ");
    var stats = data.stats || {};
    root.appendChild(chips([
      [tr("struct.layers"), layers],
      [tr("struct.mpNodes"), stats.nodes !== undefined ? stats.nodes.toLocaleString() : "—"],
      [tr("struct.mpDepth"), stats.depth !== undefined ? stats.depth : "—"],
    ]));
    if (data.top_keys && data.top_keys.length) {
      root.appendChild(chips(data.top_keys.slice(0, 16).map(function (key) { return ["{} ", key]; })));
    }
    root.appendChild(treeSection(data.tree));
  }

  function renderBindict(root, data) {
    root.appendChild(chips([
      [tr("struct.bdRows"), data.row_count !== undefined ? Number(data.row_count).toLocaleString() : "—"],
      [tr("struct.bdStrings"), data.string_count !== undefined ? Number(data.string_count).toLocaleString() : "—"],
      data.unit_count ? [tr("struct.bdUnits"), Number(data.unit_count).toLocaleString()] : null,
    ]));
    if (data.top_keys && data.top_keys.length) {
      root.appendChild(chips(data.top_keys.slice(0, 16).map(function (key) { return ["{} ", key]; })));
    }
    root.appendChild(treeSection(data.rows));
    if (data.strings && data.strings.length) {
      var details = document.createElement("details");
      details.className = "struct-details";
      details.innerHTML = `<summary>${esc(tr("struct.bdStringPool"))}</summary>`;
      var pre = document.createElement("pre");
      pre.className = "struct-pre";
      pre.textContent = data.strings.join("\n");
      details.appendChild(pre);
      root.appendChild(details);
    }
  }

  var RENDERERS = {
    msgpack: renderMsgpack,
    bindict: renderBindict,
    heightmap: renderHeightmap,
    meta: renderMeta,
    efx: renderEfx,
    trk: renderTrk,
    scn: renderScn,
    shader_cache: renderShaderCache,
    cache_plain: renderCachePlain,
  };

  function fillBlocks(host, data) {
    var renderer = RENDERERS[data.kind];
    var warnKeys = (data.warnings || []).slice();
    if (renderer) {
      renderer(host, data);
      if (data.note) warnKeys.push(data.note);
    } else {
      warnKeys.push(data.note || "struct.unknown");
      if (data.hex_head) host.appendChild(hexDetails(data.hex_head));
    }
    warnKeys.forEach(function (key) {
      var warn = document.createElement("div");
      warn.className = "struct-warn";
      warn.textContent = `${tr(key)}${key === "inspect.scnStopEarly" && data.stop_reason ? " · " + data.stop_reason : ""}`;
      host.appendChild(warn);
    });
  }

  function renderStructBlocks(target, data) {
    var media = toElement(target);
    if (!media) return;
    ensureStyle();
    media.innerHTML = "";
    media.dataset.structPath = data.path || "";
    media.dataset.kind = String(data.kind || "");
    var flow = document.createElement("div");
    flow.className = "struct-flow";
    media.appendChild(flow);
    fillBlocks(flow, data);
    media.classList.remove("swap-in");
    void media.offsetWidth;
    media.classList.add("swap-in");
    if (!langCache || langCache.code !== currentCode()) {
      var path = data.path || "";
      loadLang().then(function () {
        if (media.dataset.structPath === path && media.childElementCount) renderStructBlocks(media, data);
      });
    }
  }

  function render(target, data) {
    var media = toElement(target);
    if (!media) return;
    ensureStyle();
    var titleKey = `struct.title.${(data.ext || "").replace(".", "")}`;
    var root = document.createElement("div");
    root.className = "struct-preview";
    root.dataset.path = data.path || "";
    var head = document.createElement("div");
    head.className = "struct-head";
    head.innerHTML = `<span class="struct-title"><svg class="icon" aria-hidden="true"><use href="#i-code"></use></svg>${esc(tr(titleKey))}</span>`;
    root.appendChild(head);
    fillBlocks(root, data);
    media.innerHTML = "";
    media.appendChild(root);
    media.classList.remove("swap-in");
    void media.offsetWidth;
    media.classList.add("swap-in");
    if (!langCache || langCache.code !== currentCode()) {
      var path = data.path || "";
      loadLang().then(function () {
        var current = media.querySelector(".struct-preview");
        if (current && current.dataset.path === path) render(media, data);
      });
    }
  }

  window.renderStructPreview = render;
  window.renderStructBlocks = renderStructBlocks;
})();
