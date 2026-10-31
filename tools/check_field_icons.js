"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../web/field_icons.js"), "utf8");

function run(rules, enabled = true) {
  const nodes = [];
  const context = {
    window: {},
    localStorage: { getItem: () => JSON.stringify({ version: 1, enabled, rules }) },
    document: {
      readyState: "loading", addEventListener() {}, querySelectorAll: () => nodes,
      createElement() {
        return { addEventListener() {}, getAttribute(name) { return this[name]; }, remove() { this.host.children = []; } };
      },
    },
  };
  vm.runInNewContext(source, context, { filename: "field_icons.js" });
  function label(kind, values) {
    const node = {
      dataset: { fieldIconKind: kind, fieldIconValues: JSON.stringify(values) }, children: [],
      querySelector() { return this.children[0]; },
      prepend(image) { image.host = this; this.children.unshift(image); },
    };
    nodes.push(node);
    context.window.FieldIcons.refresh();
    return node.children[0];
  }
  return { label, api: context.window.FieldIcons };
}

const rule = (target, text, source = "props/test.kta", match = "exact") => ({ target, text, source, match, size: 24, enabled: true });
let tests = 0;
function check(name, fn) { fn(); tests += 1; console.log(`通过：${name}`); }

check("字段匹配不区分大小写，保留正确贴图路径和尺寸", () => {
  const app = run([rule("field", "Mesh")]);
  const image = app.label("field", ["mesh"]);
  assert.equal(image.src, "/api/image?path=props%2Ftest.kta");
  assert.equal(image.width, 24);
  assert.equal(app.label("object", ["Mesh"]), undefined);
});
check("组件可使用编号和模型路径别名匹配", () => {
  assert.ok(run([rule("component", "100001")]).label("component", ["小地板", 100001]));
  assert.ok(run([rule("component", "ground/", "props/test.kta", "contains")]).label("component", ["小地板", "props/ground/test.gim"]));
});
check("按第一条启用规则匹配，支持全局停用", () => {
  const rules = [{ ...rule("all", "Mesh", "one.png"), enabled: false }, rule("all", "Mesh", "two.png"), rule("field", "Mesh", "three.png")];
  assert.equal(run(rules).label("field", ["Mesh"]).src, "/api/image?path=two.png");
  assert.equal(run(rules, false).label("field", ["Mesh"]), undefined);
});
check("上传图可从存储恢复，非法路径不生成图片", () => {
  const data = "data:image/png;base64,aGVsbG8=";
  assert.equal(run([rule("field", "Mesh", data)]).label("field", ["Mesh"]).src, data);
  for (const bad of ["javascript:alert(1)", "https://example.com/a.png", "../private.png", "file:///C:/a.png", "data:image/svg+xml;base64,PHN2Zz4="]) {
    assert.equal(run([rule("field", "Mesh", bad)]).label("field", ["Mesh"]), undefined);
  }
});
check("标签和别名中的 HTML 只能显示为文本", () => {
  const html = run([]).api.label("field", '<img src=x onerror=alert(1)>', ['" onmouseover=alert(1)']);
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img"));
  assert.ok(html.includes("&quot;"));
});
console.log(`字段贴图 ${tests} 项回归全部通过。`);
