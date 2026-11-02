(function () {
  "use strict";

  var STYLE_ID = "anim-preview-style";
  var MAX_ROWS = 24;
  var MAX_READOUT = 12;
  var MAX_EVENT_MARKS = 160;
  var AXIS_LABEL = { translation: "平移", rotation: "旋转", scale: "缩放", curve: "曲线", event: "事件" };
  var SOURCE_LABEL = { cached_pose: "缓存姿态", rest: "静止轨道", motion: "运动轨道", curve: "曲线图", json: "JSON", rawanimation: "RAW" };
  var KIND_LABEL = { skeleton: "骨骼", mesh: "网格", texture: "贴图", animation: "动画", config: "配置", other: "文件" };

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
    });
  }

  function num(value, fallback) {
    var number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clamp(value, low, high) {
    return Math.max(low, Math.min(high, value));
  }

  function fmt(value, digits) {
    var number = Number(value);
    if (!Number.isFinite(number)) return "--";
    var abs = Math.abs(number);
    if (abs !== 0 && (abs < 0.001 || abs >= 10000)) return number.toExponential(2);
    return number.toFixed(digits == null ? 3 : digits);
  }

  function fmtTime(value) {
    return (Number(value) || 0).toFixed(2);
  }

  function fmtDuration(value) {
    var number = Number(value) || 0;
    if (number >= 60) {
      var minutes = Math.floor(number / 60);
      return minutes + "m " + (number - minutes * 60).toFixed(1) + "s";
    }
    return number.toFixed(2) + "s";
  }

  function icon(name) {
    if (name === "pause") {
      return '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>';
    }
    if (name === "reset") {
      return '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M12 5a7 7 0 1 1-6.7 9h2.1a5 5 0 1 0 4.6-7V4l-4 3.5L12 11V5z"/></svg>';
    }
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>';
  }

  function styleText() {
    return [
      ".anim-preview{display:flex;flex-direction:column;gap:12px;color:var(--md-on-surface);font:12px/1.5 var(--md-font-ui);min-width:0}",
      ".anim-preview:focus-visible{outline:1px solid var(--md-primary);outline-offset:2px}",
      ".anim-card{background:var(--md-surface-container-low);border:1px solid var(--md-outline-variant);border-radius:var(--md-shape-lg);padding:12px;min-width:0}",
      ".anim-head{display:flex;flex-wrap:wrap;justify-content:space-between;gap:10px;align-items:flex-start}",
      ".anim-head-main{display:flex;flex-direction:column;gap:3px;min-width:0}",
      ".anim-name{font-size:16px;font-weight:600;word-break:break-all}",
      ".anim-sub{color:var(--md-on-surface-variant);font:10px/1.4 var(--md-font-mono);word-break:break-all}",
      ".anim-timeline{display:flex;align-items:center;gap:7px;min-height:44px;padding:0 10px;border:0;border-radius:var(--md-shape-lg);background:var(--md-surface-container-high);font:11px/1.2 var(--md-font-mono)}",
      ".anim-btn{flex:none;min-width:30px;height:30px;padding:0 9px;border:0;border-radius:var(--md-shape-full);background:var(--md-surface-container-highest);color:var(--md-on-surface);cursor:pointer;font:inherit;font-size:10px;display:inline-flex;align-items:center;justify-content:center;gap:4px;transition:background .15s ease,color .15s ease}",
      ".anim-btn svg{width:14px;height:14px}",
      ".anim-btn:hover{background:var(--md-primary-container);color:var(--md-on-primary-container)}",
      ".anim-btn:disabled{opacity:.4;cursor:default}",
      ".anim-track{position:relative;flex:1;min-width:60px;height:18px;cursor:pointer;border:0;border-radius:var(--md-shape-full);overflow:hidden;background:repeating-linear-gradient(90deg,var(--md-track-tick) 0 1px,transparent 1px 8px),var(--md-surface-container-lowest)}",
      ".anim-fill{position:absolute;left:0;top:0;bottom:0;width:0;border-radius:var(--md-shape-full);background:var(--md-primary)}",
      ".anim-cursor{position:absolute;top:50%;left:0;width:4px;height:24px;margin-left:-2px;transform:translateY(-50%);border-radius:var(--md-shape-full);background:var(--md-tertiary);box-shadow:0 0 0 2px var(--md-ring-shadow);pointer-events:none}",
      ".anim-event{position:absolute;top:50%;width:3px;height:10px;margin-left:-1px;transform:translateY(-50%);border-radius:2px;background:var(--md-secondary);opacity:.85;pointer-events:none}",
      ".anim-clock{flex:none;min-width:96px;text-align:right;color:var(--md-on-surface-variant);font-size:10px}",
      ".anim-loop{flex:none;display:inline-flex;align-items:center;gap:4px;color:var(--md-on-surface-variant);font-size:10px;cursor:pointer;user-select:none}",
      ".anim-loop input{accent-color:var(--md-primary);margin:0}",
      ".anim-body{display:grid;grid-template-columns:minmax(220px,290px) minmax(0,1fr);gap:12px;align-items:start}",
      ".anim-side{display:flex;flex-direction:column;gap:8px;min-width:0}",
      ".anim-actions{display:flex;flex-wrap:wrap;gap:6px}",
      ".anim-filter{width:100%;box-sizing:border-box;height:30px;border:1px solid var(--md-outline-variant);border-radius:var(--md-shape-sm);background:var(--md-surface-container-lowest);color:var(--md-on-surface);padding:0 8px;font:11px/1 var(--md-font-mono);outline:none}",
      ".anim-filter:focus{border-color:var(--md-primary)}",
      ".anim-channels{max-height:430px;overflow:auto;border:1px solid var(--md-outline-variant);border-radius:var(--md-shape-md);background:var(--md-surface-container-lowest);padding:4px}",
      ".anim-group{border-bottom:1px solid var(--md-row-line)}",
      ".anim-group:last-child{border-bottom:0}",
      ".anim-group summary{display:flex;align-items:center;gap:6px;padding:5px 4px;cursor:pointer;list-style:none;font:11px/1.3 var(--md-font-mono);min-width:0}",
      ".anim-group summary::-webkit-details-marker{display:none}",
      ".anim-group summary::before{content:'›';flex:none;color:var(--md-on-surface-variant);width:10px;transition:transform .15s ease}",
      ".anim-group[open] summary::before{transform:rotate(90deg)}",
      ".anim-group input{accent-color:var(--md-primary);margin:0;flex:none}",
      ".anim-node{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--md-on-surface)}",
      ".anim-count{margin-left:auto;color:var(--md-on-surface-variant);font-size:9px;flex:none}",
      ".anim-track-row{display:flex;align-items:center;gap:6px;padding:3px 4px 3px 20px;border-radius:var(--md-shape-xs);cursor:pointer;font:10px/1.4 var(--md-font-mono);min-width:0}",
      ".anim-track-row:hover{background:var(--md-state-hover)}",
      ".anim-track-row input{accent-color:var(--md-primary);margin:0;flex:none}",
      ".anim-swatch{width:8px;height:8px;border-radius:50%;flex:none}",
      ".anim-track-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".anim-track-meta{margin-left:auto;color:var(--md-on-surface-variant);font-size:9px;flex:none;white-space:nowrap}",
      ".anim-main{display:flex;flex-direction:column;gap:8px;min-width:0}",
      ".anim-curves-wrap{overflow:auto;border:1px solid var(--md-outline-variant);border-radius:var(--md-shape-md);background:var(--md-canvas-bg);max-height:560px}",
      ".anim-curves{display:block;width:100%}",
      ".anim-hint{color:var(--md-on-surface-variant);font:10px/1.4 var(--md-font-mono)}",
      ".anim-readout{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:4px 8px;font:10px/1.45 var(--md-font-mono)}",
      ".anim-readout-row{display:grid;grid-template-columns:minmax(0,1fr);gap:1px;min-width:0;padding:3px 6px;border-radius:var(--md-shape-xs);background:var(--md-surface-container-lowest)}",
      ".anim-readout-name{color:var(--md-on-surface-variant);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}",
      ".anim-readout-value{color:var(--md-primary);white-space:normal;word-break:break-all}",
      ".anim-refs .bank-chip{max-width:100%}",
      ".anim-ref-path{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:420px}",
      ".anim-bones-wrap{border:1px solid var(--md-outline-variant);border-radius:var(--md-shape-md);background:var(--md-canvas-bg);overflow:hidden}",
      ".anim-bones{display:block;width:100%;height:220px}",
      ".anim-error{color:var(--md-error);font:11px/1.6 var(--md-font-mono);word-break:break-word}",
      ".anim-empty{color:var(--md-on-surface-variant);font:11px/1.6 var(--md-font-mono);padding:14px;text-align:center}",
      "@media (max-width:760px){.anim-body{grid-template-columns:minmax(0,1fr)}.anim-channels{max-height:260px}.anim-clock{min-width:76px;font-size:9px}}"
    ].join("\n");
  }

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var node = document.createElement("style");
    node.id = STYLE_ID;
    node.textContent = styleText();
    document.head.appendChild(node);
  }

  function themeColors() {
    var style = getComputedStyle(document.documentElement);
    function read(name, fallback) {
      var value = style.getPropertyValue(name).trim();
      return value || fallback;
    }
    return [
      read("--md-primary", "#7bdbe6"),
      read("--md-tertiary", "#eec06c"),
      read("--md-error", "#ffb4ab"),
      read("--md-secondary", "#b2cbd0")
    ];
  }

  function trackLabel(track) {
    var name = AXIS_LABEL[track.channel] || track.channel || "通道";
    var source = SOURCE_LABEL[track.source];
    return source ? name + " · " + source : name;
  }

  function trackKeyCount(track) {
    return Array.isArray(track.points) ? track.points.length : Number(track.keys) || 0;
  }

  function sampleTrack(track, time) {
    var points = track.points || [];
    if (!points.length) return null;
    if (time <= points[0][0]) return points[0].slice(1);
    var last = points[points.length - 1];
    if (time >= last[0]) return last.slice(1);
    var low = 0;
    var high = points.length - 1;
    while (low + 1 < high) {
      var middle = (low + high) >> 1;
      if (points[middle][0] <= time) low = middle;
      else high = middle;
    }
    var left = points[low];
    var right = points[high];
    var span = right[0] - left[0];
    var ratio = span > 1e-9 ? (time - left[0]) / span : 0;
    var values = [];
    for (var index = 1; index < left.length; index += 1) {
      var a = Number(left[index]) || 0;
      var b = Number(right[index]) || 0;
      values.push(a + (b - a) * ratio);
    }
    return values;
  }

  function normalizeTrack(track) {
    if (!track || typeof track !== "object") return null;
    var points = Array.isArray(track.points) ? track.points.filter(function (point) {
      return Array.isArray(point) && point.length > 1 && Number.isFinite(Number(point[0]));
    }).map(function (point) {
      return point.map(function (value, index) {
        return index === 0 ? Number(value) : num(value, 0);
      });
    }) : [];
    points.sort(function (a, b) { return a[0] - b[0]; });
    var axes = Array.isArray(track.axes) && track.axes.length ? track.axes.slice(0, Math.max(1, points[0] ? points[0].length - 1 : 1)) : ["value"];
    return {
      node: String(track.node || track.name || track.bone || "Track"),
      channel: String(track.channel || "curve"),
      source: String(track.source || "json"),
      axes: axes,
      keys: Number(track.keys) || points.length,
      start: Number(track.start) || (points[0] ? points[0][0] : 0),
      end: Number(track.end) || (points[points.length - 1] ? points[points.length - 1][0] : 0),
      points: points
    };
  }

  function normalizeData(data) {
    var source = data && typeof data === "object" ? data : {};
    var tracks = Array.isArray(source.tracks) ? source.tracks.map(normalizeTrack).filter(Boolean) : [];
    if (!tracks.length && source.sample_keys && typeof source.sample_keys === "object") {
      Object.keys(source.sample_keys).forEach(function (channel) {
        var groups = {};
        (source.sample_keys[channel] || []).forEach(function (sample) {
          if (!sample || !Array.isArray(sample.values)) return;
          var node = String(sample.node || "Track");
          groups[node] = groups[node] || [];
          groups[node].push([num(sample.time, 0)].concat(sample.values.map(function (value) { return num(value, 0); })));
        });
        Object.keys(groups).forEach(function (node) {
          tracks.push(normalizeTrack({ node: node, channel: channel, source: "sample", points: groups[node] }));
        });
      });
    }
    var events = Array.isArray(source.events) ? source.events.filter(function (event) {
      return event && Array.isArray(event.points);
    }) : [];
    var refs = Array.isArray(source.refs) ? source.refs : [];
    var bones = Array.isArray(source.bones) ? source.bones : (source.skeleton && Array.isArray(source.skeleton.bones) ? source.skeleton.bones : null);
    var boneNames = Array.isArray(source.bone_names) ? source.bone_names : [];
    var duration = num(source.duration, 0);
    tracks.forEach(function (track) { duration = Math.max(duration, track.end); });
    events.forEach(function (event) {
      (event.points || []).forEach(function (point) { duration = Math.max(duration, num(point[0], 0)); });
    });
    return {
      raw: source,
      name: String(source.name || ""),
      format: String(source.format || ""),
      duration: duration > 0 ? duration : 1,
      fps: num(source.fps, 30),
      loop: source.loop !== false,
      tracks: tracks,
      events: events,
      refs: refs,
      bones: bones,
      boneNames: boneNames,
      error: source.error ? String(source.error) : "",
      warnings: Array.isArray(source.warnings) ? source.warnings : []
    };
  }

  function defaultSelection(tracks) {
    if (!tracks.length) return new Set();
    var preferred = [];
    tracks.forEach(function (track, index) {
      if (["rest", "motion", "curve"].indexOf(track.source) >= 0 && trackKeyCount(track) >= 3) preferred.push(index);
    });
    if (preferred.length) return new Set(preferred.slice(0, 6));
    var node = tracks[0].node;
    var group = [];
    tracks.forEach(function (track, index) {
      if (track.node === node) group.push(index);
    });
    return new Set(group.slice(0, 6));
  }

  function groupTracks(tracks) {
    var groups = [];
    var index = {};
    tracks.forEach(function (track, trackIndex) {
      var key = track.node;
      if (index[key] == null) {
        index[key] = groups.length;
        groups.push({ node: key, tracks: [] });
      }
      groups[index[key]].tracks.push(trackIndex);
    });
    return groups;
  }

  function refPath(ref) {
    if (typeof ref === "string") return ref;
    if (ref && typeof ref === "object") return String(ref.path || ref.name || "");
    return "";
  }

  function refKind(ref) {
    if (ref && typeof ref === "object" && ref.kind) return KIND_LABEL[ref.kind] || String(ref.kind);
    var path = refPath(ref);
    var ext = path.indexOf(".") >= 0 ? path.split(".").pop().toLowerCase() : "";
    return KIND_LABEL[ext] || (ext ? ext.toUpperCase() : "文件");
  }

  function refsMarkup(refs) {
    if (!refs.length) return "";
    var shown = refs.slice(0, 24);
    var html = shown.map(function (ref) {
      var path = refPath(ref);
      return '<span class="bank-chip" title="' + esc(path) + '"><b>' + esc(refKind(ref)) + '</b><span class="anim-ref-path">' + esc(path) + "</span></span>";
    }).join("");
    if (refs.length > shown.length) html += '<span class="bank-chip">+<b>' + (refs.length - shown.length) + "</b></span>";
    return '<div class="bank-chips anim-refs">' + html + "</div>";
  }

  function headChips(data) {
    var chips = [];
    if (data.format) chips.push('<span class="bank-chip">格式 <b>' + esc(data.format.toUpperCase()) + "</b></span>");
    chips.push('<span class="bank-chip">时长 <b>' + esc(fmtDuration(data.duration)) + "</b></span>");
    if (data.fps) chips.push('<span class="bank-chip">FPS <b>' + esc(fmt(data.fps, 1)) + "</b></span>");
    if (data.tracks.length) chips.push('<span class="bank-chip">轨道 <b>' + data.tracks.length + "</b></span>");
    var keys = data.tracks.reduce(function (sum, track) { return sum + trackKeyCount(track); }, 0);
    if (keys) chips.push('<span class="bank-chip">关键帧 <b>' + keys + "</b></span>");
    var raw = data.raw || {};
    if (raw.joint_count) chips.push('<span class="bank-chip">关节 <b>' + raw.joint_count + "</b></span>");
    if (raw.pose_count) chips.push('<span class="bank-chip">姿态 <b>' + raw.pose_count + "</b></span>");
    if (raw.track_count && !data.tracks.length) chips.push('<span class="bank-chip">骨骼 <b>' + raw.track_count + "</b></span>");
    if (raw.acl_version != null) chips.push('<span class="bank-chip">ACL <b>v' + esc(raw.acl_version) + "</b></span>");
    if (data.refs.length) chips.push('<span class="bank-chip">引用 <b>' + data.refs.length + "</b></span>");
    if (raw.pose_layout === "tsq") chips.push('<span class="bank-chip">缓存布局 <b>T+S+Q</b></span>');
    return '<div class="bank-chips">' + chips.join("") + "</div>";
  }

  function errorMarkup(data) {
    var extra = [];
    if (data.raw && data.raw.size) extra.push("size " + data.raw.size);
    if (data.raw && data.raw.magic) extra.push("magic " + data.raw.magic);
    return '<div class="anim-card"><div class="section-title">解析失败</div><div class="anim-error">' + esc(data.error) + "</div>" +
      (extra.length ? '<div class="anim-sub">' + esc(extra.join(" · ")) + "</div>" : "") +
      (data.refs.length ? '<div class="section-title">引用资源</div>' + refsMarkup(data.refs) : "") + "</div>";
  }

  function channelMarkup(data, groups, selected) {
    var rows = groups.map(function (group, groupIndex) {
      var items = group.tracks.map(function (trackIndex) {
        var track = data.tracks[trackIndex];
        var color = track.channel === "rotation" ? "var(--md-tertiary)" : track.channel === "scale" ? "var(--md-error)" : track.channel === "curve" ? "var(--md-secondary)" : "var(--md-primary)";
        return '<label class="anim-track-row" data-search="' + esc((track.node + " " + track.channel + " " + track.source).toLowerCase()) + '">' +
          '<input type="checkbox" data-track="' + trackIndex + '"' + (selected.has(trackIndex) ? " checked" : "") + ">" +
          '<i class="anim-swatch" style="background:' + color + '"></i>' +
          '<span class="anim-track-label" title="' + esc(track.node + " · " + trackLabel(track)) + '">' + esc(trackLabel(track)) + "</span>" +
          '<span class="anim-track-meta">' + trackKeyCount(track) + "f</span></label>";
      }).join("");
      var all = group.tracks.every(function (index) { return selected.has(index); });
      var some = group.tracks.some(function (index) { return selected.has(index); });
      return '<details class="anim-group" data-group="' + groupIndex + '"' + (groupIndex < 12 ? " open" : "") + ">" +
        '<summary><input type="checkbox" data-group-check="' + groupIndex + '"' + (all ? " checked" : "") + ' data-some="' + (some && !all ? "1" : "0") + '">' +
        '<span class="anim-node" title="' + esc(group.node) + '">' + esc(group.node) + "</span>" +
        '<span class="anim-count">' + group.tracks.length + " ch</span></summary>" +
        '<div class="anim-tracks">' + items + "</div></details>";
    }).join("");
    return '<div class="anim-side anim-card">' +
      '<div class="section-title">通道</div>' +
      '<input class="anim-filter" type="search" placeholder="搜索节点 / 通道" aria-label="搜索节点">' +
      '<div class="anim-actions"><button class="anim-btn" type="button" data-act="all">全选</button><button class="anim-btn" type="button" data-act="none">清空</button></div>' +
      '<div class="anim-channels">' + (rows || '<div class="anim-empty">没有可绘制的通道</div>') + "</div></div>";
  }

  function markup(data, groups, selected) {
    var eventTimes = [];
    data.events.forEach(function (event) {
      (event.points || []).forEach(function (point) {
        if (eventTimes.length < MAX_EVENT_MARKS) eventTimes.push({ time: num(point[0], 0), node: event.node, value: point[1] });
      });
    });
    var marks = eventTimes.map(function (item) {
      var percent = data.duration > 0 ? clamp(item.time / data.duration, 0, 1) * 100 : 0;
      return '<i class="anim-event" style="left:' + percent.toFixed(3) + '%" title="' + esc(item.node + " · " + item.value) + '"></i>';
    }).join("");
    var signals = Array.isArray(data.raw.signals) ? data.raw.signals : [];
    var signalChips = signals.length ? '<div class="bank-chips">' + signals.slice(0, 16).map(function (name) {
      return '<span class="bank-chip">信号 <b>' + esc(name) + "</b></span>";
    }).join("") + "</div>" : "";
    var warnings = data.warnings.length ? '<div class="section-title">提示</div><div class="anim-hint">' + data.warnings.map(esc).join(" · ") + "</div>" : "";
    var boneCount = data.bones ? data.bones.length : 0;
    var boneNames = Array.isArray(data.boneNames) ? data.boneNames : [];
    var boneNamesMarkup = boneNames.length ? '<div class="anim-card"><div class="section-title">骨骼名</div><div class="bank-chips">' +
      boneNames.slice(0, 80).map(function (name) {
        return '<span class="bank-chip">' + esc(name) + "</span>";
      }).join("") + (boneNames.length > 80 ? '<span class="bank-chip">+<b>' + (boneNames.length - 80) + "</b></span>" : "") + "</div></div>" : "";
    return '<div class="anim-preview">' +
      '<div class="anim-card">' +
        '<div class="anim-head"><div class="anim-head-main"><div class="anim-name">' + esc(data.name || "NeoX animation") + "</div>" +
          '<div class="anim-sub">' + esc([data.format, data.raw && data.raw.compression, data.raw && data.raw.compression_version].filter(Boolean).join(" · ")) + "</div></div>" +
          headChips(data) + "</div>" +
        (data.refs.length ? '<div class="section-title">引用资源</div>' + refsMarkup(data.refs) : "") +
      "</div>" +
      boneNamesMarkup +
      '<div class="anim-card"><div class="section-title">时间轴</div>' +
        '<div class="anim-timeline">' +
          '<button class="anim-btn" type="button" data-act="play" title="播放/暂停" aria-label="播放/暂停">' + icon("pause") + "</button>" +
          '<button class="anim-btn" type="button" data-act="reset" title="回到起点" aria-label="回到起点">' + icon("reset") + "</button>" +
          '<div class="anim-track" tabindex="0"><div class="anim-fill"></div><div class="anim-cursor"></div>' + marks + "</div>" +
          '<span class="anim-clock">0.00 / ' + fmtTime(data.duration) + "</span>" +
          '<label class="anim-loop"><input type="checkbox"' + (data.loop ? " checked" : "") + ">循环</label>" +
        "</div>" + signalChips + "</div>" +
      '<div class="anim-body">' + channelMarkup(data, groups, selected) +
        '<div class="anim-main"><div class="anim-card">' +
          '<div class="section-title">关键帧曲线</div>' +
          '<div class="anim-curves-wrap"><canvas class="anim-curves"></canvas></div>' +
          '<div class="anim-hint" data-hint></div>' +
          '<div class="section-title">当前数值</div>' +
          '<div class="anim-readout"></div>' +
        "</div>" +
        (boneCount ? '<div class="anim-card"><div class="section-title">骨骼示意</div><div class="anim-bones-wrap"><canvas class="anim-bones"></canvas></div><div class="anim-hint">线段按骨骼父子关系连接，数值来自同名平移通道</div></div>' : "") +
        "</div></div>" +
      warnings +
      "</div>";
  }

  function bindState(container, data, state) {
    state.container = container;
    state.data = data;
    state.groups = groupTracks(data.tracks);
    state.selected = defaultSelection(data.tracks);
    state.playing = true;
    state.time = 0;
    state.loop = data.loop;
    state.colors = themeColors();
    state.text = getComputedStyle(document.documentElement).getPropertyValue("--md-on-surface").trim() || "#dfe3e6";
    state.muted = getComputedStyle(document.documentElement).getPropertyValue("--md-on-surface-variant").trim() || "#bfc8cb";
    state.readoutRows = [];
    state.layout = null;
    state.frame = 0;
    state.last = 0;

    state.canvas = container.querySelector(".anim-curves");
    state.ctx = state.canvas ? state.canvas.getContext("2d") : null;
    state.wrap = container.querySelector(".anim-curves-wrap");
    state.hint = container.querySelector("[data-hint]");
    state.readout = container.querySelector(".anim-readout");
    state.clock = container.querySelector(".anim-clock");
    state.fill = container.querySelector(".anim-fill");
    state.cursor = container.querySelector(".anim-cursor");
    state.trackBar = container.querySelector(".anim-track");
    state.playButton = container.querySelector('[data-act="play"]');
    state.boneCanvas = container.querySelector(".anim-bones");
    state.boneCtx = state.boneCanvas ? state.boneCanvas.getContext("2d") : null;
    state.query = "";
  }

  function syncGroupBoxes(state) {
    state.groups.forEach(function (group, groupIndex) {
      var box = state.container.querySelector('[data-group-check="' + groupIndex + '"]');
      if (!box) return;
      var count = group.tracks.filter(function (index) { return state.selected.has(index); }).length;
      box.checked = count === group.tracks.length;
      box.indeterminate = count > 0 && count < group.tracks.length;
    });
  }

  function syncTrackBoxes(state) {
    state.container.querySelectorAll("[data-track]").forEach(function (box) {
      box.checked = state.selected.has(Number(box.dataset.track));
    });
    syncGroupBoxes(state);
  }

  function buildReadout(state) {
    var selected = Array.from(state.selected).sort(function (a, b) { return a - b; });
    var shown = selected.slice(0, MAX_READOUT);
    state.readout.innerHTML = shown.map(function (index) {
      var track = state.data.tracks[index];
      return '<div class="anim-readout-row" data-readout="' + index + '"><span class="anim-readout-name" title="' + esc(track.node + " · " + trackLabel(track)) + '">' +
        esc(track.node + " · " + trackLabel(track)) + '</span><span class="anim-readout-value">--</span></div>';
    }).join("") + (selected.length > shown.length ? '<div class="anim-readout-row"><span class="anim-readout-name">还有 ' + (selected.length - shown.length) + " 条未显示</span></div>" : "");
    state.readoutRows = shown.map(function (index) {
      return { index: index, node: state.readout.querySelector('[data-readout="' + index + '"] .anim-readout-value') };
    });
    if (!shown.length) state.readout.innerHTML = '<div class="anim-hint">勾选左侧通道后显示数值</div>';
  }

  function valueText(values) {
    if (!values || !values.length) return "--";
    return values.map(function (value) { return fmt(value, 3); }).join(" / ");
  }

  function updateReadout(state) {
    state.readoutRows.forEach(function (row) {
      var track = state.data.tracks[row.index];
      var values = sampleTrack(track, state.time);
      if (row.node) row.node.textContent = valueText(values);
    });
  }

  function updateClock(state) {
    var percent = state.data.duration > 0 ? clamp(state.time / state.data.duration, 0, 1) : 0;
    state.fill.style.width = (percent * 100).toFixed(3) + "%";
    state.cursor.style.left = (percent * 100).toFixed(3) + "%";
    state.clock.textContent = fmtTime(state.time) + " / " + fmtTime(state.data.duration);
  }

  function updatePlayButton(state) {
    if (!state.playButton) return;
    state.playButton.innerHTML = icon(state.playing ? "pause" : "play");
  }

  function fitCanvas(canvas, height) {
    var dpr = window.devicePixelRatio || 1;
    var width = Math.max(240, canvas.parentElement ? canvas.parentElement.clientWidth : 600);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = "100%";
    canvas.style.height = height + "px";
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx: ctx, width: width, height: height };
  }

  function drawRows(state) {
    var canvas = state.canvas;
    if (!canvas || !state.ctx) return;
    var selected = Array.from(state.selected).sort(function (a, b) { return a - b; });
    var shown = selected.slice(0, MAX_ROWS);
    var rowHeight = 58;
    var height = Math.max(120, shown.length * rowHeight + 28);
    var fitted = fitCanvas(canvas, height);
    var ctx = fitted.ctx;
    var width = fitted.width;
    var labelWidth = Math.min(200, Math.max(96, width * 0.22));
    var padRight = 14;
    var top = 22;
    var plotWidth = Math.max(20, width - labelWidth - padRight);
    state.layout = { left: labelWidth, width: plotWidth, top: top, rowHeight: rowHeight, count: shown.length };
    ctx.clearRect(0, 0, width, height);
    var colors = state.colors;
    ctx.font = "10px ui-monospace, Consolas, monospace";
    ctx.textBaseline = "middle";
    ctx.textAlign = "left";
    ctx.fillStyle = state.muted;
    for (var tick = 0; tick <= 4; tick += 1) {
      var x = labelWidth + plotWidth * tick / 4;
      ctx.strokeStyle = "rgba(191,200,203,.12)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, top - 6);
      ctx.lineTo(x, height - 4);
      ctx.stroke();
      ctx.fillText((state.data.duration * tick / 4).toFixed(2) + "s", x - (tick === 0 ? 0 : 12), 9);
    }
    if (!shown.length) {
      ctx.fillText("勾选左侧通道后显示曲线", labelWidth + 12, height / 2);
      state.hint.textContent = "";
      return;
    }
    shown.forEach(function (trackIndex, rowIndex) {
      var track = state.data.tracks[trackIndex];
      var y0 = top + rowIndex * rowHeight;
      var rowTop = y0 + 4;
      var rowBottom = y0 + rowHeight - 6;
      var plotHeight = Math.max(10, rowBottom - rowTop);
      var points = track.points || [];
      var min = Infinity;
      var max = -Infinity;
      points.forEach(function (point) {
        for (var axis = 1; axis < point.length; axis += 1) {
          var value = Number(point[axis]);
          if (!Number.isFinite(value)) continue;
          if (value < min) min = value;
          if (value > max) max = value;
        }
      });
      if (!Number.isFinite(min) || !Number.isFinite(max)) {
        min = 0;
        max = 1;
      }
      if (max - min < 1e-9) {
        var center = (min + max) / 2;
        min = center - 0.5;
        max = center + 0.5;
      }
      var span = max - min;
      ctx.fillStyle = rowIndex % 2 ? "rgba(255,255,255,.02)" : "rgba(255,255,255,.01)";
      ctx.fillRect(labelWidth, y0, plotWidth, rowHeight);
      ctx.fillStyle = state.text;
      var label = track.node + " · " + trackLabel(track);
      if (label.length > 28) label = label.slice(0, 27) + "…";
      ctx.fillText(label, 6, y0 + rowHeight / 2);
      ctx.textAlign = "right";
      ctx.fillStyle = state.muted;
      ctx.fillText(fmt(max, 2), labelWidth - 8, rowTop);
      ctx.fillText(fmt(min, 2), labelWidth - 8, rowBottom);
      ctx.textAlign = "left";
      if (min < 0 && max > 0) {
        var zeroY = rowTop + (1 - (0 - min) / span) * plotHeight;
        ctx.strokeStyle = "rgba(191,200,203,.22)";
        ctx.beginPath();
        ctx.moveTo(labelWidth, zeroY);
        ctx.lineTo(labelWidth + plotWidth, zeroY);
        ctx.stroke();
      }
      var stride = Math.max(1, Math.ceil(points.length / 900));
      for (var axis = 1; axis < (points[0] || []).length; axis += 1) {
        ctx.strokeStyle = colors[(axis - 1) % colors.length];
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        var started = false;
        for (var index = 0; index < points.length; index += stride) {
          var point = points[index];
          var value = Number(point[axis]);
          if (!Number.isFinite(value)) continue;
          var px = labelWidth + clamp(point[0] / state.data.duration, 0, 1) * plotWidth;
          var py = rowTop + (1 - (value - min) / span) * plotHeight;
          if (!started) {
            ctx.moveTo(px, py);
            started = true;
          } else {
            ctx.lineTo(px, py);
          }
        }
        var lastPoint = points[points.length - 1];
        if (lastPoint) {
          var lastValue = Number(lastPoint[axis]);
          if (Number.isFinite(lastValue)) {
            var lastX = labelWidth + clamp(lastPoint[0] / state.data.duration, 0, 1) * plotWidth;
            var lastY = rowTop + (1 - (lastValue - min) / span) * plotHeight;
            if (started) ctx.lineTo(lastX, lastY);
          }
        }
        ctx.stroke();
        if (points.length <= 48 && started) {
          ctx.fillStyle = colors[(axis - 1) % colors.length];
          for (var pointIndex = 0; pointIndex < points.length; pointIndex += 1) {
            var dot = points[pointIndex];
            var dotValue = Number(dot[axis]);
            if (!Number.isFinite(dotValue)) continue;
            var dotX = labelWidth + clamp(dot[0] / state.data.duration, 0, 1) * plotWidth;
            var dotY = rowTop + (1 - (dotValue - min) / span) * plotHeight;
            ctx.beginPath();
            ctx.arc(dotX, dotY, 2, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      }
      if (track.axes && track.axes.length > 1) {
        var legendX = labelWidth + plotWidth - 8;
        for (var axisIndex = 0; axisIndex < track.axes.length; axisIndex += 1) {
          var text = String(track.axes[axisIndex]).toUpperCase();
          ctx.fillStyle = colors[axisIndex % colors.length];
          ctx.fillText(text, legendX - ctx.measureText(text).width, rowTop + axisIndex * 11);
        }
      }
    });
    var cursorX = labelWidth + clamp(state.time / state.data.duration, 0, 1) * plotWidth;
    ctx.strokeStyle = state.colors[1] || "#eec06c";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(cursorX, top - 4);
    ctx.lineTo(cursorX, height - 4);
    ctx.stroke();
    var hidden = selected.length - shown.length;
    var notes = [];
    if (hidden > 0) notes.push("已勾选 " + selected.length + " 条，画布只显示前 " + MAX_ROWS + " 条 · 未显示 " + hidden + " 条");
    var contentEnd = 0;
    shown.forEach(function (trackIndex) {
      var track = state.data.tracks[trackIndex];
      if (track.end > contentEnd) contentEnd = track.end;
    });
    if (shown.length && contentEnd > 0 && contentEnd < state.data.duration * 0.2) {
      notes.push("所选通道关键帧集中在 0 ~ " + fmt(contentEnd, 2) + "s，缓存姿态通常只有两帧");
    }
    state.hint.textContent = notes.join(" · ");
  }

  function boneList(data) {
    if (!Array.isArray(data.bones) || !data.bones.length) return [];
    var indexByName = {};
    data.bones.forEach(function (bone, index) {
      if (bone && bone.name != null) indexByName[String(bone.name)] = index;
    });
    return data.bones.map(function (bone, index) {
      var parent = bone && bone.parent;
      if (parent == null || parent === "") parent = -1;
      else if (typeof parent === "string") parent = indexByName[parent];
      parent = Number(parent);
      if (!Number.isFinite(parent)) parent = -1;
      var position = bone && bone.position;
      if (position && !Array.isArray(position) && typeof position === "object") {
        position = [position.x, position.y, position.z];
      }
      if (!Array.isArray(position)) position = bone && Array.isArray(bone.local) ? bone.local : [0, 0, 0];
      return {
        name: String(bone && bone.name != null ? bone.name : "bone" + index),
        parent: parent,
        position: [num(position[0], 0), num(position[1], 0), num(position[2], 0)]
      };
    });
  }

  function drawBones(state) {
    if (!state.boneCtx || !state.boneCanvas) return;
    var bones = boneList(state.data);
    var canvas = state.boneCanvas;
    var width = Math.max(240, canvas.parentElement ? canvas.parentElement.clientWidth : 600);
    var height = 220;
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = "100%";
    canvas.style.height = height + "px";
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    if (!bones.length) return;
    var translation = {};
    state.data.tracks.forEach(function (track) {
      if (track.channel !== "translation" || !track.points.length) return;
      if (translation[track.node] == null || track.source === "cached_pose") translation[track.node] = track;
    });
    var world = new Array(bones.length);
    var active = new Array(bones.length);
    function resolve(index, depth) {
      if (index < 0 || index >= bones.length || depth > bones.length + 4) return [0, 0, 0];
      if (world[index]) return world[index];
      var bone = bones[index];
      var local = bone.position;
      var track = translation[bone.name];
      if (track) {
        var values = sampleTrack(track, state.time);
        if (values && values.length >= 3) {
          local = [values[0], values[1], values[2]];
          active[index] = true;
        }
      }
      var parent = bone.parent;
      var parentWorld = parent >= 0 && parent < bones.length && parent !== index ? resolve(parent, depth + 1) : [0, 0, 0];
      world[index] = [parentWorld[0] + local[0], parentWorld[1] + local[1], parentWorld[2] + local[2]];
      return world[index];
    }
    for (var index = 0; index < bones.length; index += 1) resolve(index, 0);
    var minX = Infinity;
    var maxX = -Infinity;
    var minY = Infinity;
    var maxY = -Infinity;
    world.forEach(function (point) {
      if (point[0] < minX) minX = point[0];
      if (point[0] > maxX) maxX = point[0];
      if (point[1] < minY) minY = point[1];
      if (point[1] > maxY) maxY = point[1];
    });
    if (!Number.isFinite(minX) || maxX - minX < 1e-6) {
      minX -= 0.5;
      maxX += 0.5;
    }
    if (!Number.isFinite(minY) || maxY - minY < 1e-6) {
      minY -= 0.5;
      maxY += 0.5;
    }
    var pad = 16;
    var scale = Math.min((width - pad * 2) / Math.max(1e-6, maxX - minX), (height - pad * 2) / Math.max(1e-6, maxY - minY));
    function project(point) {
      return [pad + (point[0] - minX) * scale, height - pad - (point[1] - minY) * scale];
    }
    var primary = state.colors[0] || "#7bdbe6";
    var tertiary = state.colors[1] || "#eec06c";
    var muted = state.muted || "#899296";
    bones.forEach(function (bone, index) {
      var parent = bone.parent;
      if (parent < 0 || parent >= bones.length || parent === index) return;
      var a = project(world[parent]);
      var b = project(world[index]);
      ctx.strokeStyle = active[index] ? tertiary : primary;
      ctx.lineWidth = active[index] ? 2 : 1.2;
      ctx.beginPath();
      ctx.moveTo(a[0], a[1]);
      ctx.lineTo(b[0], b[1]);
      ctx.stroke();
    });
    bones.forEach(function (bone, index) {
      var point = project(world[index]);
      ctx.fillStyle = active[index] ? tertiary : primary;
      ctx.beginPath();
      ctx.arc(point[0], point[1], active[index] ? 3 : 2, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.fillStyle = muted;
    ctx.font = "9px ui-monospace, Consolas, monospace";
    ctx.fillText("X " + fmt(minX, 2) + " → " + fmt(maxX, 2) + "   Y " + fmt(minY, 2) + " → " + fmt(maxY, 2), 8, height - 6);
  }

  function draw(state) {
    if (!state.data) return;
    drawRows(state);
    drawBones(state);
    updateClock(state);
    updateReadout(state);
  }

  function seek(state, time) {
    state.time = clamp(time, 0, state.data.duration);
    state.last = performance.now();
    draw(state);
  }

  function togglePlay(state) {
    if (!state.playing && !state.loop && state.time >= state.data.duration - 1e-6) state.time = 0;
    state.playing = !state.playing;
    state.last = performance.now();
    updatePlayButton(state);
    if (state.playing) startLoop(state);
    else stopLoop(state);
  }

  function step(state, now) {
    if (!state.playing || state.dead) return;
    var delta = Math.max(0, Math.min(0.2, (now - state.last) / 1000));
    state.last = now;
    state.time += delta;
    if (state.time >= state.data.duration) {
      if (state.loop) state.time = state.data.duration > 0 ? state.time % state.data.duration : 0;
      else {
        state.time = state.data.duration;
        state.playing = false;
        updatePlayButton(state);
        stopLoop(state);
        draw(state);
        return;
      }
    }
    draw(state);
  }

  function startLoop(state) {
    if (state.dead || state.worker || state.interval) return;
    state.last = performance.now();
    if (typeof Worker !== "undefined" && typeof Blob !== "undefined" && typeof URL !== "undefined") {
      try {
        var source = "var timer=0;onmessage=function(event){if(event.data==='start'){timer=setInterval(function(){postMessage(1)},33)}else{clearInterval(timer)}}";
        var url = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
        var worker = new Worker(url);
        state.workerUrl = url;
        state.worker = worker;
        worker.onmessage = function () {
          if (!state.container || !state.container.isConnected) {
            stopLoop(state);
            return;
          }
          step(state, performance.now());
        };
        worker.onerror = function () {
          if (state.dead) return;
          stopLoop(state);
          if (!state.interval) state.interval = setInterval(function () { step(state, performance.now()); }, 33);
        };
        worker.postMessage("start");
        return;
      } catch (error) {
        state.worker = null;
        if (state.workerUrl) URL.revokeObjectURL(state.workerUrl);
        state.workerUrl = null;
      }
    }
    state.interval = setInterval(function () {
      if (!state.container || !state.container.isConnected) {
        stopLoop(state);
        return;
      }
      step(state, performance.now());
    }, 33);
  }

  function stopLoop(state) {
    if (state.worker) {
      state.worker.terminate();
      state.worker = null;
    }
    if (state.workerUrl) {
      URL.revokeObjectURL(state.workerUrl);
      state.workerUrl = null;
    }
    if (state.interval) {
      clearInterval(state.interval);
      state.interval = 0;
    }
  }

  function bindEvents(container, state) {
    container.querySelector('[data-act="play"]').addEventListener("click", function () { togglePlay(state); });
    container.querySelector('[data-act="reset"]').addEventListener("click", function () {
      seek(state, 0);
    });
    container.querySelector('[data-act="all"]').addEventListener("click", function () {
      state.data.tracks.forEach(function (track, index) { state.selected.add(index); });
      syncTrackBoxes(state);
      buildReadout(state);
      draw(state);
    });
    container.querySelector('[data-act="none"]').addEventListener("click", function () {
      state.selected.clear();
      syncTrackBoxes(state);
      buildReadout(state);
      draw(state);
    });
    var loopBox = container.querySelector(".anim-loop input");
    if (loopBox) {
      loopBox.addEventListener("change", function () { state.loop = loopBox.checked; });
    }
    container.querySelectorAll("[data-track]").forEach(function (box) {
      box.addEventListener("change", function () {
        var index = Number(box.dataset.track);
        if (box.checked) state.selected.add(index);
        else state.selected.delete(index);
        syncGroupBoxes(state);
        buildReadout(state);
        draw(state);
      });
    });
    container.querySelectorAll("[data-group-check]").forEach(function (box) {
      box.addEventListener("click", function (event) { event.stopPropagation(); });
      box.addEventListener("change", function () {
        var group = state.groups[Number(box.dataset.groupCheck)];
        if (!group) return;
        group.tracks.forEach(function (index) {
          if (box.checked) state.selected.add(index);
          else state.selected.delete(index);
        });
        syncTrackBoxes(state);
        buildReadout(state);
        draw(state);
      });
    });
    var filter = container.querySelector(".anim-filter");
    if (filter) {
      filter.addEventListener("input", function () {
        var query = filter.value.trim().toLowerCase();
        state.query = query;
        state.groups.forEach(function (group, groupIndex) {
          var node = container.querySelector('.anim-group[data-group="' + groupIndex + '"]');
          if (!node) return;
          var text = group.node.toLowerCase() + " " + group.tracks.map(function (index) {
            var track = state.data.tracks[index];
            return track.channel + " " + track.source;
          }).join(" ");
          var match = !query || text.indexOf(query) >= 0;
          node.hidden = !match;
          if (query && match) node.open = true;
        });
      });
    }
    var timeline = container.querySelector(".anim-track");
    var seeking = false;
    function seekFromEvent(event) {
      var rect = timeline.getBoundingClientRect();
      var ratio = rect.width > 0 ? clamp((event.clientX - rect.left) / rect.width, 0, 1) : 0;
      seek(state, ratio * state.data.duration);
    }
    timeline.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) return;
      seeking = true;
      timeline.setPointerCapture && timeline.setPointerCapture(event.pointerId);
      seekFromEvent(event);
      event.preventDefault();
    });
    timeline.addEventListener("pointermove", function (event) {
      if (seeking) seekFromEvent(event);
    });
    timeline.addEventListener("pointerup", function (event) {
      seeking = false;
      if (timeline.releasePointerCapture) timeline.releasePointerCapture(event.pointerId);
    });
    timeline.addEventListener("pointercancel", function () { seeking = false; });
    var canvas = state.canvas;
    if (canvas) {
      canvas.addEventListener("pointerdown", function (event) {
        if (event.button !== 0 || !state.layout) return;
        var rect = canvas.getBoundingClientRect();
        var x = event.clientX - rect.left;
        if (x < state.layout.left) return;
        var ratio = clamp((x - state.layout.left) / state.layout.width, 0, 1);
        seek(state, ratio * state.data.duration);
        event.preventDefault();
      });
    }
    container.addEventListener("keydown", function (event) {
      var tag = event.target && event.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON" || tag === "A") return;
      if (event.code === "Space" || event.key === " ") {
        event.preventDefault();
        togglePlay(state);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        seek(state, state.time - (event.shiftKey ? 1 : 0.05));
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        seek(state, state.time + (event.shiftKey ? 1 : 0.05));
      } else if (event.key === "Home") {
        event.preventDefault();
        seek(state, 0);
      } else if (event.key === "End") {
        event.preventDefault();
        seek(state, state.data.duration);
      }
    });
    if (typeof ResizeObserver !== "undefined" && state.wrap) {
      var pending = 0;
      state.resizeObserver = new ResizeObserver(function () {
        if (pending) cancelAnimationFrame(pending);
        pending = requestAnimationFrame(function () {
          pending = 0;
          state.pendingResize = 0;
          draw(state);
        });
        state.pendingResize = pending;
      });
      state.resizeObserver.observe(state.wrap);
    } else {
      state.resizeHandler = function () { draw(state); };
      window.addEventListener("resize", state.resizeHandler);
    }
  }

  function destroy(state) {
    state.dead = true;
    stopLoop(state);
    if (state.pendingResize) cancelAnimationFrame(state.pendingResize);
    if (state.resizeObserver) state.resizeObserver.disconnect();
    if (state.resizeHandler) window.removeEventListener("resize", state.resizeHandler);
    if (state.container) state.container.__animationPreview = null;
  }

  function renderAnimationPreview(container, data) {
    var node = typeof container === "string" ? document.getElementById(container) || document.querySelector(container) : container;
    if (!node) return null;
    ensureStyle();
    if (node.__animationPreview) destroy(node.__animationPreview);
    var normalized = normalizeData(data);
    if (normalized.error) {
      node.innerHTML = '<div class="anim-preview">' + errorMarkup(normalized) + "</div>";
      return null;
    }
    var groups = groupTracks(normalized.tracks);
    var selected = defaultSelection(normalized.tracks);
    node.innerHTML = markup(normalized, groups, selected);
    node.tabIndex = 0;
    var state = {};
    node.__animationPreview = state;
    bindState(node, normalized, state);
    buildReadout(state);
    syncTrackBoxes(state);
    updatePlayButton(state);
    bindEvents(node, state);
    if (normalized.tracks.length) {
      draw(state);
      startLoop(state);
    } else {
      updateClock(state);
      if (state.boneCanvas) drawBones(state);
    }
    return state;
  }

  window.renderAnimationPreview = renderAnimationPreview;
})();
