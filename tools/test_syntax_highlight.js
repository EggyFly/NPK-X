"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../web/syntax_highlight.js"), "utf8");

function load() {
  const context = { window: {} };
  vm.runInNewContext(source, context, { filename: "syntax_highlight.js" });
  return context.window.SyntaxHighlight;
}

function cost(fn) {
  const start = process.hrtime.bigint();
  const value = fn();
  return { value, ms: Number(process.hrtime.bigint() - start) / 1e6 };
}

function fnv(text) {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

let tests = 0;
function check(name, fn) {
  fn();
  tests += 1;
  console.log(`通过：${name}`);
}

check("XML 预览不会输出原始 script 标签", () => {
  const api = load();
  const html = api.render('<script>alert(1)</script>', "asset/test.xml");
  assert.ok(html);
  assert.ok(!html.includes("<script"));
  assert.ok(html.includes("&lt;"));
  assert.ok(html.includes('tok-tag">script'));
});

check("200KB 未闭合块注释在 100ms 内回退", () => {
  const api = load();
  const text = "/*a".repeat(Math.floor(204800 / 3));
  assert.ok(text.length <= api.MAX_CHARS);
  for (const name of ["sample.hlsl", "sample.js", "sample.css", "sample.json"]) {
    for (let round = 0; round < 3; round += 1) {
      const run = cost(() => api.render(text, name));
      assert.equal(run.value, null);
      assert.ok(run.ms < 100, `${name} 第 ${round + 1} 次耗时 ${run.ms.toFixed(2)}ms`);
    }
  }
});

check("同长度不同内容不同路径的缓存互不串内容", () => {
  const api = load();
  const textA = "float4 colorA = 1;";
  const textB = "float4 colorB = 2;";
  assert.equal(textA.length, textB.length);
  const htmlA = api.render(textA, "shader/a.hlsl");
  const htmlB = api.render(textB, "shader/b.hlsl");
  assert.ok(htmlA.includes("colorA"));
  assert.ok(htmlB.includes("colorB"));
  assert.ok(!htmlA.includes("colorB"));
  assert.ok(!htmlB.includes("colorA"));
  assert.notEqual(htmlA, htmlB);
  assert.equal(api.render(textA, "shader/a.hlsl"), htmlA);
  const hitA = "wzenchkj";
  const hitB = "sfipebab";
  assert.equal(hitA.length, hitB.length);
  assert.equal(fnv(hitA), fnv(hitB));
  const htmlHitA = api.render(hitA, "shader/hit-a.hlsl");
  const htmlHitB = api.render(hitB, "shader/hit-b.hlsl");
  assert.ok(htmlHitA.includes(hitA));
  assert.ok(htmlHitB.includes(hitB));
  assert.ok(!htmlHitA.includes(hitB));
  assert.ok(!htmlHitB.includes(hitA));
});

check("未知扩展名返回 null", () => {
  const api = load();
  assert.equal(api.render("just plain words here", "asset/unknown.zzz"), null);
});

check("字符串和行注释里的 /* 不触发回退", () => {
  const api = load();
  const js = api.render('const p = "src/*";\nconst q = 1;\n', "app.js");
  assert.ok(js.includes('<span class="tok-kw">const</span>'));
  const hlsl = api.render("// /*\nfloat4 main() {}", "shader/line.hlsl");
  assert.ok(hlsl.includes('<span class="tok-com">// /*</span>'));
});

check("Python 关键字与函数名正常着色", () => {
  const api = load();
  const html = api.render("def foo():\n    return True\n", "python");
  assert.ok(html.includes('<span class="tok-kw">def</span>'));
  assert.ok(html.includes('<span class="tok-fn">foo</span>'));
  assert.ok(html.includes('<span class="tok-lit">True</span>'));
});

check("JSON 键名与字面量正常着色", () => {
  const api = load();
  const html = api.render('{"name": true}', "json");
  assert.ok(html.includes('<span class="tok-attr">&quot;name&quot;</span>'));
  assert.ok(html.includes('<span class="tok-lit">true</span>'));
});

check("HLSL 类型、函数名与关键字正常着色", () => {
  const api = load();
  const html = api.render("float4 main() : SV_Target {\n  return 1.0;\n}", "shader/sample.hlsl");
  assert.ok(html.includes('<span class="tok-type">float4</span>'));
  assert.ok(html.includes('<span class="tok-fn">main</span>'));
  assert.ok(html.includes('<span class="tok-kw">return</span>'));
  const comment = api.render("/* note */\nfloat4 main() {}", "shader/comment.hlsl");
  assert.ok(comment.includes('<span class="tok-com">/* note */</span>'));
});

console.log(`全部通过：${tests} 项`);
