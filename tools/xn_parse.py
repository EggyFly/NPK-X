"""Minimal Python 2.7 marshal reader for decrypted XN scripts."""

import json
import struct
from pathlib import Path

FLAG_REF = 0x80


class Code:
    __slots__ = ("name", "filename", "names", "consts", "code", "children", "argcount", "varnames", "firstlineno", "lnotab")

    def __init__(self, name, filename, names, consts, code, children, argcount=0, varnames=(), firstlineno=0, lnotab=b""):
        self.name = name
        self.filename = filename
        self.names = names
        self.consts = consts
        self.code = code
        self.children = children
        self.argcount = argcount
        self.varnames = varnames
        self.firstlineno = firstlineno
        self.lnotab = lnotab


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.refs = []
        self.children = []

    def take(self, size: int) -> bytes:
        chunk = self.data[self.pos:self.pos + size]
        if len(chunk) != size:
            raise ValueError("truncated marshal data")
        self.pos += size
        return chunk

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def peek(self) -> str:
        if self.pos >= len(self.data):
            return ""
        return chr(self.data[self.pos] & 0x7F)

    def string(self, size: int) -> bytes:
        return self.take(size)

    def long(self):
        count = self.i32()
        sign = -1 if count < 0 else 1
        digits = [struct.unpack("<H", self.take(2))[0] for _ in range(abs(count))]
        value = 0
        for digit in reversed(digits):
            value = (value << 15) | digit
        return sign * value

    def object(self):
        flag = self.take(1)[0]
        kind = chr(flag & 0x7F)
        ref_index = None
        if flag & FLAG_REF:
            ref_index = len(self.refs)
            self.refs.append(None)
        result = None
        if kind == "0":
            result = None
        elif kind == "N":
            result = None
        elif kind == "F":
            result = False
        elif kind == "T":
            result = True
        elif kind == "i":
            result = self.i32()
        elif kind == "I":
            result = struct.unpack("<q", self.take(8))[0]
        elif kind == "l":
            result = self.long()
        elif kind == "f":
            result = float(self.take(self.take(1)[0]).decode("ascii"))
        elif kind == "g":
            result = struct.unpack("<d", self.take(8))[0]
        elif kind in ("s", "t", "u", "a", "A"):
            raw = self.string(self.u32())
            result = raw.decode("utf-8", "replace") if kind == "u" else raw
        elif kind in ("z", "Z"):
            raw = self.string(self.take(1)[0])
            result = raw.decode("latin1", "replace")
        elif kind == "R":
            index = self.u32()
            result = self.refs[index] if index < len(self.refs) else None
        elif kind == "r":
            index = self.u32()
            result = self.refs[index] if index < len(self.refs) else None
        elif kind == "(":
            count = self.u32()
            result = tuple(self.object() for _ in range(count))
        elif kind == ")":
            count = self.take(1)[0]
            result = tuple(self.object() for _ in range(count))
        elif kind == "[":
            count = self.u32()
            result = [self.object() for _ in range(count)]
        elif kind == "{":
            result = {}
            while self.peek() != "0":
                key = self.object()
                result[key] = self.object()
            self.object()
        elif kind == "<":
            count = self.u32()
            result = {self.object() for _ in range(count)}
        elif kind == ">":
            count = self.u32()
            result = frozenset(self.object() for _ in range(count))
        elif kind == "c":
            result = self.code_object()
        else:
            raise ValueError("unsupported marshal type %r" % kind)
        if ref_index is not None:
            self.refs[ref_index] = result
        elif kind == "t":
            self.refs.append(result)
        return result

    def code_object(self) -> Code:
        argcount = self.i32()
        self.i32()
        self.i32()
        self.i32()
        code = self.object()
        consts = self.object()
        names = self.object()
        varnames = self.object()
        self.object()
        self.object()
        filename = self.object()
        name = self.object()
        firstlineno = self.i32()
        lnotab = self.object()
        children = []
        for const in consts or ():
            if isinstance(const, Code):
                children.append(const)
        return Code(
            text(name),
            text(filename),
            names if isinstance(names, tuple) else (),
            consts if isinstance(consts, tuple) else (),
            code if isinstance(code, bytes) else b"",
            children,
            argcount,
            varnames if isinstance(varnames, tuple) else (),
            firstlineno,
            lnotab if isinstance(lnotab, bytes) else b"",
        )


def load(plain: bytes):
    reader = Reader(plain)
    return reader.object()


def text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


def walk(code, depth=0, limit=64):
    if not isinstance(code, Code) or depth > limit:
        return
    yield code
    for child in code.children:
        yield from walk(child, depth + 1, limit)


def preview(plain: bytes, string_limit: int = 600) -> dict:
    root = load(plain)
    if not isinstance(root, Code):
        raise ValueError("not a python marshal code object")
    names = []
    strings = []
    functions = []
    seen_names = set()
    seen_strings = set()
    objects = 0
    for code in walk(root):
        objects += 1
        if code is not root and code.name and not code.name.startswith("<"):
            argcount = max(0, min(code.argcount, len(code.varnames)))
            functions.append({
                "name": code.name,
                "args": [text(value) for value in code.varnames[:argcount]],
                "line": code.firstlineno,
                "bytes": len(code.code),
            })
        for name in code.names:
            value = text(name)
            if value and value not in seen_names:
                seen_names.add(value)
                names.append(value)
        for const in code.consts:
            if isinstance(const, (bytes, str)):
                value = text(const)
                if 2 <= len(value) <= 240 and value not in seen_strings:
                    seen_strings.add(value)
                    strings.append(value)
    return {
        "code_name": root.name,
        "filename": root.filename,
        "code_objects": objects,
        "names": names[:1200],
        "strings": strings[:string_limit],
        "functions": functions[:300],
        "function_count": len(functions),
        "bytecode_bytes": len(root.code),
        "name_count": len(names),
        "string_count": len(strings),
    }


def candidates(plain: bytes, limit: int = 6000) -> set[str]:
    root = load(plain)
    if not isinstance(root, Code):
        return set()
    found = set()
    for code in walk(root):
        for name in code.names:
            value = text(name)
            if 2 <= len(value) <= 90:
                found.add(value)
        for const in code.consts:
            if isinstance(const, (bytes, str)):
                value = text(const)
                if 2 <= len(value) <= 90:
                    found.add(value)
        if len(found) >= limit:
            break
    return found


_OPMAP_CACHE = None
HAVE_ARGUMENT = 90
EXTRA_ARG = 131
FUSED_FAST_CONST = 0xAD


def opmap():
    global _OPMAP_CACHE
    if _OPMAP_CACHE is None:
        base, opext, std = {}, {}, {}
        try:
            path = Path(__file__).resolve().parent.parent / "data" / "xn_opmap.json"
            raw = json.loads(path.read_text("utf-8"))
            std = raw.get("std", {})
            base = {int(key): value for key, value in raw.get("base", {}).items()}
            opext = {int(key): tuple(value) for key, value in raw.get("opext", {}).items()}
        except (OSError, ValueError, TypeError):
            pass
        _OPMAP_CACHE = (base, opext, std)
    return _OPMAP_CACHE


def lnotab_map(code: Code) -> dict:
    result = {}
    if not code.lnotab:
        return result
    address = 0
    line = code.firstlineno
    index = 0
    while index + 1 < len(code.lnotab):
        address += code.lnotab[index]
        delta = code.lnotab[index + 1]
        if delta >= 128:
            delta -= 256
        line += delta
        result[address] = line
        index += 2
    return result


def _annotate(code: Code, name: str, arg) -> str:
    if arg is None:
        return ""
    if name == "LOAD_CONST" and arg < len(code.consts):
        value = code.consts[arg]
        if isinstance(value, Code):
            return "  # <code %s>" % value.name
        if isinstance(value, bytes):
            return "  # %r" % value[:80]
        return "  # %r" % (value,)
    if name in ("LOAD_FAST", "STORE_FAST", "DELETE_FAST") and arg < len(code.varnames):
        return "  # %s" % text(code.varnames[arg])
    if name in ("LOAD_GLOBAL", "LOAD_NAME", "LOAD_ATTR", "STORE_NAME", "STORE_ATTR", "STORE_GLOBAL", "DELETE_ATTR") and arg < len(code.names):
        return "  # %s" % text(code.names[arg])
    if name == "COMPARE_OP":
        table = ["<", "<=", "==", "!=", ">", ">=", "in", "not in", "is", "is not", "exception match", "BAD"]
        if arg < len(table):
            return "  # %s" % table[arg]
    return ""


def _read_ops(code: Code):
    base, opext, std = opmap()
    data = code.code
    position = 0
    pending = 0
    result = []
    while position < len(data):
        start = position
        raw = data[position]
        position += 1
        if raw == EXTRA_ARG:
            arg = struct.unpack_from("<H", data, position)[0] if position + 2 <= len(data) else 0
            result.append({"offset": start, "op": "OPEXT_EXTRA_ARGUMENT", "arg": arg, "expanded": []})
            position += 2
            continue
        if raw == 126:
            arg = struct.unpack_from("<H", data, position)[0] if position + 2 <= len(data) else 0
            pending = arg
            result.append({"offset": start, "op": "EXTENDED_ARG", "arg": arg, "expanded": []})
            position += 2
            continue
        if raw == FUSED_FAST_CONST:
            arg = struct.unpack_from("<H", data, position)[0] if position + 2 <= len(data) else 0
            if pending:
                arg |= pending << 16
                pending = 0
            result.append({
                "offset": start,
                "op": "FUSED_LOAD_FAST_0_LOAD_CONST",
                "arg": arg,
                "expanded": [["LOAD_FAST", 0], ["LOAD_CONST", arg]],
            })
            position += 2
            continue
        if raw in base:
            name = base[raw]
            arg = None
            if std.get(name, 0) >= HAVE_ARGUMENT:
                arg = struct.unpack_from("<H", data, position)[0] if position + 2 <= len(data) else 0
                if pending:
                    arg |= pending << 16
                    pending = 0
                position += 2
            result.append({"offset": start, "op": name, "arg": arg, "expanded": []})
            continue
        if raw in opext:
            names = opext[raw]
            expanded = []
            first = True
            broken = False
            for name in names:
                arg = None
                if std.get(name, 0) >= HAVE_ARGUMENT:
                    if first:
                        arg = struct.unpack_from("<H", data, position)[0] if position + 2 <= len(data) else 0
                        if pending:
                            arg |= pending << 16
                            pending = 0
                        position += 2
                        first = False
                    else:
                        marker = data[position] if position < len(data) else 0
                        if marker != EXTRA_ARG:
                            broken = True
                            break
                        position += 1
                        arg = struct.unpack_from("<H", data, position)[0] if position + 2 <= len(data) else 0
                        position += 2
                expanded.append([name, arg])
            result.append({
                "offset": start,
                "op": ("BROKEN_" if broken else "") + "OPEXT",
                "arg": None,
                "expanded": expanded,
            })
            continue
        result.append({"offset": start, "op": "UNKNOWN_%02X" % raw, "arg": None, "expanded": []})
    return result


def disassemble(code: Code, limit: int = 1200) -> list:
    lines = lnotab_map(code)
    current = code.firstlineno
    output = []
    for item in _read_ops(code)[:limit]:
        current = lines.get(item["offset"], current)
        item["line"] = current
        item["note"] = _annotate(code, item["op"], item["arg"])
        if item["expanded"]:
            item["expanded_text"] = " ; " + " ".join(
                "%s(%s)" % (name, arg if arg is not None else "") for name, arg in item["expanded"]
            )
        else:
            item["expanded_text"] = ""
        output.append(item)
    return output


def find_code(root: Code, name: str):
    for code in walk(root):
        if code.name == name:
            return code
    for code in walk(root):
        if name and name in code.name:
            return code
    return None


def disasm_text(plain: bytes, func: str = "", limit: int = 1200) -> str:
    root = load(plain)
    if not isinstance(root, Code):
        raise ValueError("not a python marshal code object")
    code = find_code(root, func) if func else root
    if code is None:
        raise ValueError("function not found: %s" % func)
    items = disassemble(code, limit)
    header = "# %s  args=%s  line=%s  code_bytes=%s" % (
        code.name,
        ", ".join(text(value) for value in code.varnames[:code.argcount]),
        code.firstlineno,
        len(code.code),
    )
    lines = [header]
    for item in items:
        arg = "" if item["arg"] is None else str(item["arg"])
        lines.append(
            "%5d %5d  %-38s %6s%s%s"
            % (item["offset"], item["line"], item["op"], arg, item["note"], item["expanded_text"])
        )
    return "\n".join(lines)
