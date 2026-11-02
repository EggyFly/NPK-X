(function () {
  "use strict";

  if (typeof window === "undefined") return;
  if (window.renderSkeletonPreview) return;

  var STYLE_ID = "skeleton-preview-style";
  var CHEVRON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  var CSS = [
    ".skeleton-preview{display:flex;flex-direction:column;gap:10px;width:100%;padding:12px;border:1px solid var(--md-outline-variant,#3f484b);border-radius:var(--md-shape-lg,16px);background:var(--md-surface-container-low,#171c1f);color:var(--md-on-surface,#dfe3e6);box-sizing:border-box;font-family:var(--md-font-ui,'Segoe UI',sans-serif)}",
    ".skeleton-preview *{box-sizing:border-box}",
    ".skeleton-preview-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;flex-wrap:wrap}",
    ".skeleton-preview-title{font-size:13px;font-weight:600;letter-spacing:.2px}",
    ".skeleton-preview-sub{font:10px/1.4 var(--md-font-mono,Consolas,monospace);color:var(--md-on-surface-variant,#bfc8cb);margin-top:2px;word-break:break-all}",
    ".skeleton-preview .bank-chips{display:flex;flex-wrap:wrap;gap:5px}",
    ".skeleton-preview .bank-chip{display:inline-flex;align-items:center;gap:5px;border:0;border-radius:var(--md-shape-sm,8px);padding:4px 8px;background:var(--md-surface-container-high,#252b2e);color:var(--md-on-surface-variant,#bfc8cb);font:10px/1.4 var(--md-font-mono,Consolas,monospace);white-space:nowrap}",
    ".skeleton-preview .bank-chip b{color:var(--md-on-surface,#dfe3e6);font-weight:600}",
    ".skeleton-preview .section-title{color:var(--md-on-surface-variant,#bfc8cb);font-size:10px;font-weight:600;letter-spacing:.3px;margin:0 0 6px}",
    ".skeleton-preview-body{display:grid;grid-template-columns:minmax(0,1fr) 226px;gap:10px;align-items:stretch}",
    ".skeleton-stage{position:relative;min-width:0;border:1px solid var(--md-outline-variant,#3f484b);border-radius:var(--md-shape-md,12px);overflow:hidden;background:var(--md-canvas-bg,#0a0e10)}",
    ".skeleton-canvas{display:block;width:100%;height:420px;touch-action:none;cursor:grab;outline:none}",
    ".skeleton-canvas.dragging{cursor:grabbing}",
    ".skeleton-canvas:focus-visible{box-shadow:inset 0 0 0 2px var(--md-primary,#7bdbe6)}",
    ".skeleton-side{display:flex;flex-direction:column;gap:8px;min-width:0;max-height:422px;padding:9px;border:1px solid var(--md-outline-variant,#3f484b);border-radius:var(--md-shape-md,12px);background:var(--md-surface-container,#1b2023);overflow:hidden}",
    ".skeleton-side-head{display:flex;align-items:center;justify-content:space-between;gap:6px}",
    ".skeleton-side-head .section-title{margin:0}",
    ".skeleton-tree-actions{display:flex;gap:4px}",
    ".skeleton-mini{border:0;border-radius:var(--md-shape-sm,8px);background:var(--md-surface-container-high,#252b2e);color:var(--md-on-surface-variant,#bfc8cb);font:9px/1.4 var(--md-font-mono,Consolas,monospace);padding:3px 6px;cursor:pointer}",
    ".skeleton-mini:hover{background:var(--md-surface-container-highest,#303639);color:var(--md-on-surface,#dfe3e6)}",
    ".skeleton-tree{flex:1 1 auto;min-height:80px;overflow:auto;padding:2px;border-radius:var(--md-shape-sm,8px);background:var(--md-surface-container-lowest,#0a0e10)}",
    ".skeleton-tree-node{min-width:0}",
    ".skeleton-bone-row{display:flex;align-items:center;gap:2px;min-width:0;border-radius:6px;padding:1px 2px}",
    ".skeleton-bone-row:hover{background:var(--md-state-hover,rgba(223,227,230,.08))}",
    ".skeleton-bone-row.is-selected{background:var(--md-primary-container,#004f55);color:var(--md-on-primary-container,#9df1fa)}",
    ".skeleton-tree-toggle{flex:none;width:16px;height:20px;display:inline-flex;align-items:center;justify-content:center;border:0;background:transparent;color:inherit;padding:0;cursor:pointer;visibility:hidden}",
    ".skeleton-tree-toggle.has-children{visibility:visible}",
    ".skeleton-tree-toggle svg{width:12px;height:12px;transition:transform .14s ease}",
    ".skeleton-tree-node.is-open>.skeleton-bone-row .skeleton-tree-toggle svg{transform:rotate(90deg)}",
    ".skeleton-bone-name{flex:1 1 auto;min-width:0;border:0;background:transparent;color:inherit;text-align:left;font:10px/1.5 var(--md-font-mono,Consolas,monospace);padding:1px 2px;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".skeleton-bone-count{flex:none;color:var(--md-on-surface-variant,#bfc8cb);font:9px/1 var(--md-font-mono,Consolas,monospace);opacity:.7;padding-right:2px}",
    ".skeleton-tree-children{display:none}",
    ".skeleton-tree-node.is-open>.skeleton-tree-children{display:block}",
    ".skeleton-readout{flex:none;border-top:1px solid var(--md-outline-variant,#3f484b);padding-top:7px;display:grid;grid-template-columns:46px minmax(0,1fr);gap:3px 7px;font:10px/1.45 var(--md-font-mono,Consolas,monospace)}",
    ".skeleton-readout .k{color:var(--md-on-surface-variant,#bfc8cb)}",
    ".skeleton-readout .v{color:var(--md-on-surface,#dfe3e6);word-break:break-all}",
    ".skeleton-readout .v.is-empty{color:var(--md-on-surface-variant,#bfc8cb);font-family:var(--md-font-ui,'Segoe UI',sans-serif)}",
    ".skeleton-readout .name{font-weight:600}",
    ".skeleton-warn{border:1px dashed var(--md-error,#ffb4ab);border-radius:var(--md-shape-sm,8px);padding:6px 8px;color:var(--md-error,#ffb4ab);font-size:10px;line-height:1.5}",
    ".skeleton-empty{display:flex;align-items:center;justify-content:center;height:180px;padding:20px;text-align:center;color:var(--md-on-surface-variant,#bfc8cb);font-size:12px}",
    "@media (max-width:720px){.skeleton-preview-body{grid-template-columns:minmax(0,1fr)}.skeleton-side{max-height:260px}.skeleton-canvas{height:320px}}"
  ].join("");

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  function toElement(target) {
    if (typeof target === "string") return document.querySelector(target);
    return target && target.nodeType === 1 ? target : null;
  }

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function add(parent, child) {
    parent.appendChild(child);
    return child;
  }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function num(value, fallback) {
    var result = Number(value);
    return Number.isFinite(result) ? result : fallback;
  }

  function vec(source, size, fallback) {
    var out = [];
    for (var i = 0; i < size; i += 1) out.push(num(source && source[i], fallback[i]));
    return out;
  }

  function fmt(value, digits) {
    var result = Number(value);
    if (!Number.isFinite(result)) return "0";
    if (Math.abs(result) < 0.0000005) result = 0;
    return result.toFixed(digits === undefined ? 3 : digits).replace(/\.?0+$/, "");
  }

  function matrixOf(local) {
    var t = vec(local && local.t, 3, [0, 0, 0]);
    var q = vec(local && local.q, 4, [0, 0, 0, 1]);
    var s = vec(local && local.s, 3, [1, 1, 1]);
    var length = Math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]) || 1;
    var x = q[0] / length;
    var y = q[1] / length;
    var z = q[2] / length;
    var w = q[3] / length;
    return [
      (1 - 2 * (y * y + z * z)) * s[0], 2 * (x * y - z * w) * s[0], 2 * (x * z + y * w) * s[0], t[0],
      2 * (x * y + z * w) * s[1], (1 - 2 * (x * x + z * z)) * s[1], 2 * (y * z - x * w) * s[1], t[1],
      2 * (x * z - y * w) * s[2], 2 * (y * z + x * w) * s[2], (1 - 2 * (x * x + y * y)) * s[2], t[2],
      0, 0, 0, 1
    ];
  }

  function multiply(parent, local) {
    var out = new Array(16);
    for (var row = 0; row < 4; row += 1) {
      for (var col = 0; col < 4; col += 1) {
        var value = 0;
        for (var k = 0; k < 4; k += 1) value += parent[row * 4 + k] * local[k * 4 + col];
        out[row * 4 + col] = value;
      }
    }
    return out;
  }

  function flatWorld(source, index) {
    var item = source[index];
    if (!item) return null;
    if (Array.isArray(item) && item.length >= 16 && !Array.isArray(item[0])) return item.slice(0, 16).map(function (value) { return num(value, 0); });
    if (Array.isArray(item) && Array.isArray(item[0])) {
      var out = [];
      for (var row = 0; row < 4; row += 1) for (var col = 0; col < 4; col += 1) out.push(num(item[row] && item[row][col], 0));
      return out;
    }
    return null;
  }

  function buildModel(data) {
    var sourceNames = Array.isArray(data && data.names) ? data.names : [];
    var names = sourceNames.map(function (name) { return String(name); });
    var total = names.length;
    var parents = [];
    for (var i = 0; i < total; i += 1) {
      var parent = Number(data && Array.isArray(data.parents) ? data.parents[i] : -1);
      parents.push(Number.isInteger(parent) && parent >= 0 && parent < total ? parent : -1);
    }
    var localSource = Array.isArray(data && data.bind_local) ? data.bind_local : [];
    var locals = [];
    for (var j = 0; j < total; j += 1) locals.push(localSource[j] || { t: [0, 0, 0], q: [0, 0, 0, 1], s: [1, 1, 1] });
    var worldSource = Array.isArray(data && data.bind_world) ? data.bind_world : [];
    var worlds = new Array(total);

    function resolve(index) {
      if (worlds[index]) return worlds[index];
      var stack = [];
      var cursor = index;
      var seen = new Set();
      while (cursor >= 0 && !worlds[cursor] && !seen.has(cursor)) {
        seen.add(cursor);
        stack.push(cursor);
        cursor = parents[cursor];
      }
      if (cursor >= 0 && seen.has(cursor)) {
        parents[cursor] = -1;
      }
      while (stack.length) {
        var current = stack.pop();
        var parent = parents[current];
        var local = matrixOf(locals[current]);
        worlds[current] = parent >= 0 && worlds[parent] ? multiply(worlds[parent], local) : local;
      }
      return worlds[index];
    }

    for (var k = 0; k < total; k += 1) worlds[k] = flatWorld(worldSource, k);
    for (var m = 0; m < total; m += 1) {
      if (!worlds[m]) resolve(m);
    }

    var positions = worlds.map(function (matrix) {
      return [matrix[3] || 0, matrix[7] || 0, matrix[11] || 0];
    });
    var children = names.map(function () { return []; });
    var roots = [];
    for (var n = 0; n < total; n += 1) {
      if (parents[n] >= 0) children[parents[n]].push(n);
      else roots.push(n);
    }
    return {
      names: names,
      parents: parents,
      locals: locals,
      worlds: worlds,
      positions: positions,
      children: children,
      roots: roots,
    };
  }

  function render(container, data) {
    ensureStyle();
    var host = toElement(container);
    if (!host) return null;
    if (host.__skeletonPreviewApi && typeof host.__skeletonPreviewApi.destroy === "function") {
      host.__skeletonPreviewApi.destroy();
    }
    host.innerHTML = "";

    var model = buildModel(data || {});
    var total = model.names.length;
    var root = add(host, make("div", "skeleton-preview"));
    if (!total) {
      add(root, make("div", "skeleton-empty", "没有可显示的骨骼数据"));
      return null;
    }

    var head = add(root, make("div", "skeleton-preview-head"));
    var headMain = add(head, make("div"));
    add(headMain, make("div", "skeleton-preview-title", "骨骼绑定预览"));
    add(headMain, make("div", "skeleton-preview-sub", "NeoX .skeleton · 绑定姿态 · " + total + " 根骨骼"));
    var chips = add(head, make("div", "bank-chips"));
    var chipBones = add(chips, make("span", "bank-chip"));
    add(chipBones, make("b", "", total));
    add(chipBones, document.createTextNode("骨骼"));
    var chipRoots = add(chips, make("span", "bank-chip"));
    add(chipRoots, make("b", "", model.roots.length));
    add(chipRoots, document.createTextNode("根节点"));
    var maxChildren = 0;
    model.children.forEach(function (list) { maxChildren = Math.max(maxChildren, list.length); });
    var chipBranch = add(chips, make("span", "bank-chip"));
    add(chipBranch, make("b", "", maxChildren));
    add(chipBranch, document.createTextNode("最大分支"));

    if (data && Array.isArray(data.warnings) && data.warnings.length) {
      add(root, make("div", "skeleton-warn", data.warnings.join(" · ")));
    }

    var body = add(root, make("div", "skeleton-preview-body"));
    var stage = add(body, make("div", "skeleton-stage"));
    var canvas = add(stage, make("canvas", "skeleton-canvas"));
    canvas.tabIndex = 0;
    canvas.setAttribute("aria-label", "骨骼绑定姿态预览");
    var side = add(body, make("div", "skeleton-side"));

    var sideHead = add(side, make("div", "skeleton-side-head"));
    add(sideHead, make("div", "section-title", "骨骼层级"));
    var actions = add(sideHead, make("div", "skeleton-tree-actions"));
    var expandAll = add(actions, make("button", "skeleton-mini", "展开"));
    var collapseAll = add(actions, make("button", "skeleton-mini", "折叠"));
    expandAll.type = "button";
    collapseAll.type = "button";
    var tree = add(side, make("div", "skeleton-tree"));
    var readout = add(side, make("div", "skeleton-readout"));

    var nodeByIndex = new Array(total);
    var rowByIndex = new Array(total);
    var expanded = new Set();

    function shouldOpen(index, depth) {
      if (total <= 220) return true;
      return depth <= 1;
    }

    function buildNode(index, depth) {
      var node = make("div", "skeleton-tree-node");
      var row = add(node, make("div", "skeleton-bone-row"));
      row.style.paddingLeft = (depth * 12) + "px";
      var toggle = add(row, make("button", "skeleton-tree-toggle"));
      toggle.type = "button";
      toggle.innerHTML = CHEVRON;
      toggle.setAttribute("aria-label", "展开或折叠子骨骼");
      var nameButton = add(row, make("button", "skeleton-bone-name", model.names[index]));
      nameButton.type = "button";
      nameButton.title = model.names[index] + " (#" + index + ")";
      var count = add(row, make("span", "skeleton-bone-count", model.children[index].length ? String(model.children[index].length) : ""));
      var childBox = add(node, make("div", "skeleton-tree-children"));
      nodeByIndex[index] = { node: node, toggle: toggle, childBox: childBox };
      rowByIndex[index] = row;
      if (shouldOpen(index, depth)) expanded.add(index);
      if (model.children[index].length) {
        toggle.classList.add("has-children");
        model.children[index].forEach(function (child) { add(childBox, buildNode(child, depth + 1)); });
      }
      if (expanded.has(index)) node.classList.add("is-open");
      row.addEventListener("click", function (event) {
        if (event.target === toggle || toggle.contains(event.target)) return;
        select(index, true);
      });
      toggle.addEventListener("click", function (event) {
        event.stopPropagation();
        setOpen(index, !expanded.has(index));
      });
      return node;
    }

    model.roots.forEach(function (index) { add(tree, buildNode(index, 0)); });

    function setOpen(index, open) {
      var entry = nodeByIndex[index];
      if (!entry) return;
      if (open) expanded.add(index);
      else expanded.delete(index);
      entry.node.classList.toggle("is-open", open);
    }

    function expandAllNodes() {
      for (var i = 0; i < total; i += 1) {
        if (model.children[i].length) {
          expanded.add(i);
          if (nodeByIndex[i]) nodeByIndex[i].node.classList.add("is-open");
        }
      }
    }

    function collapseAllNodes() {
      expanded.clear();
      for (var i = 0; i < total; i += 1) if (nodeByIndex[i]) nodeByIndex[i].node.classList.remove("is-open");
    }

    function reveal(index) {
      var cursor = model.parents[index];
      var guard = 0;
      while (cursor >= 0 && guard < total) {
        expanded.add(cursor);
        if (nodeByIndex[cursor]) nodeByIndex[cursor].node.classList.add("is-open");
        cursor = model.parents[cursor];
        guard += 1;
      }
    }

    function showReadout(index) {
      readout.innerHTML = "";
      if (index < 0) {
        var empty = add(readout, make("div", "v is-empty", "点击画布或列表里的骨骼查看绑定数据"));
        empty.style.gridColumn = "1 / -1";
        return;
      }
      var local = model.locals[index];
      var world = model.worlds[index];
      var parent = model.parents[index];
      var rows = [
        ["名称", '<span class="name">' + esc(model.names[index]) + "</span>"],
        ["索引", index + " / " + total],
        ["父级", parent >= 0 ? esc(model.names[parent]) + " (#" + parent + ")" : "无"],
        ["子级", String(model.children[index].length)],
        ["世界", fmt(world[3]) + " / " + fmt(world[7]) + " / " + fmt(world[11])],
        ["T", fmt(local.t[0]) + "/" + fmt(local.t[1]) + "/" + fmt(local.t[2])],
        ["Q", fmt(local.q[0]) + "/" + fmt(local.q[1]) + "/" + fmt(local.q[2]) + "/" + fmt(local.q[3])],
        ["S", fmt(local.s[0]) + "/" + fmt(local.s[1]) + "/" + fmt(local.s[2])]
      ];
      rows.forEach(function (item) {
        add(readout, make("div", "k", item[0]));
        var value = add(readout, make("div", "v"));
        value.innerHTML = item[1];
      });
    }

    var state = {
      yaw: 0.72,
      pitch: 0.2,
      zoom: 1,
      selected: -1,
      hover: -1,
      dragging: false,
      moved: false,
      dragX: 0,
      dragY: 0,
      width: 0,
      height: 0,
      dpr: 0,
      points: new Array(total),
      segments: [],
      drawPending: 0,
      destroy: false,
    };

    var center = [0, 0, 0];
    var radius = 1;
    var floorY = 0;
    (function solveBounds() {
      var min = [Infinity, Infinity, Infinity];
      var max = [-Infinity, -Infinity, -Infinity];
      model.positions.forEach(function (point) {
        for (var axis = 0; axis < 3; axis += 1) {
          var value = point[axis] || 0;
          if (value < min[axis]) min[axis] = value;
          if (value > max[axis]) max[axis] = value;
        }
      });
      if (!Number.isFinite(min[0])) {
        min = [-1, -1, -1];
        max = [1, 1, 1];
      }
      center = [(min[0] + max[0]) * 0.5, (min[1] + max[1]) * 0.5, (min[2] + max[2]) * 0.5];
      floorY = min[1];
      var span = Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2]);
      radius = Math.max(span * 0.5, 0.01);
      model.positions.forEach(function (point) {
        var dx = point[0] - center[0];
        var dy = point[1] - center[1];
        var dz = point[2] - center[2];
        radius = Math.max(radius, Math.sqrt(dx * dx + dy * dy + dz * dz));
      });
    })();

    function project(point) {
      var x = point[0] - center[0];
      var y = point[1] - center[1];
      var z = point[2] - center[2];
      var cy = Math.cos(state.yaw);
      var sy = Math.sin(state.yaw);
      var x1 = x * cy - z * sy;
      var z1 = x * sy + z * cy;
      var cp = Math.cos(state.pitch);
      var sp = Math.sin(state.pitch);
      var y1 = y * cp - z1 * sp;
      var z2 = y * sp + z1 * cp;
      var focal = radius * 4;
      var factor = focal / (focal + z2);
      var base = Math.min(state.width, state.height) * 0.4 / radius;
      var scale = base * state.zoom * factor;
      return {
        x: state.width * 0.5 + x1 * scale,
        y: state.height * 0.5 - y1 * scale,
        depth: z2,
      };
    }

    function resize() {
      var width = Math.max(1, Math.round(canvas.clientWidth));
      var height = Math.max(1, Math.round(canvas.clientHeight));
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      if (width === state.width && height === state.height && dpr === state.dpr) return;
      state.width = width;
      state.height = height;
      state.dpr = dpr;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      draw();
    }

    function drawGrid(ctx) {
      var span = Math.max(4, Math.ceil(radius / 2));
      var step = Math.max(0.5, Math.pow(10, Math.floor(Math.log10(span))));
      var lines = Math.min(18, Math.ceil(span / step));
      ctx.strokeStyle = "rgba(223,227,230,.06)";
      ctx.lineWidth = 1;
      for (var i = -lines; i <= lines; i += 1) {
        var offset = i * step;
        var a = project([center[0] - span, floorY, center[2] + offset]);
        var b = project([center[0] + span, floorY, center[2] + offset]);
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        var c = project([center[0] + offset, floorY, center[2] - span]);
        var d = project([center[0] + offset, floorY, center[2] + span]);
        ctx.beginPath(); ctx.moveTo(c.x, c.y); ctx.lineTo(d.x, d.y); ctx.stroke();
      }
    }

    function draw() {
      if (state.destroy) return;
      var ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
      ctx.clearRect(0, 0, state.width, state.height);
      var gradient = ctx.createLinearGradient(0, 0, 0, state.height);
      gradient.addColorStop(0, "rgba(23,28,31,.2)");
      gradient.addColorStop(1, "rgba(10,14,16,.7)");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, state.width, state.height);
      drawGrid(ctx);

      for (var i = 0; i < total; i += 1) state.points[i] = project(model.positions[i]);
      var order = [];
      for (var j = 0; j < total; j += 1) order.push(j);
      order.sort(function (left, right) { return state.points[right].depth - state.points[left].depth; });

      state.segments = [];
      ctx.lineCap = "round";
      order.forEach(function (index) {
        var parent = model.parents[index];
        if (parent < 0) return;
        var a = state.points[parent];
        var b = state.points[index];
        var selected = index === state.selected || parent === state.selected;
        var hover = index === state.hover || parent === state.hover;
        if (selected) ctx.strokeStyle = "rgba(238,192,108,.96)";
        else if (hover) ctx.strokeStyle = "rgba(157,241,250,.9)";
        else ctx.strokeStyle = "rgba(123,219,230,.55)";
        ctx.lineWidth = selected ? 2.2 : 1.2;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
        state.segments.push({ index: index, x1: a.x, y1: a.y, x2: b.x, y2: b.y });
      });

      order.forEach(function (index) {
        var point = state.points[index];
        var selected = index === state.selected;
        var hover = index === state.hover;
        var size = selected ? 4.6 : hover ? 3.6 : 2.3;
        ctx.beginPath();
        ctx.arc(point.x, point.y, size, 0, Math.PI * 2);
        ctx.fillStyle = selected ? "rgba(238,192,108,1)" : hover ? "rgba(157,241,250,1)" : "rgba(123,219,230,.78)";
        ctx.fill();
        if (selected) {
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = "rgba(10,14,16,.8)";
          ctx.stroke();
        }
      });

      if (state.selected >= 0) {
        var labelPoint = state.points[state.selected];
        var label = model.names[state.selected];
        ctx.font = "10px " + (getComputedStyle(root).getPropertyValue("--md-font-mono") || "Consolas, monospace");
        var width = ctx.measureText(label).width + 10;
        var x = Math.min(state.width - width - 4, labelPoint.x + 8);
        var y = Math.max(14, labelPoint.y - 14);
        ctx.fillStyle = "rgba(10,14,16,.82)";
        ctx.fillRect(x, y - 11, width, 16);
        ctx.strokeStyle = "rgba(238,192,108,.55)";
        ctx.strokeRect(x + 0.5, y - 10.5, width - 1, 15);
        ctx.fillStyle = "rgba(238,192,108,.98)";
        ctx.fillText(label, x + 5, y);
      }
    }

    function scheduleDraw() {
      if (state.drawPending || state.destroy) return;
      state.drawPending = requestAnimationFrame(function () {
        state.drawPending = 0;
        draw();
      });
    }

    function distanceToSegment(px, py, segment) {
      var dx = segment.x2 - segment.x1;
      var dy = segment.y2 - segment.y1;
      if (!dx && !dy) return Math.hypot(px - segment.x1, py - segment.y1);
      var t = ((px - segment.x1) * dx + (py - segment.y1) * dy) / (dx * dx + dy * dy);
      t = Math.max(0, Math.min(1, t));
      return Math.hypot(px - (segment.x1 + dx * t), py - (segment.y1 + dy * t));
    }

    function nearestBone(x, y) {
      var best = -1;
      var bestDistance = 10;
      for (var i = 0; i < total; i += 1) {
        var point = state.points[i];
        if (!point) continue;
        var distance = Math.hypot(x - point.x, y - point.y);
        if (distance < bestDistance) { bestDistance = distance; best = i; }
      }
      for (var j = 0; j < state.segments.length; j += 1) {
        var segment = state.segments[j];
        var value = distanceToSegment(x, y, segment);
        if (value < bestDistance) { bestDistance = value; best = segment.index; }
      }
      return best;
    }

    function syncRows() {
      for (var i = 0; i < total; i += 1) {
        if (rowByIndex[i]) rowByIndex[i].classList.toggle("is-selected", i === state.selected);
      }
    }

    function select(index, fromTree) {
      if (index < 0 || index >= total) index = -1;
      state.selected = index;
      syncRows();
      showReadout(index);
      if (index >= 0) {
        reveal(index);
        if (fromTree && rowByIndex[index] && rowByIndex[index].scrollIntoView) {
          rowByIndex[index].scrollIntoView({ block: "nearest" });
        }
      }
      scheduleDraw();
    }

    function localPoint(event) {
      var rect = canvas.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    }

    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var options = controller ? { signal: controller.signal } : undefined;

    canvas.addEventListener("pointerdown", function (event) {
      state.dragging = true;
      state.moved = false;
      state.dragX = event.clientX;
      state.dragY = event.clientY;
      canvas.classList.add("dragging");
      if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId);
      canvas.focus();
    }, options);

    canvas.addEventListener("pointermove", function (event) {
      if (state.dragging) {
        var dx = event.clientX - state.dragX;
        var dy = event.clientY - state.dragY;
        if (Math.abs(dx) + Math.abs(dy) > 3) state.moved = true;
        state.yaw += dx * 0.008;
        state.pitch = Math.max(-1.35, Math.min(1.35, state.pitch + dy * 0.008));
        state.dragX = event.clientX;
        state.dragY = event.clientY;
        scheduleDraw();
        return;
      }
      var point = localPoint(event);
      var hit = nearestBone(point.x, point.y);
      if (hit !== state.hover) {
        state.hover = hit;
        canvas.style.cursor = hit >= 0 ? "pointer" : "grab";
        scheduleDraw();
      }
    }, options);

    canvas.addEventListener("pointerup", function (event) {
      if (!state.dragging) return;
      state.dragging = false;
      canvas.classList.remove("dragging");
      if (!state.moved) {
        var point = localPoint(event);
        select(nearestBone(point.x, point.y));
      }
    }, options);

    canvas.addEventListener("pointercancel", function () {
      state.dragging = false;
      canvas.classList.remove("dragging");
    }, options);

    canvas.addEventListener("pointerleave", function () {
      if (state.dragging) return;
      canvas.style.cursor = "grab";
      if (state.hover !== -1) { state.hover = -1; scheduleDraw(); }
    }, options);

    canvas.addEventListener("wheel", function (event) {
      event.preventDefault();
      state.zoom = Math.max(0.35, Math.min(6, state.zoom * (event.deltaY < 0 ? 1.12 : 0.89)));
      scheduleDraw();
    }, options);

    canvas.addEventListener("dblclick", function () {
      state.yaw = 0.72;
      state.pitch = 0.2;
      state.zoom = 1;
      scheduleDraw();
    }, options);

    expandAll.addEventListener("click", function () { expandAllNodes(); }, options);
    collapseAll.addEventListener("click", function () { collapseAllNodes(); }, options);

    var resizeObserver = null;
    if (typeof ResizeObserver === "function") {
      resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(stage);
    } else {
      window.addEventListener("resize", resize, options);
    }

    showReadout(-1);
    resize();
    requestAnimationFrame(draw);

    var api = {
      redraw: scheduleDraw,
      select: select,
      destroy: function () {
        state.destroy = true;
        if (state.drawPending) cancelAnimationFrame(state.drawPending);
        if (resizeObserver) resizeObserver.disconnect();
        if (controller) controller.abort();
        host.innerHTML = "";
        if (host.__skeletonPreviewApi === api) delete host.__skeletonPreviewApi;
      },
      getState: function () {
        return {
          selected: state.selected,
          hover: state.hover,
          yaw: state.yaw,
          pitch: state.pitch,
          zoom: state.zoom,
        };
      },
    };
    host.__skeletonPreviewApi = api;
    return api;
  }

  window.renderSkeletonPreview = render;
})();
