#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mine every NPK / PPK payload for path tables and path-like text blocks.

Streams each catalog entry in fixed chunks (never loads a whole package),
decompresses zstd payloads on the fly, cuts printable runs out of the byte
stream, and collects candidates shaped like ``a/b.ext``.  A candidate is kept
when MurmurHash3(candidate, seed 0x9747B28C) hits the signature of an entry
that is still unresolved; candidates that own no known path hash are stored
for the pathdict diff.  Contiguous newline separated path lists are reported
as table blocks.

Phases: scan -> merge -> verify.  Run ``--phase all`` (default) for everything.
"""

from __future__ import annotations

import argparse
import array
import gzip
import heapq
import json
import os
import re
import sqlite3
import struct
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mmh3
import zstandard

import nxpk_mode3 as N

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
CHUNK_IN = 1 << 20
TEXT_CHUNK = 1 << 18
CARRY = 512
MIN_RUN = 48
MAX_CAND = 260
MAX_TABLE_BLOCKS = 200
MAX_HITS = 300000
TABLE_MIN_LINES = 20
ALIAS_PAIRS = {
    ("blendspace1d", "blendspace"),
    ("blendspace", "blendspace1d"),
    ("cube", "dds"),
    ("spr", "bin"),
    ("shv", "bin"),
    ("qss", "bin"),
}
PRINT_BYTES = bytes(range(32, 127)) + b"\r\n\t"
EXTRA_EXTS = {
    "tga", "psd", "png", "jpg", "jpeg", "bmp", "dds", "kta", "ktx", "gim",
    "mesh", "mtg", "sfx", "efx", "bnk", "wem", "skeleton", "animation",
    "rawanimation", "animconfig", "plist", "csb", "json", "scn", "stb", "nstb",
    "trackgroup", "animresmap", "animgraph", "blendspace", "blendspace1d",
    "luac", "lua", "trk", "fbx", "lod", "gmp", "hlsl", "mp4", "eggimonbin",
    "postprocess", "txt", "xml", "csv", "ini", "cfg", "lang", "prefab", "wav",
    "ogg", "mp3", "ktx2", "astc", "basis", "spx", "spr", "atlas", "skel",
}

RUN_RE = re.compile(rb"[\x20-\x7e\r\n\t]{%d,}" % MIN_RUN)
TOKEN_RE = re.compile(rb"[A-Za-z0-9_.\-\\/]{4,%d}\.[A-Za-z0-9]{1,12}" % MAX_CAND)
SAFE_RE = re.compile(r"[A-Za-z0-9_.\-/]+")
PATH_LINE_RE = re.compile(rb"[A-Za-z0-9_.\-\\/]{3,300}\.[A-Za-z0-9]{1,12}")

_WORK = {}


def default_cache_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "EggyNpkBrowser"
    return Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "npk-x"


def read_entry_chunks(package_path: Path, offset: int, packed: int, compression: int):
    with open(package_path, "rb") as handle:
        handle.seek(offset)
        left = packed
        if compression == 3:
            obj = zstandard.ZstdDecompressor().decompressobj()
            while left > 0:
                raw = handle.read(min(CHUNK_IN, left))
                if not raw:
                    break
                left -= len(raw)
                out = obj.decompress(raw)
                if out:
                    yield out
        elif compression == 0:
            while left > 0:
                raw = handle.read(min(CHUNK_IN, left))
                if not raw:
                    break
                left -= len(raw)
                yield raw
        else:
            raise ValueError("unsupported compression %d" % compression)


def split_sized(chunks, size: int):
    for chunk in chunks:
        if len(chunk) <= size:
            yield chunk
        else:
            for start in range(0, len(chunk), size):
                yield chunk[start:start + size]


def unwrap_zstd(chunks, state):
    first = next(iter(chunks), None)
    if first is None:
        return iter(())
    if first[:4] != ZSTD_MAGIC:
        return _chain(first, chunks)

    state["nested"] += 1

    def gen():
        obj = zstandard.ZstdDecompressor().decompressobj()
        try:
            out = obj.decompress(first)
            if out:
                yield out
            for chunk in chunks:
                out = obj.decompress(chunk)
                if out:
                    yield out
                if obj.eof:
                    break
        except zstandard.ZstdError:
            state["nested_fail"] += 1

    return gen()


def _chain(first, chunks):
    yield first
    for chunk in chunks:
        yield chunk


def normalize_token(raw: bytes):
    if b"/" not in raw and b"\\" not in raw:
        return None
    text = raw.decode("latin1").replace("\\", "/")
    had_lead = text.startswith("/")
    text = text.lstrip("/")
    while text.startswith("./"):
        text = text[2:]
    if len(text) < 5 or len(text) > MAX_CAND:
        return None
    if "//" in text or ".." in text or text.startswith("."):
        return None
    name = text.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    if dot <= 0:
        return None
    ext = name[dot + 1:].lower()
    if ext not in _WORK["exts"]:
        return None
    if not SAFE_RE.fullmatch(text):
        return None
    if not any(ch.isalpha() for ch in text.split("/", 1)[0]):
        return None
    forms = [(text, "exact")]
    if had_lead:
        forms.append(("/" + text, "lead"))
    if text.lower() != text:
        forms.append((text.lower(), "lower"))
    if text.upper() != text:
        forms.append((text.upper(), "upper"))
    return text, forms


def path_in_payload(payload: bytes, path: str):
    head = payload[:32 * 1024 * 1024]
    for form in (path, path.replace("/", "\\")):
        if form.encode("utf-8", "ignore") in head:
            return True
    return path.lower().encode("utf-8", "ignore") in head.lower()


def stem_in_payload(payload: bytes, path: str):
    head = payload[:32 * 1024 * 1024]
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if len(stem) < 6:
        return False
    return stem.lower().encode("utf-8", "ignore") in head.lower()


def table_block(run: bytes):
    if len(run) < 2048:
        return None
    lines = [line.strip() for line in run.split(b"\n") if line.strip()]
    if len(lines) < TABLE_MIN_LINES:
        return None
    good = sum(1 for line in lines if PATH_LINE_RE.fullmatch(line))
    if good < len(lines) * 0.8:
        return None
    return {
        "lines": len(lines),
        "good": good,
        "sample": [line.decode("latin1")[:120] for line in lines[:3]],
    }


def note_candidate(text, forms, ctx):
    unresolved = _WORK["unresolved"]
    known = _WORK["known"]
    hashes = set()
    hit = False
    for form, kind in forms:
        sig = N.path_hash(form)
        hashes.add(sig)
        if sig in unresolved:
            hit = True
            key = (sig, text, kind)
            if key not in ctx["hits"] and len(ctx["hits"]) < MAX_HITS:
                ctx["hits"][key] = {
                    "sig": sig,
                    "candidate": text,
                    "form": form,
                    "kind": kind,
                    "entry": ctx["entry"],
                    "package": ctx["package"],
                }
    if hit:
        ctx["state"]["hits"] += 1
        return
    for sig in hashes:
        if sig in known:
            ctx["state"]["cand_known"] += 1
            return
    if text in ctx["cands"]:
        ctx["state"]["cand_dup"] += 1
        return
    ctx["cands"].add(text)
    ctx["state"]["cand_new"] += 1
    ctx["entry_cands"][ctx["entry"]] += 1


def scan_stream(chunks, ctx):
    carry = b""
    state = ctx["state"]
    for chunk in chunks:
        state["bytes_out"] += len(chunk)
        if chunk:
            nonprint = len(chunk.translate(None, PRINT_BYTES))
            state["nonprint"] += nonprint
            if nonprint > len(chunk) * 0.55:
                carry = (carry + chunk)[-CARRY:]
                continue
        buf = carry + chunk if carry else chunk
        skip_head = len(carry)
        for match in RUN_RE.finditer(buf):
            run = match.group(0)
            run_at = match.start()
            state["runs"] += 1
            if len(run) >= 2048:
                block = table_block(run)
                if block is not None and len(ctx["blocks"]) < MAX_TABLE_BLOCKS:
                    key = (ctx["entry"], block["sample"][0])
                    if key not in ctx["block_seen"]:
                        ctx["block_seen"].add(key)
                        block["entry"] = ctx["entry"]
                        block["package"] = ctx["package"]
                        ctx["blocks"].append(block)
                        state["table_blocks"] += 1
            for token in TOKEN_RE.finditer(run):
                state["tokens"] += 1
                if skip_head and run_at + token.start() == 0:
                    state["head_cut"] += 1
                    continue
                text = normalize_token(token.group(0))
                if text is not None:
                    note_candidate(text[0], text[1], ctx)
        carry = buf[-CARRY:]


def scan_entry(row, package_path: Path, ctx):
    state = ctx["state"]
    offset = int(row["offset"])
    packed = int(row["packed"])
    unpacked = int(row["unpacked"])
    compression = int(row["compression"])
    if packed <= 0 or unpacked <= 0:
        return
    state["entries"] += 1
    state["bytes_in"] += packed
    try:
        chunks = read_entry_chunks(package_path, offset, packed, compression)
        chunks = split_sized(chunks, TEXT_CHUNK)
        chunks = unwrap_zstd(chunks, state)
        scan_stream(chunks, ctx)
    except Exception:
        state["errors"] += 1


def flush_candidates(ctx):
    if not ctx["cands"]:
        return
    path = Path(ctx["cand_path"])
    lines = sorted(ctx["cands"])
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")
    ctx["cands"].clear()


def do_package(task):
    name, package_path, db_path, work, max_mb = task
    state = Counter()
    ctx = {
        "state": state,
        "package": name,
        "entry": "",
        "cands": set(),
        "hits": {},
        "blocks": [],
        "block_seen": set(),
        "entry_cands": Counter(),
        "cand_path": str(Path(work) / "cand" / (name.replace("/", "_") + ".txt")),
    }
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT path, offset, packed, unpacked, compression FROM files WHERE package=? ORDER BY offset",
        (name,),
    ).fetchall()
    con.close()

    try:
        Path(ctx["cand_path"]).unlink()
    except FileNotFoundError:
        pass

    started = time.time()
    for row in rows:
        if max_mb and int(row["unpacked"]) > max_mb * 1024 * 1024:
            state["skipped_big"] += 1
            continue
        ctx["entry"] = row["path"]
        scan_entry(row, Path(package_path), ctx)

    flush_candidates(ctx)

    stem = name.replace("/", "_")
    out_dir = Path(work)
    with open(out_dir / "hits" / (stem + ".jsonl"), "w", encoding="utf-8", newline="\n") as handle:
        for item in ctx["hits"].values():
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    stats = {
        "package": name,
        "path": str(package_path),
        "entries": state["entries"],
        "skipped_big": state["skipped_big"],
        "bytes_in": state["bytes_in"],
        "bytes_out": state["bytes_out"],
        "nonprint": state["nonprint"],
        "runs": state["runs"],
        "tokens": state["tokens"],
        "head_cut": state["head_cut"],
        "cand_new": state["cand_new"],
        "cand_dup": state["cand_dup"],
        "cand_known": state["cand_known"],
        "hits": state["hits"],
        "table_blocks": state["table_blocks"],
        "nested": state["nested"],
        "nested_fail": state["nested_fail"],
        "errors": state["errors"],
        "seconds": round(time.time() - started, 1),
        "top_entries": [
            {"entry": entry, "candidates": count}
            for entry, count in ctx["entry_cands"].most_common(60)
        ],
        "blocks": ctx["blocks"],
    }
    (out_dir / "stats" / (stem + ".json")).write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return name, stats


def init_worker(work):
    work = Path(work)
    unresolved_raw = json.loads((work / "unresolved.json").read_text("utf-8"))
    unresolved = {}
    for sig, rows in unresolved_raw.items():
        unresolved[int(sig)] = rows
    known = array.array("I")
    data = (work / "known.bin").read_bytes()
    known.frombytes(data)
    _WORK["unresolved"] = unresolved
    _WORK["known"] = set(known)
    _WORK["exts"] = set(json.loads((work / "exts.json").read_text("utf-8")))
    for sub in ("cand", "hits", "stats"):
        (work / sub).mkdir(parents=True, exist_ok=True)


def prepare(db_path: Path, work: Path):
    work.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    unresolved = defaultdict(list)
    for row in con.execute(
        "SELECT path, signature, ext, package FROM files WHERE substr(path,1,12)='_unresolved/'"
    ):
        unresolved[row[1]].append([row[0], row[2].lstrip(".") if row[2] else "", row[3]])
    known = array.array("I")
    for (sig,) in con.execute(
        "SELECT DISTINCT signature FROM files WHERE substr(path,1,12)!='_unresolved/'"
    ):
        known.append(int(sig) & 0xFFFFFFFF)
    exts = {row[0].lstrip(".").lower() for row in con.execute("SELECT DISTINCT ext FROM files") if row[0]}
    exts |= EXTRA_EXTS
    con.close()
    (work / "unresolved.json").write_text(
        json.dumps({str(k): v for k, v in unresolved.items()}, ensure_ascii=False), encoding="utf-8"
    )
    (work / "known.bin").write_bytes(known.tobytes())
    (work / "exts.json").write_text(json.dumps(sorted(exts)), encoding="utf-8")
    print("prepare: unresolved sigs %d, known sigs %d, exts %d" % (
        len(unresolved), len(known), len(exts)))
    return len(unresolved), len(known)


def list_packages(db_path: Path, only: str):
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    rows = con.execute(
        "SELECT packages.name, packages.path, count(*) FROM files "
        "JOIN packages ON packages.name = files.package "
        "GROUP BY files.package ORDER BY 3 DESC"
    ).fetchall()
    con.close()
    picks = []
    for name, path, count in rows:
        is_ppk = name.startswith("ppk/")
        if only == "ppk" and not is_ppk:
            continue
        if only == "npk" and is_ppk:
            continue
        picks.append((name, path))
    return picks


def build_root_index(known_paths):
    counts = Counter()
    for text in known_paths:
        if "/" in text:
            counts[text.split("/", 1)[0]] += 1
    return {root for root, count in counts.items() if count >= 20}


def build_glue_re(roots):
    if not roots:
        return None
    pattern = "|".join(re.escape(root) for root in sorted(roots, key=len, reverse=True))
    return re.compile(r"(?<=[A-Za-z0-9_])(?:" + pattern + r")/")


def trim_glued(text: str, roots, glue_re):
    if glue_re is None:
        return []
    if text.split("/", 1)[0] in roots:
        return []
    out = []
    position = 1
    while True:
        match = glue_re.search(text, position)
        if not match:
            break
        trimmed = text[match.start():]
        if len(trimmed) >= 8:
            out.append(trimmed)
        position = match.start() + 1
    return out


def package_of_stem(stem: str):
    return "ppk/" + stem[4:] if stem.startswith("ppk_") else stem


def merge_phase(work: Path, pathdict: Path | None, out_dir: Path):
    cand_files = sorted((work / "cand").glob("*.txt"))
    known_paths = set()
    total_known = 0
    if pathdict and Path(pathdict).is_file():
        with gzip.open(pathdict, "rt", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                text = line.strip().replace("\\", "/").lower()
                if text:
                    known_paths.add(text)
                    total_known += 1
    roots = build_root_index(known_paths)
    glue_re = build_glue_re(roots)
    unresolved_raw = json.loads((work / "unresolved.json").read_text("utf-8"))
    unresolved = {int(sig): rows for sig, rows in unresolved_raw.items()}

    augment_hits = {}
    extras = set()
    glued_originals = set()
    for path in cand_files:
        package = package_of_stem(path.stem)
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                text = line.rstrip("\n")
                if not text:
                    continue
                trims = trim_glued(text, roots, glue_re)
                if trims:
                    glued_originals.add(text)
                for trimmed in trims:
                    if trimmed in extras:
                        continue
                    extras.add(trimmed)
                    for form, kind in ((trimmed, "trim"), (trimmed.lower(), "lower"), (trimmed.upper(), "upper")):
                        sig = N.path_hash(form)
                        if sig in unresolved:
                            augment_hits.setdefault((sig, trimmed, kind), {
                                "sig": sig, "candidate": trimmed, "form": form, "kind": kind,
                                "entry": "(glued prefix)", "package": package, "source": text,
                            })

    readers = [open(path, "r", encoding="utf-8", errors="ignore") for path in cand_files]
    merged = heapq.merge(*readers)
    seen = set()
    out_path = out_dir / "pathtable-new-paths.txt.gz"
    total = 0
    new = 0
    prev = None
    out_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8", newline="\n") as handle:
        for line in merged:
            text = line.rstrip("\n")
            if not text or text == prev:
                continue
            prev = text
            total += 1
            seen.add(text)
            if text.lower() in known_paths or text in glued_originals:
                continue
            new += 1
            handle.write(text)
            handle.write("\n")
        extra_new = 0
        for text in sorted(extras):
            if text in seen or text.lower() in known_paths:
                continue
            extra_new += 1
            handle.write(text)
            handle.write("\n")
    for handle in readers:
        handle.close()
    if augment_hits:
        with open(work / "hits" / "augment.jsonl", "w", encoding="utf-8", newline="\n") as handle:
            for item in augment_hits.values():
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    print("merge: candidate paths %d, new vs pathdict %d, glued extras %d (pathdict %d)" % (
        total, new + extra_new, extra_new, total_known))
    return total, new + extra_new, out_path


def load_hits(work: Path):
    hits = {}
    for path in sorted((work / "hits").glob("*.jsonl")):
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                key = (int(item["sig"]), item["candidate"], item.get("kind", "exact"))
                if key not in hits:
                    hits[key] = item
    return hits


def verify_phase(db_path: Path, work: Path, out_dir: Path):
    import npk_browser as B

    hits = load_hits(work)
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    con.row_factory = sqlite3.Row
    unresolved = defaultdict(list)
    for row in con.execute(
        "SELECT path, signature, ext, package, offset, packed, unpacked, compression "
        "FROM files WHERE substr(path,1,12)='_unresolved/'"
    ):
        unresolved[row["signature"]].append(row)
    named_by_sig = {}
    for row in con.execute(
        "SELECT path, signature FROM files WHERE substr(path,1,12)!='_unresolved/'"
    ):
        named_by_sig.setdefault(row["signature"], row["path"])
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    dirs = {row[0].lower() for (row,) in con.execute("SELECT path FROM dirs")}
    con.close()

    by_sig = defaultdict(list)
    for item in hits.values():
        by_sig[item["sig"]].append(item)
    print("verify: hit sigs %d, hit records %d" % (len(by_sig), len(hits)))

    results = {}
    near = []
    weak = []
    ambiguous = []
    stats = Counter()

    def rank(kinds):
        if "exact" in kinds:
            return 0
        if "lead" in kinds or "trim" in kinds:
            return 1
        return 2

    for sig in sorted(by_sig):
        rows = unresolved.get(sig) or []
        if not rows:
            stats["hit_not_unresolved"] += 1
            continue
        rows_by_ext = defaultdict(list)
        for row in rows:
            rows_by_ext[(row["ext"] or "").lstrip(".").lower()].append(row)
        cand_kinds = defaultdict(set)
        for item in by_sig[sig]:
            cand_kinds[item["candidate"]].add(item.get("kind", "exact"))
        pool = sorted(cand_kinds, key=lambda cand: (rank(cand_kinds[cand]), cand))
        best_rank = rank(cand_kinds[pool[0]])
        pool = [cand for cand in pool if rank(cand_kinds[cand]) == best_rank]
        if len(pool) > 1:
            ambiguous.append({
                "sig": sig, "sig_hex": "%08x" % sig, "candidates": pool[:8],
                "reason": "multiple candidates share the signature",
            })
            stats["ambiguous"] += 1
            continue
        picked = pool[0]
        kinds = cand_kinds[picked]
        ext = picked.rsplit(".", 1)[-1].lower()
        if best_rank == 2:
            folder = picked.rsplit("/", 1)[0].lower() if "/" in picked else ""
            if folder not in dirs:
                weak.append({
                    "sig": sig, "sig_hex": "%08x" % sig, "candidate": picked,
                    "kind": sorted(kinds), "reason": "case variant, directory unknown",
                })
                stats["weak_case"] += 1
                continue
        target_ext = ext
        if ext not in rows_by_ext:
            target_ext = ""
            for other in rows_by_ext:
                if (ext, other) in ALIAS_PAIRS:
                    target_ext = other
                    break
            if not target_ext:
                near.append({
                    "sig": sig, "sig_hex": "%08x" % sig,
                    "candidates": sorted(cand_kinds),
                    "reason": "extension mismatch",
                    "wanted": sorted(rows_by_ext),
                })
                stats["ext_mismatch"] += 1
                continue
        target = rows_by_ext[target_ext][0]
        package_path = packages.get(target["package"])
        if not package_path:
            stats["no_package"] += 1
            continue
        try:
            entry = N.Entry(
                target["signature"], target["offset"], target["packed"],
                target["unpacked"], 0, 0, target["compression"], 0,
            )
            payload = B.read_entry_bytes(package_path, entry)
            sniff = B.detect_unresolved(package_path, entry)
        except Exception as exc:
            near.append({
                "sig": sig, "sig_hex": "%08x" % sig, "candidate": picked,
                "reason": "entry read failed: %s" % exc,
            })
            stats["read_fail"] += 1
            continue
        sniff_ext = (sniff[0] or "").lstrip(".").lower()
        if sniff_ext and sniff_ext != target_ext:
            near.append({
                "sig": sig, "sig_hex": "%08x" % sig, "candidate": picked,
                "reason": "sniffed extension differs", "sniffed": sniff_ext, "ext": target_ext,
            })
            stats["sniff_mismatch"] += 1
            continue
        self_named = path_in_payload(payload, picked)
        stem_named = stem_in_payload(payload, picked)
        rule = "pathtable"
        if target_ext != ext:
            cubemap = (target_ext == "dds" and payload[:4] == b"DDS "
                       and struct.unpack_from("<I", payload, 112)[0] & 0x200)
            if self_named:
                rule = "pathtable-alias-self"
            elif cubemap:
                rule = "pathtable-alias-cubemap"
            elif stem_named:
                rule = "pathtable-alias-stem"
            else:
                near.append({
                    "sig": sig, "sig_hex": "%08x" % sig, "candidate": picked,
                    "reason": "extension alias without content evidence",
                    "wanted": sorted(rows_by_ext),
                })
                stats["alias_unproven"] += 1
                continue
        elif best_rank != 0:
            rule = "pathtable-normalized"
        if sig in named_by_sig:
            near.append({
                "sig": sig, "sig_hex": "%08x" % sig, "candidate": picked,
                "reason": "signature already named", "named": named_by_sig[sig],
            })
            stats["named_collision"] += 1
            continue
        hit_item = min(
            (item for item in by_sig[sig] if item["candidate"] == picked),
            key=lambda item: rank({item.get("kind", "exact")}),
        )
        form = hit_item.get("form", picked)
        if N.path_hash(form) != sig:
            near.append({
                "sig": sig, "sig_hex": "%08x" % sig, "candidate": picked, "form": form,
                "reason": "recomputed hash mismatch",
            })
            stats["hash_mismatch"] += 1
            continue
        for row in rows:
            results[row["path"]] = {
                "rule": rule,
                "path": form,
                "path_normalized": picked,
                "sig": "%08x" % sig,
                "package": row["package"],
                "ext": ext,
                "entry_ext": target_ext,
                "kind": sorted(kinds),
                "self_named": self_named,
                "stem_named": stem_named,
                "evidence": ("glued candidate %r" % hit_item["source"]) if hit_item.get("source")
                else ("path-like text in %s (%s)" % (hit_item["entry"], hit_item["package"])),
            }
        stats["verified"] += len(rows)
        if rule.startswith("pathtable-alias"):
            stats["verified_alias"] += len(rows)
        elif rule == "pathtable":
            stats["verified_exact"] += len(rows)
        else:
            stats["verified_normalized"] += len(rows)

    strict = {k: v for k, v in results.items() if not v["rule"].startswith("pathtable-alias")}
    alias = {k: v for k, v in results.items() if v["rule"].startswith("pathtable-alias")}
    payload = {
        "generated_by": "tools/ppk_pathtable_scan.py",
        "total_unresolved": sum(len(v) for v in unresolved.values()),
        "hit_sigs": len(by_sig),
        "hit_candidates": len(hits),
        "verified": len(results),
        "mappings": strict,
        "alias_mappings": alias,
        "near_misses": near,
        "weak_case_hits": weak,
        "ambiguous": ambiguous,
        "stats": dict(stats),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "04-pathtable-mined-names.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("verify: verified %d rows (%d strict, %d alias), near misses %d, weak %d, ambiguous %d -> %s" % (
        len(results), len(strict), len(alias), len(near), len(weak), len(ambiguous), out_file))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=default_cache_dir() / "catalog.sqlite")
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep")
    parser.add_argument("--pathdict", type=Path, default=None)
    parser.add_argument("--phase", choices=["prepare", "scan", "merge", "verify", "all"], default="all")
    parser.add_argument("--only", choices=["all", "npk", "ppk"], default="all")
    parser.add_argument("--workers", type=int, default=max(1, min(10, (os.cpu_count() or 4) - 2)))
    parser.add_argument("--max-mb", type=int, default=0)
    parser.add_argument("--package", default="", help="substring filter for package names")
    parser.add_argument("--reuse", action="store_true", help="skip packages whose stats file exists")
    args = parser.parse_args()

    work = args.work or (args.db.parent / "pathtable_mine")
    pathdict = args.pathdict or (args.db.parent / "pathdict.generated.txt.gz")
    if args.phase in ("prepare", "all"):
        prepare(args.db, work)
    if args.phase in ("scan", "all"):
        packages = list_packages(args.db, args.only)
        if args.package:
            packages = [item for item in packages if args.package in item[0]]
        if args.reuse:
            packages = [
                item for item in packages
                if not (work / "stats" / (item[0].replace("/", "_") + ".json")).is_file()
            ]
        tasks = [(name, path, str(args.db), str(work), args.max_mb) for name, path in packages]
        print("scan: %d packages, %d workers" % (len(tasks), args.workers))
        started = time.time()
        done = 0
        with Pool(args.workers, initializer=init_worker, initargs=(str(work),)) as pool:
            for name, stats in pool.imap_unordered(do_package, tasks, chunksize=1):
                done += 1
                print("[%d/%d] %-22s entries=%-6d bytes=%8.1fMB out=%8.1fMB runs=%-8d tokens=%-9d new=%-7d hits=%-5d blocks=%-3d %.0fs" % (
                    done, len(tasks), name, stats["entries"], stats["bytes_in"] / 1e6,
                    stats["bytes_out"] / 1e6, stats["runs"], stats["tokens"],
                    stats["cand_new"], stats["hits"], stats["table_blocks"],
                    time.time() - started))
    if args.phase in ("merge", "all"):
        merge_phase(work, pathdict, args.out)
    if args.phase in ("verify", "all"):
        verify_phase(args.db, work, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
