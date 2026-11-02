(function () {
  "use strict";

  const STYLE_ID = "spine-preview-style";
  const RAD = Math.PI / 180;
  const PLAY_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
  const PAUSE_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h4v14H7zm6 0h4v14h-4z"/></svg>';
  const FALLBACK_COLORS = ["#7bdbe6", "#eec06c", "#b2cbd0", "#9df1fa", "#ffb4ab", "#c8e6a0", "#d8b4fe", "#f9a8d4"];

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function add(parent, child) {
    parent.appendChild(child);
    return child;
  }

  function toElement(target) {
    if (typeof target === "string") return document.querySelector(target);
    return target && target.nodeType === 1 ? target : null;
  }

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = [
      ".spine-preview{display:flex;flex-direction:column;gap:10px;width:100%;padding:12px;border:1px solid var(--md-outline-variant,#3f484b);border-radius:var(--md-shape-lg,16px);background:var(--md-surface-container-low,#171c1f);color:var(--md-on-surface,#dfe3e6);box-sizing:border-box;font-family:var(--md-font-ui,\"Segoe UI\",sans-serif)}",
      ".spine-preview *{box-sizing:border-box}",
      ".spine-preview-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;flex-wrap:wrap}",
      ".spine-preview-title{font-size:13px;font-weight:600;letter-spacing:.2px}",
      ".spine-preview-sub{font-family:var(--md-font-mono,Consolas,monospace);font-size:10px;color:var(--md-on-surface-variant,#bfc8cb);margin-top:2px;word-break:break-all}",
      ".spine-preview .bank-chips{display:flex;flex-wrap:wrap;gap:5px}",
      ".spine-preview .bank-chip{display:inline-flex;align-items:center;gap:5px;border:0;border-radius:var(--md-shape-sm,8px);padding:4px 8px;background:var(--md-surface-container-high,#252b2e);color:var(--md-on-surface-variant,#bfc8cb);font:10px/1.4 var(--md-font-mono,Consolas,monospace);cursor:default;white-space:nowrap}",
      ".spine-preview .bank-chip b{color:var(--md-on-surface,#dfe3e6);font-weight:600}",
      ".spine-preview .bank-chip.is-active{background:var(--md-primary-container,#004f55);color:var(--md-on-primary-container,#9df1fa)}",
      ".spine-preview .section-title{color:var(--md-on-surface-variant,#bfc8cb);font-size:10px;font-weight:600;letter-spacing:.3px;margin:2px 0 6px}",
      ".spine-preview-stage{position:relative;width:100%;border-radius:var(--md-shape-md,12px);overflow:hidden;background:var(--md-canvas-bg,#0a0e10);border:1px solid var(--md-outline-variant,#3f484b)}",
      ".spine-preview-canvas{display:block;width:100%;height:auto;touch-action:none;cursor:crosshair}",
      ".spine-preview-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:20px;text-align:center;color:var(--md-on-surface-variant,#bfc8cb);font-size:12px;background:var(--md-canvas-bg,#0a0e10)}",
      ".spine-preview-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap}",
      ".spine-preview-label{font-size:10px;color:var(--md-on-surface-variant,#bfc8cb);white-space:nowrap}",
      ".spine-preview-select{height:30px;min-width:130px;max-width:240px;border:1px solid var(--md-outline,#899296);border-radius:var(--md-shape-sm,8px);background:var(--md-surface-container,#1b2023);color:var(--md-on-surface,#dfe3e6);padding:0 8px;font-size:11px;outline:none}",
      ".spine-preview-select:focus-visible{border-color:var(--md-primary,#7bdbe6);box-shadow:0 0 0 2px var(--md-primary-soft,rgba(123,219,230,.12))}",
      ".spine-preview-btn{width:32px;height:30px;display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:var(--md-shape-sm,8px);background:var(--md-primary-container,#004f55);color:var(--md-on-primary-container,#9df1fa);cursor:pointer;padding:0}",
      ".spine-preview-btn svg{width:17px;height:17px;fill:currentColor}",
      ".spine-preview-btn:disabled{opacity:.4;cursor:default}",
      ".spine-preview-btn:not(:disabled):hover{filter:brightness(1.15)}",
      ".spine-preview-range{flex:1 1 150px;min-width:110px;height:4px;accent-color:var(--md-primary,#7bdbe6);cursor:pointer}",
      ".spine-preview-time{font:10px/1.4 var(--md-font-mono,Consolas,monospace);color:var(--md-on-surface-variant,#bfc8cb);white-space:nowrap;min-width:82px;text-align:right}",
      ".spine-preview-toggle{display:inline-flex;align-items:center;gap:5px;font-size:10px;color:var(--md-on-surface-variant,#bfc8cb);white-space:nowrap;cursor:pointer}",
      ".spine-preview-toggle input{accent-color:var(--md-primary,#7bdbe6)}",
      ".spine-preview-bones{max-height:96px;overflow:auto;padding:2px;border-radius:var(--md-shape-sm,8px)}",
      ".spine-preview .spine-preview-bones button{cursor:pointer;border:0;font:10px/1.4 var(--md-font-mono,Consolas,monospace)}",
      ".spine-preview .spine-preview-bones button:hover{background:var(--md-surface-container-highest,#303639)}",
      ".spine-preview-error{display:flex;flex-direction:column;gap:6px;padding:14px;border:1px dashed var(--md-error,#ffb4ab);border-radius:var(--md-shape-md,12px);background:var(--md-error-container,#93000a);color:var(--md-on-error-container,#ffdad6);font-size:12px}",
      ".spine-preview-error b{font-size:13px}",
      ".spine-preview-foot{font-size:10px;color:var(--md-on-surface-variant,#bfc8cb);line-height:1.5}",
      "@media (max-width:560px){.spine-preview{padding:9px}.spine-preview-time{min-width:70px}.spine-preview-select{max-width:160px}}",
    ].join("");
    document.head.appendChild(style);
  }

  function finite(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function hexToRgba(value, fallback) {
    const base = fallback || [123, 219, 230, 1];
    if (typeof value !== "string") return base.slice();
    let text = value.trim().replace("#", "");
    if (text.length === 3) text = text.split("").map(function (part) { return part + part; }).join("");
    if (text.length !== 6 && text.length !== 8) return base.slice();
    if (!/^[0-9a-fA-F]+$/.test(text)) return base.slice();
    const red = parseInt(text.slice(0, 2), 16);
    const green = parseInt(text.slice(2, 4), 16);
    const blue = parseInt(text.slice(4, 6), 16);
    const alpha = text.length === 8 ? parseInt(text.slice(6, 8), 16) / 255 : 1;
    return [red, green, blue, alpha];
  }

  function rgbaText(color) {
    return "rgba(" + Math.round(color[0]) + "," + Math.round(color[1]) + "," + Math.round(color[2]) + "," + clamp(color[3], 0, 1).toFixed(3) + ")";
  }

  function mixColor(left, right, ratio) {
    return [
      left[0] + (right[0] - left[0]) * ratio,
      left[1] + (right[1] - left[1]) * ratio,
      left[2] + (right[2] - left[2]) * ratio,
      left[3] + (right[3] - left[3]) * ratio,
    ];
  }

  function shortestAngle(from, to) {
    let delta = (to - from) % 360;
    if (delta > 180) delta -= 360;
    if (delta < -180) delta += 360;
    return from + delta;
  }

  function buildBones(source) {
    const map = new Map();
    const list = [];
    for (let index = 0; index < source.length; index += 1) {
      const raw = source[index];
      if (!raw || typeof raw !== "object" || typeof raw.name !== "string" || !raw.name) continue;
      if (map.has(raw.name)) continue;
      const bone = {
        name: raw.name,
        parent: typeof raw.parent === "string" && raw.parent ? raw.parent : null,
        x: finite(raw.x, 0),
        y: finite(raw.y, 0),
        rotation: finite(raw.rotation, 0),
        scaleX: finite(raw.scaleX, 1),
        scaleY: finite(raw.scaleY, 1),
        shearX: finite(raw.shearX, 0),
        shearY: finite(raw.shearY, 0),
        length: finite(raw.length, 0),
        transform: typeof raw.transform === "string" ? raw.transform : "normal",
        index: map.size,
        children: [],
      };
      map.set(bone.name, bone);
      list.push(bone);
    }
    list.forEach(function (bone) {
      if (bone.parent && !map.has(bone.parent)) bone.parent = null;
      if (bone.parent === bone.name) bone.parent = null;
    });
    list.forEach(function (bone) {
      const seen = new Set();
      let cursor = bone;
      while (cursor && cursor.parent && map.has(cursor.parent)) {
        if (seen.has(cursor.name)) {
          bone.parent = null;
          break;
        }
        seen.add(cursor.name);
        cursor = map.get(cursor.parent);
      }
    });
    list.forEach(function (bone) {
      if (!bone.parent) return;
      const parent = map.get(bone.parent);
      if (parent) parent.children.push(bone.name);
    });
    list.forEach(function (bone) {
      let depth = 0;
      let cursor = bone;
      const seen = new Set();
      while (cursor && cursor.parent && map.has(cursor.parent) && !seen.has(cursor.name)) {
        seen.add(cursor.name);
        cursor = map.get(cursor.parent);
        depth += 1;
        if (depth > 1024) break;
      }
      bone.depth = depth;
    });
    list.sort(function (left, right) {
      if (left.depth !== right.depth) return left.depth - right.depth;
      return left.index - right.index;
    });
    return { list: list, map: map };
  }

  function buildTracks(animation) {
    const bones = new Map();
    const slots = new Map();
    const drawOrder = [];
    const channels = animation && Array.isArray(animation.channels) ? animation.channels : [];
    channels.forEach(function (channel) {
      if (!channel || !channel.type || !Array.isArray(channel.frames) || !channel.frames.length) return;
      if (channel.kind === "bone" && channel.target) {
        let bucket = bones.get(channel.target);
        if (!bucket) {
          bucket = {};
          bones.set(channel.target, bucket);
        }
        bucket[channel.type] = channel.frames;
      } else if (channel.kind === "slot" && channel.target) {
        let bucket = slots.get(channel.target);
        if (!bucket) {
          bucket = {};
          slots.set(channel.target, bucket);
        }
        bucket[channel.type] = channel.frames;
      } else if (channel.kind === "drawOrder") {
        drawOrder.push(channel.frames);
      }
    });
    return { bones: bones, slots: slots, drawOrder: drawOrder.length ? drawOrder[0] : null };
  }

  function frameIndex(frames, time) {
    if (!Array.isArray(frames) || !frames.length) return -1;
    if (time < finite(frames[0].t, 0)) return -1;
    let low = 0;
    let high = frames.length - 1;
    while (low < high) {
      const middle = (low + high + 1) >> 1;
      if (finite(frames[middle].t, 0) <= time) low = middle;
      else high = middle - 1;
    }
    return low;
  }

  function sampleNumber(frames, time, key, fallback, cycle) {
    const index = frameIndex(frames, time);
    if (index < 0) return fallback;
    const current = frames[index];
    const raw = Number(current[key]);
    let value = Number.isFinite(raw) ? raw : fallback;
    const next = frames[index + 1];
    if (!next || current.curve === "stepped") return value;
    const start = finite(current.t, 0);
    const end = finite(next.t, start);
    const nextRaw = Number(next[key]);
    const target = Number.isFinite(nextRaw) ? nextRaw : fallback;
    if (end <= start) return target;
    const ratio = clamp((time - start) / (end - start), 0, 1);
    if (cycle) {
      const targetShort = shortestAngle(value, target);
      return value + (targetShort - value) * ratio;
    }
    return value + (target - value) * ratio;
  }

  function sampleColor(frames, time, fallback) {
    const index = frameIndex(frames, time);
    if (index < 0) return fallback.slice();
    const current = frames[index];
    const left = hexToRgba(current.color, fallback);
    const next = frames[index + 1];
    if (!next || current.curve === "stepped") return left;
    const start = finite(current.t, 0);
    const end = finite(next.t, start);
    if (end <= start) return hexToRgba(next.color, fallback);
    const ratio = clamp((time - start) / (end - start), 0, 1);
    return mixColor(left, hexToRgba(next.color, fallback), ratio);
  }

  function sampleAttachment(frames, time, fallback) {
    const index = frameIndex(frames, time);
    if (index < 0) return fallback;
    const name = frames[index].name;
    return typeof name === "string" ? name : "";
  }

  function axisMatrix(rotation, scaleX, scaleY, shearX, shearY) {
    const radX = (rotation + shearX) * RAD;
    const radY = (rotation + 90 + shearY) * RAD;
    return {
      a: Math.cos(radX) * scaleX,
      b: Math.cos(radY) * scaleY,
      c: Math.sin(radX) * scaleX,
      d: Math.sin(radY) * scaleY,
      e: 0,
      f: 0,
    };
  }

  function multiplyMatrix(parent, local) {
    return {
      a: parent.a * local.a + parent.b * local.c,
      b: parent.a * local.b + parent.b * local.d,
      c: parent.c * local.a + parent.d * local.c,
      d: parent.c * local.b + parent.d * local.d,
      e: parent.a * local.e + parent.b * local.f + parent.e,
      f: parent.c * local.e + parent.d * local.f + parent.f,
    };
  }

  function applyMatrix(matrix, x, y) {
    return {
      x: matrix.a * x + matrix.b * y + matrix.e,
      y: matrix.c * x + matrix.d * y + matrix.f,
    };
  }

  function computePose(bones, boneTracks, time, setupOnly) {
    const world = new Map();
    bones.forEach(function (bone) {
      const tracks = setupOnly ? null : boneTracks.get(bone.name);
      const translate = tracks && tracks.translate;
      const rotate = tracks && tracks.rotate;
      const scale = tracks && tracks.scale;
      const shear = tracks && tracks.shear;
      const localX = sampleNumber(translate, time, "x", bone.x, false);
      const localY = sampleNumber(translate, time, "y", bone.y, false);
      const rotation = sampleNumber(rotate, time, "v", bone.rotation, true);
      const scaleX = sampleNumber(scale, time, "x", bone.scaleX, false);
      const scaleY = sampleNumber(scale, time, "y", bone.scaleY, false);
      const shearX = sampleNumber(shear, time, "x", bone.shearX, false);
      const shearY = sampleNumber(shear, time, "y", bone.shearY, false);
      const parent = bone.parent ? world.get(bone.parent) : null;
      const local = axisMatrix(rotation, scaleX, scaleY, shearX, shearY);
      let worldX;
      let worldY;
      let matrix;
      if (!parent) {
        worldX = localX;
        worldY = localY;
        matrix = { a: local.a, b: local.b, c: local.c, d: local.d, e: localX, f: localY };
      } else {
        worldX = parent.a * localX + parent.b * localY + parent.e;
        worldY = parent.c * localX + parent.d * localY + parent.f;
        if (bone.transform === "onlyTranslation") {
          matrix = { a: local.a, b: local.b, c: local.c, d: local.d, e: worldX, f: worldY };
        } else if (bone.transform === "noRotationOrReflection") {
          let pa = parent.a;
          let pb = parent.b;
          let pc = parent.c;
          let pd = parent.d;
          let s = pa * pa + pc * pc;
          let prx = 0;
          if (s > 0.0001) {
            s = Math.abs(pa * pd - pb * pc) / s;
            pb = pc * s;
            pd = pa * s;
            prx = Math.atan2(pc, pa) / RAD;
          } else {
            pa = 0;
            pc = 0;
            prx = 90 - Math.atan2(pd, pb) / RAD;
          }
          const rx = (rotation + shearX - prx) * RAD;
          const ry = (rotation + shearY - prx + 90) * RAD;
          const la = Math.cos(rx) * scaleX;
          const lb = Math.cos(ry) * scaleY;
          const lc = Math.sin(rx) * scaleX;
          const ld = Math.sin(ry) * scaleY;
          matrix = {
            a: pa * la - pb * lc,
            b: pa * lb - pb * ld,
            c: pc * la + pd * lc,
            d: pc * lb + pd * ld,
            e: worldX,
            f: worldY,
          };
        } else if (bone.transform === "noScale" || bone.transform === "noScaleOrReflection") {
          const cos = Math.cos(rotation * RAD);
          const sin = Math.sin(rotation * RAD);
          let za = parent.a * cos + parent.b * sin;
          let zc = parent.c * cos + parent.d * sin;
          let s = Math.sqrt(za * za + zc * zc);
          if (s > 0.00001) s = 1 / s;
          za *= s;
          zc *= s;
          s = Math.sqrt(za * za + zc * zc);
          if (bone.transform === "noScale" && parent.a * parent.d - parent.b * parent.c < 0) s = -s;
          const r = Math.PI / 2 + Math.atan2(zc, za);
          const zb = Math.cos(r) * s;
          const zd = Math.sin(r) * s;
          const la = Math.cos(shearX * RAD) * scaleX;
          const lb = Math.cos((90 + shearY) * RAD) * scaleY;
          const lc = Math.sin(shearX * RAD) * scaleX;
          const ld = Math.sin((90 + shearY) * RAD) * scaleY;
          matrix = {
            a: za * la + zb * lc,
            b: za * lb + zb * ld,
            c: zc * la + zd * lc,
            d: zc * lb + zd * ld,
            e: worldX,
            f: worldY,
          };
        } else {
          matrix = multiplyMatrix(parent, local);
          matrix.e = worldX;
          matrix.f = worldY;
        }
      }
      let worldRotation;
      let worldScaleX;
      let worldScaleY;
      if (!parent) {
        worldRotation = rotation;
        worldScaleX = scaleX;
        worldScaleY = scaleY;
      } else if (bone.transform === "normal") {
        worldRotation = parent.rotation + rotation;
        worldScaleX = parent.scaleX * scaleX;
        worldScaleY = parent.scaleY * scaleY;
      } else if (bone.transform === "onlyTranslation") {
        worldRotation = rotation;
        worldScaleX = scaleX;
        worldScaleY = scaleY;
      } else {
        worldRotation = Math.atan2(matrix.c, matrix.a) / RAD;
        worldScaleX = Math.sqrt(matrix.a * matrix.a + matrix.c * matrix.c);
        worldScaleY = Math.sqrt(matrix.b * matrix.b + matrix.d * matrix.d);
      }
      world.set(bone.name, {
        x: worldX,
        y: worldY,
        rotation: worldRotation,
        scaleX: worldScaleX,
        scaleY: worldScaleY,
        shearX: shearX,
        shearY: shearY,
        localX: localX,
        localY: localY,
        a: matrix.a,
        b: matrix.b,
        c: matrix.c,
        d: matrix.d,
        e: matrix.e,
        f: matrix.f,
        matrix: matrix,
      });
    });
    return world;
  }

  function solveBounds(skeleton, bones, world, slots, attachments, boneByIndex) {
    const width = finite(skeleton.width, 0);
    const height = finite(skeleton.height, 0);
    const points = [];
    function addPoint(x, y) {
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      if (Math.abs(x) > 1000000 || Math.abs(y) > 1000000) return;
      points.push([x, y]);
    }
    if (width > 0 && height > 0) {
      const x = finite(skeleton.x, 0);
      const y = finite(skeleton.y, 0);
      addPoint(x, y);
      addPoint(x + width, y);
      addPoint(x + width, y + height);
      addPoint(x, y + height);
    }
    bones.forEach(function (bone) {
      const pose = world.get(bone.name);
      if (pose) addPoint(pose.x, pose.y);
    });
    slots.forEach(function (slot) {
      if (!slot) return;
      const pose = slot.bone ? world.get(slot.bone) : null;
      const bucket = attachments[slot.name];
      const attachment = bucket && slot.attachment ? bucket[slot.attachment] : null;
      if (!attachment) return;
      if (attachment.type === "mesh" && !attachment.data_truncated) {
        const count = meshVertexCount(attachment);
        if (count) {
          const step = Math.max(1, Math.ceil(count / 2000));
          for (let index = 0; index < count; index += step) {
            const point = meshVertexPoint(attachment, index, world, boneByIndex, slot.bone);
            if (point) addPoint(point.x, point.y);
          }
          return;
        }
      }
      const box = attachmentBox(attachment);
      if (!pose || !box) return;
      const halfWidth = box.width / 2;
      const halfHeight = box.height / 2;
      [[-halfWidth, -halfHeight], [halfWidth, -halfHeight], [halfWidth, halfHeight], [-halfWidth, halfHeight]].forEach(function (corner) {
        const point = localPoint(pose, box.x + corner[0], box.y + corner[1]);
        addPoint(point.x, point.y);
      });
    });
    if (!points.length) return { minX: -100, minY: -100, maxX: 100, maxY: 100 };
    let minX = points[0][0];
    let minY = points[0][1];
    let maxX = minX;
    let maxY = minY;
    points.forEach(function (point) {
      minX = Math.min(minX, point[0]);
      minY = Math.min(minY, point[1]);
      maxX = Math.max(maxX, point[0]);
      maxY = Math.max(maxY, point[1]);
    });
    if (maxX - minX < 1) {
      minX -= 50;
      maxX += 50;
    }
    if (maxY - minY < 1) {
      minY -= 50;
      maxY += 50;
    }
    return { minX: minX, minY: minY, maxX: maxX, maxY: maxY };
  }

  function localPoint(boneWorld, localX, localY) {
    if (boneWorld && boneWorld.matrix) return applyMatrix(boneWorld.matrix, localX, localY);
    const rad = boneWorld.rotation * RAD;
    const cos = Math.cos(rad);
    const sin = Math.sin(rad);
    return {
      x: boneWorld.x + localX * boneWorld.scaleX * cos - localY * boneWorld.scaleY * sin,
      y: boneWorld.y + localX * boneWorld.scaleX * sin + localY * boneWorld.scaleY * cos,
    };
  }

  function attachmentBox(attachment) {
    if (!attachment) return null;
    if (attachment.type === "region") {
      return {
        x: finite(attachment.x, 0),
        y: finite(attachment.y, 0),
        width: finite(attachment.width, 0),
        height: finite(attachment.height, 0),
        rotation: finite(attachment.rotation, 0),
        scaleX: finite(attachment.scaleX, 1),
        scaleY: finite(attachment.scaleY, 1),
      };
    }
    const box = Array.isArray(attachment.bbox) ? attachment.bbox : null;
    if (box && box.length === 4) {
      const minX = finite(box[0], 0);
      const minY = finite(box[1], 0);
      const maxX = finite(box[2], 0);
      const maxY = finite(box[3], 0);
      return {
        x: (minX + maxX) / 2,
        y: (minY + maxY) / 2,
        width: Math.max(maxX - minX, 1),
        height: Math.max(maxY - minY, 1),
        rotation: 0,
        scaleX: 1,
        scaleY: 1,
      };
    }
    const width = finite(attachment.width, 0);
    const height = finite(attachment.height, 0);
    if (width > 0 || height > 0) {
      return {
        x: 0,
        y: 0,
        width: Math.max(width, 1),
        height: Math.max(height, 1),
        rotation: 0,
        scaleX: 1,
        scaleY: 1,
      };
    }
    return null;
  }

  function meshVertexCount(attachment) {
    if (!attachment) return 0;
    if (Array.isArray(attachment.weights)) return attachment.weights.length;
    if (Array.isArray(attachment.vertices)) return Math.floor(attachment.vertices.length / 2);
    return 0;
  }

  function meshVertexPoint(attachment, index, world, boneByIndex, fallbackBone) {
    if (Array.isArray(attachment.weights)) {
      const group = attachment.weights[index];
      if (!Array.isArray(group)) return null;
      let x = 0;
      let y = 0;
      for (let cursor = 0; cursor + 3 < group.length; cursor += 4) {
        const boneName = boneByIndex.get(finite(group[cursor], -1));
        const pose = boneName ? world.get(boneName) : null;
        const weight = finite(group[cursor + 3], 0);
        if (!pose || !pose.matrix || weight === 0) continue;
        const point = applyMatrix(pose.matrix, finite(group[cursor + 1], 0), finite(group[cursor + 2], 0));
        x += point.x * weight;
        y += point.y * weight;
      }
      return { x: x, y: y };
    }
    const pose = fallbackBone ? world.get(fallbackBone) : null;
    if (!pose) return null;
    return localPoint(pose, finite(attachment.vertices[index * 2], 0), finite(attachment.vertices[index * 2 + 1], 0));
  }

  function meshUvPoint(attachment, index, region, image, normalized) {
    if (!Array.isArray(attachment.uvs) || attachment.uvs.length < index * 2 + 2) return null;
    const u = finite(attachment.uvs[index * 2], 0);
    const v = finite(attachment.uvs[index * 2 + 1], 0);
    if (normalized) return [u * normalized.width, v * normalized.height];
    if (region) {
      return [
        finite(region.x, 0) + u * finite(region.width, image.naturalWidth || image.width || 1),
        finite(region.y, 0) + v * finite(region.height, image.naturalHeight || image.height || 1),
      ];
    }
    return [
      u * finite(attachment.width, image.naturalWidth || image.width || 1),
      v * finite(attachment.height, image.naturalHeight || image.height || 1),
    ];
  }

  function inflateTriangle(point0, point1, point2, amount) {
    const centerX = (point0.x + point1.x + point2.x) / 3;
    const centerY = (point0.y + point1.y + point2.y) / 3;
    function move(point) {
      const dx = point.x - centerX;
      const dy = point.y - centerY;
      const length = Math.sqrt(dx * dx + dy * dy);
      if (length < 0.0001) return point;
      return { x: point.x + (dx / length) * amount, y: point.y + (dy / length) * amount };
    }
    return [move(point0), move(point1), move(point2)];
  }

  function triangleMatrix(texture0, texture1, texture2, point0, point1, point2) {
    const d1x = texture1[0] - texture0[0];
    const d1y = texture1[1] - texture0[1];
    const d2x = texture2[0] - texture0[0];
    const d2y = texture2[1] - texture0[1];
    const det = d1x * d2y - d1y * d2x;
    if (!Number.isFinite(det) || Math.abs(det) < 0.000001) return null;
    const s1x = point1.x - point0.x;
    const s1y = point1.y - point0.y;
    const s2x = point2.x - point0.x;
    const s2y = point2.y - point0.y;
    const a = (s1x * d2y - s2x * d1y) / det;
    const c = (-s1x * d2x + s2x * d1x) / det;
    const b = (s1y * d2y - s2y * d1y) / det;
    const d = (-s1y * d2x + s2y * d1x) / det;
    return {
      a: a,
      b: b,
      c: c,
      d: d,
      e: point0.x - a * texture0[0] - c * texture0[1],
      f: point0.y - b * texture0[0] - d * texture0[1],
    };
  }


  function makeTextureLookup(textures, onLoad) {
    const loaded = new Map();
    const failed = new Set();
    function unwrap(value) {
      if (!value) return null;
      if (typeof value === "string") return value;
      if (value.image) return value.image;
      if (value.texture) return value.texture;
      if (value.canvas) return value.canvas;
      if (value.bitmap) return value.bitmap;
      return value;
    }
    function lookup(keys) {
      if (!textures) return null;
      for (let index = 0; index < keys.length; index += 1) {
        const key = keys[index];
        if (!key) continue;
        let value = null;
        if (textures instanceof Map) {
          value = textures.get(key);
        } else if (typeof textures === "function") {
          value = textures(key);
        } else if (Array.isArray(textures)) {
          for (let cursor = 0; cursor < textures.length; cursor += 1) {
            const item = textures[cursor];
            if (item && (item.name === key || item.page === key || item.key === key)) {
              value = item.image || item.texture || item.canvas || item;
              break;
            }
          }
        } else if (typeof textures === "object") {
          value = textures[key];
        }
        value = unwrap(value);
        if (!value) continue;
        if (typeof value === "string") {
          if (failed.has(value)) continue;
          let image = loaded.get(value);
          if (!image) {
            image = new Image();
            image.decoding = "async";
            image.onload = function () { if (typeof onLoad === "function") onLoad(); };
            image.onerror = function () { failed.add(value); };
            image.src = value;
            loaded.set(value, image);
          }
          if (image.complete && image.naturalWidth) return image;
          continue;
        }
        if ((typeof value.getContext === "function" || typeof value.width === "number") && value.width > 0 && value.height > 0) return value;
      }
      return null;
    }
    return lookup;
  }

  function pageKeys(page) {
    const keys = [];
    const seen = new Set();
    function add(value) {
      if (typeof value !== "string" || !value || seen.has(value)) return;
      seen.add(value);
      keys.push(value);
    }
    add(page.texture);
    add(page.name);
    const sources = [page.texture, page.name];
    sources.forEach(function (value) {
      if (typeof value !== "string" || !value) return;
      const slash = value.replace(/\\/g, "/");
      add(slash);
      add(slash.split("/").pop());
      add(value.replace(/\\/g, "/").replace(/\.(png|jpg|jpeg|kta|ktx|webp)$/i, ""));
      add(value.replace(/\.png$/i, ".kta"));
      add(value.replace(/\.kta$/i, ".png"));
    });
    return keys;
  }

  function buildRegionMap(atlas) {
    const map = new Map();
    const pages = Array.isArray(atlas.pages) ? atlas.pages : [];
    if (atlas.regions && typeof atlas.regions === "object") {
      Object.keys(atlas.regions).forEach(function (name) {
        const region = atlas.regions[name];
        if (region && typeof region === "object" && !map.has(name)) map.set(name, region);
      });
    }
    pages.forEach(function (page, pageIndex) {
      const regions = Array.isArray(page.regions) ? page.regions : [];
      regions.forEach(function (region) {
        if (!region || typeof region.name !== "string") return;
        if (!map.has(region.name)) {
          region.page = Number.isInteger(region.page) ? region.page : pageIndex;
          map.set(region.name, region);
        }
      });
    });
    return map;
  }

  function buildSlotOrder(slots, drawFrames, time) {
    const base = slots.slice();
    if (!drawFrames || !drawFrames.length) return base;
    const index = frameIndex(drawFrames, time);
    if (index < 0) return base;
    const frame = drawFrames[index];
    const offsets = Array.isArray(frame.offsets) ? frame.offsets : [];
    if (!offsets.length) return base;
    const order = base.slice();
    offsets.forEach(function (item) {
      if (!item || typeof item.slot !== "string") return;
      let from = -1;
      for (let cursor = 0; cursor < order.length; cursor += 1) {
        if (order[cursor].name === item.slot) {
          from = cursor;
          break;
        }
      }
      if (from < 0) return;
      const moving = order.splice(from, 1)[0];
      const target = clamp(from + finite(item.offset, 0), 0, order.length);
      order.splice(target, 0, moving);
    });
    return order;
  }

  function collectAnimationNames(animations) {
    const names = [];
    const seen = new Set();
    animations.forEach(function (animation) {
      if (!animation || typeof animation.name !== "string" || !animation.name || seen.has(animation.name)) return;
      seen.add(animation.name);
      names.push(animation.name);
    });
    return names;
  }

  function findAnimation(animations, name) {
    for (let index = 0; index < animations.length; index += 1) {
      if (animations[index] && animations[index].name === name) return animations[index];
    }
    return null;
  }

  function render(container, data, textures) {
    injectStyle();
    const host = toElement(container);
    if (!host) return null;
    if (host.__spinePreviewApi && typeof host.__spinePreviewApi.destroy === "function") {
      host.__spinePreviewApi.destroy();
    }
    host.innerHTML = "";

    const root = add(host, make("div", "spine-preview"));
    if (!data || typeof data !== "object") {
      const error = add(root, make("div", "spine-preview-error"));
      add(error, make("b", "", "Spine 预览失败"));
      add(error, make("span", "", "没有收到可解析的数据"));
      return null;
    }
    if (data.error) {
      const error = add(root, make("div", "spine-preview-error"));
      add(error, make("b", "", "Spine 解析失败"));
      add(error, make("span", "", String(data.error)));
      return null;
    }

    const skeleton = data.skeleton && typeof data.skeleton === "object" ? data.skeleton : {};
    const boneInfo = buildBones(Array.isArray(skeleton.bones) ? skeleton.bones : []);
    const bones = boneInfo.list;
    const boneByIndex = new Map();
    bones.forEach(function (bone) { boneByIndex.set(bone.index, bone.name); });
    const slots = Array.isArray(data.slots) ? data.slots.filter(function (slot) { return slot && typeof slot === "object"; }) : [];
    const animations = Array.isArray(data.animations) ? data.animations : [];
    const animationNames = collectAnimationNames(animations);
    const skins = Array.isArray(skeleton.skins) ? skeleton.skins : [];
    const atlas = data.atlas && typeof data.atlas === "object" ? data.atlas : { pages: [], regions: {} };
    const pages = Array.isArray(atlas.pages) ? atlas.pages : [];
    const regionMap = buildRegionMap(atlas);
    const defaultSkinName = typeof skeleton.default_skin === "string" ? skeleton.default_skin : "";
    let defaultSkin = null;
    for (let index = 0; index < skins.length; index += 1) {
      if (skins[index] && skins[index].name === defaultSkinName) {
        defaultSkin = skins[index];
        break;
      }
    }
    if (!defaultSkin) {
      for (let index = 0; index < skins.length; index += 1) {
        if (skins[index] && skins[index].name === "default") {
          defaultSkin = skins[index];
          break;
        }
      }
    }
    if (!defaultSkin && skins.length) defaultSkin = skins[0];
    const attachments = defaultSkin && defaultSkin.attachments && typeof defaultSkin.attachments === "object" ? defaultSkin.attachments : {};

    const state = {
      animationName: animationNames.length ? animationNames[0] : "",
      animation: animationNames.length ? findAnimation(animations, animationNames[0]) : null,
      time: 0,
      duration: 0,
      playing: false,
      loop: true,
      speed: 1,
      tracks: buildTracks(animationNames.length ? findAnimation(animations, animationNames[0]) : null),
      selectedBone: "",
      hoverBone: "",
      showBones: true,
      raf: 0,
      lastStamp: 0,
      drawPending: 0,
      bonePoints: new Map(),
      destroy: false,
    };
    state.duration = state.animation ? Math.max(finite(state.animation.duration, 0), 0) : 0;

    const head = add(root, make("div", "spine-preview-head"));
    const headText = add(head, make("div"));
    add(headText, make("div", "spine-preview-title", "Spine 骨骼预览"));
    add(headText, make("div", "spine-preview-sub", [data.format || "spine", data.version || "未知版本"].join(" · ")));
    const chips = add(headText, make("div", "bank-chips"));
    function chip(label, value) {
      const node = add(chips, make("span", "bank-chip"));
      add(node, make("b", "", value));
      node.appendChild(document.createTextNode(" " + label));
      return node;
    }
    chip("骨骼", bones.length);
    chip("插槽", slots.length);
    chip("动画", animations.length);
    chip("图集页", pages.length);
    if (atlas.region_count || regionMap.size) chip("区域", atlas.region_count || regionMap.size);

    const stage = add(root, make("div", "spine-preview-stage"));
    const canvas = add(stage, make("canvas", "spine-preview-canvas"));
    const empty = add(stage, make("div", "spine-preview-empty", bones.length ? "没有可绘制的内容" : "没有骨骼数据"));
    if (bones.length && slots.length) empty.style.display = "none";
    const context = canvas.getContext("2d");
    if (!context) {
      empty.textContent = "当前浏览器不支持 Canvas 2D";
      return null;
    }

    const controls = add(root, make("div", "spine-preview-controls"));
    add(controls, make("span", "spine-preview-label", "动画"));
    const select = add(controls, make("select", "spine-preview-select"));
    select.setAttribute("aria-label", "选择动画");
    if (!animationNames.length) {
      const option = add(select, make("option", "", "静态姿势"));
      option.value = "";
      select.disabled = true;
    } else {
      animationNames.forEach(function (name) {
        const option = add(select, make("option", "", name));
        option.value = name;
      });
    }
    const playButton = add(controls, make("button", "spine-preview-btn"));
    playButton.type = "button";
    playButton.title = "播放";
    playButton.setAttribute("aria-label", "播放或暂停");
    playButton.innerHTML = PLAY_ICON;
    playButton.disabled = !state.animation;
    const range = add(controls, make("input", "spine-preview-range"));
    range.type = "range";
    range.min = "0";
    range.max = "1000";
    range.step = "1";
    range.value = "0";
    range.setAttribute("aria-label", "动画进度");
    range.disabled = !state.animation;
    const timeLabel = add(controls, make("span", "spine-preview-time", "0.00 / 0.00s"));
    const loopLabel = add(controls, make("label", "spine-preview-toggle"));
    const loopInput = add(loopLabel, make("input"));
    loopInput.type = "checkbox";
    loopInput.checked = true;
    loopLabel.appendChild(document.createTextNode("循环"));

    const boneTitle = add(root, make("div", "section-title", "骨骼高亮"));
    const boneChips = add(root, make("div", "bank-chips spine-preview-bones"));
    const boneButtons = new Map();
    const boneLimit = 240;
    bones.slice(0, boneLimit).forEach(function (bone) {
      const button = add(boneChips, make("button", "bank-chip", bone.name));
      button.type = "button";
      button.title = "高亮 " + bone.name;
      boneButtons.set(bone.name, button);
      button.addEventListener("click", function () {
        state.selectedBone = state.selectedBone === bone.name ? "" : bone.name;
        syncBoneButtons();
        draw();
      });
    });
    if (bones.length > boneLimit) {
      add(boneChips, make("span", "bank-chip", "+" + (bones.length - boneLimit)));
    }
    const foot = add(root, make("div", "spine-preview-foot", "点击画布或骨骼标签可高亮；贴图不可用时绘制彩色占位矩形和名称。"));

    const lookupTexture = makeTextureLookup(textures, function () { scheduleDraw(); });
    const uprightCache = new WeakMap();
    const view = { width: 640, height: 400, scale: 1, ratio: 1, bounds: { minX: -100, minY: -100, maxX: 100, maxY: 100 } };

    function uprightRegion(image, region) {
      if (!image || !region) return null;
      const degrees = ((finite(region.degrees, region.rotate ? 90 : 0) % 360) + 360) % 360;
      if (!degrees) return null;
      const cached = uprightCache.get(region);
      if (cached && cached.image === image) return cached.canvas;
      try {
        const width = Math.max(1, Math.round(finite(region.width, 1)));
        const height = Math.max(1, Math.round(finite(region.height, 1)));
        const x = finite(region.x, 0);
        const y = finite(region.y, 0);
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const paint = canvas.getContext("2d");
        if (!paint) return null;
        paint.save();
        if (degrees === 90) {
          const sourceWidth = Math.max(1, Math.round(finite(region.height, 1)));
          const sourceHeight = Math.max(1, Math.round(finite(region.width, 1)));
          paint.translate(width, 0);
          paint.rotate(Math.PI / 2);
          paint.drawImage(image, x, y, sourceWidth, sourceHeight, 0, 0, sourceWidth, sourceHeight);
        } else if (degrees === 180) {
          paint.translate(width, height);
          paint.rotate(Math.PI);
          paint.drawImage(image, x, y, width, height, 0, 0, width, height);
        } else if (degrees === 270) {
          const sourceWidth = Math.max(1, Math.round(finite(region.height, 1)));
          const sourceHeight = Math.max(1, Math.round(finite(region.width, 1)));
          paint.translate(0, height);
          paint.rotate(-Math.PI / 2);
          paint.drawImage(image, x, y, sourceWidth, sourceHeight, 0, 0, sourceWidth, sourceHeight);
        } else {
          const rad = degrees * RAD;
          const cos = Math.cos(rad);
          const sin = Math.sin(rad);
          const sourceWidth = Math.abs(width * cos) + Math.abs(height * sin);
          const sourceHeight = Math.abs(width * sin) + Math.abs(height * cos);
          paint.translate(width / 2, height / 2);
          paint.rotate(rad);
          paint.drawImage(image, x, y, sourceWidth, sourceHeight, -sourceWidth / 2, -sourceHeight / 2, sourceWidth, sourceHeight);
        }
        paint.restore();
        uprightCache.set(region, { image: image, canvas: canvas });
        return canvas;
      } catch (error) {
        return null;
      }
    }

    function syncBoneButtons() {
      boneButtons.forEach(function (button, name) {
        button.classList.toggle("is-active", name === state.selectedBone);
      });
    }

    function formatTime(seconds) {
      return finite(seconds, 0).toFixed(2);
    }

    function updateTimeline() {
      const duration = state.duration;
      const ratio = duration > 0 ? clamp(state.time / duration, 0, 1) : 0;
      range.value = String(Math.round(ratio * 1000));
      timeLabel.textContent = formatTime(state.time) + " / " + formatTime(duration) + "s";
    }

    function setAnimation(name) {
      state.animationName = name;
      state.animation = findAnimation(animations, name);
      state.tracks = buildTracks(state.animation);
      state.duration = state.animation ? Math.max(finite(state.animation.duration, 0), 0) : 0;
      state.time = 0;
      state.selectedBone = "";
      syncBoneButtons();
      playButton.disabled = !state.animation;
      range.disabled = !state.animation;
      if (!state.animation) pause();
      updateTimeline();
      draw();
    }

    function setTime(seconds) {
      const duration = state.duration;
      state.time = clamp(finite(seconds, 0), 0, duration > 0 ? duration : 0);
      updateTimeline();
      draw();
    }

    function play() {
      if (!state.animation || state.destroy) return;
      if (state.duration <= 0) return;
      if (state.time >= state.duration) state.time = 0;
      state.playing = true;
      state.lastStamp = 0;
      playButton.innerHTML = PAUSE_ICON;
      playButton.title = "暂停";
      if (!state.raf) state.raf = requestAnimationFrame(tick);
    }

    function pause() {
      state.playing = false;
      playButton.innerHTML = PLAY_ICON;
      playButton.title = "播放";
      if (state.raf) {
        cancelAnimationFrame(state.raf);
        state.raf = 0;
      }
      state.lastStamp = 0;
    }

    function tick(stamp) {
      state.raf = 0;
      if (!state.playing || state.destroy) return;
      if (!state.lastStamp) state.lastStamp = stamp;
      const delta = Math.min(Math.max((stamp - state.lastStamp) / 1000, 0), 0.1);
      state.lastStamp = stamp;
      state.time += delta * state.speed;
      if (state.time >= state.duration) {
        if (state.loop) {
          state.time = state.duration > 0 ? state.time % state.duration : 0;
        } else {
          state.time = state.duration;
          pause();
        }
      }
      updateTimeline();
      draw();
      if (state.playing) state.raf = requestAnimationFrame(tick);
    }

    function scheduleDraw() {
      if (state.drawPending) return;
      state.drawPending = requestAnimationFrame(function () {
        state.drawPending = 0;
        draw();
      });
    }

    function resize() {
      const width = Math.max(260, Math.round(stage.clientWidth || host.clientWidth || 640));
      const height = Math.max(220, Math.min(620, Math.round(width * 0.62)));
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(width * ratio));
      canvas.height = Math.max(1, Math.round(height * ratio));
      canvas.style.width = width + "px";
      canvas.style.height = height + "px";
      view.width = width;
      view.height = height;
      view.ratio = ratio;
      draw();
    }

    function project(point) {
      const bounds = view.bounds;
      const centerX = (bounds.minX + bounds.maxX) / 2;
      const centerY = (bounds.minY + bounds.maxY) / 2;
      return {
        x: view.width / 2 + (point.x - centerX) * view.scale,
        y: view.height / 2 - (point.y - centerY) * view.scale,
      };
    }

    function fitBounds(bounds) {
      const width = Math.max(bounds.maxX - bounds.minX, 1);
      const height = Math.max(bounds.maxY - bounds.minY, 1);
      const padding = 24;
      view.scale = Math.max(0.01, Math.min((view.width - padding * 2) / width, (view.height - padding * 2) / height));
      view.bounds = bounds;
    }

    function drawBackground(ctx) {
      ctx.save();
      ctx.setTransform(view.ratio, 0, 0, view.ratio, 0, 0);
      ctx.clearRect(0, 0, view.width, view.height);
      const gradient = ctx.createLinearGradient(0, 0, 0, view.height);
      gradient.addColorStop(0, "rgba(16,22,25,1)");
      gradient.addColorStop(1, "rgba(8,11,13,1)");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, view.width, view.height);
      ctx.strokeStyle = "rgba(191,200,203,0.06)";
      ctx.lineWidth = 1;
      const step = 32;
      for (let x = 0; x < view.width; x += step) {
        ctx.beginPath();
        ctx.moveTo(x + 0.5, 0);
        ctx.lineTo(x + 0.5, view.height);
        ctx.stroke();
      }
      for (let y = 0; y < view.height; y += step) {
        ctx.beginPath();
        ctx.moveTo(0, y + 0.5);
        ctx.lineTo(view.width, y + 0.5);
        ctx.stroke();
      }
      ctx.restore();
    }

    function slotColor(slot, slotTracks) {
      const tracks = slotTracks && slotTracks.color;
      const fallback = hexToRgba(slot && slot.color, [255, 255, 255, 1]);
      return sampleColor(tracks, state.time, fallback);
    }

    function attachmentOf(slot, slotTracks) {
      const tracks = slotTracks && slotTracks.attachment;
      const setupName = slot && typeof slot.attachment === "string" ? slot.attachment : "";
      const name = sampleAttachment(tracks, state.time, setupName);
      if (!name) return null;
      const bucket = attachments[slot.name];
      if (bucket && bucket[name]) return bucket[name];
      if (bucket) {
        const keys = Object.keys(bucket);
        for (let index = 0; index < keys.length; index += 1) {
          if (bucket[keys[index]] && bucket[keys[index]].name === name) return bucket[keys[index]];
        }
      }
      for (let index = 0; index < skins.length; index += 1) {
        const skin = skins[index];
        const slotBucket = skin && skin.attachments ? skin.attachments[slot.name] : null;
        if (slotBucket && slotBucket[name]) return slotBucket[name];
      }
      return null;
    }

    function textureFor(attachment, region) {
      if (region && Number.isInteger(region.page) && pages[region.page]) {
        const image = lookupTexture(pageKeys(pages[region.page]));
        if (image) return image;
      }
      const keys = [];
      if (attachment && typeof attachment.path === "string") keys.push(attachment.path);
      if (attachment && typeof attachment.name === "string") keys.push(attachment.name);
      if (region && typeof region.name === "string") keys.push(region.name);
      return lookupTexture(keys);
    }

    function drawAttachmentLabel(ctx, attachment, point, screenWidth) {      const label = attachment.name || attachment.path || "";
      ctx.save();
      ctx.font = "600 9px " + (getComputedStyle(root).getPropertyValue("--md-font-mono") || "Consolas, monospace");
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const labelWidth = Math.min(ctx.measureText(label).width + 8, Math.max(screenWidth - 2, 12));
      ctx.fillStyle = "rgba(10,14,16,0.72)";
      ctx.fillRect(clamp(point.x - labelWidth / 2, 1, view.width - labelWidth - 1), point.y - 7, labelWidth, 14);
      ctx.fillStyle = "rgba(223,227,230,0.92)";
      ctx.fillText(label, clamp(point.x, labelWidth / 2 + 1, view.width - labelWidth / 2 - 1), point.y);
      ctx.restore();
    }

    function drawMeshSlot(ctx, attachment, region, image, color, world, fallbackBone) {
      const count = meshVertexCount(attachment);
      if (!count) return false;
      const points = new Array(count);
      for (let index = 0; index < count; index += 1) {
        const point = meshVertexPoint(attachment, index, world, boneByIndex, fallbackBone);
        points[index] = point ? project(point) : null;
      }
      const triangles = Array.isArray(attachment.triangles) ? attachment.triangles : [];
      const hasUv = Array.isArray(attachment.uvs) && attachment.uvs.length >= count * 2;
      const normalized = image ? uprightRegion(image, region) : null;
      let drewImage = false;
      ctx.save();
      ctx.globalAlpha = clamp(color[3], 0, 1);
      if (image && hasUv && triangles.length >= 3) {
        const end = Math.min(triangles.length, 24000);
        for (let cursor = 0; cursor + 2 < end; cursor += 3) {
          const first = triangles[cursor];
          const second = triangles[cursor + 1];
          const third = triangles[cursor + 2];
          if (first < 0 || first >= count || second < 0 || second >= count || third < 0 || third >= count) continue;
          const point0 = points[first];
          const point1 = points[second];
          const point2 = points[third];
          if (!point0 || !point1 || !point2) continue;
          const texture0 = meshUvPoint(attachment, first, region, image, normalized);
          const texture1 = meshUvPoint(attachment, second, region, image, normalized);
          const texture2 = meshUvPoint(attachment, third, region, image, normalized);
          if (!texture0 || !texture1 || !texture2) continue;
          const matrix = triangleMatrix(texture0, texture1, texture2, point0, point1, point2);
          if (!matrix) continue;
          const clip = inflateTriangle(point0, point1, point2, 1.0);
          ctx.save();
          ctx.beginPath();
          ctx.moveTo(clip[0].x, clip[0].y);
          ctx.lineTo(clip[1].x, clip[1].y);
          ctx.lineTo(clip[2].x, clip[2].y);
          ctx.closePath();
          ctx.clip();
          ctx.setTransform(
            matrix.a * view.ratio, matrix.b * view.ratio, matrix.c * view.ratio,
            matrix.d * view.ratio, matrix.e * view.ratio, matrix.f * view.ratio,
          );
          try {
            ctx.drawImage(image, 0, 0);
            drewImage = true;
          } catch (error) {
            drewImage = false;
          }
          ctx.restore();
        }
      }
      if (!drewImage) {
        ctx.beginPath();
        if (triangles.length >= 3) {
          for (let cursor = 0; cursor + 2 < triangles.length; cursor += 3) {
            const point0 = points[triangles[cursor]];
            const point1 = points[triangles[cursor + 1]];
            const point2 = points[triangles[cursor + 2]];
            if (!point0 || !point1 || !point2) continue;
            ctx.moveTo(point0.x, point0.y);
            ctx.lineTo(point1.x, point1.y);
            ctx.lineTo(point2.x, point2.y);
            ctx.closePath();
          }
        } else {
          for (let index = 0; index < points.length; index += 1) {
            if (!points[index]) continue;
            if (index === 0) ctx.moveTo(points[index].x, points[index].y);
            else ctx.lineTo(points[index].x, points[index].y);
          }
          ctx.closePath();
        }
        ctx.fillStyle = "rgba(138,180,248,0.14)";
        ctx.fill();
        ctx.strokeStyle = "rgba(150,205,235,0.55)";
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      ctx.restore();
      return drewImage;
    }

    function drawSlot(ctx, slot, slotTracks, world) {
      const boneWorld = slot && slot.bone ? world.get(slot.bone) : null;
      if (!boneWorld) return;
      const attachment = attachmentOf(slot, slotTracks);
      if (!attachment) return;
      const box = attachmentBox(attachment);
      if (!box) return;
      const region = regionMap.get(attachment.path || attachment.name) || regionMap.get(attachment.name) || null;
      if (attachment.type === "region") {
        if (!(box.width > 0) && region) box.width = finite(region.origWidth, finite(region.width, 0));
        if (!(box.height > 0) && region) box.height = finite(region.origHeight, finite(region.height, 0));
      }
      const width = Math.max(box.width, 1);
      const height = Math.max(box.height, 1);
      const color = slotColor(slot, slotTracks);
      if (color[3] <= 0.01) return;
      const image = textureFor(attachment, region);
      const origin = project({ x: boneWorld.x, y: boneWorld.y });
      const matrix = boneWorld.matrix;
      const screenWidth = Math.abs(view.scale * Math.hypot(matrix.a, matrix.c) * box.scaleX * width);
      const screenHeight = Math.abs(view.scale * Math.hypot(matrix.b, matrix.d) * box.scaleY * height);
      if (attachment.type === "mesh" && !attachment.data_truncated) {
        const drewImage = drawMeshSlot(ctx, attachment, region, image, color, world, slot.bone);
        if (!drewImage && screenWidth > 26 && screenHeight > 14) {
          drawAttachmentLabel(ctx, attachment, project(localPoint(boneWorld, box.x, box.y)), screenWidth);
        }
        return;
      }
      ctx.save();
      ctx.globalAlpha = clamp(color[3], 0, 1);
      ctx.translate(origin.x, origin.y);
      ctx.transform(
        matrix.a * view.scale, -matrix.c * view.scale, matrix.b * view.scale, -matrix.d * view.scale,
        0, 0,
      );
      ctx.translate(box.x, box.y);
      ctx.rotate(-box.rotation * RAD);
      ctx.scale(box.scaleX, box.scaleY);
      let drewImage = false;
      if (image) {
        let sourceImage = image;
        let sourceX = 0;
        let sourceY = 0;
        let sourceWidth = image.naturalWidth || image.width || width;
        let sourceHeight = image.naturalHeight || image.height || height;
        let drawX = -width / 2;
        let drawY = -height / 2;
        let drawWidth = width;
        let drawHeight = height;
        if (region) {
          sourceX = finite(region.x, 0);
          sourceY = finite(region.y, 0);
          sourceWidth = Math.max(finite(region.rotate ? region.height : region.width, sourceWidth), 1);
          sourceHeight = Math.max(finite(region.rotate ? region.width : region.height, sourceHeight), 1);
          if (region.rotate) {
            const normalized = uprightRegion(image, region);
            if (normalized) {
              sourceImage = normalized;
              sourceX = 0;
              sourceY = 0;
              sourceWidth = normalized.width;
              sourceHeight = normalized.height;
            }
          }
          const origWidth = finite(region.origWidth, 0);
          const origHeight = finite(region.origHeight, 0);
          if (origWidth > 0 && origHeight > 0 && width >= origWidth - 0.5 && height >= origHeight - 0.5) {
            const fitX = width / origWidth;
            const fitY = height / origHeight;
            drawX = -width / 2 + finite(region.offsetX, 0) * fitX;
            drawY = -height / 2 + finite(region.offsetY, 0) * fitY;
            drawWidth = Math.max(finite(region.width, width) * fitX, 1);
            drawHeight = Math.max(finite(region.height, height) * fitY, 1);
          }
        }
        ctx.save();
        ctx.scale(1, -1);
        try {
          ctx.drawImage(sourceImage, sourceX, sourceY, sourceWidth, sourceHeight, drawX, drawY, drawWidth, drawHeight);
          drewImage = true;
        } catch (error) {
          drewImage = false;
        }
        ctx.restore();
      }
      if (drewImage) {
        ctx.globalAlpha = 1;
        ctx.lineWidth = Math.max(0.5 / Math.max(view.scale, 0.01), 0.5);
        ctx.strokeStyle = "rgba(0,0,0,0.28)";
        ctx.strokeRect(-width / 2, -height / 2, width, height);
      } else {
        const base = hexToRgba(slot && slot.color, hexToRgba(FALLBACK_COLORS[slot && Number.isInteger(slot.index) ? slot.index % FALLBACK_COLORS.length : 0], [123, 219, 230, 1]));
        const rgb = mixColor(base, color, 0.35);
        ctx.fillStyle = rgbaText(rgb);
        ctx.fillRect(-width / 2, -height / 2, width, height);
        ctx.lineWidth = Math.max(1.2 / Math.max(view.scale, 0.01), 0.8);
        ctx.strokeStyle = "rgba(223,227,230,0.55)";
        ctx.strokeRect(-width / 2, -height / 2, width, height);
      }
      ctx.restore();
      if (!drewImage && screenWidth > 26 && screenHeight > 14) {
        drawAttachmentLabel(ctx, attachment, project(localPoint(boneWorld, box.x, box.y)), screenWidth);
      }
    }

    function drawBones(ctx, bonesList, world) {
      if (!state.showBones || !bonesList.length) return;
      const primary = getComputedStyle(root).getPropertyValue("--md-primary") || "#7bdbe6";
      const accent = getComputedStyle(root).getPropertyValue("--md-tertiary") || "#eec06c";
      state.bonePoints = new Map();
      ctx.save();
      ctx.setTransform(view.ratio, 0, 0, view.ratio, 0, 0);
      bonesList.forEach(function (bone) {
        const pose = world.get(bone.name);
        if (!pose) return;
        const point = project({ x: pose.x, y: pose.y });
        state.bonePoints.set(bone.name, point);
        const selected = bone.name === state.selectedBone;
        const hovered = bone.name === state.hoverBone;
        const parent = bone.parent ? world.get(bone.parent) : null;
        if (parent) {
          const from = project({ x: parent.x, y: parent.y });
          ctx.beginPath();
          ctx.moveTo(from.x, from.y);
          ctx.lineTo(point.x, point.y);
          ctx.strokeStyle = selected ? accent : hovered ? "#ffffff" : "rgba(123,219,230,0.55)";
          ctx.lineWidth = selected ? 2.6 : hovered ? 2 : 1.1;
          ctx.stroke();
        }
        if (bone.length > 0) {
          const tip = localPoint(pose, bone.length, 0);
          const tipPoint = project(tip);
          ctx.beginPath();
          ctx.moveTo(point.x, point.y);
          ctx.lineTo(tipPoint.x, tipPoint.y);
          ctx.strokeStyle = selected ? "rgba(238,192,108,0.8)" : "rgba(191,200,203,0.28)";
          ctx.lineWidth = selected ? 1.6 : 0.8;
          ctx.stroke();
        }
        ctx.beginPath();
        ctx.arc(point.x, point.y, selected ? 5 : hovered ? 4 : 2.6, 0, Math.PI * 2);
        ctx.fillStyle = selected ? accent : hovered ? "#ffffff" : primary;
        ctx.globalAlpha = selected || hovered ? 1 : 0.78;
        ctx.fill();
        ctx.globalAlpha = 1;
        if (selected) {
          ctx.beginPath();
          ctx.arc(point.x, point.y, 10, 0, Math.PI * 2);
          ctx.strokeStyle = "rgba(238,192,108,0.55)";
          ctx.lineWidth = 1;
          ctx.stroke();
          const label = bone.name + "  " + pose.rotation.toFixed(1) + "°";
          ctx.font = "600 10px " + (getComputedStyle(root).getPropertyValue("--md-font-mono") || "Consolas, monospace");
          ctx.textAlign = "left";
          ctx.textBaseline = "middle";
          const textWidth = ctx.measureText(label).width;
          const labelX = clamp(point.x + 12, 4, view.width - textWidth - 10);
          const labelY = clamp(point.y - 12, 12, view.height - 10);
          ctx.fillStyle = "rgba(10,14,16,0.86)";
          ctx.fillRect(labelX - 4, labelY - 8, textWidth + 8, 16);
          ctx.fillStyle = accent;
          ctx.fillText(label, labelX, labelY);
        }
      });
      ctx.restore();
    }

    function draw() {
      if (state.destroy) return;
      const ctx = context;
      const world = computePose(bones, state.tracks.bones, state.time, !state.animation);
      const bounds = solveBounds(skeleton, bones, world, slots, attachments, boneByIndex);
      fitBounds(bounds);
      drawBackground(ctx);
      ctx.save();
      ctx.setTransform(view.ratio, 0, 0, view.ratio, 0, 0);
      const order = buildSlotOrder(slots, state.tracks.drawOrder, state.time);
      order.forEach(function (slot) {
        drawSlot(ctx, slot, state.tracks.slots.get(slot.name), world);
      });
      ctx.restore();
      drawBones(ctx, bones, world);
      updateTimeline();
    }

    function nearestBone(x, y) {
      let best = "";
      let bestDistance = 11 * 11;
      state.bonePoints.forEach(function (point, name) {
        const dx = point.x - x;
        const dy = point.y - y;
        const distance = dx * dx + dy * dy;
        if (distance < bestDistance) {
          bestDistance = distance;
          best = name;
        }
      });
      return best;
    }

    const controller = new AbortController();
    const options = { signal: controller.signal };
    canvas.addEventListener("pointermove", function (event) {
      const rect = canvas.getBoundingClientRect();
      const name = nearestBone(event.clientX - rect.left, event.clientY - rect.top);
      if (name !== state.hoverBone) {
        state.hoverBone = name;
        canvas.style.cursor = name ? "pointer" : "crosshair";
        draw();
      }
    }, options);
    canvas.addEventListener("pointerleave", function () {
      if (state.hoverBone) {
        state.hoverBone = "";
        draw();
      }
    }, options);
    canvas.addEventListener("pointerdown", function (event) {
      const rect = canvas.getBoundingClientRect();
      const name = nearestBone(event.clientX - rect.left, event.clientY - rect.top);
      state.selectedBone = name || "";
      syncBoneButtons();
      draw();
    }, options);
    playButton.addEventListener("click", function () {
      if (state.playing) pause();
      else play();
    }, options);
    select.addEventListener("change", function () {
      pause();
      setAnimation(select.value);
    }, options);
    range.addEventListener("input", function () {
      const ratio = finite(range.value, 0) / 1000;
      setTime(ratio * state.duration);
    }, options);
    loopInput.addEventListener("change", function () {
      state.loop = Boolean(loopInput.checked);
    }, options);

    let observer = null;
    if (typeof ResizeObserver === "function") {
      observer = new ResizeObserver(function () { resize(); });
      observer.observe(stage);
    } else {
      window.addEventListener("resize", resize, options);
    }

    const api = {
      play: function () {
        play();
      },
      pause: function () {
        pause();
      },
      setAnimation: function (name) {
        select.value = name;
        setAnimation(name);
      },
      setTime: function (seconds) {
        setTime(seconds);
      },
      redraw: function () {
        draw();
      },
      destroy: function () {
        state.destroy = true;
        state.playing = false;
        if (state.raf) cancelAnimationFrame(state.raf);
        if (state.drawPending) cancelAnimationFrame(state.drawPending);
        if (observer) observer.disconnect();
        controller.abort();
        host.innerHTML = "";
        if (host.__spinePreviewApi === api) delete host.__spinePreviewApi;
      },
      getState: function () {
        return {
          animation: state.animationName,
          time: state.time,
          duration: state.duration,
          playing: state.playing,
          selectedBone: state.selectedBone,
        };
      },
    };
    host.__spinePreviewApi = api;
    updateTimeline();
    resize();
    return api;
  }

  injectStyle();
  window.renderSpinePreview = render;
})();
