"use strict";

// 直接执行页面中的真实函数，并记录提交给 WebGL 的数据，避免复制渲染实现。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "web", "app.js"), "utf8");

function pageFunction(name) {
  const pattern = new RegExp(`^(?:async )?function ${name}\\([^]*?^}`, "m");
  const match = source.match(pattern);
  assert.ok(match, `页面函数不存在：${name}`);
  return match[0];
}

function mockGl() {
  let nextId = 0;
  const bound = new Map();
  const enabled = new Set();
  const gl = {
    ARRAY_BUFFER: 1, ELEMENT_ARRAY_BUFFER: 2, STATIC_DRAW: 3, DYNAMIC_DRAW: 4,
    FLOAT: 5, UNSIGNED_INT: 6, UNSIGNED_SHORT: 7, BLEND: 8, DEPTH_TEST: 9,
    CULL_FACE: 10, TRIANGLES: 11, LINES: 12, POINTS: 13, TEXTURE0: 20,
    TEXTURE1: 21, TEXTURE2: 22, TEXTURE3: 23, TEXTURE_2D: 24,
    COLOR_BUFFER_BIT: 1, DEPTH_BUFFER_BIT: 2,
    SRC_ALPHA: 30, ONE_MINUS_SRC_ALPHA: 31, CW: 32, CCW: 33, BACK: 34,
    depthWrite: true, draws: [], updates: [], uploads: 0, winding: 33,
    createBuffer: () => ({ id: ++nextId }),
    createVertexArray: () => ({ id: ++nextId, attributes: new Map() }),
    bindBuffer(target, buffer) { bound.set(target, buffer); },
    bufferData(target, data) { bound.get(target).data = Array.from(data); this.uploads += 1; },
    bufferSubData(target, offset, data) {
      const buffer = bound.get(target);
      buffer.data.splice(offset / 4, data.length, ...data);
      this.updates.push(buffer);
    },
    bindVertexArray(vao) { this.vao = vao; },
    enable(flag) { enabled.add(flag); },
    disable(flag) { enabled.delete(flag); },
    depthMask(value) { this.depthWrite = value; },
    frontFace(value) { this.winding = value; },
    cullFace(value) { this.cullMode = value; },
    vertexAttribPointer(location, size, type, normalized, stride, offset) {
      if (this.vao) this.vao.attributes.set(location, { buffer: bound.get(this.ARRAY_BUFFER), stride, offset });
    },
    drawElementsInstanced(mode, count, type, offset, instances) {
      this.draws.push({ vao: this.vao, count, instances,
        blend: enabled.has(this.BLEND), depthWrite: this.depthWrite,
        first: this.vao.attributes.get(2).offset / 112, winding: this.winding,
        cull: enabled.has(this.CULL_FACE), cullMode: this.cullMode });
    },
  };
  for (const name of ["enableVertexAttribArray", "vertexAttribDivisor",
    "deleteVertexArray", "deleteBuffer", "viewport", "clearColor", "clear", "useProgram",
    "uniformMatrix4fv", "uniform4f", "uniform3f", "uniform1f", "blendFunc",
    "activeTexture", "bindTexture", "drawArrays"]) gl[name] = () => {};
  return gl;
}

function makeContext() {
  const state = {
    console, URLSearchParams, Float32Array, Uint32Array, Uint16Array,
    MAP_MESH_LIMIT: 240,
    map3d: { gl: mockGl(), models: new Map(), hiddenModels: new Set(), units: [],
      token: 1, total: 0, loading: 0, loadErrors: 0, uniforms: {},
      canvas: { width: 640, height: 480 }, camera: { target: [0, 0, 0], distance: 100 } },
    mapView: { ignoreErrors: true, modelColors: {}, modelIcons: {}, data: null },
    mapMsgAdd: () => {}, tr: (key) => key,
    updateMapHud: () => {}, updateMapMode: () => {}, requestMap3DDraw: () => {},
    resizeMap3D: () => {}, map3DViewProj: () => new Float32Array(16),
    map3DBeginScene: () => {}, map3DCaptureScene: () => {},
    map3DFrustum: () => [], map3DSphereInFrustum: () => true,
    map3DCameraEye: () => [0, 10, 10], map3DApplyMaterial: () => {}, drawMapMinimap: () => {},
    map3DLoadTexture: (file, done) => done({ file }),
  };
  vm.createContext(state);
  for (const name of ["apiUrl", "mapAxis", "mapUnits", "gimTrsMatrix", "map3DEulerQuat",
    "map3DNumber", "map3DModelKey", "map3DValidPaint", "map3DUnitMatrix",
    "map3DModelColor", "map3DUnitColor", "map3DUnitPaint", "map3DBuildModel",
    "map3DLoadMaterialTextures", "map3DRefreshColors", "map3DRefreshEntryColors",
    "rebuildMapPoints", "loadMap3DMeshes", "map3DSkipMaterial",
    "map3DBindInstanceRange", "map3DDrawInstanceRange", "drawMap3D"]) {
    vm.runInContext(pageFunction(name), state, { filename: `app.js:${name}` });
  }
  return state;
}

function triangle() {
  return {
    vertices: [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
    normals: [[0, 0, -1], [0, 0, -1], [0, 0, -1]],
    uvs: [[0, 0], [1, 0], [0, 1]], indices: [0, 1, 2],
    bounds: { min: [0, 0, 0], max: [1, 1, 0] },
  };
}

function unit(model, material, x, extra = {}) {
  return { model, material, position: [x, 0, 0], scale: [1, 1, 1],
    angle: [0, 0, 0], alpha: 1, visible: true, ...extra };
}

function normalUpload(mesh, label) {
  const ctx = makeContext();
  ctx.map3DBuildModel("props/test.mesh", mesh, [unit("props/test.mesh", "", 0)], [], [], []);
  const entry = ctx.map3d.models.get("props/test.mesh");
  const uploaded = entry.normalBuffer.data;
  let zeroY = 0;
  for (let i = 0; i < mesh.vertices.length; i += 1) {
    for (let axis = 0; axis < 3; axis += 1) {
      const expected = Math.fround(mesh.normals[i][axis]);
      assert.equal(uploaded[i * 3 + axis], expected, `${label} 顶点 ${i} 法线 ${axis}`);
    }
    if (mesh.normals[i][1] === 0) zeroY += 1;
  }
  assert.ok(zeroY > 0, `${label} 必须覆盖 Y=0 的真实法线`);
  console.log(`通过：${label} 的 ${mesh.vertices.length} 个法线保持原值，含 ${zeroY} 个 Y=0`);
}

async function main() {
  normalUpload(triangle(), "侧面三角形");
  const ctx = makeContext();
  assert.deepEqual(Array.from(ctx.map3DUnitPaint({ paint: [[0, 0, 0], [2, 3, 4], null], alpha: 0.4 })[0]), [0, 0, 0, 0.4]);
  assert.deepEqual(Array.from(ctx.map3DUnitColor({ color: [0, 0, 0] })), [0, 0, 0, 1]);
  assert.deepEqual(Array.from(ctx.map3DUnitPaint({ paint: [null, null, null] })[0]), [-1, -1, -1, 1]);
  const matrix = ctx.map3DUnitMatrix(unit("", "", 3, { scale: [0, -2, 0.5] }));
  assert.equal(Math.hypot(...matrix.slice(0, 3)), 0);
  assert.equal(matrix[5], -2);
  assert.equal(matrix[10], 0.5);
  console.log("通过：黑色和深色染色、缺省染色、零缩放和负缩放");

  const model = "props/shared.gim";
  const units = [
    unit(model, "mat/a.mtg", 1, { color: [0, 0, 0] }),
    unit(model, "mat/b.mtg", 2, { alpha: 0.4 }),
    unit(model, "mat/a.mtg", 3),
    unit(model, "mat/missing.mtg", 4),
    unit("props/direct.mesh", "mat/a.mtg", 5),
    unit(model, "mat/hidden.mtg", 6, { visible: false }),
    unit("props/collision_ground.gim", "", 7),
    unit("", "", 8),
  ];
  ctx.map3d.units = units;
  ctx.mapView.data = { units, bounds: { axis: "xz" } };
  assert.equal(ctx.mapUnits().length, 7);
  const requested = [];
  ctx.getJson = async (url) => {
    const query = new URLSearchParams(url.split("?")[1]);
    const file = query.get("path");
    const material = query.get("material");
    requested.push([file, material]);
    if (material === "mat/missing.mtg") throw new Error("测试缺失材质");
    const mesh = triangle();
    const response = file.endsWith(".mesh") ? mesh : { mesh_data: mesh };
    return { ...response, materials: [{ surface: "test.surf", tex0: `${material}.kta` }] };
  };
  await ctx.loadMap3DMeshes({ units }, 1);
  assert.equal(requested.length, 4);
  assert.ok(!requested.some(([, material]) => material === "mat/hidden.mtg"));
  assert.equal(ctx.map3d.models.size, 3);
  assert.equal(ctx.map3d.models.get(ctx.map3DModelKey(units[0])).count, 2);
  const first = ctx.map3d.models.get(ctx.map3DModelKey(units[0]));
  const second = ctx.map3d.models.get(ctx.map3DModelKey(units[1]));
  assert.equal(first.textures[0][0].file, "mat/a.mtg.kta");
  assert.equal(second.textures[0][0].file, "mat/b.mtg.kta");
  assert.ok(ctx.map3d.models.has(ctx.map3DModelKey(units[4])));
  assert.equal(ctx.map3d.loadErrors, 1);
  assert.equal(ctx.map3d.pointCount, 2);
  assert.deepEqual(ctx.map3d.points.data.filter((_, index) => index % 7 === 0), [4, 8]);
  console.log("通过：材质分别合批和绑定纹理、隐藏和碰撞组件不加载、直接 mesh 响应、失败材质保留占位点");

  ctx.map3DRefreshColors(model);
  assert.equal(ctx.map3d.gl.updates.length, 2);
  ctx.drawMap3D();
  const opaqueDraw = ctx.map3d.gl.draws.find((draw) => draw.vao === first.vao);
  const transparentDraw = ctx.map3d.gl.draws.find((draw) => draw.vao === second.vao);
  assert.deepEqual([opaqueDraw.blend, opaqueDraw.depthWrite], [false, true]);
  assert.deepEqual([transparentDraw.blend, transparentDraw.depthWrite], [true, false]);
  ctx.mapView.ignoreErrors = false;
  ctx.rebuildMapPoints();
  assert.equal(ctx.map3d.pointCount, 6);
  console.log("通过：材质分组染色刷新、不透明与半透明通道、严格模式占位点");

  const sorting = makeContext();
  sorting.map3d.camera = { target: [0, 0, 1] };
  sorting.map3DCameraEye = () => [0, 0, 0];
  const aUnits = [
    unit("a", "", 0, { position: [0, 0, 30] }),
    unit("a", "", 0, { position: [0, 0, 10], scale: [-1, 1, 1] }),
  ];
  sorting.map3DBuildModel("a", triangle(), aUnits, [{ transparent: true, cull_back: true }], [], []);
  sorting.map3DBuildModel("b", triangle(), [unit("b", "", 0, { position: [100, 0, 20] })],
    [{ transparent: true, cull_back: false }], [], []);
  sorting.map3DBuildModel("opaque", triangle(), [unit("opaque", "", 0), unit("opaque", "", 1),
    unit("opaque", "", 2, { scale: [-1, 1, 1] })], [{ cull_back: true }], [], []);
  const entryA = sorting.map3d.models.get("a");
  const entryB = sorting.map3d.models.get("b");
  const entryOpaque = sorting.map3d.models.get("opaque");
  const uploadCount = sorting.map3d.gl.uploads;
  let began = 0;
  const captures = [];
  sorting.map3DBeginScene = () => { began += 1; };
  sorting.map3DCaptureScene = () => captures.push(sorting.map3d.gl.draws.slice());
  sorting.drawMap3D();
  let sorted = sorting.map3d.gl.draws;
  assert.deepEqual(sorted.filter((draw) => !draw.blend).map((draw) => [draw.instances, draw.first, draw.winding]),
    [[2, 0, sorting.map3d.gl.CCW], [1, 2, sorting.map3d.gl.CW]]);
  assert.ok(sorted.filter((draw) => !draw.blend).every((draw) => draw.vao === entryOpaque.vao && draw.depthWrite));
  assert.deepEqual(sorted.filter((draw) => draw.blend).map((draw) => [draw.vao.id, draw.first, draw.instances]),
    [[entryA.vao.id, 0, 1], [entryB.vao.id, 0, 1], [entryA.vao.id, 1, 1]]);
  assert.ok(sorted.filter((draw) => draw.blend).every((draw) => !draw.depthWrite));
  const mirroredDraw = sorted.find((draw) => draw.vao === entryA.vao && draw.first === 1);
  assert.deepEqual([mirroredDraw.cull, mirroredDraw.winding, mirroredDraw.cullMode], [true, sorting.map3d.gl.CW, sorting.map3d.gl.BACK]);
  assert.equal(sorted.find((draw) => draw.vao === entryB.vao).cull, false);
  assert.equal(sorting.map3d.gl.uploads, uploadCount, "排序不得重新上传实例数据");
  assert.equal(sorting.map3d.gl.depthWrite, true);
  assert.equal(sorting.map3d.gl.winding, sorting.map3d.gl.CCW);
  assert.equal(began, 1);
  assert.equal(captures.length, 1);
  assert.equal(captures[0].length, 2);
  assert.ok(captures[0].every((draw) => !draw.blend), "场景颜色和深度必须在所有不透明组件之后、透明组件之前捕获");
  sorting.map3d.gl.draws = [];
  sorting.map3DCameraEye = () => [0, 0, 40];
  sorting.map3d.camera.target = [0, 0, 0];
  sorting.drawMap3D();
  sorted = sorting.map3d.gl.draws.filter((draw) => draw.blend);
  assert.deepEqual(sorted.map((draw) => [draw.vao.id, draw.first]),
    [[entryA.vao.id, 1], [entryB.vao.id, 0], [entryA.vao.id, 0]]);
  assert.equal(sorting.map3d.gl.uploads, uploadCount);
  assert.equal(began, 2);
  assert.equal(captures.length, 2);
  console.log("通过：半透明跨模型逐实例按视深排序、镜像绕序、材质背面剔除、相机转向、无实例重传");
  const empty = makeContext();
  let emptyCapture = 0;
  empty.map3DCaptureScene = () => { emptyCapture += 1; };
  empty.drawMap3D();
  assert.equal(emptyCapture, 1, "空场景也必须把离屏背景送到默认帧缓冲");
  console.log("通过：场景颜色/深度捕获时序及空场景回退");

  const transformed = makeContext();
  transformed.map3d.camera = { target: [0, 0, 1] };
  transformed.map3DCameraEye = () => [0, 0, 0];
  const offsetMesh = triangle();
  offsetMesh.bounds = { min: [0, 0, 9], max: [0, 0, 11] };
  transformed.map3DBuildModel("offset", offsetMesh, [unit("offset", "", 0,
    { position: [0, 0, 1], scale: [1, 1, 2] })], [{ transparent: true }], [], []);
  transformed.map3DBuildModel("centered", triangle(), [unit("centered", "", 0,
    { position: [0, 0, 15] })], [{ transparent: true }], [], []);
  transformed.map3DBuildModel("rotated", offsetMesh, [unit("rotated", "", 0,
    { position: [0, 0, 3], angle: [0, 90, 0] })], [{ transparent: true }], [], []);
  const offset = transformed.map3d.models.get("offset");
  const rotated = transformed.map3d.models.get("rotated");
  assert.deepEqual(Array.from(offset.instanceCenters), [0, 0, 21]);
  assert.ok(Math.abs(rotated.instanceCenters[0] - 10) < 1e-5 && Math.abs(rotated.instanceCenters[2] - 3) < 1e-5);
  transformed.drawMap3D();
  assert.deepEqual(transformed.map3d.gl.draws.map((draw) => draw.vao.id), [offset.vao.id,
    transformed.map3d.models.get("centered").vao.id, rotated.vao.id]);
  console.log("通过：使用旋转和非均匀缩放后的网格中心排序，避免使用组件原点");

  const binding = makeContext();
  binding.performance = { now: () => 2000 };
  binding.map3d.animationStart = 0;
  binding.map3d.sceneReady = true;
  const uniforms = {};
  binding.map3d.uniforms = new Proxy({}, { get: (_, name) => name });
  for (const method of ["uniform1f", "uniform2f", "uniform3f", "uniform4f"]) {
    binding.map3d.gl[method] = (name, ...values) => { uniforms[name] = values; };
  }
  for (const name of ["gimNormalize", "gimCross", "gimLookAt"]) vm.runInContext(pageFunction(name), binding);
  vm.runInContext(pageFunction("map3DApplyMaterial"), binding);
  const water = { shader_kind: "water_color", roughness: 0, metalness: 0, mix_map: false,
    alpha_val: null, glass_opacity: 0, normal_slot: "tex1",
    ice_param: [0, 0, 0.2, 1], foam_color: [0.2, 0.3, 0.4, 1],
    tex0: "gradient.kta", tex1: "normal.kta", tex2: "noise.kta" };
  binding.map3DBuildModel("water", triangle(), [unit("water", "", 0)], [water], [], []);
  binding.map3DApplyMaterial(binding.map3d.models.get("water"), { material: 0 });
  assert.deepEqual(uniforms.waterKind, [2]);
  assert.deepEqual(uniforms.glass, [0]);
  assert.deepEqual(uniforms.roughness, [0]);
  assert.deepEqual(uniforms.metalness, [0]);
  assert.deepEqual(uniforms.mixMap, [0]);
  assert.deepEqual(uniforms.alphaMtl, [1]);
  assert.deepEqual(uniforms.glassOpacity, [0]);
  assert.deepEqual(uniforms.waterIce, [0, 0, 0.2, 1]);
  assert.deepEqual(uniforms.foamColor, [0.2, 0.3, 0.4, 1]);
  const glass = { shader_kind: "glass", normal_slot: "tex0", alpha_val: 0, glass_opacity: 0,
    pattern_enabled: true, pattern_op: 0, pattern_tex: "pattern.kta" };
  binding.map3DBuildModel("glass", triangle(), [unit("glass", "", 0)], [glass], [], []);
  binding.map3DApplyMaterial(binding.map3d.models.get("glass"), { material: 0 });
  assert.deepEqual(uniforms.glass, [1]);
  assert.deepEqual(uniforms.waterKind, [0]);
  assert.deepEqual(uniforms.normalSlot, [0]);
  assert.deepEqual(uniforms.alphaMtl, [0]);
  assert.deepEqual(uniforms.glassOpacity, [0]);
  assert.deepEqual(uniforms.patternOpacity, [0]);
  assert.equal(binding.map3d.models.get("glass").textures[0][3].file, "pattern.kta");
  const fall = { shader_kind: "waterfall", waterfall_textures: ["flow.kta", "noise.kta", ""] };
  binding.map3DBuildModel("fall", triangle(), [unit("fall", "", 0)], [fall], [], []);
  const fallEntry = binding.map3d.models.get("fall");
  assert.equal(fallEntry.textures[0][0].file, "flow.kta");
  assert.equal(fallEntry.textures[0][1].file, "noise.kta");
  assert.equal(fallEntry.textures[0][2], undefined);
  assert.equal(binding.map3d.animated, true);
  console.log("通过：水与玻璃分支、法线槽、零粗糙度/透明度、泡沫和反射参数、瀑布纹理寄存器映射");

  const meshArg = process.argv.indexOf("--mesh");
  if (meshArg >= 0) {
    assert.ok(process.argv[meshArg + 1], "--mesh 需要真实网格路径");
    const file = path.resolve(process.argv[meshArg + 1]);
    const python = path.join(root, "bin", "python", process.platform === "win32" ? "python.exe" : "python");
    const script = "import json,sys; sys.path.insert(0,sys.argv[1]); import mesh_parse; from pathlib import Path; print(json.dumps(mesh_parse.parse(Path(sys.argv[2]).read_bytes())))";
    const mesh = JSON.parse(execFileSync(python, ["-X", "utf8", "-c", script, path.join(root, "tools"), file],
      { encoding: "utf8", maxBuffer: 32 * 1024 * 1024 }));
    normalUpload(mesh, path.basename(file));
  }
  console.log("地图渲染回归全部通过");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
