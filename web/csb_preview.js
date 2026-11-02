(function () {
  "use strict";

  if (typeof window === "undefined") return;
  if (window.renderCsbPreview) return;

  var STYLE_ID = "csb-preview-style";
  var IMAGE_LIMIT = 400;
  var imageCache = new Map();

  var CSS = [
    ".csb-preview { width: 100%; display: flex; flex-direction: column; gap: 10px; }",
    ".csb-actions { display: flex; align-items: center; gap: 4px; flex: none; }",
    ".csb-zoom { min-width: 40px; text-align: center; color: var(--md-on-surface-variant, #bfc8cb); font: 10px/1 var(--md-font-mono, monospace); }",
    ".csb-body { display: flex; gap: 10px; align-items: stretch; width: 100%; min-width: 0; }",
    ".csb-stage { flex: 1 1 auto; min-width: 0; position: relative; overflow: hidden; background: var(--md-surface-container-lowest, #0a0e10); border: 1px solid var(--md-outline-variant, #3f484b); border-radius: var(--md-shape-md, 10px); }",
    ".csb-canvas { display: block; width: 100%; height: 420px; touch-action: none; cursor: grab; outline: none; }",
    ".csb-canvas.dragging { cursor: grabbing; }",
    ".csb-side { flex: 0 0 190px; width: 190px; max-height: 420px; overflow: auto; padding: 10px; background: var(--md-surface-container-low, #171c1f); border: 1px solid var(--md-outline-variant, #3f484b); border-radius: var(--md-shape-md, 10px); }",
    ".csb-side-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 8px; }",
    ".csb-side-head b { font-size: 12px; }",
    ".csb-side-head span { color: var(--md-on-surface-variant, #bfc8cb); font: 9px var(--md-font-mono, monospace); }",
    ".csb-side .meta-grid { grid-template-columns: 62px minmax(0, 1fr); }",
    ".csb-path { word-break: break-all; font: 9px/1.5 var(--md-font-mono, monospace); }",
    ".csb-tex-chip { cursor: pointer; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }",
    ".csb-tex-chip:hover { background: var(--md-surface-container-highest, #303639); }",
    ".csb-tex-box { display: flex; flex-direction: column; gap: 6px; }",
    ".csb-note { margin: 0; }",
    "@media (max-width: 700px) { .csb-body { flex-direction: column; } .csb-side { width: 100%; flex: none; max-height: 190px; } .csb-canvas { height: 300px; } }"
  ].join("\n");

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = CSS;
    document.head.appendChild(style);
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

  function pair(value, fallback) {
    if (!Array.isArray(value) || value.length < 2) return fallback.slice();
    return [num(value[0], fallback[0]), num(value[1], fallback[1])];
  }

  function childrenOf(node) {
    return node && Array.isArray(node.children) ? node.children : [];
  }

  function countNodes(node) {
    if (!node) return 0;
    var total = 1;
    var kids = childrenOf(node);
    for (var i = 0; i < kids.length; i++) total += countNodes(kids[i]);
    return total;
  }

  function mul(parent, local) {
    return {
      a: parent.a * local.a + parent.c * local.b,
      b: parent.b * local.a + parent.d * local.b,
      c: parent.a * local.c + parent.c * local.d,
      d: parent.b * local.c + parent.d * local.d,
      e: parent.a * local.e + parent.c * local.f + parent.e,
      f: parent.b * local.e + parent.d * local.f + parent.f
    };
  }

  function localMatrix(node) {
    var rotate = (num(node.rotate, 0) * Math.PI) / 180;
    var cos = Math.cos(rotate);
    var sin = Math.sin(rotate);
    var scale = pair(node.scale, [1, 1]);
    var pos = pair(node.pos, [0, 0]);
    var size = pair(node.size, [0, 0]);
    var anchor = pair(node.anchor, [0, 0]);
    var sx = scale[0];
    var sy = scale[1];
    var ox = -size[0] * anchor[0];
    var oy = -size[1] * anchor[1];
    var a = cos * sx;
    var b = sin * sx;
    var c = -sin * sy;
    var d = cos * sy;
    return {
      a: a,
      b: b,
      c: c,
      d: d,
      e: pos[0] + a * ox + c * oy,
      f: pos[1] + b * ox + d * oy
    };
  }

  function rectOf(node) {
    var size = pair(node.size, [0, 0]);
    return {
      x: 0,
      y: 0,
      w: size[0],
      h: size[1]
    };
  }

  function applyPoint(matrix, x, y) {
    return [matrix.a * x + matrix.c * y + matrix.e, matrix.b * x + matrix.d * y + matrix.f];
  }

  function cornersOf(matrix, rect) {
    return [
      applyPoint(matrix, rect.x, rect.y),
      applyPoint(matrix, rect.x + rect.w, rect.y),
      applyPoint(matrix, rect.x + rect.w, rect.y + rect.h),
      applyPoint(matrix, rect.x, rect.y + rect.h)
    ];
  }

  function bboxOf(corners) {
    var minX = Infinity;
    var minY = Infinity;
    var maxX = -Infinity;
    var maxY = -Infinity;
    for (var i = 0; i < corners.length; i++) {
      minX = Math.min(minX, corners[i][0]);
      minY = Math.min(minY, corners[i][1]);
      maxX = Math.max(maxX, corners[i][0]);
      maxY = Math.max(maxY, corners[i][1]);
    }
    return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
  }

  function polyPath(ctx, corners) {
    ctx.beginPath();
    ctx.moveTo(corners[0][0], corners[0][1]);
    for (var i = 1; i < corners.length; i++) ctx.lineTo(corners[i][0], corners[i][1]);
    ctx.closePath();
  }

  function insidePoly(corners, x, y) {
    var hit = false;
    for (var i = 0, j = corners.length - 1; i < corners.length; j = i++) {
      var xi = corners[i][0];
      var yi = corners[i][1];
      var xj = corners[j][0];
      var yj = corners[j][1];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi + 1e-9) + xi) hit = !hit;
    }
    return hit;
  }

  function baseName(path) {
    var text = String(path || "").replace(/\\/g, "/");
    var index = text.lastIndexOf("/");
    return index >= 0 ? text.slice(index + 1) : text;
  }

  function fmt(value) {
    var number = num(value, 0);
    if (Math.abs(number - Math.round(number)) < 0.001) return String(Math.round(number));
    return String(Math.round(number * 100) / 100);
  }

  function paletteOf(element) {
    var style = getComputedStyle(element);
    function read(name, fallback) {
      var value = style.getPropertyValue(name).trim();
      return value || fallback;
    }
    return {
      accent: read("--md-primary", "#8ab4f8"),
      text: read("--md-on-surface", "#dfe3e6"),
      muted: read("--md-on-surface-variant", "#bfc8cb"),
      line: read("--md-outline-variant", "#3f484b"),
      bg: read("--md-surface-container-lowest", "#0a0e10")
    };
  }

  function fileUrl(path) {
    var clean = String(path || "").replace(/\\/g, "/");
    if (typeof window.CSB_FILE_URL === "function") return window.CSB_FILE_URL(clean);
    var route = window.CSB_FILE_ROUTE || "/api/image";
    return route + (route.indexOf("?") >= 0 ? "&" : "?") + "path=" + encodeURIComponent(clean);
  }

  function imagePaths(path) {
    var clean = String(path || "").replace(/\\/g, "/");
    if (!clean) return [];
    var match = clean.match(/\.[a-z0-9]+$/i);
    var ext = match ? match[0].toLowerCase() : "";
    var stem = ext ? clean.slice(0, clean.length - ext.length) : clean;
    var isImage = ext === ".png" || ext === ".jpg" || ext === ".jpeg" || ext === ".webp" || ext === ".bmp" || ext === ".tga";
    var canPrefix = clean.indexOf("gui2/") !== 0 && clean.charAt(0) !== "/";
    var paths = [];
    function add(item) {
      if (item && paths.indexOf(item) < 0) paths.push(item);
    }
    if (isImage) {
      if (canPrefix) add("gui2/" + stem + ".kta");
      add(stem + ".kta");
    }
    if (canPrefix) add("gui2/" + clean);
    add(clean);
    return paths;
  }

  function imageUrls(path) {
    var paths = imagePaths(path);
    if (!paths.length) return [];
    if (typeof window.CSB_FILE_URL === "function") {
      return paths.map(function (item) {
        return window.CSB_FILE_URL(item);
      }).filter(function (url, index, list) {
        return url && list.indexOf(url) === index;
      });
    }
    var route = window.CSB_FILE_ROUTE || "/api/image";
    var urls = [];
    for (var i = 0; i < paths.length; i++) {
      urls.push(fileUrl(paths[i]));
      if (route !== "/api/image") {
        urls.push("/api/image?path=" + encodeURIComponent(paths[i]));
      }
    }
    return urls.filter(function (url, index) {
      return url && urls.indexOf(url) === index;
    });
  }

  function getImage(path, redraw) {
    if (!path) return null;
    var item = imageCache.get(path);
    if (item) {
      if (item.state === "ready") return item.img;
      if (item.state === "loading") {
        if (item.waiters.indexOf(redraw) < 0) item.waiters.push(redraw);
      }
      return null;
    }
    if (imageCache.size >= IMAGE_LIMIT) return null;
    item = { state: "loading", img: new Image(), urls: imageUrls(path), index: 0, waiters: [redraw] };
    imageCache.set(path, item);
    function done(state) {
      item.state = state;
      var waiters = item.waiters.slice();
      item.waiters.length = 0;
      for (var i = 0; i < waiters.length; i++) {
        try { waiters[i](); } catch (error) {}
      }
    }
    function next() {
      if (item.index >= item.urls.length) {
        done("error");
        return;
      }
      var url = item.urls[item.index++];
      item.img.onload = function () { done("ready"); };
      item.img.onerror = function () {
        item.img.onload = null;
        item.img.onerror = null;
        next();
      };
      item.img.src = url;
    }
    next();
    return null;
  }

  function selectFirstByTexture(node, texture) {
    if (!node) return null;
    if (node.texture === texture) return node;
    var kids = childrenOf(node);
    for (var i = 0; i < kids.length; i++) {
      var found = selectFirstByTexture(kids[i], texture);
      if (found) return found;
    }
    return null;
  }

  function measureBounds(root) {
    var minX = Infinity;
    var minY = Infinity;
    var maxX = -Infinity;
    var maxY = -Infinity;
    function visit(node, parent) {
      if (!node) return;
      var matrix = mul(parent, localMatrix(node));
      var rect = rectOf(node);
      if (rect.w > 0 && rect.h > 0) {
        var corners = cornersOf(matrix, rect);
        for (var i = 0; i < corners.length; i++) {
          if (!Number.isFinite(corners[i][0]) || !Number.isFinite(corners[i][1])) continue;
          if (Math.abs(corners[i][0]) > 1000000 || Math.abs(corners[i][1]) > 1000000) continue;
          minX = Math.min(minX, corners[i][0]);
          minY = Math.min(minY, corners[i][1]);
          maxX = Math.max(maxX, corners[i][0]);
          maxY = Math.max(maxY, corners[i][1]);
        }
      }
      var kids = childrenOf(node);
      for (var k = 0; k < kids.length; k++) visit(kids[k], matrix);
    }
    visit(root, { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 });
    if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;
    if (maxX - minX <= 0 || maxY - minY <= 0) return null;
    return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
  }

  function drawNineSlice(ctx, image, width, height, capInsets) {
    var imageWidth = image.naturalWidth || image.width || 0;
    var imageHeight = image.naturalHeight || image.height || 0;
    if (!(imageWidth > 0 && imageHeight > 0) || !Array.isArray(capInsets) || capInsets.length < 4) {
      ctx.drawImage(image, 0, 0, width, height);
      return;
    }
    var left = Math.max(0, Math.min(imageWidth, num(capInsets[0], 0)));
    var top = Math.max(0, Math.min(imageHeight, num(capInsets[1], 0)));
    var centerWidth = Math.max(0, num(capInsets[2], 0));
    var centerHeight = Math.max(0, num(capInsets[3], 0));
    var right = Math.max(0, imageWidth - left - centerWidth);
    var bottom = Math.max(0, imageHeight - top - centerHeight);
    centerWidth = Math.max(0, imageWidth - left - right);
    centerHeight = Math.max(0, imageHeight - top - bottom);
    if (centerWidth <= 0 || centerHeight <= 0) {
      ctx.drawImage(image, 0, 0, width, height);
      return;
    }
    var drawLeft = left;
    var drawRight = right;
    var drawTop = top;
    var drawBottom = bottom;
    if (drawLeft + drawRight > width) {
      var scaleX = width / (drawLeft + drawRight);
      drawLeft *= scaleX;
      drawRight *= scaleX;
    }
    if (drawTop + drawBottom > height) {
      var scaleY = height / (drawTop + drawBottom);
      drawTop *= scaleY;
      drawBottom *= scaleY;
    }
    var drawCenterWidth = Math.max(0, width - drawLeft - drawRight);
    var drawCenterHeight = Math.max(0, height - drawTop - drawBottom);
    var sx = [0, left, left + centerWidth, imageWidth];
    var sy = [0, top, top + centerHeight, imageHeight];
    var dx = [0, drawLeft, drawLeft + drawCenterWidth, width];
    var dy = [0, drawTop, drawTop + drawCenterHeight, height];
    for (var row = 0; row < 3; row++) {
      for (var col = 0; col < 3; col++) {
        var sourceWidth = sx[col + 1] - sx[col];
        var sourceHeight = sy[row + 1] - sy[row];
        var drawWidth = dx[col + 1] - dx[col];
        var drawHeight = dy[row + 1] - dy[row];
        if (sourceWidth <= 0 || sourceHeight <= 0 || drawWidth <= 0 || drawHeight <= 0) continue;
        ctx.drawImage(image, sx[col], sy[row], sourceWidth, sourceHeight, dx[col], dy[row], drawWidth, drawHeight);
      }
    }
  }


  function htmlForChips(header, nodeCount, textureCount) {
    var chips = [
      '<span class="bank-chip">版本 <b>' + esc(header.version || "未知") + "</b></span>",
      '<span class="bank-chip">节点 <b>' + nodeCount.toLocaleString() + "</b></span>",
      '<span class="bank-chip">纹理 <b>' + textureCount.toLocaleString() + "</b></span>",
      '<span class="bank-chip">画布 <b>' + fmt(header.width) + " × " + fmt(header.height) + "</b></span>"
    ];
    if (header.truncated) chips.push('<span class="bank-chip">节点已截断</span>');
    return chips.join("");
  }

  function sideEmpty() {
    return '<div class="note">点击画布中的节点查看属性</div>';
  }

  function sideFor(hit) {
    if (!hit) return sideEmpty();
    var node = hit.node;
    var pos = pair(node.pos, [0, 0]);
    var size = pair(node.size, [0, 0]);
    var scale = pair(node.scale, [1, 1]);
    var anchor = pair(node.anchor, [0, 0]);
    var rows = [
      ["名称", node.name || "未命名"],
      ["类型", node.type || "Node"],
      ["位置", fmt(pos[0]) + ", " + fmt(pos[1])],
      ["尺寸", fmt(size[0]) + " × " + fmt(size[1])],
      ["缩放", fmt(scale[0]) + ", " + fmt(scale[1])],
      ["锚点", fmt(anchor[0]) + ", " + fmt(anchor[1])],
      ["旋转", fmt(num(node.rotate, 0)) + "°"],
      ["可见", node.visible !== false ? "是" : "否"],
      ["透明度", fmt(num(node.alpha, 255)) + " / 255"],
      ["Tag", fmt(num(node.tag, 0))],
      ["子节点", String(childrenOf(node).length)]
    ];
    if (node.texture) rows.push(["纹理", node.texture]);
    var body = rows.map(function (row) {
      var value = row[1];
      if (row[0] === "纹理") {
        return '<span class="k">' + esc(row[0]) + '</span><span class="v csb-path">' + esc(value) + "</span>";
      }
      return '<span class="k">' + esc(row[0]) + '</span><span class="v">' + esc(value) + "</span>";
    }).join("");
    return '<div class="csb-side-head"><b>节点属性</b><span>深度 ' + hit.depth + "</span></div>" +
      '<div class="meta-grid">' + body + "</div>";
  }

  function drawLabel(state, entry, selected, hovered) {
    var ctx = state.ctx;
    var dpr = state.dpr;
    var box = entry.box;
    var node = entry.node;
    var text = node.name || node.type || "Node";
    var fontSize = Math.round((selected || hovered ? 11 : 9) * dpr);
    ctx.font = (selected || hovered ? "600 " : "") + fontSize + "px " + state.font;
    var width = Math.min(ctx.measureText(text).width, Math.max(box.w - 4 * dpr, 40 * dpr));
    if (width <= 0) return;
    var x = box.x + 2 * dpr;
    var y = box.y + fontSize + 1 * dpr;
    if (y > state.height || x > state.width) return;
    ctx.fillStyle = "rgba(8, 12, 14, 0.62)";
    ctx.fillRect(x - 2 * dpr, y - fontSize - 1 * dpr, width + 4 * dpr, fontSize + 3 * dpr);
    ctx.fillStyle = selected || hovered ? state.palette.accent : state.palette.text;
    ctx.textBaseline = "alphabetic";
    ctx.fillText(text, x, y, Math.max(box.w - 4 * dpr, 20 * dpr));
    if (box.h > 26 * dpr && box.w > 54 * dpr && (selected || hovered || entry.depth < 4)) {
      ctx.font = Math.round(8 * dpr) + "px " + state.font;
      ctx.fillStyle = state.palette.muted;
      ctx.fillText(node.type || "Node", x, y + 9 * dpr, Math.max(box.w - 4 * dpr, 20 * dpr));
    }
  }

  function draw(state) {
    var canvas = state.canvas;
    var ctx = state.ctx;
    var dpr = state.dpr;
    var width = canvas.clientWidth;
    var height = canvas.clientHeight;
    if (width < 10 || height < 10) return;
    var pixelWidth = Math.round(width * dpr);
    var pixelHeight = Math.round(height * dpr);
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }
    state.width = pixelWidth;
    state.height = pixelHeight;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = state.palette.bg;
    ctx.fillRect(0, 0, pixelWidth, pixelHeight);

    var box = state.viewBox || { x: 0, y: 0, w: state.rootWidth, h: state.rootHeight };
    var boxWidth = Math.max(1, box.w);
    var boxHeight = Math.max(1, box.h);
    var fit = Math.min(pixelWidth / boxWidth, pixelHeight / boxHeight) * 0.94;
    var scale = fit * state.view.zoom;
    var view = {
      a: scale,
      b: 0,
      c: 0,
      d: -scale,
      e: pixelWidth / 2 + state.view.panX * dpr - (box.x + boxWidth / 2) * scale,
      f: pixelHeight / 2 + state.view.panY * dpr + (box.y + boxHeight / 2) * scale
    };
    state.unit = scale / dpr;
    state.hits = [];
    state.labels = [];
    state.placeholders = [];
    state.order = 0;

    var rootMatrix = mul(view, localMatrix(state.root));
    var rootRect = rectOf(state.root);
    var frameMatrix = rootMatrix;
    var frameRect = rootRect;
    if (rootRect.w <= 0 || rootRect.h <= 0) {
      frameMatrix = view;
      frameRect = { x: box.x, y: box.y, w: boxWidth, h: boxHeight };
    }
    var rootCorners = cornersOf(frameMatrix, frameRect);
    polyPath(ctx, rootCorners);
    ctx.fillStyle = "rgba(255, 255, 255, 0.025)";
    ctx.fill();
    ctx.strokeStyle = "rgba(255, 255, 255, 0.18)";
    ctx.lineWidth = 1;
    ctx.stroke();

    if (state.unit > 0.12) {
      var step = 100;
      if (state.unit < 0.25) step = 200;
      else if (Math.max(frameRect.w, frameRect.h) < 400) step = 50;
      ctx.strokeStyle = "rgba(255, 255, 255, 0.035)";
      ctx.beginPath();
      for (var gx = frameRect.x; gx <= frameRect.x + frameRect.w + 0.01; gx += step) {
        var p1 = applyPoint(frameMatrix, gx, frameRect.y);
        var p2 = applyPoint(frameMatrix, gx, frameRect.y + frameRect.h);
        ctx.moveTo(p1[0], p1[1]);
        ctx.lineTo(p2[0], p2[1]);
      }
      for (var gy = frameRect.y; gy <= frameRect.y + frameRect.h + 0.01; gy += step) {
        var p3 = applyPoint(frameMatrix, frameRect.x, gy);
        var p4 = applyPoint(frameMatrix, frameRect.x + frameRect.w, gy);
        ctx.moveTo(p3[0], p3[1]);
        ctx.lineTo(p4[0], p4[1]);
      }
      ctx.stroke();
    }

    function visit(node, parentMatrix, depth, parentAlpha, parentHidden) {
      if (!node) return;
      var matrix = mul(parentMatrix, localMatrix(node));
      var rect = rectOf(node);
      var corners = cornersOf(matrix, rect);
      var box = bboxOf(corners);
      var hidden = parentHidden || node.visible === false;
      var alpha = parentAlpha * (num(node.alpha, 255) / 255);
      var hit = {
        node: node,
        corners: corners,
        box: box,
        anchor: applyPoint(matrix, 0, 0),
        depth: depth,
        order: state.order++
      };
      state.hits.push(hit);
      state.labels.push({ node: node, box: box, depth: depth, hit: hit });

      if (rect.w > 0 && rect.h > 0) {
        var image = node.texture ? getImage(node.texture, state.schedule) : null;
        if (image) {
          ctx.save();
          ctx.setTransform(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f);
          ctx.globalAlpha = Math.max(0, Math.min(1, alpha * (hidden ? 0.25 : 1)));
          ctx.beginPath();
          ctx.rect(rect.x, rect.y, rect.w, rect.h);
          ctx.clip();
          ctx.translate(rect.x, rect.y + rect.h);
          ctx.scale(1, -1);
          if (node.scale9 && Array.isArray(node.capInsets)) {
            drawNineSlice(ctx, image, rect.w, rect.h, node.capInsets);
          } else {
            ctx.drawImage(image, 0, 0, rect.w, rect.h);
          }
          ctx.restore();
        } else {
          polyPath(ctx, corners);
          ctx.globalAlpha = Math.max(hidden ? 0.08 : 0.14, Math.min(1, alpha) * 0.9);
          ctx.fillStyle = node.texture ? "rgba(138, 180, 248, 0.14)" : "rgba(138, 180, 248, 0.055)";
          ctx.fill();
          ctx.globalAlpha = 1;
        }
        polyPath(ctx, corners);
        ctx.globalAlpha = hidden ? 0.22 : 0.5;
        ctx.strokeStyle = hidden ? state.palette.muted : "rgba(150, 205, 235, 0.55)";
        ctx.lineWidth = 1;
        if (hidden) ctx.setLineDash([4 * dpr, 3 * dpr]);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
        if (node.texture && !image) {
          state.placeholders.push({ node: node, box: box, corners: corners });
        }
      }

      var kids = childrenOf(node);
      for (var i = 0; i < kids.length; i++) {
        visit(kids[i], matrix, depth + 1, alpha, hidden);
      }
    }

    visit(state.root, view, 0, 1, false);

    for (var index = 0; index < state.placeholders.length; index++) {
      var item = state.placeholders[index];
      polyPath(ctx, item.corners);
      ctx.setLineDash([4 * dpr, 3 * dpr]);
      ctx.strokeStyle = "rgba(138, 180, 248, 0.42)";
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.setLineDash([]);
      if (item.box.w > 54 * dpr && item.box.h > 16 * dpr) {
        var name = baseName(item.node.texture);
        ctx.font = Math.round(8 * dpr) + "px " + state.font;
        ctx.fillStyle = state.palette.muted;
        ctx.fillText(name, item.box.x + 3 * dpr, item.box.y + item.box.h - 4 * dpr, Math.max(item.box.w - 6 * dpr, 20 * dpr));
      }
    }

    var selected = state.selected ? state.selected.node : null;
    var hovered = state.hover ? state.hover.node : null;
    var drawn = 0;
    state.labels.sort(function (a, b) { return a.depth - b.depth; });
    for (var labelIndex = 0; labelIndex < state.labels.length; labelIndex++) {
      var entry = state.labels[labelIndex];
      if (entry.node === selected || entry.node === hovered) continue;
      if (drawn >= 220) continue;
      if (entry.box.w < 34 * dpr || entry.box.h < 12 * dpr) continue;
      if (entry.hit.depth > 12) continue;
      if (entry.hit.depth >= 2 && childrenOf(entry.node).length) continue;
      drawLabel(state, entry, false, false);
      drawn++;
    }
    for (var focusIndex = 0; focusIndex < state.labels.length; focusIndex++) {
      var focus = state.labels[focusIndex];
      if (focus.node === selected) drawLabel(state, focus, true, false);
      else if (focus.node === hovered) drawLabel(state, focus, false, true);
    }

    function outline(hit, color, lineWidth, handles) {
      if (!hit) return;
      polyPath(ctx, hit.corners);
      ctx.strokeStyle = color;
      ctx.lineWidth = lineWidth;
      ctx.stroke();
      if (!handles) return;
      var size = 3 * dpr;
      for (var i = 0; i < hit.corners.length; i++) {
        ctx.fillStyle = color;
        ctx.fillRect(hit.corners[i][0] - size / 2, hit.corners[i][1] - size / 2, size, size);
      }
    }

    if (hovered && hovered !== selected) {
      var hoverHit = null;
      for (var h = 0; h < state.hits.length; h++) {
        if (state.hits[h].node === hovered) hoverHit = state.hits[h];
      }
      if (hoverHit) outline(hoverHit, "rgba(138, 180, 248, 0.7)", 1, false);
    }
    if (selected) {
      var selectedHit = null;
      for (var s = 0; s < state.hits.length; s++) {
        if (state.hits[s].node === selected) selectedHit = state.hits[s];
      }
      if (selectedHit) outline(selectedHit, state.palette.accent, 2, true);
    }
  }

  function hitAt(state, point) {
    for (var i = state.hits.length - 1; i >= 0; i--) {
      var hit = state.hits[i];
      if (hit.box.w > 1 && hit.box.h > 1) {
        if (insidePoly(hit.corners, point.x, point.y)) return hit;
      } else {
        var dx = hit.anchor[0] - point.x;
        var dy = hit.anchor[1] - point.y;
        var radius = 6 * state.dpr;
        if (dx * dx + dy * dy <= radius * radius) return hit;
      }
    }
    return null;
  }

  function localPoint(canvas, event) {
    var rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) * (canvas.width / Math.max(1, rect.width)),
      y: (event.clientY - rect.top) * (canvas.height / Math.max(1, rect.height))
    };
  }

  function normalize(data) {
    if (!data || typeof data !== "object") return null;
    var root = Array.isArray(data.nodes) ? data.nodes[0] : data;
    if (!root || typeof root !== "object") return null;
    var header = data.header && typeof data.header === "object" ? data.header : {};
    var textures = Array.isArray(data.textures) ? data.textures.filter(function (item) {
      return typeof item === "string" && item;
    }) : [];
    var nodeCount = countNodes(root);
    var width = num(header.width, 0);
    var height = num(header.height, 0);
    if (!(width > 0)) width = num(root.size && root.size[0], 0);
    if (!(height > 0)) height = num(root.size && root.size[1], 0);
    var viewBox = null;
    if (width > 0 && height > 0) {
      viewBox = { x: 0, y: 0, w: width, h: height };
    } else {
      var bounds = measureBounds(root);
      if (bounds) {
        var pad = Math.max(4, Math.max(bounds.w, bounds.h) * 0.03);
        viewBox = { x: bounds.x - pad, y: bounds.y - pad, w: bounds.w + pad * 2, h: bounds.h + pad * 2 };
        width = viewBox.w;
        height = viewBox.h;
      }
    }
    if (!viewBox) {
      width = 1920;
      height = 1080;
      viewBox = { x: 0, y: 0, w: width, h: height };
    }
    var scene = header.scene || root.name || root.type || "CSB";
    var version = header.version || "";
    return {
      root: root,
      header: header,
      textures: textures,
      nodeCount: nodeCount,
      textureCount: textures.length,
      width: width,
      height: height,
      viewBox: viewBox,
      scene: scene,
      version: version
    };
  }

  function render(container, data) {
    if (!container) return null;
    if (container._csbCleanup) container._csbCleanup();
    ensureStyle();
    var model = normalize(data);
    if (!model) {
      container.innerHTML = '<div class="errorbox">没有可显示的 CSB 节点</div>';
      return null;
    }
    container.classList.add("gmp-mode");

    var textureBox = model.textures.length
      ? '<div class="csb-tex-box"><div class="section-title">纹理 ' + model.textureCount + ' 张</div><div class="bank-chips csb-textures"></div></div>'
      : "";
    container.innerHTML = '<div class="gmp-preview csb-preview">' +
      '<div class="gmp-head"><div class="gmp-title"><b>' + esc(model.scene) + "</b><span>" +
      esc(model.version || "csb") + " · " + model.nodeCount.toLocaleString() + " 个节点 · " +
      model.textureCount.toLocaleString() + " 张纹理</span></div>" +
      '<div class="csb-actions">' +
      '<button type="button" class="mini-btn" data-csb-act="fit">适配</button>' +
      '<button type="button" class="mini-btn" data-csb-act="out">−</button>' +
      '<span class="csb-zoom">100%</span>' +
      '<button type="button" class="mini-btn" data-csb-act="in">＋</button>' +
      "</div></div>" +
      '<div class="bank-chips">' + htmlForChips({ version: model.version, width: model.width, height: model.height, truncated: model.header.truncated }, model.nodeCount, model.textureCount) + "</div>" +
      '<div class="csb-body"><div class="csb-stage"><canvas class="csb-canvas"></canvas></div>' +
      '<div class="csb-side">' + sideEmpty() + "</div></div>" +
      '<div class="note csb-note">按 Cocos 左下原点布局，Y 轴已翻转到画布。虚线为隐藏节点，纹理加载失败时显示占位框与文件名。</div>' +
      textureBox +
      "</div>";

    var canvas = container.querySelector(".csb-canvas");
    var side = container.querySelector(".csb-side");
    var zoomLabel = container.querySelector(".csb-zoom");
    var textureList = container.querySelector(".csb-textures");
    var ctx = canvas.getContext("2d");
    if (!ctx) {
      container.querySelector(".csb-stage").innerHTML = '<div class="note">当前浏览器不支持 Canvas</div>';
      return null;
    }

    var state = {
      canvas: canvas,
      ctx: ctx,
      side: side,
      root: model.root,
      rootWidth: model.width,
      rootHeight: model.height,
      viewBox: model.viewBox,
      view: { zoom: 1, panX: 0, panY: 0 },
      selected: null,
      hover: null,
      hits: [],
      labels: [],
      placeholders: [],
      width: 0,
      height: 0,
      dpr: window.devicePixelRatio || 1,
      unit: 1,
      font: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      palette: paletteOf(container),
      raf: 0,
      disposed: false,
      order: 0
    };

    state.schedule = function () {
      if (state.raf || state.disposed) return;
      state.raf = requestAnimationFrame(function () {
        state.raf = 0;
        if (!state.disposed) draw(state);
      });
    };

    function updateSide(hit) {
      side.innerHTML = sideFor(hit);
    }

    function setZoom(value) {
      state.view.zoom = Math.max(0.25, Math.min(8, value));
      zoomLabel.textContent = Math.round(state.view.zoom * 100) + "%";
      state.schedule();
    }

    function selectNode(node) {
      if (!node) {
        state.selected = null;
        updateSide(null);
        state.schedule();
        return;
      }
      var depth = 0;
      function scan(current, level) {
        if (!current) return false;
        if (current === node) {
          depth = level;
          return true;
        }
        var kids = childrenOf(current);
        for (var i = 0; i < kids.length; i++) {
          if (scan(kids[i], level + 1)) return true;
        }
        return false;
      }
      scan(state.root, 0);
      state.selected = { node: node, depth: depth };
      updateSide(state.selected);
      state.schedule();
    }

    var drag = null;
    canvas.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) return;
      canvas.setPointerCapture(event.pointerId);
      drag = {
        id: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        panX: state.view.panX,
        panY: state.view.panY,
        moved: false
      };
      canvas.classList.add("dragging");
    });
    canvas.addEventListener("pointermove", function (event) {
      if (drag && drag.id === event.pointerId) {
        var dx = event.clientX - drag.x;
        var dy = event.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
        if (drag.moved) {
          state.view.panX = drag.panX + dx;
          state.view.panY = drag.panY + dy;
          state.schedule();
        }
        return;
      }
      var hit = hitAt(state, localPoint(canvas, event));
      var hitNode = hit ? hit.node : null;
      var hoverNode = state.hover ? state.hover.node : null;
      if (hitNode !== hoverNode) {
        state.hover = hit;
        canvas.style.cursor = hit ? "pointer" : "grab";
        state.schedule();
      }
    });
    canvas.addEventListener("pointerup", function (event) {
      if (!drag) return;
      var moved = drag.moved;
      drag = null;
      canvas.classList.remove("dragging");
      if (moved) return;
      var hit = hitAt(state, localPoint(canvas, event));
      state.selected = hit;
      updateSide(hit);
      state.schedule();
    });
    canvas.addEventListener("pointercancel", function () {
      drag = null;
      canvas.classList.remove("dragging");
    });
    canvas.addEventListener("wheel", function (event) {
      event.preventDefault();
      var factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
      setZoom(state.view.zoom * factor);
    }, { passive: false });

    var actions = container.querySelectorAll("[data-csb-act]");
    for (var actionIndex = 0; actionIndex < actions.length; actionIndex++) {
      actions[actionIndex].addEventListener("click", function (event) {
        var action = event.currentTarget.getAttribute("data-csb-act");
        if (action === "fit") {
          state.view.zoom = 1;
          state.view.panX = 0;
          state.view.panY = 0;
          zoomLabel.textContent = "100%";
          state.schedule();
        } else if (action === "in") {
          setZoom(state.view.zoom * 1.25);
        } else if (action === "out") {
          setZoom(state.view.zoom / 1.25);
        }
      });
    }

    if (textureList) {
      for (var textureIndex = 0; textureIndex < Math.min(model.textures.length, 80); textureIndex++) {
        var texture = model.textures[textureIndex];
        var chip = document.createElement("button");
        chip.type = "button";
        chip.className = "bank-chip csb-tex-chip";
        chip.title = texture;
        chip.textContent = baseName(texture);
        chip.addEventListener("click", function (event) {
          var path = event.currentTarget.title;
          var node = selectFirstByTexture(state.root, path);
          selectNode(node);
        });
        textureList.appendChild(chip);
      }
      if (model.textures.length > 80) {
        var more = document.createElement("span");
        more.className = "bank-chip";
        more.textContent = "还有 " + (model.textures.length - 80) + " 张";
        textureList.appendChild(more);
      }
    }

    var observer = null;
    if (typeof ResizeObserver === "function") {
      observer = new ResizeObserver(function () { state.schedule(); });
      observer.observe(container.querySelector(".csb-stage"));
    }

    container._csbCleanup = function () {
      state.disposed = true;
      if (state.raf) cancelAnimationFrame(state.raf);
      if (observer) observer.disconnect();
      container._csbCleanup = null;
    };

    requestAnimationFrame(function () { draw(state); });
    return {
      redraw: function () { state.schedule(); },
      select: selectNode
    };
  }

  window.renderCsbPreview = render;
})();
