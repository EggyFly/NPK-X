"use strict";

// 运行页面真实函数，检查帧缓冲生命周期与读写隔离，不复制生产实现。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "..", "web", "app.js"), "utf8");

function pageFunction(name) {
  const match = source.match(new RegExp(`^(?:async )?function ${name}[(][^]*?^}`, "m"));
  assert.ok(match, `页面函数不存在：${name}`);
  return match[0];
}

function mockGl() {
  let nextId = 0;
  const gl = { events: [], textures: new Map(), attachments: new Map(),
    activeSlot: 0, deletedTextures: [], deletedFramebuffers: [],
    framebuffer: null, readFramebuffer: null, drawFramebuffer: null, complete: true };
  const names = ["TEXTURE_2D", "FRAMEBUFFER", "READ_FRAMEBUFFER", "DRAW_FRAMEBUFFER",
    "DEPTH24_STENCIL8", "RGBA8", "DEPTH_STENCIL", "RGBA", "UNSIGNED_INT_24_8",
    "UNSIGNED_BYTE", "TEXTURE_MIN_FILTER", "TEXTURE_MAG_FILTER", "NEAREST", "LINEAR",
    "TEXTURE_WRAP_S", "TEXTURE_WRAP_T", "CLAMP_TO_EDGE", "COLOR_ATTACHMENT0",
    "DEPTH_STENCIL_ATTACHMENT", "FRAMEBUFFER_COMPLETE", "COLOR_BUFFER_BIT",
    "DEPTH_BUFFER_BIT", "ARRAY_BUFFER", "DEPTH_TEST", "CULL_FACE", "BLEND", "CCW"];
  names.forEach((name, index) => { gl[name] = index + 1; });
  gl.COLOR_BUFFER_BIT = 0x4000;
  gl.DEPTH_BUFFER_BIT = 0x100;
  gl.TEXTURE0 = 100; gl.TEXTURE4 = 104; gl.TEXTURE5 = 105;
  gl.createTexture = () => ({ texture: ++nextId });
  gl.createFramebuffer = () => ({ framebuffer: ++nextId });
  gl.activeTexture = (slot) => { gl.activeSlot = slot - gl.TEXTURE0; };
  gl.bindTexture = (target, texture) => {
    gl.textures.set(gl.activeSlot, texture);
    gl.events.push({ name: "texture", slot: gl.activeSlot, texture });
  };
  gl.texImage2D = (target, level, internal, width, height, border, format, type) => {
    Object.assign(gl.textures.get(gl.activeSlot), { internal, width, height, format, type });
  };
  gl.texParameteri = () => {};
  gl.deleteTexture = (value) => gl.deletedTextures.push(value);
  gl.deleteFramebuffer = (value) => gl.deletedFramebuffers.push(value);
  gl.bindFramebuffer = (target, value) => {
    if (target === gl.FRAMEBUFFER || target === gl.READ_FRAMEBUFFER) gl.readFramebuffer = value;
    if (target === gl.FRAMEBUFFER || target === gl.DRAW_FRAMEBUFFER) gl.drawFramebuffer = value;
    if (target === gl.FRAMEBUFFER) gl.framebuffer = value;
    gl.events.push({ name: "framebuffer", target, value });
  };
  gl.framebufferTexture2D = (target, attachment, textureTarget, texture) => {
    gl.attachments.set(attachment, texture);
  };
  gl.checkFramebufferStatus = () => gl.complete ? gl.FRAMEBUFFER_COMPLETE : -1;
  gl.blitFramebuffer = (...args) => gl.events.push({ name: "blit", args,
    read: gl.readFramebuffer, draw: gl.drawFramebuffer,
    sampledColor: gl.textures.get(4), sampledDepth: gl.textures.get(5) });
  gl.clear = () => gl.events.push({ name: "clear", draw: gl.drawFramebuffer });
  for (const name of ["viewport", "clearColor", "depthMask", "enable", "disable", "frontFace"]) gl[name] = () => {};
  return gl;
}

function makeContext() {
  const ctx = { map3d: { gl: mockGl(), canvas: { width: 640, height: 360 },
    models: new Map(), loadErrors: 0, pointCount: 0 },
    mapView: { ignoreErrors: true }, resizeMap3D: () => {},
    map3DViewProj: () => new Float32Array(16), drawMapMinimap: () => {} };
  vm.createContext(ctx);
  for (const name of ["map3DReleaseScene", "map3DBeginScene", "map3DCaptureScene", "drawMap3D"]) {
    vm.runInContext(pageFunction(name), ctx);
  }
  return ctx;
}

const ctx = makeContext();
const gl = ctx.map3d.gl;
ctx.map3DBeginScene();
const first = ctx.map3d.scene;
assert.deepEqual([first.width, first.height, first.color.width, first.depth.height], [640, 360, 640, 360]);
assert.deepEqual([first.depth.internal, first.depth.format, first.depth.type],
  [gl.DEPTH24_STENCIL8, gl.DEPTH_STENCIL, gl.UNSIGNED_INT_24_8]);
assert.equal(gl.attachments.get(gl.COLOR_ATTACHMENT0), first.color);
assert.equal(gl.attachments.get(gl.DEPTH_STENCIL_ATTACHMENT), first.depth);
assert.equal(gl.drawFramebuffer, first.framebuffer);
assert.equal(ctx.map3d.sceneReady, false);
assert.equal(gl.activeSlot, 0);
assert.equal(gl.textures.get(4), null);
assert.equal(gl.textures.get(5), null);
ctx.map3DCaptureScene();
const blit = gl.events.find((event) => event.name === "blit");
assert.equal(blit.read, first.framebuffer);
assert.equal(blit.draw, null);
assert.equal(blit.sampledColor, null);
assert.equal(blit.sampledDepth, null);
assert.deepEqual(blit.args, [0, 0, 640, 360, 0, 0, 640, 360,
  gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT, gl.NEAREST]);
assert.equal(gl.drawFramebuffer, null);
assert.equal(gl.readFramebuffer, null);
assert.equal(gl.textures.get(4), first.color);
assert.equal(gl.textures.get(5), first.depth);
assert.equal(ctx.map3d.sceneReady, true);
assert.equal(gl.activeSlot, 0);
console.log("通过：匹配默认深度格式，先捕获后绑定采样纹理，默认帧缓冲恢复");

ctx.map3DBeginScene();
assert.equal(ctx.map3d.scene, first);
assert.equal(gl.deletedFramebuffers.length, 0);
assert.equal(gl.textures.get(4), null);
assert.equal(gl.textures.get(5), null);
assert.equal(ctx.map3d.sceneReady, false);
ctx.map3d.canvas = { width: 1280, height: 720 };
ctx.map3DBeginScene();
const resized = ctx.map3d.scene;
assert.notEqual(resized, first);
assert.deepEqual([resized.width, resized.height], [1280, 720]);
assert.deepEqual(gl.deletedFramebuffers, [first.framebuffer]);
assert.deepEqual(gl.deletedTextures, [first.color, first.depth]);
ctx.map3DReleaseScene();
ctx.map3DReleaseScene();
assert.equal(ctx.map3d.scene, null);
assert.equal(ctx.map3d.sceneReady, false);
assert.equal(gl.deletedFramebuffers.length, 2);
assert.equal(gl.deletedTextures.length, 4);
console.log("通过：同尺寸复用，尺寸变化释放旧附件，重复释放安全");

const fallback = makeContext();
fallback.map3d.gl.complete = false;
fallback.map3DBeginScene();
assert.equal(fallback.map3d.scene, null);
assert.equal(fallback.map3d.gl.drawFramebuffer, null);
assert.equal(fallback.map3d.gl.deletedTextures.length, 2);
fallback.map3DCaptureScene();
assert.equal(fallback.map3d.sceneReady, false);
assert.ok(!fallback.map3d.gl.events.some((event) => event.name === "blit"));
console.log("通过：不完整帧缓冲释放附件并回退默认缓冲");

const empty = makeContext();
empty.drawMap3D();
const events = empty.map3d.gl.events;
const clearIndex = events.findIndex((event) => event.name === "clear");
const captureIndex = events.findIndex((event) => event.name === "blit");
const colorBinding = events.findIndex((event) => event.name === "texture" && event.slot === 4 && event.texture);
assert.ok(clearIndex >= 0 && captureIndex > clearIndex && colorBinding > captureIndex);
assert.equal(events[clearIndex].draw, empty.map3d.scene.framebuffer);
assert.equal(empty.map3d.gl.drawFramebuffer, null);
console.log("通过：空场景使用真实绘制流程先绘制离屏背景再捕获显示");
