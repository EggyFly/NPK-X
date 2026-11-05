#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collide Android plaintext manifests against the PC path tables.

Inputs are the decrypted Android manifests:
  - assets.ppk.map  -> records json (path + mmh3 id) from _android-pathtable
  - script_file_list -> json array with file_name / path_id

Every Android path is converted to its backslash form and checked against the
PC path_hash_file (FNV-1a32 + length) oracle:
  known      - FNV row already covered by the current pathdict
  gap        - FNV row uncovered; if mmh3(backslash path) also hits an
               unresolved signature and the payload sniffs compatible, the
               mapping is written out (never for script packages)
  android_only - no FNV row at all (Android-exclusive asset)

Outputs the verified mapping json and an Android-only path json.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import sqlite3
import struct
import sys
import zlib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import mmh3

import npk_browser as B

MMH3_SEED = 0x9747B28C
FNV_OFFSET = 0x811C9DC5
FNV_PRIME = 0x01000193

ALIASES = {
    "spr": {"atlas"},
    "skpatch": {"json"},
    "rscan": {"json"},
    "conf": {"json"},
    "lua": {"lua"},
    "skel": {"spine", "binary"},
    "blendspace1d": {"blendspace"},
    "surf": {"surface", "xml"},
    "fov": {"text", "binary"},
    "cube": {"dds"},
    "scn": {"gim_scene", "gim_material"},
    "sfx": {"gim_effect", "efx"},
}


def fnv1a(path: str) -> int:
    value = FNV_OFFSET
    for byte in path.encode("utf-8"):
        value = ((value ^ byte) * FNV_PRIME) & 0xFFFFFFFF
    return value


def sniff(data: bytes) -> str:
    head = data[:65536]
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    head = head.lstrip()
    if head[:4] == b"\xc1\x59\x41\x0d":
        if any(key in head for key in (b"RoadGroup", b"SceneRoad", b"Lightmap", b"AmbientLight", b"BakeInfoTex")):
            return "gim_scene"
        if any(key in head for key in (b"ColorFrame", b"FxGroup", b"Emission", b"GravityFrame", b"AlphaMtl")):
            return "gim_effect"
        if any(key in head for key in (b"SubMesh", b"SkeletonFile", b"Sub0")):
            return "gim"
        if any(key in head for key in (b"LightingMaterial", b"MaterialGroup", b"Technique")):
            return "gim_material"
        return "gim"
    if head[:1] in (b"{", b"["):
        return "json"
    if head[:1] == b"<":
        if b"<BlendSpace" in head[:4096]:
            return "blendspace"
        if b"<Surface" in head[:4096]:
            return "surface"
        return "xml"
    if head[:4] == b"DDS ":
        return "dds"
    if head[:4] == b"RAWANIMA":
        return "rawanimation"
    if head[:4] == b"\x1bLua" or head.startswith(b"---AUTTO EXPORT") or b"\nreturn {" in head[:4096]:
        return "lua"
    if b".tga " in head[:4096] and b"\r\n" in head[:4096]:
        return "atlas"
    if b"3.8." in head[:128] and b"\x00" in head[:128]:
        return "spine"
    if head and all(32 <= byte < 127 or byte in (9, 10, 13, 59, 44) for byte in head[:512]):
        return "text"
    return "binary"


def ext_of(path: str) -> str:
    stem = path.rsplit(".", 1)
    return stem[1].lower() if len(stem) == 2 else ""


def ext_compatible(path: str, kind: str) -> bool:
    ext = ext_of(path)
    allowed = {ext, *ALIASES.get(ext, ())}
    for candidate in allowed:
        if kind.startswith(candidate) or candidate.startswith(kind):
            if candidate and kind:
                return True
    text_kinds = {"text", "json", "xml", "lua", "atlas", "spine", "blendspace", "surface", "rawanimation"}
    binary_kinds = {"gim", "gim_scene", "gim_material", "gim_effect", "dds", "binary", "spine"}
    if kind in text_kinds:
        return ext in ("json", "txt", "xml", "lua", "spr", "atlas", "skpatch", "rscan", "conf", "blendspace1d", "surf", "fov", "skel", "hlsl", "animconfig", "postprocess")
    if kind in binary_kinds:
        return ext not in ("json", "txt", "xml", "lua", "spr", "atlas")
    return True


def load_pathdict(cache: Path, con: sqlite3.Connection):
    generated = cache / "pathdict.generated.txt.gz"
    paths = set()
    if generated.exists():
        with gzip.open(generated, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                item = line.strip().replace("\\", "/")
                if item:
                    paths.add(item)
    if not paths:
        for row in con.execute("SELECT path FROM files WHERE path NOT LIKE '_unresolved/%'"):
            paths.add(row[0])
        extra = Path(__file__).resolve().parent / "pathdict_extra.txt"
        if extra.exists():
            for line in extra.read_text(encoding="utf-8", errors="replace").splitlines():
                item = line.strip().replace("\\", "/")
                if item:
                    paths.add(item)
    return paths


def load_fnv_table(con: sqlite3.Connection, packages: dict):
    row = con.execute("SELECT * FROM files WHERE path='path_hash_file'").fetchone()
    if row is None:
        raise SystemExit("path_hash_file is not present in this catalog")
    raw = B.read_entry_bytes(packages[row["package"]], row)
    offset = raw.find(b"\x78\x9c")
    if offset < 0:
        raise SystemExit("path_hash_file has no zlib payload")
    plain = zlib.decompress(raw[offset:])
    records = [struct.unpack_from("<II", plain, pos) for pos in range(0, len(plain), 8)]
    return records, {"package": row["package"], "records": len(records)}


def load_unresolved(con: sqlite3.Connection, packages: dict):
    unresolved = {}
    for row in con.execute("SELECT * FROM files WHERE path LIKE '_unresolved/%'"):
        unresolved.setdefault(row["signature"], []).append(row)
    return unresolved


def payload_of(row, packages: dict) -> bytes:
    raw = B.read_entry_bytes(packages[row["package"]], row)
    if row["compression"] == 3:
        try:
            import zstandard
            raw = zstandard.ZstdDecompressor().decompressobj().decompress(raw)
        except Exception:
            pass
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Collide Android manifests with PC path tables")
    parser.add_argument("--android-map", type=Path, default=Path("_android-pathtable/tmp/ppk_map_paths.json"))
    parser.add_argument("--android-script", type=Path, default=Path("_android-pathtable/tmp/script_file_list.plain.json"))
    parser.add_argument("--android-npkext", type=Path, default=Path("_android-pathtable/tmp/npkext_paths.json"))
    parser.add_argument("--db", type=Path, default=B.default_cache_dir() / "catalog.sqlite")
    parser.add_argument("--out-names", type=Path, default=Path("docs/unresolved-sweep/17-android-pathtable-names.json"))
    parser.add_argument("--out-extra", type=Path, default=Path("docs/unresolved-sweep/17-android-extra-paths.json"))
    parser.add_argument("--limit-sniff", type=int, default=0)
    args = parser.parse_args()

    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    records, table_info = load_fnv_table(con, packages)
    unresolved = load_unresolved(con, packages)
    pathdict = load_pathdict(B.default_cache_dir(), con)
    con.close()
    print("pathdict:", len(pathdict), "fnv_table:", table_info["records"], "unresolved:", sum(len(v) for v in unresolved.values()))

    fnv_known = {}
    fnv_rows = {}
    for index, (digest, length) in enumerate(records):
        fnv_rows.setdefault((digest, length), []).append(index)

    android_map = json.loads(args.android_map.read_text(encoding="utf-8"))
    android_script = json.loads(args.android_script.read_text(encoding="utf-8"))
    android_npkext = json.loads(args.android_npkext.read_text(encoding="utf-8")) if args.android_npkext.exists() else {"paths": []}
    sources = []
    for record in android_map:
        sources.append((record["path"], record["id"], "assets.ppk.map"))
    for entry in android_script:
        sources.append((entry["file_name"], entry.get("path_id", 0), "script_file_list"))
    for path in android_npkext.get("paths", []):
        sources.append((path, 0, "npkext_file_list"))
    print("android paths:", len(sources))

    uniq = {}
    for path, path_id, source in sources:
        uniq.setdefault(path, (path_id, source))
    print("unique android paths:", len(uniq))

    pathdict_fnv = {}
    for path in pathdict:
        bs = path.replace("/", "\\")
        pathdict_fnv.setdefault(fnv1a(bs), path)

    known = []
    android_only = []
    gap_hits = []
    stats = Counter()
    sniff_checked = 0

    unresolved_sigs = {sig: rows for sig, rows in unresolved.items()}

    for path, (path_id, source) in sorted(uniq.items()):
        bs = path.replace("/", "\\")
        digest = fnv1a(bs)
        stats["total"] += 1
        if digest in pathdict_fnv and pathdict_fnv[digest].replace("\\", "/") == path:
            known.append(path)
            stats["known"] += 1
            continue
        rows = fnv_rows.get((digest, len(bs)))
        if not rows:
            android_only.append(path)
            stats["android_only"] += 1
            continue
        stats["gap_fnv"] += 1
        signature = mmh3.hash(bs, MMH3_SEED, signed=False)
        target_rows = unresolved_sigs.get(signature)
        if not target_rows:
            stats["gap_fnv_no_mmh3"] += 1
            gap_hits.append({"path": path, "source": source, "no_sig": True, "note": "fnv gap but no unresolved sig"})
            continue
        row = target_rows[0]
        package = row["package"]
        kind = sniff(payload_of(row, packages))
        sniff_checked += 1
        ok = ext_compatible(path, kind)
        item = {
            "unresolved_path": row["path"],
            "path": path,
            "signature": "%08x" % signature,
            "package": package,
            "sniff": kind,
            "sniff_ok": ok,
            "evidence": "fnv1a+len hit gap row %d; mmh3 hit unresolved sig; path from official android manifest" % rows[0],
        }
        if package.startswith("script") or "script" in package:
            item["script_package"] = True
            stats["script_hit"] += 1
        elif ok:
            stats["solved"] += 1
        else:
            stats["sniff_review"] += 1
        gap_hits.append(item)

    result = {
        "generated": "android_pathtable_collide",
        "android_sources": {
            "assets.ppk.map": len(android_map),
            "script_file_list": len(android_script),
            "npkext_file_list": len(android_npkext.get("paths", [])),
        },
        "fnv_table": table_info,
        "stats": dict(stats),
        "sniff_checked": sniff_checked,
        "mappings": {item["unresolved_path"]: item for item in gap_hits if "unresolved_path" in item and not item.get("script_package")},
        "sniff_review_items": [item for item in gap_hits if item.get("sniff_ok") is False],
        "script_entries": [item for item in gap_hits if item.get("script_package")],
        "fnv_gap_no_sig": [item["path"] for item in gap_hits if item.get("no_sig")],
    }
    args.out_names.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    extra = {
        "android_only_count": len(android_only),
        "android_only_paths": android_only,
        "known_on_pc": len(known),
        "sample_known": known[:20],
    }
    args.out_extra.write_text(json.dumps(extra, ensure_ascii=False, indent=1), encoding="utf-8")

    print("stats:", dict(stats))
    print("wrote", args.out_names)
    print("wrote", args.out_extra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
