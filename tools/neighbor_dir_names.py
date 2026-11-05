#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Infer directories for unresolved PPK entries from index adjacency.

path_hash_file keeps (FNV-1a(path), len(path)) records in resource list order
and directories group tightly along that order, so the explained records
around an unknown record give its directory.  Candidates from those anchor
directories, anchor stems, shard index neighbours, content dirs and same-stem
pairing are accepted only when FNV+length hits an unknown record, MurmurHash3
of the backslash path equals the entry signature, and the entry magic agrees.
Round-trip on already named entries calibrates every pool.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import re
import sqlite3
import struct
import sys
import time
import zlib
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mmh3
import neox_object
import zstandard
import npk_browser as B

SEED = 0x9747B28C
FNV_OFFSET = 0x811C9DC5
FNV_PRIME = 0x01000193
DEFAULT_DB = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "EggyNpkBrowser" / "catalog.sqlite"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep" / "11-neighbor-dir-names.json"

STEM_EXTS = (".bin", ".gim", ".mtg", ".scn", ".lod", ".plist", ".eggimonbin")
CAND_EXTS = {
    ".bin": (".bin", ".atlas", ".json", ".spr", ".qss", ".conf", ".rscan", ".skpatch"),
    ".gim": (".gim", ".mesh"),
    ".mesh": (".mesh", ".gim"),
    ".mtg": (".mtg",),
    ".scn": (".scn",),
    ".lod": (".lod",),
    ".plist": (".plist",),
    ".eggimonbin": (".eggimonbin",),
    ".kta": (".kta",),
    ".ktx": (".ktx",),
    ".skeleton": (".skeleton",),
}
PAIR_SOURCES = {
    ".kta": (".atlas", ".plist", ".gim"),
    ".mesh": (".mtg", ".skeleton", ".gim"),
    ".skeleton": (".mesh", ".mtg"),
}
ATLAS_HEAD = re.compile(rb"\r?\n*([A-Za-z0-9_\-]{3,120})\.(?:png|kta|jpg|jpeg)\r?\nsize: \d+,\d+\r?\nformat: ")
UUID_RE = re.compile(rb"uuid(?:[\xa0-\xbf]|\xc4[\x20-\xff])([A-Za-z0-9]{22})")
TYPE_RE = re.compile(rb"\xc4\x04type\xc4.([\x20-\x7e]{3,120})", re.DOTALL)
PLIST_TEX = re.compile(rb"textureFileName</key>\s*<string>([^<]{1,200}?)</string>")
FIELD_WORDS = {
    "name", "sub0", "sub1", "sub2", "sub3", "high", "low", "mid", "lod", "mesh", "mtg",
    "skeletonfile", "mtgfile", "filename", "meshfile", "animconfigfile", "blastfile",
    "softbone", "socketobject", "castshadow", "receiveshadow", "usefog", "object",
    "logicproperties", "neox", "boundingcenter", "boundinghalf", "boundinginfo",
    "geobatchhint", "animaccumenable", "materialgroup", "lightingmaterial", "technique",
    "material", "keyframe", "macro0", "root", "node", "true", "false", "submesh",
    "subresource", "track", "head", "lodpolicy", "colorframe", "roadgroup", "audio",
    "sceneroad", "lightmap",
}
NAME_FIELDS = ("name", "sub0", "sub1", "sub2", "sub3", "filename", "mtgfile", "skeletonfile", "meshfile")


def fnv1a(path: str) -> int:
    value = FNV_OFFSET
    for byte in path.encode("utf-8"):
        value = ((value ^ byte) * FNV_PRIME) & 0xFFFFFFFF
    return value


def fnv_join(value: int, text: str) -> int:
    for byte in text.encode("utf-8"):
        value = ((value ^ byte) * FNV_PRIME) & 0xFFFFFFFF
    return value


def path_sig(path: str) -> int:
    return mmh3.hash(path.replace("/", "\\"), SEED, signed=False)


def unwrap(data: bytes) -> bytes:
    if data[:4] == b"\x28\xb5\x2f\xfd":
        try:
            return zstandard.ZstdDecompressor().decompressobj().decompress(data)
        except Exception:
            return data
    if data[:1] == b"\x78" and data[1:2] in (b"\x01", b"\x5e", b"\x9c", b"\xda"):
        try:
            return zlib.decompress(data)
        except Exception:
            return data
    return data


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


def magic_ok(cand_ext: str, head: bytes) -> bool:
    data = unwrap(head)
    if cand_ext in (".kta", ".ktx"):
        return data[:4] in (b"\xabKTX", b"DDS ")
    kind = sniff(data)
    if cand_ext == ".atlas":
        return kind == "atlas"
    if cand_ext == ".plist":
        return kind == "xml"
    if cand_ext in (".gim", ".mesh", ".mtg", ".scn"):
        return kind in ("gim", "gim_scene", "gim_material", "gim_effect", "binary")
    if cand_ext == ".lod":
        return kind in ("binary", "xml")
    if cand_ext == ".json":
        return kind in ("json", "text")
    if cand_ext == ".bin":
        return kind in ("binary", "json", "atlas", "text", "xml", "spine")
    return True


def snake_case(value: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    text = re.sub(r"([A-Z])([A-Z][a-z])", r"\1_\2", text)
    return text.replace("-", "_").lower()


def looks_like_stem(text: str) -> bool:
    if not 3 <= len(text) <= 64 or "/" in text or "\\" in text:
        return False
    if text.lower() in FIELD_WORDS:
        return False
    if text.isupper() or text.isdigit():
        return False
    return text.isascii() and text.replace("_", "").replace("-", "").isalnum()


def extract_atlas(data: bytes):
    match = ATLAS_HEAD.match(data[:256])
    if not match:
        return [], [], ""
    return [match.group(1).decode("latin1")], [], "atlas"


def extract_eggimon(data: bytes):
    if b"uuid" not in data[:8192]:
        return [], [], ""
    match = UUID_RE.search(data[:131072])
    if not match:
        return [], [], ""
    uuid = match.group(1).decode("latin1")
    stems = [uuid.lower()]
    if uuid != uuid.lower():
        stems.append(uuid)
    return stems, [], "eggimon-uuid"


def extract_schema(data: bytes):
    if not data or data[0] > 0x8F and data[0] not in (0xDE, 0xDF):
        return [], [], ""
    stems = []
    for match in TYPE_RE.finditer(data[:256]):
        length = data[match.start(1) - 1]
        raw = match.group(1)
        if not 3 <= length <= len(raw):
            continue
        value = raw[:length].decode("latin1")
        if value[0].isupper() and value.replace("_", "").isalnum():
            stems.append(snake_case(value))
    if not stems:
        return [], [], ""
    return stems[:2], [], "meta-schema"


def extract_neox(data: bytes):
    try:
        parsed = neox_object.parse(data)
    except Exception:
        return [], [], ""
    stems = []
    content_dirs = []
    seen = set()

    def consider(text):
        text = text.strip().replace("\\", "/").strip("/")
        if not text or text in seen:
            return
        seen.add(text)
        if "/" in text or "." in text:
            head, _, tail = text.rpartition("/")
            tail = tail or head
            dot = tail.rfind(".")
            if dot > 0:
                tail = tail[:dot]
            if head and head != "." and "/" in head:
                content_dirs.append(head)
            if looks_like_stem(tail):
                stems.append(tail)
        elif looks_like_stem(text):
            stems.append(text)

    for obj in parsed.get("objects") or []:
        for field, value in obj.items():
            if not isinstance(value, str) or not value:
                continue
            if field.lower() in NAME_FIELDS or len(value) <= 64:
                consider(value)
            if len(stems) >= 14:
                break
        if len(stems) >= 14:
            break
    return stems[:14], content_dirs[:16], "neox-name"


def extract_plist(data: bytes):
    if b"<plist" not in data[:400]:
        return [], [], ""
    match = PLIST_TEX.search(data[:131072])
    if not match:
        return [], [], ""
    text = match.group(1).decode("latin1").replace("\\", "/")
    tail = text.rsplit("/", 1)[-1]
    dot = tail.rfind(".")
    if dot <= 0:
        return [], [], ""
    stem = tail[:dot]
    if not looks_like_stem(stem):
        return [], [], ""
    return [stem], [], "plist-texture"


EXTRACTORS = {
    ".bin": (extract_atlas, extract_eggimon, extract_schema),
    ".eggimonbin": (extract_eggimon,),
    ".gim": (extract_neox,),
    ".mtg": (extract_neox,),
    ".scn": (extract_neox,),
    ".lod": (extract_neox,),
    ".plist": (extract_plist,),
}


def extract_from(plain: bytes, ext: str):
    for func in EXTRACTORS.get(ext, ()):
        stems, dirs, rule = func(plain)
        if rule:
            return stems, dirs, rule
    return [], [], ""


def read_entry(package_path: str, row) -> bytes:
    offset, packed, compression = row[4], row[5], row[7]
    with open(package_path, "rb") as handle:
        handle.seek(offset)
        raw = handle.read(packed)
    return raw if compression == 0 else zstandard.ZstdDecompressor().decompressobj().decompress(raw)


def load_catalog(db_path: Path):
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=60)
    try:
        packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
        rows = [tuple(row) for row in con.execute(
            "SELECT rowid, path, package, signature, offset, packed, unpacked, compression, ext "
            "FROM files ORDER BY rowid")]
        phf = con.execute(
            "SELECT package, offset, packed, unpacked, compression FROM files WHERE path='path_hash_file'"
        ).fetchone()
    finally:
        con.close()
    return packages, rows, phf


def load_records(packages, phf):
    package, offset, packed, compression = phf[0], phf[1], phf[2], phf[4]
    with open(packages[package], "rb") as handle:
        handle.seek(offset)
        raw = handle.read(packed)
    data = raw if compression == 0 else zstandard.ZstdDecompressor().decompressobj().decompress(raw)
    plain = zlib.decompress(data[data.find(b"\x78\x9c"):])
    if len(plain) % 8:
        raise SystemExit("path_hash_file payload is not 8-byte records")
    return [struct.unpack_from("<II", plain, pos) for pos in range(0, len(plain), 8)]


def load_known_paths(cache_dir: Path, rows) -> set:
    paths = set()
    for row in rows:
        if not row[1].startswith("_unresolved/"):
            paths.add(row[1])
    generated = cache_dir / "pathdict.generated.txt.gz"
    if generated.exists():
        with gzip.open(generated, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                item = line.strip().replace("\\", "/")
                if item:
                    paths.add(item)
    extra = Path(__file__).resolve().parent / "pathdict_extra.txt"
    if extra.exists():
        for line in extra.read_text(encoding="utf-8", errors="replace").splitlines():
            item = line.strip().replace("\\", "/")
            if item:
                paths.add(item)
    return paths


class FnvIndex:
    def __init__(self, records):
        self.records = records
        self.first_pos = {}
        for i, rec in enumerate(records):
            if rec not in self.first_pos:
                self.first_pos[rec] = i

    def rebuild(self, known_fnv, anchor_range=3):
        records = self.records
        total = len(records)
        explained = bytearray(total)
        pos_paths = defaultdict(list)
        first_pos = self.first_pos
        for path, (f, l) in known_fnv.items():
            pos = first_pos.get((f, l))
            if pos is not None:
                explained[pos] = 1
                pos_paths[pos].append(path)
        self.explained = explained
        self.pos_paths = pos_paths
        runs = []
        start = None
        for i in range(total):
            if not explained[i]:
                if start is None:
                    start = i
            elif start is not None:
                runs.append((start, i))
                start = None
        if start is not None:
            runs.append((start, total))
        self.run_bounds = runs
        run_of = {}
        run_dirs = []
        run_paths = []
        for ridx, (s, e) in enumerate(runs):
            paths = []
            for pos in range(max(0, s - anchor_range), s):
                paths.extend(pos_paths.get(pos, ()))
            for pos in range(e, min(total, e + anchor_range)):
                paths.extend(pos_paths.get(pos, ()))
            dirs = set()
            for p in paths:
                head = p.rsplit("/", 1)[0] if "/" in p else ""
                if head:
                    dirs.add(head)
            for pos in range(s, e):
                run_of[pos] = ridx
            run_dirs.append(dirs)
            run_paths.append(paths)
        self.run_of = run_of
        self.run_dirs = run_dirs
        self.run_paths = run_paths
        unknown_by_fnv = defaultdict(set)
        for s, e in runs:
            for pos in range(s, e):
                f, l = records[pos]
                unknown_by_fnv[f].add(l)
        self.unknown_by_fnv = unknown_by_fnv


class PkgIndex:
    def __init__(self, rows):
        self.by_pkg = defaultdict(list)
        for row in rows:
            self.by_pkg[row[2]].append(row)
        for lst in self.by_pkg.values():
            lst.sort(key=lambda r: r[0])
        self.pos = {}
        for lst in self.by_pkg.values():
            for i, row in enumerate(lst):
                self.pos[row[0]] = i

    def around(self, row, window, named_rids, source_exts=None):
        lst = self.by_pkg[row[2]]
        i = self.pos[row[0]]
        dirs = set()
        pairs = set()
        near = []
        for j in range(max(0, i - window), min(len(lst), i + window + 1)):
            if j == i:
                continue
            other = lst[j]
            if other[0] not in named_rids:
                continue
            path = other[1]
            head, _, name = path.rpartition("/")
            if not head:
                continue
            near.append(path)
            dirs.add(head)
            if source_exts and other[8] in source_exts:
                stem = name.rsplit(".", 1)[0] if "." in name else name
                if stem:
                    pairs.add((head, stem, path))
        return dirs, pairs, near


class Solver:
    def __init__(self, args):
        self.args = args
        self.started = time.time()
        self.packages, self.rows, phf = load_catalog(args.db)
        self.records = load_records(self.packages, phf)
        self.cache_dir = args.db.parent
        self.known_paths = load_known_paths(self.cache_dir, self.rows)
        self.known_fnv = {p: (fnv1a(p.replace("/", "\\")), len(p)) for p in self.known_paths}
        self.idx = FnvIndex(self.records)
        self.idx.rebuild(self.known_fnv, args.anchor_range)
        self.unres = [row for row in self.rows if row[1].startswith("_unresolved/") and row[2].startswith("ppk/")]
        self.sig_rows = defaultdict(list)
        for row in self.unres:
            self.sig_rows[row[3]].append(row)
        self.named_rids = {row[0] for row in self.rows if not row[1].startswith("_unresolved/")}
        self.pkg = PkgIndex([row for row in self.rows if row[2].startswith("ppk/")])
        self.mont_dirs = {row[1].rsplit("/", 1)[0] for row in self.rows
                          if row[8] == ".eggimonbin" and not row[1].startswith("_unresolved/")
                          and "/" in row[1]}
        self.mapped = {}
        self.rejected = []
        self.reject_seen = set()
        self.ambiguous = 0
        self.stem_rows = {}
        self.stem_buckets = defaultdict(set)
        self.stem_rule = {}
        self.heads = {}
        self.row_by_path = {row[1]: row for row in self.unres}
        exts = {row[8] for row in self.unres} | {e for group in CAND_EXTS.values() for e in group}
        self.exts_by_len = defaultdict(list)
        for ext in exts:
            self.exts_by_len[len(ext)].append(ext)
        self.stats = {
            "catalog_rows": len(self.rows),
            "unresolved_ppk": len(self.unres),
            "fnv_records": len(self.records),
            "fnv_explained": 0,
            "fnv_unknown": 0,
            "unknown_runs": 0,
            "per_pass": [],
            "by_rule": Counter(),
            "by_ext": Counter(),
        }

    def read_stems(self):
        limit = self.args.limit
        done = 0
        for row in self.unres:
            if row[8] not in STEM_EXTS:
                continue
            if limit and done >= limit:
                break
            done += 1
            try:
                plain = unwrap(read_entry(self.packages[row[2]], row))
            except Exception:
                continue
            self.heads[row[1]] = plain[:65536]
            stems, content_dirs, rule = extract_from(plain, row[8])
            if stems:
                self.stem_rows[row[1]] = {"stems": stems, "dirs": content_dirs, "rule": rule}
        for path, info in self.stem_rows.items():
            ext = self.row_by_path[path][8]
            for stem in info["stems"]:
                if not stem.isascii():
                    continue
                for cand in CAND_EXTS[ext]:
                    self.stem_buckets[len(stem) + len(cand)].add((stem, cand))
        print("stem rows %d (read %d), %.0fs" % (len(self.stem_rows), done, time.time() - self.started), flush=True)

    def build_stem_rule(self):
        for path, info in self.stem_rows.items():
            for stem in info["stems"]:
                self.stem_rule.setdefault(stem, info["rule"])

    def cand_ext_ok(self, row_ext, cand_ext):
        return cand_ext == row_ext or cand_ext in CAND_EXTS.get(row_ext, ())

    def roundtrip_anchor_dirs(self):
        records = self.idx.records
        total = len(records)
        explained = self.idx.explained
        pos_paths = self.idx.pos_paths
        first_pos = self.idx.first_pos
        hit = miss = norec = 0
        for row in self.rows:
            if not row[2].startswith("ppk/") or row[1].startswith("_unresolved/"):
                continue
            pos = first_pos.get(self.known_fnv.get(row[1], (0, 0)))
            if pos is None:
                norec += 1
                continue
            a = b = pos
            while a > 0 and explained[a]:
                a -= 1
            while b < total - 1 and explained[b]:
                b += 1
            dirs = set()
            for cc in range(max(0, a - 3), min(total, b + 4)):
                for pth in pos_paths.get(cc, ()):
                    head = pth.rsplit("/", 1)[0]
                    if head:
                        dirs.add(head)
            true_dir = row[1].rsplit("/", 1)[0]
            if true_dir in dirs:
                hit += 1
            else:
                miss += 1
        return {"named_with_record": hit + miss, "dir_hit": hit, "dir_miss": miss, "no_record": norec}

    def roundtrip_windows(self):
        hit = miss = 0
        for row in self.rows:
            if not row[2].startswith("ppk/") or row[1].startswith("_unresolved/"):
                continue
            dirs, _, _ = self.pkg.around(row, self.args.window, self.named_rids)
            if row[1].rsplit("/", 1)[0] in dirs:
                hit += 1
            else:
                miss += 1
        return {"named": hit + miss, "dir_hit": hit, "dir_miss": miss}

    def roundtrip_pairing(self):
        stats = {}
        for target, sources in PAIR_SOURCES.items():
            hit = miss = nopair = 0
            for row in self.rows:
                if row[8] != target or not row[2].startswith("ppk/") or row[1].startswith("_unresolved/"):
                    continue
                _, pairs, _ = self.pkg.around(row, self.args.window, self.named_rids, sources)
                true_dir, _, name = row[1].rpartition("/")
                true_stem = name.rsplit(".", 1)[0]
                if any(head == true_dir and stem == true_stem for head, stem, _ in pairs):
                    hit += 1
                elif pairs:
                    miss += 1
                else:
                    nopair += 1
            stats[target] = {"named": hit + miss + nopair, "pair_hit": hit, "pair_miss": miss, "no_pair": nopair}
        return stats

    def roundtrip_stems(self):
        rng = random.Random(20261105)
        stats = {}
        for ext in STEM_EXTS:
            pool = [row for row in self.rows
                    if row[8] == ext and row[2].startswith("ppk/")
                    and not row[1].startswith("_unresolved/") and row[6] < 8388608]
            if not pool:
                continue
            sample = rng.sample(pool, min(self.args.sample, len(pool)))
            counts = Counter()
            for row in sample:
                try:
                    plain = unwrap(read_entry(self.packages[row[2]], row))
                except Exception:
                    counts["read_fail"] += 1
                    continue
                stems, _, rule = extract_from(plain, ext)
                if not rule:
                    counts["no_rule"] += 1
                    continue
                counts["rule_" + rule] += 1
                true_stem = row[1].rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if any(stem.lower() == true_stem.lower() for stem in stems):
                    counts["stem_ok"] += 1
                else:
                    counts["stem_miss"] += 1
            stats[ext] = dict(counts)
        return stats

    def pool_anchor_dirs(self):
        cands = []
        bucket = self.stem_buckets
        records = self.idx.records
        for ridx, (s, e) in enumerate(self.idx.run_bounds):
            dirs = self.idx.run_dirs[ridx]
            if not dirs:
                continue
            h_dirs = {d: fnv1a(d.replace("/", "\\") + "\\") for d in dirs}
            anchors = self.idx.run_paths[ridx][:4]
            memo = {}
            for pos in range(s, e):
                f, l = records[pos]
                for d, h0 in h_dirs.items():
                    need = l - len(d) - 1
                    if need < 2:
                        continue
                    key = (d, need)
                    pool = memo.get(key)
                    if pool is None:
                        pool = []
                        for stem, ext in bucket.get(need, ()):
                            path = d + "/" + stem + ext
                            pool.append((fnv_join(h0, stem + ext), path, stem, ext))
                        memo[key] = pool
                    for fh, path, stem, ext in pool:
                        if fh != f:
                            continue
                        sig = path_sig(path)
                        for row in self.sig_rows.get(sig, ()):
                            if not self.cand_ext_ok(row[8], ext):
                                continue
                            ev = {
                                "pool": "anchor-dir",
                                "record_pos": pos,
                                "record_len": l,
                                "fnv": "0x%08x" % f,
                                "run_size": e - s,
                                "anchors": anchors,
                                "stem": stem,
                                "stem_rule": self.stem_rule.get(stem, ""),
                            }
                            cands.append((row, path, ev))
        return cands

    def pool_anchor_stems(self):
        cands = []
        for ridx, (s, e) in enumerate(self.idx.run_bounds):
            anchors = []
            seen = set()
            for p in self.idx.run_paths[ridx]:
                if p not in seen:
                    seen.add(p)
                    anchors.append(p)
            if not anchors:
                continue
            pre = {}
            for a_path in anchors:
                head, _, name = a_path.rpartition("/")
                if not head:
                    continue
                dot = name.rfind(".")
                if dot <= 0:
                    continue
                stem = name[:dot]
                if not stem or not stem.isascii():
                    continue
                pre[a_path] = (head, stem, fnv1a(head.replace("/", "\\") + "\\"))
            if not pre:
                continue
            memo = {}
            for pos in range(s, e):
                f, l = self.idx.records[pos]
                for a_path, (head, stem, h0) in pre.items():
                    need = l - len(head) - 1 - len(stem)
                    if need < 1:
                        continue
                    key = (a_path, need)
                    pool = memo.get(key)
                    if pool is None:
                        pool = []
                        for ext in self.exts_by_len.get(need, ()):
                            path = head + "/" + stem + ext
                            pool.append((fnv_join(h0, stem + ext), path, ext))
                        memo[key] = pool
                    for fh, path, ext in pool:
                        if fh != f:
                            continue
                        sig = path_sig(path)
                        for row in self.sig_rows.get(sig, ()):
                            if not self.cand_ext_ok(row[8], ext):
                                continue
                            ev = {
                                "pool": "anchor-stem",
                                "record_pos": pos,
                                "record_len": l,
                                "fnv": "0x%08x" % f,
                                "run_size": e - s,
                                "anchor": a_path,
                                "stem": stem,
                            }
                            cands.append((row, path, ev))
        return cands

    def pool_pairing(self):
        cands = []
        for row in self.unres:
            sources = PAIR_SOURCES.get(row[8])
            if not sources:
                continue
            _, pairs, _ = self.pkg.around(row, self.args.window, self.named_rids, sources)
            if not pairs:
                continue
            h_dirs = {}
            for head, stem, src in sorted(pairs):
                path = head + "/" + stem + row[8]
                if head not in h_dirs:
                    h_dirs[head] = fnv1a(head.replace("/", "\\") + "\\")
                f = fnv_join(h_dirs[head], stem + row[8])
                lens = self.idx.unknown_by_fnv.get(f)
                if lens is None or len(path) not in lens:
                    continue
                if path_sig(path) != row[3]:
                    continue
                pos = self.idx.first_pos.get((f, len(path)))
                ev = {
                    "pool": "pair-index",
                    "record_pos": pos,
                    "record_len": len(path),
                    "fnv": "0x%08x" % f,
                    "source": src,
                    "window": self.args.window,
                }
                cands.append((row, path, ev))
        return cands

    def pool_index_dirs(self):
        cands = []
        for path, info in self.stem_rows.items():
            row = self.row_by_path[path]
            dirs, _, near = self.pkg.around(row, self.args.window, self.named_rids)
            sources = {}
            for d in dirs:
                sources[d] = ("index-neighbor", [p for p in near if p.rpartition("/")[0] == d][:3])
            for d in info["dirs"]:
                if d and d not in sources:
                    sources[d] = ("content-dir", [])
            if info["rule"] == "eggimon-uuid":
                for d in self.mont_dirs:
                    if d not in sources:
                        sources[d] = ("mont-dir", [])
            if not sources:
                continue
            h_dirs = {d: fnv1a(d.replace("/", "\\") + "\\") for d in sources}
            for stem in info["stems"]:
                if not stem.isascii():
                    continue
                for ext in CAND_EXTS[row[8]]:
                    for d, (pool_name, sup) in sources.items():
                        path = d + "/" + stem + ext
                        f = fnv_join(h_dirs[d], stem + ext)
                        lens = self.idx.unknown_by_fnv.get(f)
                        if lens is None or len(path) not in lens:
                            continue
                        if path_sig(path) != row[3]:
                            continue
                        pos = self.idx.first_pos.get((f, len(path)))
                        ev = {
                            "pool": pool_name,
                            "record_pos": pos,
                            "record_len": len(path),
                            "fnv": "0x%08x" % f,
                            "stem": stem,
                            "stem_rule": info["rule"],
                            "neighbors": sup,
                            "window": self.args.window,
                        }
                        cands.append((row, path, ev))
        return cands

    def accept(self, cands):
        by_row = defaultdict(list)
        for row, path, ev in cands:
            if path in self.known_paths:
                continue
            if row[1] in self.mapped:
                continue
            by_row[row[1]].append((row, path, ev))
        accepted = []
        for rpath, items in by_row.items():
            if len({p for _, p, _ in items}) > 1:
                self.ambiguous += 1
                continue
            row, path, ev = items[0]
            head = self.heads.get(rpath)
            if head is None:
                try:
                    head = unwrap(read_entry(self.packages[row[2]], row))[:65536]
                except Exception:
                    head = b""
                self.heads[rpath] = head
            cand_ext = "." + path.rsplit(".", 1)[-1]
            if not magic_ok(cand_ext, head):
                rkey = (rpath, path)
                if rkey not in self.reject_seen:
                    self.reject_seen.add(rkey)
                    self.rejected.append({"target": rpath, "path": path, "pool": ev["pool"],
                                          "kind": sniff(unwrap(head))})
                continue
            ev["kind"] = sniff(unwrap(head))
            accepted.append((row, {"rule": ev["pool"], "path": path, "signature": row[3], "evidence": ev}))
        return accepted

    def run(self):
        self.read_stems()
        self.build_stem_rule()
        self.stats["roundtrip"] = {
            "anchor_dirs": self.roundtrip_anchor_dirs(),
            "index_windows": self.roundtrip_windows(),
            "pairing": self.roundtrip_pairing(),
            "stems": self.roundtrip_stems(),
        }
        print("roundtrip: %s" % json.dumps(self.stats["roundtrip"], ensure_ascii=False), flush=True)
        for pass_i in range(self.args.max_passes):
            self.idx.rebuild(self.known_fnv, self.args.anchor_range)
            self.stats["fnv_explained"] = sum(self.idx.explained)
            self.stats["fnv_unknown"] = len(self.idx.records) - self.stats["fnv_explained"]
            self.stats["unknown_runs"] = len(self.idx.run_bounds)
            cands = []
            cands += self.pool_anchor_dirs()
            cands += self.pool_anchor_stems()
            cands += self.pool_pairing()
            cands += self.pool_index_dirs()
            accepted = self.accept(cands)
            for row, mapping in accepted:
                self.mapped[row[1]] = mapping
                self.known_paths.add(mapping["path"])
                self.known_fnv[mapping["path"]] = (fnv1a(mapping["path"].replace("/", "\\")), len(mapping["path"]))
                self.named_rids.add(row[0])
                self.stats["by_rule"][mapping["rule"]] += 1
                self.stats["by_ext"][self.row_by_path[row[1]][8]] += 1
            self.stats["per_pass"].append({
                "pass": pass_i + 1,
                "candidates": len(cands),
                "accepted": len(accepted),
                "mapped_total": len(self.mapped),
            })
            print("pass %d: candidates %d, accepted %d, mapped %d, %.0fs" % (
                pass_i + 1, len(cands), len(accepted), len(self.mapped), time.time() - self.started), flush=True)
            if not accepted:
                break
        remaining = Counter(row[8] for row in self.unres if row[1] not in self.mapped)
        self.stats["remaining_by_ext"] = dict(remaining.most_common())
        self.stats["by_rule"] = dict(self.stats["by_rule"])
        self.stats["by_ext"] = dict(self.stats["by_ext"].most_common())

    def write(self):
        payload = {
            "generated_by": "tools/neighbor_dir_names.py",
            "baseline": {
                "catalog_rows": self.stats["catalog_rows"],
                "unresolved_ppk": self.stats["unresolved_ppk"],
                "fnv_records": self.stats["fnv_records"],
                "fnv_explained": self.stats["fnv_explained"],
                "fnv_unknown": self.stats["fnv_unknown"],
                "unknown_runs": self.stats["unknown_runs"],
            },
            "gates": [
                "fnv1a(backslash path) hits an unexplained path_hash_file record with equal length",
                "mmh3(backslash path, 0x9747B28C) == entry signature",
                "content magic agrees with candidate extension",
            ],
            "roundtrip": self.stats.get("roundtrip", {}),
            "per_pass": self.stats["per_pass"],
            "named": len(self.mapped),
            "by_rule": self.stats["by_rule"],
            "by_ext": self.stats["by_ext"],
            "ambiguous": self.ambiguous,
            "rejected": self.rejected,
            "remaining_by_ext": self.stats["remaining_by_ext"],
            "mappings": self.mapped,
        }
        self.args.out.parent.mkdir(parents=True, exist_ok=True)
        self.args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print("wrote %s  named %d / %d  %.0fs" % (
            self.args.out, len(self.mapped), self.stats["unresolved_ppk"], time.time() - self.started), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--window", type=int, default=48)
    parser.add_argument("--anchor-range", type=int, default=3)
    parser.add_argument("--max-passes", type=int, default=5)
    parser.add_argument("--sample", type=int, default=60)
    parser.add_argument("--limit", type=int, default=0, help="cap stem-extraction reads (0 = all)")
    args = parser.parse_args()
    solver = Solver(args)
    solver.run()
    solver.write()
    return 0


if __name__ == "__main__":
    sys.exit(main())
