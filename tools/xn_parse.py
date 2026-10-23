"""Minimal Python 2.7 marshal reader for decrypted XN scripts."""

import struct

FLAG_REF = 0x80


class Code:
    __slots__ = ("name", "filename", "names", "consts", "code", "children", "argcount", "varnames", "firstlineno")

    def __init__(self, name, filename, names, consts, code, children, argcount=0, varnames=(), firstlineno=0):
        self.name = name
        self.filename = filename
        self.names = names
        self.consts = consts
        self.code = code
        self.children = children
        self.argcount = argcount
        self.varnames = varnames
        self.firstlineno = firstlineno


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
            while True:
                key = self.object()
                if key is None and self.pos < len(self.data) and self.data[self.pos - 1] == 0:
                    break
                result[key] = self.object()
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
        self.object()
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
