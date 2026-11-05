(() => {
  "use strict";

  const MAX_CHARS = 200 * 1024;
  const CACHE_MAX = 8;
  const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (value) => String(value).replace(/[&<>"']/g, (char) => ESCAPES[char]);
  const span = (type, value) => `<span class="tok-${type}">${esc(value)}</span>`;
  const cache = new Map();

  const EXT_LANG = {
    ".py": "python", ".pyw": "python", ".xn": "python",
    ".json": "json", ".jsonc": "json",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "javascript", ".tsx": "javascript",
    ".hlsl": "hlsl", ".glsl": "hlsl", ".vert": "hlsl", ".frag": "hlsl",
    ".lua": "lua",
    ".xml": "xml", ".html": "xml", ".htm": "xml", ".xhtml": "xml", ".svg": "xml", ".plist": "xml",
    ".css": "css",
  };
  const PLAIN_EXT = new Set([".md", ".markdown", ".csv", ".tsv", ".ini", ".conf", ".yaml", ".yml", ".atlas", ".spr", ".lod"]);
  const LANG_KEYS = new Set(["python", "json", "javascript", "hlsl", "lua", "xml", "css"]);
  const BLOCK_COMMENT_LANGS = new Set(["json", "javascript", "hlsl", "css"]);

  const WORDS = {
    python: {
      kw: new Set("and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case".split(" ")),
      lit: new Set("True False None".split(" ")),
      type: new Set("int str float bool list dict set tuple bytes object self cls print len range super isinstance enumerate zip open Exception ValueError TypeError KeyError".split(" ")),
    },
    javascript: {
      kw: new Set("async await break case catch class const continue debugger default delete do else export extends finally for from function get if import in instanceof let new of return set static super switch this throw try typeof var void while with yield".split(" ")),
      lit: new Set("true false null undefined NaN Infinity".split(" ")),
      type: new Set("Array Boolean Date Error Function JSON Map Math Number Object Promise RegExp Set String Symbol WeakMap WeakSet document window console globalThis parseInt parseFloat isNaN setTimeout setInterval clearTimeout clearInterval fetch".split(" ")),
    },
    lua: {
      kw: new Set("and break do else elseif end for function goto if in local not or repeat return then until while".split(" ")),
      lit: new Set("true false nil".split(" ")),
      type: new Set("self print pairs ipairs type tostring tonumber table string math io os coroutine require setmetatable getmetatable rawget rawset pcall error assert".split(" ")),
    },
    hlsl: {
      kw: new Set("attribute break case cbuffer class column_major compile const continue default discard do else enum extern for groupshared if in inline inout interface namespace new out override pass precise private protected public register return row_major sampler shared static struct switch technique template texture typedef typename uniform using varying virtual volatile while".split(" ")),
      lit: new Set("true false".split(" ")),
      type: new Set("bool double dword fixed float half int matrix min10float min12int min16float min16int min16uint string uint vector void sampler1D sampler2D sampler3D samplerCube sampler2DArray".split(" ")),
    },
  };

  function wordsPattern(set) {
    return String.raw`\b(?:${[...set].sort((a, b) => b.length - a.length).join("|")})\b`;
  }

  function makeLexer(rules) {
    const source = rules.map((rule, index) => `(?<t${index}>${rule.pattern})`).join("|");
    return { regex: new RegExp(source, "g"), types: rules.map((rule) => rule.type) };
  }

  function paint(text, lexer) {
    const regex = lexer.regex;
    const types = lexer.types;
    const out = [];
    let last = 0;
    let match;
    regex.lastIndex = 0;
    while ((match = regex.exec(text))) {
      const value = match[0];
      if (!value) {
        regex.lastIndex += 1;
        continue;
      }
      if (match.index > last) out.push(esc(text.slice(last, match.index)));
      let type = types[0];
      for (let i = 0; i < types.length; i += 1) {
        if (match.groups[`t${i}`] !== undefined) {
          type = types[i];
          break;
        }
      }
      out.push(span(type, value));
      last = match.index + value.length;
    }
    if (last < text.length) out.push(esc(text.slice(last)));
    return out.join("");
  }

  const pythonLexer = makeLexer([
    { type: "com", pattern: String.raw`#[^\n]*` },
    { type: "str", pattern: String.raw`"""(?:[\s\S]*?)"""|'''(?:[\s\S]*?)'''|"(?:\\[\s\S]|[^"\\\n])*"|'(?:\\[\s\S]|[^'\\\n])*'` },
    { type: "fn", pattern: String.raw`@[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*` },
    { type: "num", pattern: String.raw`\b(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?[jJ]?)\b` },
    { type: "lit", pattern: wordsPattern(WORDS.python.lit) },
    { type: "kw", pattern: wordsPattern(WORDS.python.kw) },
    { type: "type", pattern: wordsPattern(WORDS.python.type) },
    { type: "fn", pattern: String.raw`\b[A-Za-z_]\w*(?=\s*\()` },
  ]);

  const jsonLexer = makeLexer([
    { type: "com", pattern: String.raw`//[^\n]*|/\*[\s\S]*?\*/` },
    { type: "attr", pattern: String.raw`"(?:\\.|[^"\\])*"(?=\s*:)` },
    { type: "str", pattern: String.raw`"(?:\\.|[^"\\])*"` },
    { type: "num", pattern: String.raw`-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b` },
    { type: "lit", pattern: String.raw`\b(?:true|false|null)\b` },
  ]);

  const javascriptLexer = makeLexer([
    { type: "com", pattern: String.raw`//[^\n]*|/\*[\s\S]*?\*/` },
    { type: "str", pattern: String.raw`\x60(?:\\[\s\S]|[^\x60\\])*\x60|"(?:\\[\s\S]|[^"\\\n])*"|'(?:\\[\s\S]|[^'\\\n])*'` },
    { type: "num", pattern: String.raw`\b(?:0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?n?)\b` },
    { type: "lit", pattern: wordsPattern(WORDS.javascript.lit) },
    { type: "kw", pattern: wordsPattern(WORDS.javascript.kw) },
    { type: "type", pattern: wordsPattern(WORDS.javascript.type) },
    { type: "fn", pattern: String.raw`\b[A-Za-z_$][\w$]*(?=\s*\()` },
  ]);

  const luaLexer = makeLexer([
    { type: "com", pattern: String.raw`--\[\[[\s\S]*?\]\]|--[^\n]*` },
    { type: "str", pattern: String.raw`\[\[[\s\S]*?\]\]|"(?:\\[\s\S]|[^"\\\n])*"|'(?:\\[\s\S]|[^'\\\n])*'` },
    { type: "num", pattern: String.raw`\b(?:0[xX][0-9a-fA-F]+(?:\.[0-9a-fA-F]*)?(?:[pP][+-]?\d+)?|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)\b` },
    { type: "lit", pattern: wordsPattern(WORDS.lua.lit) },
    { type: "kw", pattern: wordsPattern(WORDS.lua.kw) },
    { type: "type", pattern: wordsPattern(WORDS.lua.type) },
    { type: "fn", pattern: String.raw`\b[A-Za-z_]\w*(?=\s*\()` },
  ]);

  const hlslLexer = makeLexer([
    { type: "com", pattern: String.raw`//[^\n]*|/\*[\s\S]*?\*/` },
    { type: "pre", pattern: String.raw`#[A-Za-z_][^\n]*` },
    { type: "str", pattern: String.raw`"(?:\\[\s\S]|[^"\\\n])*"` },
    { type: "num", pattern: String.raw`\b(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)[fFhHuUlL]*\b` },
    { type: "lit", pattern: wordsPattern(WORDS.hlsl.lit) },
    { type: "type", pattern: wordsPattern(WORDS.hlsl.type) },
    { type: "type", pattern: String.raw`\b(?:float|half|int|uint|fixed|bool|min16float|min16int|min16uint|vec|ivec|bvec|uvec|mat|dmat)[234](?:x[234])?\b` },
    { type: "type", pattern: String.raw`\b(?:RW)?(?:Texture|Buffer|StructuredBuffer|ByteAddressBuffer)[0-9A-Za-z]*\b` },
    { type: "kw", pattern: wordsPattern(WORDS.hlsl.kw) },
    { type: "fn", pattern: String.raw`\b[A-Za-z_]\w*(?=\s*\()` },
  ]);

  const cssLexer = makeLexer([
    { type: "com", pattern: String.raw`/\*[\s\S]*?\*/` },
    { type: "str", pattern: String.raw`"(?:\\[\s\S]|[^"\\\n])*"|'(?:\\[\s\S]|[^'\\\n])*'` },
    { type: "kw", pattern: String.raw`@[a-zA-Z-]+` },
    { type: "prop", pattern: String.raw`--[A-Za-z_][\w-]*` },
    { type: "num", pattern: String.raw`#[0-9a-fA-F]{3,8}\b` },
    { type: "num", pattern: String.raw`\b\d+(?:\.\d+)?(?:%|[a-zA-Z]+)?\b` },
    { type: "fn", pattern: String.raw`[-a-zA-Z_][\w-]*(?=\()` },
    { type: "lit", pattern: String.raw`!\s*important\b` },
    { type: "prop", pattern: String.raw`[-\w]+(?=\s*:)` },
    { type: "sel", pattern: String.raw`[.#][-\w]+` },
    { type: "kw", pattern: String.raw`:(?:hover|focus|active|visited|link|first-child|last-child|nth-child|before|after|root|not|is|where)\b` },
    { type: "lit", pattern: String.raw`\b(?:none|auto|inherit|initial|unset|revert|block|inline|flex|grid|absolute|relative|fixed|sticky|transparent|currentColor)\b` },
  ]);

  const lexers = {
    python: pythonLexer,
    json: jsonLexer,
    javascript: javascriptLexer,
    lua: luaLexer,
    hlsl: hlslLexer,
    css: cssLexer,
  };

  function findTagEnd(text, start) {
    let quote = "";
    for (let i = start; i < text.length; i += 1) {
      const char = text[i];
      if (quote) {
        if (char === quote) quote = "";
        continue;
      }
      if (char === '"' || char === "'") {
        quote = char;
        continue;
      }
      if (char === ">") return i;
    }
    return -1;
  }

  function findQuoteEnd(text, start, quote, limit) {
    for (let i = start; i < limit; i += 1) {
      if (text[i] === quote) return i;
    }
    return -1;
  }

  function tagParts(text, start, end) {
    const out = [esc("<")];
    let i = start;
    if (text[i] === "/") {
      out.push(esc("/"));
      i += 1;
    }
    if (text[i] === "!" || text[i] === "?") {
      out.push(span("pre", text.slice(i, end)));
      out.push(esc(">"));
      return out;
    }
    const name = /^[A-Za-z_][\w:.-]*/.exec(text.slice(i, end));
    if (!name) {
      out.push(esc(text.slice(i, end)));
      out.push(esc(">"));
      return out;
    }
    out.push(span("tag", name[0]));
    i += name[0].length;
    while (i < end) {
      const char = text[i];
      if (/\s/.test(char)) {
        out.push(esc(char));
        i += 1;
        continue;
      }
      if (char === "/" || char === "=") {
        out.push(esc(char));
        i += 1;
        continue;
      }
      if (char === '"' || char === "'") {
        const close = findQuoteEnd(text, i + 1, char, end);
        const stop = close < 0 ? end : close + 1;
        out.push(span("str", text.slice(i, stop)));
        i = stop;
        continue;
      }
      const attr = /^[^\s=/>]+/.exec(text.slice(i, end));
      if (!attr) break;
      out.push(span("attr", attr[0]));
      i += attr[0].length;
    }
    out.push(esc(">"));
    return out;
  }

  function paintXml(text) {
    const out = [];
    let i = 0;
    while (i < text.length) {
      if (text.startsWith("<!--", i)) {
        const end = text.indexOf("-->", i + 4);
        const stop = end < 0 ? text.length : end + 3;
        out.push(span("com", text.slice(i, stop)));
        i = stop;
        continue;
      }
      if (text.startsWith("<![CDATA[", i)) {
        const end = text.indexOf("]]>", i + 9);
        const stop = end < 0 ? text.length : end + 3;
        out.push(span("str", text.slice(i, stop)));
        i = stop;
        continue;
      }
      if (text[i] !== "<") {
        const next = text.indexOf("<", i);
        const stop = next < 0 ? text.length : next;
        out.push(esc(text.slice(i, stop)));
        i = stop;
        continue;
      }
      const end = findTagEnd(text, i + 1);
      if (end < 0) {
        out.push(esc(text.slice(i)));
        break;
      }
      const parts = tagParts(text, i + 1, end);
      for (const part of parts) out.push(part);
      i = end + 1;
    }
    return out.join("");
  }

  function extOf(path) {
    const name = String(path || "").split("?")[0].split("/").pop();
    const dot = name.lastIndexOf(".");
    return dot < 0 ? "" : name.slice(dot).toLowerCase();
  }

  function langFor(path, text) {
    const ext = extOf(path);
    if (EXT_LANG[ext]) return EXT_LANG[ext];
    if (PLAIN_EXT.has(ext)) return "";
    const head = text.slice(0, 600).replace(/^\s+/, "");
    if (!head) return "";
    if (/^<\?xml\b|^<!DOCTYPE\b|^<[A-Za-z][\w:.-]*(?:\s|>|\/)/i.test(head)) return "xml";
    if (/^[\[{]/.test(head) && /"\s*:/.test(head)) return "json";
    if (/^(?:async\s+)?(?:def|class|import|from)\s+[A-Za-z_]/m.test(head)) return "python";
    if (/\b(?:function|const|let|var)\b|=>/.test(head)) return "javascript";
    return "";
  }

  function hashText(text) {
    let hash = 2166136261;
    for (let i = 0; i < text.length; i += 1) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function hasOpenBlockComment(text, lang) {
    let quote = "";
    for (let i = 0; i < text.length; i += 1) {
      const char = text[i];
      if (quote) {
        if (char === "\\") {
          i += 1;
          continue;
        }
        if (char === quote || (quote !== "`" && char === "\n")) quote = "";
        continue;
      }
      if (char === '"' || char === "'" || (lang === "javascript" && char === "`")) {
        quote = char;
        continue;
      }
      if (lang !== "css" && char === "/" && text[i + 1] === "/") {
        const end = text.indexOf("\n", i + 2);
        if (end < 0) return false;
        i = end;
        continue;
      }
      if (char === "/" && text[i + 1] === "*") {
        const end = text.indexOf("*/", i + 2);
        if (end < 0) return true;
        i = end + 1;
      }
    }
    return false;
  }

  function render(text, pathOrLang) {
    if (typeof text !== "string" || !text) return null;
    // 超过上限不做词法解析, 直接交给调用方按纯文本显示, 避免大文件卡住滚动
    if (text.length > MAX_CHARS) return null;
    const fromLang = LANG_KEYS.has(pathOrLang);
    const lang = fromLang ? pathOrLang : langFor(pathOrLang, text);
    if (!lang) return null;
    // 未闭合块注释会让惰性正则一路回溯到文末, 这里先用 indexOf 线性预检
    if (BLOCK_COMMENT_LANGS.has(lang) && hasOpenBlockComment(text, lang)) return null;
    const key = fromLang
      ? `lang:${lang}:${text.length}:${hashText(text)}`
      : `${pathOrLang}:${lang}:${text.length}:${hashText(text)}`;
    const hit = cache.get(key);
    if (hit !== undefined) {
      cache.delete(key);
      cache.set(key, hit);
      return hit;
    }
    // 词法异常时退回纯文本, 不能因为高亮失败让整个预览报错
    let html;
    try {
      html = lang === "xml" ? paintXml(text) : paint(text, lexers[lang]);
    } catch (_) {
      return null;
    }
    cache.set(key, html);
    while (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value);
    return html;
  }

  window.SyntaxHighlight = {
    MAX_CHARS,
    detect: langFor,
    render,
  };
})();
