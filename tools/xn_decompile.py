#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NeoX custom Python 2.7 bytecode -> standard CPython 2.7 pyc.

Usage:
    python converter.py input.xn output.pyc [--salt module_name]
    python converter.py input.plain output.pyc

The custom opcode table was recovered from the game's own opcode.py
module (signature 0xDC830DF0, salt "opcode") in script.npk.  The table
is embedded below so this script is standalone.
"""

import argparse
import bisect
import os
import struct
import sys

MAGIC_27 = b"\x03\xf3\x0d\x0a"
HAVE_ARGUMENT = 90
EXTRA_ARG = 0x83

STD = {
    "STOP_CODE": 0, "POP_TOP": 1, "ROT_TWO": 2, "ROT_THREE": 3, "DUP_TOP": 4,
    "ROT_FOUR": 5, "NOP": 9, "UNARY_POSITIVE": 10, "UNARY_NEGATIVE": 11,
    "UNARY_NOT": 12, "UNARY_CONVERT": 13, "UNARY_INVERT": 15,
    "BINARY_POWER": 19, "BINARY_MULTIPLY": 20, "BINARY_DIVIDE": 21,
    "BINARY_MODULO": 22, "BINARY_ADD": 23, "BINARY_SUBTRACT": 24,
    "BINARY_SUBSCR": 25, "BINARY_FLOOR_DIVIDE": 26, "BINARY_TRUE_DIVIDE": 27,
    "INPLACE_FLOOR_DIVIDE": 28, "INPLACE_TRUE_DIVIDE": 29,
    "SLICE+0": 30, "SLICE+1": 31, "SLICE+2": 32, "SLICE+3": 33,
    "STORE_SLICE+0": 40, "STORE_SLICE+1": 41, "STORE_SLICE+2": 42, "STORE_SLICE+3": 43,
    "DELETE_SLICE+0": 50, "DELETE_SLICE+1": 51, "DELETE_SLICE+2": 52, "DELETE_SLICE+3": 53,
    "STORE_MAP": 54, "INPLACE_ADD": 55, "INPLACE_SUBTRACT": 56,
    "INPLACE_MULTIPLY": 57, "INPLACE_DIVIDE": 58, "INPLACE_MODULO": 59,
    "STORE_SUBSCR": 60, "DELETE_SUBSCR": 61, "BINARY_LSHIFT": 62,
    "BINARY_RSHIFT": 63, "BINARY_AND": 64, "BINARY_XOR": 65, "BINARY_OR": 66,
    "INPLACE_POWER": 67, "GET_ITER": 68, "PRINT_EXPR": 70, "PRINT_ITEM": 71,
    "PRINT_NEWLINE": 72, "PRINT_ITEM_TO": 73, "PRINT_NEWLINE_TO": 74,
    "INPLACE_LSHIFT": 75, "INPLACE_RSHIFT": 76, "INPLACE_AND": 77,
    "INPLACE_XOR": 78, "INPLACE_OR": 79, "BREAK_LOOP": 80, "WITH_CLEANUP": 81,
    "LOAD_LOCALS": 82, "RETURN_VALUE": 83, "IMPORT_STAR": 84, "EXEC_STMT": 85,
    "YIELD_VALUE": 86, "POP_BLOCK": 87, "END_FINALLY": 88, "BUILD_CLASS": 89,
    "STORE_NAME": 90, "DELETE_NAME": 91, "UNPACK_SEQUENCE": 92, "FOR_ITER": 93,
    "LIST_APPEND": 94, "STORE_ATTR": 95, "DELETE_ATTR": 96, "STORE_GLOBAL": 97,
    "DELETE_GLOBAL": 98, "DUP_TOPX": 99, "LOAD_CONST": 100, "LOAD_NAME": 101,
    "BUILD_TUPLE": 102, "BUILD_LIST": 103, "BUILD_SET": 104, "BUILD_MAP": 105,
    "LOAD_ATTR": 106, "COMPARE_OP": 107, "IMPORT_NAME": 108, "IMPORT_FROM": 109,
    "JUMP_FORWARD": 110, "JUMP_IF_FALSE_OR_POP": 111, "JUMP_IF_TRUE_OR_POP": 112,
    "JUMP_ABSOLUTE": 113, "POP_JUMP_IF_FALSE": 114, "POP_JUMP_IF_TRUE": 115,
    "LOAD_GLOBAL": 116, "CONTINUE_LOOP": 119, "SETUP_LOOP": 120,
    "SETUP_EXCEPT": 121, "SETUP_FINALLY": 122, "LOAD_FAST": 124,
    "STORE_FAST": 125, "DELETE_FAST": 126, "RAISE_VARARGS": 130,
    "CALL_FUNCTION": 131, "MAKE_FUNCTION": 132, "BUILD_SLICE": 133,
    "MAKE_CLOSURE": 134, "LOAD_CLOSURE": 135, "LOAD_DEREF": 136,
    "STORE_DEREF": 137, "CALL_FUNCTION_VAR": 140, "CALL_FUNCTION_KW": 141,
    "CALL_FUNCTION_VAR_KW": 142, "SETUP_WITH": 143, "EXTENDED_ARG": 145,
    "SET_ADD": 146, "MAP_ADD": 147,
}

CUSTOM_BASE = {
    1: "POP_TOP", 2: "DELETE_SUBSCR", 3: "INPLACE_SUBTRACT", 5: "BREAK_LOOP",
    7: "INPLACE_FLOOR_DIVIDE", 8: "GET_ITER", 10: "STOP_CODE",
    11: "PRINT_NEWLINE", 12: "INPLACE_MULTIPLY", 13: "IMPORT_STAR",
    14: "UNARY_CONVERT", 15: "BINARY_LSHIFT", 17: "BUILD_CLASS",
    19: "END_FINALLY", 20: "DELETE_SLICE+0", 21: "DELETE_SLICE+1",
    22: "DELETE_SLICE+2", 23: "DELETE_SLICE+3", 24: "BINARY_DIVIDE",
    25: "INPLACE_DIVIDE", 26: "UNARY_INVERT", 27: "ROT_TWO",
    29: "STORE_SLICE+0", 30: "STORE_SLICE+1", 31: "STORE_SLICE+2",
    32: "STORE_SLICE+3", 33: "WITH_CLEANUP", 34: "BINARY_SUBTRACT",
    35: "INPLACE_ADD", 36: "BINARY_FLOOR_DIVIDE", 37: "INPLACE_AND",
    38: "UNARY_NEGATIVE", 39: "POP_BLOCK", 43: "BINARY_RSHIFT",
    44: "UNARY_NOT", 47: "BINARY_SUBSCR", 48: "PRINT_ITEM",
    49: "YIELD_VALUE", 50: "BINARY_TRUE_DIVIDE", 54: "BINARY_XOR",
    56: "INPLACE_XOR", 57: "RETURN_VALUE", 58: "STORE_MAP",
    59: "BINARY_AND", 60: "ROT_FOUR", 61: "STORE_SUBSCR",
    62: "INPLACE_MODULO", 63: "SLICE+0", 64: "SLICE+1", 65: "SLICE+2",
    66: "SLICE+3", 67: "INPLACE_OR", 68: "BINARY_MODULO",
    70: "INPLACE_RSHIFT", 71: "ROT_THREE", 73: "BINARY_ADD",
    74: "INPLACE_POWER", 75: "BINARY_POWER", 77: "EXEC_STMT",
    78: "UNARY_POSITIVE", 79: "BINARY_OR", 81: "BINARY_MULTIPLY",
    82: "INPLACE_TRUE_DIVIDE", 83: "PRINT_EXPR", 84: "LOAD_LOCALS",
    85: "PRINT_ITEM_TO", 86: "NOP", 87: "PRINT_NEWLINE_TO", 88: "DUP_TOP",
    89: "INPLACE_LSHIFT", 90: "DELETE_FAST", 91: "DUP_TOPX",
    94: "SETUP_LOOP", 95: "BUILD_SET", 100: "BUILD_TUPLE",
    101: "BUILD_LIST", 104: "LIST_APPEND", 106: "COMPARE_OP",
    107: "SETUP_WITH", 108: "LOAD_CLOSURE", 110: "IMPORT_FROM",
    115: "MAKE_CLOSURE", 116: "DELETE_ATTR", 117: "LOAD_GLOBAL",
    120: "DELETE_GLOBAL", 123: "UNPACK_SEQUENCE", 126: "EXTENDED_ARG",
    128: "JUMP_IF_FALSE_OR_POP", 133: "POP_JUMP_IF_FALSE",
    135: "BUILD_MAP", 146: "CALL_FUNCTION", 147: "CALL_FUNCTION_VAR",
    148: "CALL_FUNCTION_KW", 149: "CALL_FUNCTION_VAR_KW",
    154: "STORE_DEREF", 156: "POP_JUMP_IF_TRUE", 157: "FOR_ITER",
    158: "SETUP_FINALLY", 159: "SETUP_EXCEPT", 161: "JUMP_FORWARD",
    162: "LOAD_CONST", 163: "LOAD_ATTR", 164: "CONTINUE_LOOP",
    165: "STORE_FAST", 166: "MAKE_FUNCTION", 167: "STORE_ATTR",
    169: "LOAD_DEREF", 170: "STORE_NAME", 174: "LOAD_NAME",
    177: "BUILD_SLICE", 178: "LOAD_FAST", 180: "RAISE_VARARGS",
    181: "STORE_GLOBAL", 182: "SET_ADD", 183: "DELETE_NAME",
    184: "JUMP_IF_TRUE_OR_POP", 187: "IMPORT_NAME", 190: "JUMP_ABSOLUTE",
    191: "MAP_ADD",
}

CUSTOM_OPEXT = {
    40: "OPEXT_POP_TOP_POP_BLOCK",
    41: "OPEXT_BINARY_SUBSCR_RETURN_VALUE",
    42: "OPEXT_POP_TOP_3",
    45: "OPEXT_LOAD_LOCALS_RETURN_VALUE",
    76: "OPEXT_POP_TOP_2",
    92: "OPEXT_POP_TOP_LOAD_CONST",
    93: "OPEXT_LOAD_CONST_LOAD_CONST_BUILD_TUPLE",
    96: "OPEXT_COMPARE_OP_POP_JUMP_IF_FALSE",
    99: "OPEXT_LOAD_CONST_LOAD_CONST_STORE_MAP",
    103: "OPEXT_CALL_FUNCTION_STORE_FAST",
    105: "OPEXT_LOAD_GLOBAL_CALL_FUNCTION",
    109: "OPEXT_LOAD_GLOBAL_LOAD_FAST",
    112: "OPEXT_LOAD_FAST_CALL_FUNCTION",
    113: "OPEXT_LOAD_ATTR_LOAD_GLOBAL",
    114: "OPEXT_LOAD_FAST_CALL_FUNCTION_POP_TOP",
    121: "OPEXT_LOAD_CONST_IMPORT_NAME",
    122: "OPEXT_LOAD_FAST_LOAD_ATTR",
    124: "OPEXT_POP_TOP_LOAD_FAST",
    125: "OPEXT_LOAD_ATTR_LOAD_ATTR",
    127: "OPEXT_LOAD_CONST_COMPARE_OP",
    129: "OPEXT_STORE_FAST_LOAD_FAST",
    130: "OPEXT_LOAD_ATTR_LOAD_FAST",
    134: "OPEXT_MAKE_FUNCTION_STORE_NAME",
    137: "OPEXT_CALL_FUNCTION_POP_TOP_LOAD_FAST",
    141: "OPEXT_CALL_FUNCTION_CALL_FUNCTION",
    142: "OPEXT_LOAD_CONST_RETURN_VALUE",
    143: "OPEXT_POP_TOP_JUMP_FORWARD",
    144: "OPEXT_POP_TOP_LOAD_GLOBAL",
    145: "OPEXT_LOAD_CONST_STORE_MAP",
    150: "OPEXT_LOAD_CONST_BINARY_SUBSCR",
    151: "OPEXT_LOAD_FAST_LOAD_FAST",
    152: "OPEXT_LOAD_CONST_CALL_FUNCTION",
    153: "OPEXT_LOAD_ATTR_CALL_FUNCTION_POP_TOP",
    155: "OPEXT_LOAD_CONST_LOAD_FAST",
    160: "OPEXT_LOAD_FAST_STORE_ATTR",
    168: "OPEXT_CALL_FUNCTION_POP_TOP",
    171: "OPEXT_LOAD_FAST_LOAD_CONST",
    175: "OPEXT_LOAD_ATTR_CALL_FUNCTION",
    176: "OPEXT_LOAD_CONST_MAKE_FUNCTION",
    179: "OPEXT_LOAD_CONST_LOAD_CONST",
    185: "OPEXT_STORE_FAST_LOAD_GLOBAL",
    186: "OPEXT_LOAD_GLOBAL_CALL_FUNCTION_POP_TOP",
    188: "OPEXT_STORE_NAME_LOAD_CONST",
}

REL_JUMPS = {"JUMP_FORWARD", "FOR_ITER", "SETUP_LOOP", "SETUP_EXCEPT",
             "SETUP_FINALLY", "SETUP_WITH"}
ABS_JUMPS = {"JUMP_ABSOLUTE", "POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE",
             "JUMP_IF_FALSE_OR_POP", "JUMP_IF_TRUE_OR_POP", "CONTINUE_LOOP"}
STD_NAMES = sorted(STD, key=len, reverse=True)


class UStr:
    __slots__ = ("raw",)

    def __init__(self, raw):
        self.raw = raw


class CodeObj:
    __slots__ = ("argcount", "nlocals", "stacksize", "flags", "code", "consts",
                 "names", "varnames", "freevars", "cellvars", "filename",
                 "name", "firstlineno", "lnotab")

    def __init__(self, **kw):
        for key in self.__slots__:
            setattr(self, key, kw[key])


class MarshalReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.refs = []

    def take(self, n):
        if self.pos + n > len(self.data):
            raise ValueError("truncated marshal at 0x%x" % self.pos)
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return struct.unpack("<H", self.take(2))[0]

    def i32(self):
        return struct.unpack("<i", self.take(4))[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def obj(self):
        flag = self.u8()
        kind = chr(flag & 0x7f)
        has_ref = bool(flag & 0x80)
        ref_index = None
        if has_ref:
            ref_index = len(self.refs)
            self.refs.append(None)
        if kind in ("0", "N"):
            value = None
        elif kind == "F":
            value = False
        elif kind == "T":
            value = True
        elif kind == "S":
            value = None
        elif kind == "i":
            value = self.i32()
        elif kind == "I":
            value = struct.unpack("<q", self.take(8))[0]
        elif kind == "l":
            value = self.long()
        elif kind == "f":
            value = float(self.take(self.u8()).decode("ascii"))
        elif kind == "g":
            value = struct.unpack("<d", self.take(8))[0]
        elif kind == "x":
            real = float(self.take(self.u8()).decode("ascii"))
            imag = float(self.take(self.u8()).decode("ascii"))
            value = complex(real, imag)
        elif kind == "y":
            value = complex(struct.unpack("<d", self.take(8))[0],
                            struct.unpack("<d", self.take(8))[0])
        elif kind in ("s", "t", "a", "A", "z", "Z"):
            if kind in ("z", "Z"):
                size = self.u8()
            else:
                size = self.u32()
            value = self.take(size)
        elif kind == "u":
            value = UStr(self.take(self.u32()))
        elif kind == "R" or kind == "r":
            index = self.u32()
            value = self.refs[index] if index < len(self.refs) else None
        elif kind == "(":
            value = tuple(self.obj() for _ in range(self.u32()))
        elif kind == ")":
            value = tuple(self.obj() for _ in range(self.u8()))
        elif kind == "[":
            value = [self.obj() for _ in range(self.u32())]
        elif kind == "{":
            value = {}
            while True:
                if self.pos < len(self.data) and self.data[self.pos] == 0:
                    self.pos += 1
                    break
                key = self.obj()
                value[key] = self.obj()
        elif kind == "<":
            value = {self.obj() for _ in range(self.u32())}
        elif kind == ">":
            value = frozenset(self.obj() for _ in range(self.u32()))
        elif kind == "c":
            value = self.code()
        else:
            raise ValueError("unsupported marshal type %r at 0x%x" % (kind, self.pos - 1))
        if ref_index is not None:
            self.refs[ref_index] = value
        elif kind == "t":
            self.refs.append(value)
        return value

    def long(self):
        count = self.i32()
        sign = -1 if count < 0 else 1
        value = 0
        for i in range(abs(count)):
            value |= self.u16() << (15 * i)
        return sign * value

    def code(self):
        argcount = self.i32()
        nlocals = self.i32()
        stacksize = self.i32()
        flags = self.i32()
        code = self.obj()
        consts = self.obj()
        names = self.obj()
        varnames = self.obj()
        freevars = self.obj()
        cellvars = self.obj()
        filename = self.obj()
        name = self.obj()
        firstlineno = self.i32()
        lnotab = self.obj()
        return CodeObj(
            argcount=argcount, nlocals=nlocals, stacksize=stacksize, flags=flags,
            code=code if isinstance(code, bytes) else b"",
            consts=consts if isinstance(consts, tuple) else (),
            names=names if isinstance(names, tuple) else (),
            varnames=varnames if isinstance(varnames, tuple) else (),
            freevars=freevars if isinstance(freevars, tuple) else (),
            cellvars=cellvars if isinstance(cellvars, tuple) else (),
            filename=filename if isinstance(filename, bytes) else b"",
            name=name if isinstance(name, bytes) else b"",
            firstlineno=firstlineno,
            lnotab=lnotab if isinstance(lnotab, bytes) else b"",
        )


class MarshalWriter:
    def __init__(self):
        self.buf = bytearray()

    def dump(self, value):
        self.obj(value)
        return bytes(self.buf)

    def obj(self, value):
        if isinstance(value, CodeObj):
            self.buf.append(ord("c"))
            self.i32(value.argcount)
            self.i32(value.nlocals)
            self.i32(value.stacksize)
            self.i32(value.flags)
            self.obj(value.code)
            self.obj(value.consts)
            self.obj(value.names)
            self.obj(value.varnames)
            self.obj(value.freevars)
            self.obj(value.cellvars)
            self.obj(value.filename)
            self.obj(value.name)
            self.i32(value.firstlineno)
            self.obj(value.lnotab)
        elif isinstance(value, UStr):
            self.buf.append(ord("u"))
            self.u32(len(value.raw))
            self.buf.extend(value.raw)
        elif isinstance(value, bytes):
            self.buf.append(ord("s"))
            self.u32(len(value))
            self.buf.extend(value)
        elif isinstance(value, str):
            raw = value.encode("utf-8")
            self.buf.append(ord("s"))
            self.u32(len(raw))
            self.buf.extend(raw)
        elif value is None:
            self.buf.append(ord("N"))
        elif value is True:
            self.buf.append(ord("T"))
        elif value is False:
            self.buf.append(ord("F"))
        elif isinstance(value, int):
            if -0x80000000 <= value <= 0x7FFFFFFF:
                self.buf.append(ord("i"))
                self.i32(value)
            else:
                self.buf.append(ord("l"))
                sign = -1 if value < 0 else 1
                digits = []
                n = abs(value)
                while n:
                    digits.append(n & 0x7FFF)
                    n >>= 15
                if not digits:
                    digits = [0]
                self.i32(sign * len(digits))
                for digit in digits:
                    self.u16(digit)
        elif isinstance(value, float):
            self.buf.append(ord("g"))
            self.buf.extend(struct.pack("<d", value))
        elif isinstance(value, complex):
            self.buf.append(ord("y"))
            self.buf.extend(struct.pack("<d", value.real))
            self.buf.extend(struct.pack("<d", value.imag))
        elif isinstance(value, tuple):
            if len(value) <= 255:
                self.buf.append(ord(")"))
                self.buf.append(len(value))
            else:
                self.buf.append(ord("("))
                self.u32(len(value))
            for item in value:
                self.obj(item)
        elif isinstance(value, list):
            self.buf.append(ord("["))
            self.u32(len(value))
            for item in value:
                self.obj(item)
        elif isinstance(value, dict):
            self.buf.append(ord("{"))
            for key, item in value.items():
                self.obj(key)
                self.obj(item)
            self.buf.append(0)
        elif isinstance(value, set):
            self.buf.append(ord("<"))
            self.u32(len(value))
            for item in value:
                self.obj(item)
        elif isinstance(value, frozenset):
            self.buf.append(ord(">"))
            self.u32(len(value))
            for item in value:
                self.obj(item)
        else:
            raise ValueError("cannot marshal %r" % (type(value),))

    def i32(self, value):
        self.buf.extend(struct.pack("<i", value))

    def u32(self, value):
        self.buf.extend(struct.pack("<I", value))

    def u16(self, value):
        self.buf.extend(struct.pack("<H", value))


class Inst:
    __slots__ = ("op", "arg", "orig_start", "orig_end", "new_off", "jump",
                 "target_orig", "ext_slot", "custom_end_new", "custom_index")

    def __init__(self, op, arg, orig_start, orig_end):
        self.op = op
        self.arg = arg
        self.orig_start = orig_start
        self.orig_end = orig_end
        self.new_off = 0
        self.jump = None
        self.target_orig = None
        self.ext_slot = None
        self.custom_end_new = 0
        self.custom_index = 0

    @property
    def size(self):
        return 1 if self.arg is None else 3


def split_opext(name):
    rest = name[len("OPEXT_"):]
    for suffix, count in (("_2", 2), ("_3", 3)):
        if rest.endswith(suffix):
            return [rest[:-len(suffix)]] * count
    parts = rest.split("_")
    out = []
    i = 0
    while i < len(parts):
        for candidate in STD_NAMES:
            bits = candidate.split("_")
            if parts[i:i + len(bits)] == bits:
                out.append(candidate)
                i += len(bits)
                break
        else:
            raise ValueError("cannot split OPEXT name %s" % name)
    return out


def build_instructions(code):
    data = code
    pos = 0
    out = []
    records = []
    pending_ext = None
    while pos < len(data):
        start = pos
        b = data[pos]
        pos += 1
        if b == EXTRA_ARG:
            pos += 2
            continue
        if b == 0x7E:
            ext = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            inst = Inst(STD["EXTENDED_ARG"], ext, start, pos)
            out.append(inst)
            records.append({"start": start, "end": pos, "first": len(out) - 1,
                            "last": len(out) - 1})
            pending_ext = inst
            continue
        if b == 0xAD:
            arg = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            if pending_ext is not None:
                arg |= pending_ext.arg << 16
            first = Inst(STD["LOAD_FAST"], 0, start, pos)
            second = Inst(STD["LOAD_CONST"], arg, start, pos)
            second.ext_slot = pending_ext
            pending_ext = None
            out.extend((first, second))
            record = {"start": start, "end": pos, "first": len(out) - 2,
                      "last": len(out) - 1}
            records.append(record)
            continue
        if b in CUSTOM_BASE:
            name = CUSTOM_BASE[b]
            std = STD[name]
            arg = None
            if std >= HAVE_ARGUMENT:
                arg = struct.unpack_from("<H", data, pos)[0]
                pos += 2
                if pending_ext is not None:
                    arg |= pending_ext.arg << 16
            inst = Inst(std, arg, start, pos)
            if std >= HAVE_ARGUMENT and pending_ext is not None:
                inst.ext_slot = pending_ext
                pending_ext = None
            out.append(inst)
            records.append({"start": start, "end": pos, "first": len(out) - 1,
                            "last": len(out) - 1})
            continue
        if b in CUSTOM_OPEXT:
            cname = CUSTOM_OPEXT[b]
            subs = split_opext(cname)
            first_arg = True
            first_index = len(out)
            for sub in subs:
                std = STD[sub]
                arg = None
                if std >= HAVE_ARGUMENT:
                    if first_arg:
                        arg = struct.unpack_from("<H", data, pos)[0]
                        pos += 2
                        if pending_ext is not None:
                            arg |= pending_ext.arg << 16
                            ext_slot = pending_ext
                            pending_ext = None
                        else:
                            ext_slot = None
                    else:
                        marker = data[pos]
                        if marker != EXTRA_ARG:
                            raise ValueError(
                                "CISC %s missing 0x83 at 0x%x (got 0x%02x)" %
                                (cname, pos, marker))
                        pos += 1
                        arg = struct.unpack_from("<H", data, pos)[0]
                        pos += 2
                        ext_slot = None
                    first_arg = False
                    inst = Inst(std, arg, start, pos)
                    inst.ext_slot = ext_slot
                else:
                    inst = Inst(std, None, start, pos)
                out.append(inst)
            records.append({"start": start, "end": pos, "first": first_index,
                            "last": len(out) - 1})
            continue
        raise ValueError("unknown custom opcode 0x%02x at 0x%x" % (b, start))

    for index, record in enumerate(records):
        for i in range(record["first"], record["last"] + 1):
            out[i].custom_index = index
            out[i].orig_end = record["end"]
    return out, records


def layout(instructions, records):
    offset = 0
    for inst in instructions:
        inst.new_off = offset
        offset += inst.size
    for record in records:
        record["new_start"] = instructions[record["first"]].new_off
        record["new_end"] = instructions[record["last"]].new_off + instructions[record["last"]].size
    for inst in instructions:
        inst.custom_end_new = records[inst.custom_index]["new_end"]
    return offset


def parse_lnotab(firstlineno, lnotab):
    events = [(0, firstlineno)]
    addr = 0
    line = firstlineno
    i = 0
    while i + 1 < len(lnotab):
        addr += lnotab[i]
        inc = lnotab[i + 1]
        if inc >= 128:
            inc -= 256
        line += inc
        events.append((addr, line))
        i += 2
    return events


def line_at(events, offset):
    line = events[0][1]
    for addr, value in events:
        if addr > offset:
            break
        line = value
    return line


def build_lnotab(instructions, records, original_lnotab, firstlineno):
    events = parse_lnotab(firstlineno, original_lnotab)
    out = bytearray()
    last_addr = 0
    last_line = firstlineno
    for record in records:
        line = line_at(events, record["start"])
        if line == last_line:
            continue
        target = record["new_start"]
        addr_delta = target - last_addr
        line_delta = line - last_line
        while addr_delta > 255:
            out.extend((255, 0))
            last_addr += 255
            addr_delta -= 255
        while line_delta > 127:
            out.extend((0, 127))
            last_line += 127
            line_delta -= 127
        while line_delta < -128:
            out.extend((0, 128))
            last_line -= 128
            line_delta += 128
        out.extend((addr_delta & 0xFF, line_delta & 0xFF))
        last_addr += addr_delta
        last_line += line_delta
    return bytes(out)


def resolve_jumps(instructions, records, new_len, original_len):
    starts = [r["start"] for r in records]
    start_to_new = {r["start"]: r["new_start"] for r in records}
    for inst in instructions:
        if inst.jump is None:
            continue
        target = inst.target_orig
        if target >= original_len:
            new_target = new_len
        elif target in start_to_new:
            new_target = start_to_new[target]
        else:
            index = bisect.bisect_right(starts, target) - 1
            if index < 0:
                raise ValueError("jump target 0x%x before first instruction" % target)
            new_target = records[index]["new_start"]
        if inst.jump == "rel":
            new_arg = new_target - inst.custom_end_new
        else:
            new_arg = new_target
        if new_arg < 0:
            raise ValueError("negative jump offset at 0x%x" % inst.orig_start)
        if new_arg > 0xFFFF:
            if inst.ext_slot is None:
                raise ValueError(
                    "new jump offset 0x%x needs EXTENDED_ARG at 0x%x" %
                    (new_arg, inst.orig_start))
            inst.ext_slot.arg = (new_arg >> 16) & 0xFFFF
            inst.arg = new_arg & 0xFFFF
        else:
            if inst.ext_slot is not None:
                inst.ext_slot.arg = 0
            inst.arg = new_arg


def connect_jumps(instructions, records, original_len):
    rel_ops = {STD[name] for name in REL_JUMPS}
    abs_ops = {STD[name] for name in ABS_JUMPS}
    for inst in instructions:
        if inst.op in rel_ops:
            inst.jump = "rel"
            inst.target_orig = inst.orig_end + inst.arg
        elif inst.op in abs_ops:
            inst.jump = "abs"
            inst.target_orig = inst.arg


def split_extended(instructions):
    for inst in instructions:
        if inst.ext_slot is None or inst.jump is not None or inst.arg is None:
            continue
        inst.ext_slot.arg = (inst.arg >> 16) & 0xFFFF
        inst.arg &= 0xFFFF


def check_large_args(instructions):
    for inst in instructions:
        if inst.jump is None and inst.arg is not None and inst.arg > 0xFFFF:
            raise ValueError(
                "arg 0x%x needs EXTENDED_ARG at 0x%x but none was present" %
                (inst.arg, inst.orig_start))


def convert_bytecode(code, lnotab, firstlineno):
    instructions, records = build_instructions(code)
    new_len = layout(instructions, records)
    connect_jumps(instructions, records, len(code))
    split_extended(instructions)
    check_large_args(instructions)
    resolve_jumps(instructions, records, new_len, len(code))
    output = bytearray()
    for inst in instructions:
        output.append(inst.op)
        if inst.arg is not None:
            output.extend(struct.pack("<H", inst.arg & 0xFFFF))
    new_lnotab = build_lnotab(instructions, records, lnotab, firstlineno)
    return bytes(output), new_lnotab, records, instructions


def rewrite_code(code):
    new_bytes, new_lnotab, records, instructions = convert_bytecode(
        code.code, code.lnotab, code.firstlineno)
    new_consts = tuple(rewrite_code(c)[0] if isinstance(c, CodeObj) else c
                       for c in code.consts)
    return CodeObj(
        argcount=code.argcount, nlocals=code.nlocals,
        stacksize=code.stacksize, flags=code.flags,
        code=new_bytes, consts=new_consts, names=code.names,
        varnames=code.varnames, freevars=code.freevars,
        cellvars=code.cellvars, filename=code.filename, name=code.name,
        firstlineno=code.firstlineno, lnotab=new_lnotab), records, instructions


def walk_codes(code):
    yield code
    for const in code.consts:
        if isinstance(const, CodeObj):
            yield from walk_codes(const)


def to_pyc(plain: bytes) -> bytes:
    reader = MarshalReader(plain)
    root = reader.obj()
    if not isinstance(root, CodeObj):
        raise ValueError("not a Python 2.7 marshal code object")
    new_root, _, _ = rewrite_code(root)
    payload = MarshalWriter().dump(new_root)
    return MAGIC_27 + b"\x00\x00\x00\x00" + payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--salt", default=None)
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    data = open(args.input, "rb").read()
    if data[:2] == b"XN":
        if not args.salt:
            raise SystemExit("encrypted XN input requires --salt")
        sys.path.insert(0, r"D:\Github\NPK-X\tools")
        import xn_crypto
        plain = xn_crypto.decrypt(data, args.salt)
        if not plain:
            raise SystemExit("XN decrypt failed")
    else:
        plain = data

    reader = MarshalReader(plain)
    root = reader.obj()
    if not isinstance(root, CodeObj):
        raise SystemExit("not a Python 2.7 marshal code object")
    if reader.pos != len(plain):
        print("warning: trailing marshal bytes %d" % (len(plain) - reader.pos),
              file=sys.stderr)

    new_root, _, _ = rewrite_code(root)
    payload = MarshalWriter().dump(new_root)
    with open(args.output, "wb") as handle:
        handle.write(MAGIC_27)
        handle.write(b"\x00\x00\x00\x00")
        handle.write(payload)

    if args.stats:
        for code in walk_codes(new_root):
            print("%-40s code=%5d line=%d consts=%d names=%d" % (
                code.name.decode("latin1") if isinstance(code.name, bytes) else code.name,
                len(code.code), code.firstlineno,
                len(code.consts), len(code.names)))
    print("wrote %s (%d bytes payload)" % (args.output, len(payload)))


if __name__ == "__main__":
    main()
