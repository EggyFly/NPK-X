#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Capture shader cache path strings from the live eggy party client memory.

Walks the committed regions of the running client.exe with VirtualQueryEx +
ReadProcessMemory (read only, no write, no inject), finds the shader cache
root strings (cross_compiled_glsl / shader_cache / local_shader_cache / [CS] )
and dumps every printable string around the hits plus region metadata, so the
16hex runtime names can be harvested while the game is running.

Output: docs/unresolved-sweep/23-runtime-capture-strings.json
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt

import json
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep" / "23-runtime-capture-strings.json"

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100

CHUNK = 8 * 1024 * 1024
OVERLAP = 128
MAX_CONTEXT = 400
MAX_TEXT = 600

ROOTS = (
    b"cross_compiled_glsl",
    b"cross_compiled_vulkan_windows",
    b"local_shader_cache",
    b"shader_cache",
    b"shadercache",
    b"[CS] ",
    b"LoadCacheMappings",
    b"cross compile runtime cache",
    b"shader_versions",
)
ASCII_RE = re.compile(b"|".join(re.escape(r) for r in ROOTS))
PRINT_RE = re.compile(rb"[\x20-\x7e]{5,}")
HEXNAME_RE = re.compile(r"\b([0-9a-fA-F]{1,16})\.(?:meta|cache)\b")
HEXSEG_RE = re.compile(rb"(?<![0-9a-fA-F])[0-9a-fA-F]{15,16}(?![0-9a-fA-F])")
U16_ROOT_TEXTS = ("cross_compiled_glsl", "shader_cache", "shadercache", "local_shader_cache", "[CS] ")
U16_RE = re.compile(b"|".join(re.escape(r.encode("utf-16-le")) for r in U16_ROOT_TEXTS))


def bind_kernel():
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wt.HANDLE
    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k32.ReadProcessMemory.restype = wt.BOOL
    k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    k32.VirtualQueryEx.restype = ctypes.c_size_t
    k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
    return k32


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.DWORD),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


class PE32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def list_clients(k32) -> list:
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    out = []
    if snap == -1 or snap == 0xFFFFFFFFFFFFFFFF:
        return out
    try:
        entry = PE32W()
        entry.dwSize = ctypes.sizeof(PE32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == "client.exe":
                out.append(entry.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def read_block(k32, h, addr: int, size: int):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)) and got.value:
        return buf.raw[: got.value]
    return None


def region_kind(type_code: int) -> str:
    return {0x1000000: "image", 0x40000: "mapped", 0x20000: "private"}.get(type_code, "other%d" % type_code)


def extract_texts(window: bytes, utf16: bool) -> list:
    if utf16:
        text = window.decode("utf-16-le", errors="ignore")
        return re.findall(r"[\x20-\x7e]{5,}", text)
    return [m.group().decode("ascii", errors="replace") for m in PRINT_RE.finditer(window)]


def scan_process(k32, h, pid: int, out_path: Path):
    addr = 0
    mbi = MBI()
    stats = {"regions": 0, "read_ok": 0, "read_fail": 0, "bytes": 0, "skipped": 0, "by_type": {}}
    strings = {}
    started = time.time()

    while True:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        base = mbi.BaseAddress
        size = mbi.RegionSize
        if size == 0 or base + size <= addr:
            addr += 0x1000
            if addr > 0x7FFFFFFEFFFF:
                break
            continue
        stats["regions"] += 1
        if mbi.State == MEM_COMMIT and mbi.Protect != PAGE_NOACCESS and not (mbi.Protect & PAGE_GUARD):
            kind = region_kind(mbi.Type)
            stats["by_type"][kind] = stats["by_type"].get(kind, 0) + size
            pos = 0
            carry = b""
            carry_len = 0
            while pos < size:
                take = min(CHUNK, size - pos)
                data = read_block(k32, h, base + pos, take)
                if data is None:
                    stats["read_fail"] += 1
                    pos += 0x10000
                    carry = b""
                    carry_len = 0
                    continue
                stats["read_ok"] += 1
                stats["bytes"] += len(data)
                buf = carry + data
                buf_abs = base + pos - carry_len
                for m in ASCII_RE.finditer(buf):
                    for text in extract_texts(buf[max(0, m.start() - MAX_CONTEXT): m.end() + MAX_CONTEXT], False):
                        if len(text) > MAX_TEXT:
                            continue
                        item = strings.get(text)
                        if item is None:
                            item = {"count": 0, "utf16": False, "regions": [], "hexnames": []}
                            strings[text] = item
                        item["count"] += 1
                        if len(item["regions"]) < 6 and (not item["regions"] or item["regions"][-1][0] != base):
                            item["regions"].append([base, buf_abs + m.start(), kind, hex(mbi.Protect)])
                        for hn in HEXNAME_RE.findall(text):
                            low = hn.lower()
                            if low not in item["hexnames"]:
                                item["hexnames"].append(low)
                if b"s\x00h\x00" in buf or b"C\x00S\x00" in buf:
                    for m in U16_RE.finditer(buf):
                        for text in extract_texts(buf[max(0, m.start() - MAX_CONTEXT): m.end() + MAX_CONTEXT], True):
                            if len(text) > MAX_TEXT:
                                continue
                            key = "U:" + text
                            item = strings.get(key)
                            if item is None:
                                item = {"count": 0, "utf16": True, "regions": [], "hexnames": []}
                                strings[key] = item
                            item["count"] += 1
                            if len(item["regions"]) < 6 and (not item["regions"] or item["regions"][-1][0] != base):
                                item["regions"].append([base, buf_abs + m.start(), kind, hex(mbi.Protect)])
                            for hn in HEXNAME_RE.findall(text):
                                low = hn.lower()
                                if low not in item["hexnames"]:
                                    item["hexnames"].append(low)
                carry = buf[len(buf) - OVERLAP:]
                carry_len = len(carry)
                pos += take
        else:
            stats["skipped"] += 1
        addr = base + size
        if addr <= base:
            break

    for item in strings.values():
        item["hexnames"] = sorted(set(item["hexnames"]))
    report = {
        "pid": pid,
        "elapsed": round(time.time() - started, 1),
        "stats": stats,
        "unique_strings": len(strings),
        "strings": strings,
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def harvest_hex(k32, h, pid: int, out_path: Path):
    addr = 0
    mbi = MBI()
    stats = {"regions": 0, "read_ok": 0, "read_fail": 0, "bytes": 0, "skipped": 0, "by_type": {}}
    names = {}
    started = time.time()

    while True:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        base = mbi.BaseAddress
        size = mbi.RegionSize
        if size == 0 or base + size <= addr:
            addr += 0x1000
            if addr > 0x7FFFFFFEFFFF:
                break
            continue
        stats["regions"] += 1
        if mbi.State == MEM_COMMIT and mbi.Protect != PAGE_NOACCESS and not (mbi.Protect & PAGE_GUARD):
            kind = region_kind(mbi.Type)
            stats["by_type"][kind] = stats["by_type"].get(kind, 0) + size
            pos = 0
            carry = b""
            carry_len = 0
            while pos < size:
                take = min(CHUNK, size - pos)
                data = read_block(k32, h, base + pos, take)
                if data is None:
                    stats["read_fail"] += 1
                    pos += 0x10000
                    carry = b""
                    carry_len = 0
                    continue
                stats["read_ok"] += 1
                stats["bytes"] += len(data)
                buf = carry + data
                for m in HEXSEG_RE.finditer(buf):
                    name = m.group().decode("ascii").lower()
                    names[name] = names.get(name, 0) + 1
                carry = buf[len(buf) - OVERLAP:]
                carry_len = len(carry)
                pos += take
        else:
            stats["skipped"] += 1
        addr = base + size
        if addr <= base:
            break

    report = {
        "pid": pid,
        "elapsed": round(time.time() - started, 1),
        "stats": stats,
        "unique_names": len(names),
        "names": names,
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


FNV_OFFSET = 0x811C9DC5
FNV_PRIME = 0x01000193
FNV_PRIME_INV = pow(FNV_PRIME, -1, 1 << 32)
SHADER_DIR = "shader_cache/cross_compiled_glsl/"
BATCH_UNIQUE = 30_000_000


def fnv_forward_prefix(text: str) -> int:
    value = FNV_OFFSET
    for byte in text.encode("utf-8"):
        value = ((value ^ byte) * FNV_PRIME) & 0xFFFFFFFF
    return value


def fnv_reverse_tail(final: int, tail: bytes) -> int:
    value = final
    for byte in reversed(tail):
        value = ((value * FNV_PRIME_INV) & 0xFFFFFFFF) ^ byte
    return value


def build_targets(gate_records):
    import numpy as np
    targets = {}
    groups = {".meta": (53, 54), ".cache": (54, 55)}
    for ext, lens in groups.items():
        finals = [fnv for fnv, length in gate_records if length in lens]
        mids = np.array([fnv_reverse_tail(f, ext.encode()) for f in finals], dtype=np.uint32)
        targets[ext] = np.unique(mids)
    return targets


def fnv_sieve_u64(vals, targets):
    import numpy as np
    hex_table = np.array([ord(c) for c in "0123456789abcdef"], dtype=np.uint8)
    prefix_state = np.uint32(fnv_forward_prefix(SHADER_DIR))
    prime = np.uint32(FNV_PRIME)
    found = {}
    m16 = vals >= (np.uint64(1) << np.uint64(60))
    m15 = (vals >= (np.uint64(1) << np.uint64(52))) & ~m16
    for ext, mids in targets.items():
        out_vals = []
        out_counts = []
        for width, mask in ((16, m16), (15, m15)):
            v = vals[mask]
            if not len(v):
                continue
            state = np.full(len(v), prefix_state, dtype=np.uint32)
            for shift in range(64 - 4 * width, 64, 4):
                nib = ((v >> np.uint64(shift)) & np.uint64(0xF)).astype(np.uint8)
                state = (state ^ hex_table[nib].astype(np.uint32)) * prime
            hit = np.isin(state, mids)
            if hit.any():
                out_vals.append(v[hit])
                out_counts.append(np.bincount(np.searchsorted(mids, state[hit]), minlength=len(mids)))
        if not out_vals:
            continue
        allv = np.concatenate(out_vals)
        allc = np.sum(out_counts, axis=0)
        uniq, idx = np.unique(allv, return_index=True)
        for u, i in zip(uniq, idx):
            value = int(u)
            found[value] = found.get(value, 0) + max(int(allc[i]), 1)
    return found


def harvest_u64(k32, h, pid: int, out_path: Path, gap_records):
    import numpy as np
    addr = 0
    mbi = MBI()
    stats = {"regions": 0, "read_ok": 0, "read_fail": 0, "bytes": 0, "skipped": 0, "by_type": {}}
    u64_floor = np.uint64(1) << np.uint64(52)
    targets = build_targets(gap_records)
    bag = []
    bag_total = 0
    hits = {}
    started = time.time()

    def flush():
        nonlocal bag, bag_total, hits
        if not bag:
            return
        allv = np.concatenate(bag)
        bag = []
        bag_total = 0
        if not len(allv):
            return
        uniq, counts = np.unique(allv, return_counts=True)
        found = fnv_sieve_u64(uniq, targets)
        for value, count in found.items():
            hits[value] = hits.get(value, 0) + count
        print("  flush: %d uniq u64, total hits %d" % (len(uniq), len(hits)), flush=True)

    while True:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        base = mbi.BaseAddress
        size = mbi.RegionSize
        if size == 0 or base + size <= addr:
            addr += 0x1000
            if addr > 0x7FFFFFFEFFFF:
                break
            continue
        stats["regions"] += 1
        if mbi.State == MEM_COMMIT and mbi.Protect != PAGE_NOACCESS and not (mbi.Protect & PAGE_GUARD):
            kind = region_kind(mbi.Type)
            stats["by_type"][kind] = stats["by_type"].get(kind, 0) + size
            pos = 0
            carry = b""
            while pos < size:
                take = min(CHUNK, size - pos)
                data = read_block(k32, h, base + pos, take)
                if data is None:
                    stats["read_fail"] += 1
                    pos += 0x10000
                    carry = b""
                    continue
                stats["read_ok"] += 1
                stats["bytes"] += len(data)
                buf = carry + data
                n = len(buf) // 8
                if n:
                    arr = np.frombuffer(buf, dtype="<u8", count=n)
                    sel = arr[arr >= u64_floor]
                    if len(sel):
                        bag.append(sel)
                        bag_total += len(sel)
                        if bag_total >= BATCH_UNIQUE:
                            flush()
                carry = buf[len(buf) - OVERLAP:]
                pos += take
        else:
            stats["skipped"] += 1
        addr = base + size
        if addr <= base:
            break
    flush()

    names = {format(v, "x"): c for v, c in hits.items()}
    report = {
        "pid": pid,
        "elapsed": round(time.time() - started, 1),
        "stats": stats,
        "unique_names": len(names),
        "names": names,
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--harvest-hex", action="store_true",
                        help="harvest every standalone 15-16 hex string instead of shader-root strings")
    parser.add_argument("--harvest-u64", action="store_true",
                        help="harvest aligned u64s whose hex form passes the FNV+length gate (binary name form)")
    parser.add_argument("--db", type=Path, default=None, help="catalog.sqlite for the FNV gate")
    args = parser.parse_args()

    k32 = bind_kernel()
    pids = [args.pid] if args.pid else list_clients(k32)
    if not pids:
        raise SystemExit("no client.exe process found")
    pid = pids[0]
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        raise SystemExit("OpenProcess(%d) failed, winerr=%d" % (pid, ctypes.GetLastError()))
    if args.harvest_u64:
        import gzip
        import re as re_mod
        import sqlite3
        import external_pathtable_hunt as HUNT
        import npk_browser as B
        import mmh3
        db = args.db or (B.default_cache_dir() / "catalog.sqlite")
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=30)
        con.row_factory = sqlite3.Row
        packages = dict(con.execute("SELECT name, path FROM packages"))
        records, _ = HUNT.load_table(con, packages)
        unresolved = HUNT.load_unresolved(con, packages)
        con.close()
        pos_map = {}
        for i, rec in enumerate(records):
            pos_map.setdefault(rec, []).append(i)
        anchors = []
        pat = re_mod.compile(r"shader_cache/cross_compiled_glsl/[0-9a-f]{15,16}\.(?:meta|cache)$")
        gz = B.default_cache_dir() / "pathdict.generated.txt.gz"
        with gzip.open(gz, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                item = line.strip()
                if item and pat.match(item):
                    anchors.append(item)
        anchor_idx = set()
        for p in anchors:
            bs = p.replace("/", "\\")
            for i in pos_map.get((HUNT.fnv1a(bs), len(bs)), []):
                anchor_idx.add(i)
        gate = set()
        for i in anchor_idx:
            for j in range(max(0, i - 2), min(len(records), i + 3)):
                f, l = records[j]
                if l in (53, 54, 55):
                    gate.add((f, l))
        print("pathdict anchors %d, anchor idx %d, gate records %d" % (len(anchors), len(anchor_idx), len(gate)), flush=True)
        out = args.out.with_name(args.out.name.replace("-strings", "-u64names"))
        print("opened pid %d, harvesting u64 names ..." % pid, flush=True)
        report = harvest_u64(k32, h, pid, out, sorted(gate))
        k32.CloseHandle(h)
        st = report["stats"]
        print("elapsed %.1fs, scanned %.2f GB, regions %d (fail %d, skipped %d)"
              % (report["elapsed"], st["bytes"] / 1073741824.0, st["regions"], st["read_fail"], st["skipped"]))
        print("u64 names passing FNV gate: %d" % report["unique_names"])
        hits_unresolved = 0
        for name in sorted(report["names"]):
            verdicts = []
            for ext in (".meta", ".cache"):
                p = (SHADER_DIR + name + ext).replace("/", "\\")
                if mmh3.hash(p.encode(), 0x9747B28C, signed=False) in unresolved:
                    verdicts.append(name + ext)
            if verdicts:
                hits_unresolved += 1
            print("  %s x%d %s" % (name, report["names"][name], "MMH3_UNRESOLVED_HIT: " + ",".join(verdicts) if verdicts else ""))
        print("names hitting unresolved sigs: %d" % hits_unresolved)
        return 0

    if args.harvest_hex:
        out = args.out.with_name(args.out.name.replace("-strings", "-hexnames"))
        print("opened pid %d, harvesting hex strings ..." % pid, flush=True)
        report = harvest_hex(k32, h, pid, out)
        k32.CloseHandle(h)
        st = report["stats"]
        print("elapsed %.1fs, scanned %.2f GB, regions %d (fail %d, skipped %d)"
              % (report["elapsed"], st["bytes"] / 1073741824.0, st["regions"], st["read_fail"], st["skipped"]))
        print("unique 15-16 hex strings: %d" % report["unique_names"])
        top = sorted(report["names"].items(), key=lambda kv: -kv[1])[:10]
        print("top: %s" % [(k, v) for k, v in top])
        return 0

    print("opened pid %d, scanning memory ..." % pid, flush=True)
    report = scan_process(k32, h, pid, args.out)
    k32.CloseHandle(h)
    st = report["stats"]
    print("elapsed %.1fs, scanned %.2f GB, regions %d (fail %d, skipped %d)"
          % (report["elapsed"], st["bytes"] / 1073741824.0, st["regions"], st["read_fail"], st["skipped"]))
    print("by_type: %s" % {k: "%.2fGB" % (v / 1073741824.0) for k, v in sorted(st["by_type"].items(), key=lambda kv: -kv[1])})
    print("unique shader-root strings: %d" % report["unique_strings"])
    hexall = {}
    for text, item in report["strings"].items():
        for hn in item["hexnames"]:
            hexall.setdefault(hn, 0)
            hexall[hn] += item["count"]
    print("unique hex.meta/.cache names: %d" % len(hexall))
    print("names with >=10 hex chars: %d" % sum(1 for k in hexall if len(k) >= 10))
    return 0


if __name__ == "__main__":
    sys.exit(main())
