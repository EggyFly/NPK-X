#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mine new vocabulary sources for unresolved path_hash_file gap records.

Sources wired in this tool:
  client_bin   path-like strings + word tokens from PC client.exe
  xn           decrypted seeded .xn module string constants
  refs         path strings referenced by named text entries (json/mtg/gim/...)
  unresolved_refs path strings referenced by unresolved entries themselves
  family_dir   pattern extrapolation of cross_dir runs (prefix_v_suffix dirs,
               digit dir grids) fed by all token pools above
  wwise_names  bnk/wem stems from catalog as extra vocabulary

Every candidate must pass: FNV-1a(backslash path)+length hit on an unexplained
path_hash_file record, mmh3(backslash path) equal to an unresolved signature,
and a content sniff compatible with the candidate extension.  32-bit collisions
(same signature reached by different candidate paths) are all reported.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import external_pathtable_hunt as H
import fnv_interval_solve as S
import npk_browser as B
import nxpk_mode3 as N

SWEEP = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep"
DEFAULT_OUT = SWEEP / "16-pc-vocab-mining-names.json"
CLIENT_EXE_CANDIDATES = (
    r"D:\FeverApps\party_pc\bin\client.exe",
    r"D:\蛋仔派对\client_scan.exe",
)

TEXT_EXTS = (".mtg", ".gim", ".efx", ".stb", ".scn", ".lod", ".atlas", ".plist",
             ".animconfig", ".surf", ".conf", ".lua", ".skpatch", ".spr",
             ".animresmap", ".nstb", ".meta", ".blendspace1d", ".trackgroup",
             ".skeleton", ".json", ".hlsl", ".xml")
UNRES_EXTS = (".mtg", ".gim", ".efx", ".stb", ".scn", ".lod", ".atlas", ".plist",
              ".animconfig", ".surf", ".mesh", ".csb", ".bin", ".json", ".eggimonbin",
              ".mesh.lod")
RAW_PAT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-/]{3,150}")
PATH_PAT = re.compile(r"^(?=.{5,200}$)[a-z0-9_][a-z0-9_.\-/]*(/[a-z0-9_][a-z0-9_.\-/]*)+\.[a-z0-9]{2,6}$")
PVS_PAT = re.compile(r"^([a-z][a-z0-9]{2,})_([a-z0-9]{1,24})_([a-z0-9]{2,})$")
TOKEN_PAT = re.compile(r"^[a-z0-9_]{1,24}$")
DIGIT_CAP = 100000
STEM_NUM_CAP = 20000


def fnv_of(path: str) -> int:
    return H.fnv1a(path.replace("/", "\\"))


def load_context(db: Path):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    records, table_info = H.load_table(con, packages)
    paths = S.load_paths(db.parent, con)
    known = {}
    for path in paths:
        known.setdefault(fnv_of(path), path)
    unresolved = H.load_unresolved(con, packages)
    con.close()
    runs = S.build_runs(records, known)
    gap = {}
    for _low, items, _high in runs:
        for sig, length in items:
            gap[(sig, length)] = None
    return {"packages": packages, "records": records, "table_info": table_info,
            "paths": paths, "known": known, "unresolved": unresolved,
            "runs": runs, "gap": gap}


def collect_strings(text: str, pool: set, host: str = ""):
    for match in RAW_PAT.findall(text):
        s = match.strip(".-/").lower()
        if s in pool:
            continue
        pool.add(s)
        if PATH_PAT.match(s):
            yield s


def scan_entry_paths(ctx, rows, pool, hits, verdict, label):
    packages = ctx["packages"]
    gap = ctx["gap"]
    unresolved = ctx["unresolved"]
    for row in rows:
        try:
            data = B.read_entry_bytes(packages[row["package"]], row)
        except Exception:
            verdict[label + "_read_fail"] += 1
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        for cand in collect_strings(text, pool):
            key = (fnv_of(cand), len(cand))
            if key not in gap:
                continue
            if N.path_hash(cand.replace("/", "\\")) not in unresolved:
                verdict[label + "_mmh3_miss"] += 1
                continue
            if key not in hits:
                hits[key] = {"path": cand, "rule": label}


def source_client_bin(ctx, tokens, hits, verdict):
    data = None
    for cand_path in CLIENT_EXE_CANDIDATES:
        if Path(cand_path).is_file():
            data = Path(cand_path).read_bytes()
            break
    if data is None:
        verdict["client_bin_absent"] += 1
        return
    strings = re.findall(rb"[\x20-\x7e]{4,200}", data)
    pool = set()
    for raw in strings:
        s = raw.decode("ascii")
        if "/" in s and " " not in s and "%" not in s and s.count("/") <= 8:
            s = s.strip("/")
            pool.add(s.strip("/").lower())
        for m in re.findall(r"[a-z0-9_]{3,40}", s):
            if "_" in m:
                tokens.add(m)
    for cand in pool:
        if not PATH_PAT.match(cand):
            continue
        key = (fnv_of(cand), len(cand))
        if key in ctx["gap"] and N.path_hash(cand.replace("/", "\\")) in ctx["unresolved"]:
            if key not in hits:
                hits[key] = {"path": cand, "rule": "client_bin"}


def source_xn(ctx, tokens, hits, verdict):
    seed_path = Path(__file__).resolve().parent / "xn_seed.json"
    try:
        seed = json.loads(seed_path.read_text("utf-8"))
    except (OSError, ValueError):
        verdict["xn_seed_absent"] += 1
        return
    name_map = {int(k): v for k, v in seed.get("map", {}).items()}
    import xn_crypto
    import xn_parse
    con = sqlite3.connect("file:%s?mode=ro" % ctx["db"], uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT path, signature, package, offset, packed, unpacked, compression "
        "FROM files WHERE ext='.xn'"))
    con.close()
    pool = set()
    ok = fail = 0
    for row in rows:
        if row["signature"] not in name_map:
            continue
        try:
            data = B.read_entry_bytes(ctx["packages"][row["package"]], row)
            plain = xn_crypto.decrypt(data, name_map[row["signature"]])
        except Exception:
            fail += 1
            continue
        if plain is None:
            fail += 1
            continue
        ok += 1
        try:
            values = xn_parse.candidates(plain)
        except Exception:
            fail += 1
            continue
        for value in values:
            if len(value) > 200:
                continue
            s = value.strip().lower()
            pool.add(s)
            for m in re.findall(r"[a-z0-9_]{3,40}", s):
                if "_" in m:
                    tokens.add(m)
    verdict["xn_decrypted"] += ok
    verdict["xn_decrypt_fail"] += fail
    for cand in pool:
        if not PATH_PAT.match(cand):
            continue
        key = (fnv_of(cand), len(cand))
        if key in ctx["gap"] and N.path_hash(cand.replace("/", "\\")) in ctx["unresolved"]:
            if key not in hits:
                hits[key] = {"path": cand, "rule": "xn"}


def source_named_refs(ctx, tokens, hits, verdict):
    con = sqlite3.connect("file:%s?mode=ro" % ctx["db"], uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT path, package, offset, packed, unpacked, compression, ext FROM files "
        "WHERE path NOT LIKE '_unresolved/%%' AND ext IN (%s) AND unpacked <= 8388608"
        % ",".join("'%s'" % e for e in TEXT_EXTS)))
    con.close()
    pool = set()
    scan_entry_paths(ctx, rows, pool, hits, verdict, "named_refs")
    for value in pool:
        for m in re.findall(r"[a-z0-9_]{3,40}", value):
            if "_" in m:
                tokens.add(m)


def source_unresolved_refs(ctx, tokens, hits, verdict):
    con = sqlite3.connect("file:%s?mode=ro" % ctx["db"], uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT path, package, offset, packed, unpacked, compression, ext FROM files "
        "WHERE path LIKE '_unresolved/%%' AND ext IN (%s) AND unpacked <= 16777216"
        % ",".join("'%s'" % e for e in UNRES_EXTS)))
    con.close()
    pool = set()
    scan_entry_paths(ctx, rows, pool, hits, verdict, "unresolved_refs")
    for value in pool:
        for m in re.findall(r"[a-z0-9_]{3,40}", value):
            if "_" in m:
                tokens.add(m)


def source_wwise_names(ctx, tokens, hits, verdict):
    con = sqlite3.connect("file:%s?mode=ro" % ctx["db"], uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    for row in con.execute("SELECT path FROM files WHERE path NOT LIKE '_unresolved/%%' AND (ext='.bnk' OR ext='.wem')"):
        stem = Path(row["path"]).stem.lower()
        if 3 <= len(stem) <= 60:
            tokens.add(stem)
    for row in con.execute("SELECT path FROM files WHERE path NOT LIKE '_unresolved/%%' AND (ext='.gmp' OR ext='.scn')"):
        stem = Path(row["path"]).stem.lower()
        if 3 <= len(stem) <= 60:
            tokens.add(stem)
    con.close()


def build_vocab(ctx, tokens):
    dirs = set()
    stems = set()
    for path in ctx["paths"]:
        name = path.rsplit("/", 1)[-1]
        stem = name.rsplit(".", 1)[0] if "." in name else name
        stems.add(stem.lower())
        parent = path.rsplit("/", 1)[0]
        if parent:
            dirs.add(parent)
    return dirs, stems


def anchor_patterns(ctx):
    """Collect prefix_v_suffix directory patterns and their variable segments."""
    pvs = {}
    for path in ctx["paths"]:
        parent, name = path.rsplit("/", 1) if "/" in path else ("", path)
        stem = name.rsplit(".", 1)[0] if "." in name else name
        match = PVS_PAT.match(stem)
        if not match:
            continue
        prefix, var, suffix = match.groups()
        pvs.setdefault((parent, prefix, suffix), set()).add(var)
    return pvs


def family_extrapolate(ctx, tokens, hits, verdict):
    runs = ctx["runs"]
    gap = ctx["gap"]
    unresolved = ctx["unresolved"]
    pvs = anchor_patterns(ctx)
    by_prefix = {}
    for (parent, prefix, suffix), vs in pvs.items():
        by_prefix.setdefault((prefix, suffix), {}).setdefault(parent, vs)
    token_buckets = {}
    for token in tokens:
        if TOKEN_PAT.match(token):
            token_buckets.setdefault(len(token), []).append(token)

    for low, items, high in runs:
        if not (low and high):
            continue
        if S.classify_unsolved(low, high, items[0][1], 8) != "cross_dir":
            continue
        dlow, dhigh = low.rpartition("/")[0], high.rpartition("/")[0]
        parent_l, leaf_l = dlow.rsplit("/", 1) if "/" in dlow else ("", dlow)
        parent_r, leaf_r = dhigh.rsplit("/", 1) if "/" in dhigh else ("", dhigh)
        file_low = low.rsplit("/", 1)[-1]
        file_high = high.rsplit("/", 1)[-1]
        ext_l = "." + file_low.rsplit(".", 1)[1] if "." in file_low else ""
        ext_r = "." + file_high.rsplit(".", 1)[1] if "." in file_high else ""
        exts = {e for e in (ext_l, ext_r) if e} or {".kta"}

        if leaf_l.isdigit() and leaf_r.isdigit() and parent_l == parent_r:
            run_digit_dir(ctx, parent_l, leaf_l, leaf_r, file_low, file_high, exts, items, hits, unresolved)
            continue
        if parent_l == parent_r:
            run_pvs_dir(ctx, parent_l, leaf_l, leaf_r, file_low, exts, items, hits, unresolved, by_prefix, token_buckets)
            continue


def run_digit_dir(ctx, parent, leaf_l, leaf_r, file_low, file_high, exts, items, hits, unresolved):
    targets = {(sig, length) for sig, length in items}
    stem_l = file_low.rsplit(".", 1)[0] if "." in file_low else file_low
    stems = {stem_l}
    for m in re.finditer(r"\d+", stem_l):
        width = len(m.group(0))
        for v in range(0, min(STEM_NUM_CAP, 2000)):
            stems.add(stem_l[:m.start()] + str(v).zfill(width) + stem_l[m.end():])
    nums = set()
    for width in {len(leaf_l), len(leaf_r)}:
        for v in range(0, DIGIT_CAP):
            nums.add(str(v).zfill(width))
    lengths = {length for _sig, length in items}
    for num in nums:
        directory = parent + "/" + num
        base_len = len(directory) + 1
        for stem in stems:
            stem_len = len(stem)
            ok_exts = [e for e in exts if base_len + stem_len + len(e) in lengths]
            if not ok_exts:
                continue
            for ext in ok_exts:
                cand = directory + "/" + stem + ext
                key = (fnv_of(cand), len(cand))
                if key in targets and key not in hits:
                    if N.path_hash(cand.replace("/", "\\")) in unresolved:
                        hits[key] = {"path": cand, "rule": "digit_dir",
                                     "anchor_dir": parent + "/" + leaf_l}
                    targets.discard(key)


def run_pvs_dir(ctx, parent, leaf_l, leaf_r, file_low, exts, items, hits, unresolved, by_prefix, tokens):
    m_l, m_r = PVS_PAT.match(leaf_l), PVS_PAT.match(leaf_r)
    if not m_l or not m_r or m_l.group(1) != m_r.group(1) or m_l.group(3) != m_r.group(3):
        return
    prefix, v_l, suffix = m_l.groups()
    targets = {(sig, length) for sig, length in items}
    lengths = {length for _sig, length in items}
    variants = set()
    for parent_map in by_prefix.get((prefix, suffix), {}).values():
        variants |= parent_map
    low, high = (v_l, v_r) if v_l <= v_r else (v_r, v_l)
    in_range = {v for v in variants if low < v < high} or variants
    stem_l = file_low.rsplit(".", 1)[0] if "." in file_low else file_low
    base_len = len(stem_l) - len(v_l)
    exts_all = exts | {".kta", ".gim", ".mesh", ".mtg", ".stb"}
    dir_prefix = parent + "/"
    for v in sorted(in_range):
        stem = stem_l.replace(v_l, v)
        directory = dir_prefix + stem
        for ext in exts_all:
            if len(directory) + 1 + len(stem) + len(ext) not in lengths:
                continue
            cand = directory + "/" + stem + ext
            key = (fnv_of(cand), len(cand))
            if key in targets and key not in hits:
                if N.path_hash(cand.replace("/", "\\")) in unresolved:
                    hits[key] = {"path": cand, "rule": "pvs_dir",
                                 "anchor_dir": dir_prefix + leaf_l}
                targets.discard(key)
    if tokens:
        for need_len in sorted({length - len(dir_prefix) - 1 - base_len - len(e)
                                for length in lengths for e in exts_all}):
            if not (1 <= need_len <= 24):
                continue
            for v in tokens_by_len(tokens, need_len) or ():
                stem = stem_l.replace(v_l, v)
                directory = dir_prefix + stem
                for ext in exts_all:
                    if len(directory) + 1 + len(stem) + len(ext) not in lengths:
                        continue
                    cand = directory + "/" + stem + ext
                    key = (fnv_of(cand), len(cand))
                    if key in targets and key not in hits:
                        if N.path_hash(cand.replace("/", "\\")) in unresolved:
                            hits[key] = {"path": cand, "rule": "pvs_token",
                                         "anchor_dir": dir_prefix + leaf_l}
                        targets.discard(key)


def tokens_by_len(token_buckets, need_len):
    return token_buckets.get(need_len, ())


def sniff_verify(ctx, hits, verdict):
    con = sqlite3.connect("file:%s?mode=ro" % ctx["db"], uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    rows = {}
    for row in con.execute("SELECT path, package, offset, packed, unpacked, compression, ext FROM files WHERE path LIKE '_unresolved/%%'"):
        rows[row["path"]] = row
    con.close()
    by_sig = {}
    for path, row in rows.items():
        sig = path.split("/")[-1].rsplit(".", 1)[0]
        by_sig.setdefault(sig, []).append(row)
    verified = {}
    for (sig, length), info in hits.items():
        cand = info["path"]
        mmh3 = N.path_hash(cand.replace("/", "\\"))
        if mmh3 != sig:
            verdict["sniff_sig_mismatch"] += 1
            continue
        rows_sig = by_sig.get("%08x" % sig) or by_sig.get("%08X" % sig)
        if rows_sig is None:
            rows_sig = [r for s, rs in by_sig.items() for r in rs
                        if int(s, 16) == sig]
        if not rows_sig:
            verdict["sniff_no_entry"] += 1
            continue
        ext = cand.rsplit(".", 1)[-1] if "." in cand.rsplit("/", 1)[-1] else ""
        accepted = None
        for row in rows_sig:
            try:
                data = B.read_entry_bytes(ctx["packages"][row["package"]], row)
            except Exception:
                continue
            kind = H.sniff(data)
            if H.ext_ok(cand, row["ext"], kind):
                accepted = {"row": row["path"], "kind": kind}
                break
        if accepted is None:
            verdict["sniff_reject"] += 1
            continue
        verified[(sig, length)] = dict(info, entry=accepted["row"], kind=accepted["kind"])
    return verified


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=B.default_cache_dir() / "catalog.sqlite")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sources", default="client_bin,xn,refs,unresolved_refs,wwise_names,family")
    args = parser.parse_args()

    started = time.time()
    ctx = load_context(args.db)
    ctx["db"] = args.db
    gap_total = len(ctx["gap"])
    print("gap records %d, table %d, pathdict %d, unresolved sigs %d" % (
        gap_total, len(ctx["records"]), len(ctx["paths"]), len(ctx["unresolved"])), flush=True)

    tokens = set()
    hits = {}
    verdict = Counter()
    enabled = {s.strip() for s in args.sources.split(",") if s.strip()}
    if "client_bin" in enabled:
        source_client_bin(ctx, tokens, hits, verdict)
        print("client_bin done, hits %d, tokens %d, %.0fs" % (len(hits), len(tokens), time.time() - started), flush=True)
    if "xn" in enabled:
        before = len(hits)
        source_xn(ctx, tokens, hits, verdict)
        print("xn done, +%d hits, tokens %d, %.0fs" % (len(hits) - before, len(tokens), time.time() - started), flush=True)
    if "refs" in enabled:
        before = len(hits)
        source_named_refs(ctx, tokens, hits, verdict)
        print("refs done, +%d hits, %.0fs" % (len(hits) - before, time.time() - started), flush=True)
    if "unresolved_refs" in enabled:
        before = len(hits)
        source_unresolved_refs(ctx, tokens, hits, verdict)
        print("unresolved_refs done, +%d hits, %.0fs" % (len(hits) - before, time.time() - started), flush=True)
    if "wwise_names" in enabled:
        source_wwise_names(ctx, tokens, hits, verdict)
        print("wwise_names done, tokens %d, %.0fs" % (len(tokens), time.time() - started), flush=True)
    if "family" in enabled:
        before = len(hits)
        family_extrapolate(ctx, tokens, hits, verdict)
        print("family done, +%d hits, %.0fs" % (len(hits) - before, time.time() - started), flush=True)

    verified = sniff_verify(ctx, hits, verdict)
    print("raw hits %d, verified %d, verdict %s, %.0fs" % (
        len(hits), len(verified), dict(verdict), time.time() - started), flush=True)

    collisions = []
    by_sig = {}
    for (sig, length), info in verified.items():
        by_sig.setdefault(sig, []).append(info["path"])
    for sig, group in by_sig.items():
        if len(group) > 1:
            collisions.append({"signature": "%08x" % sig, "paths": sorted(group)})
    solved = {(sig, length) for (sig, length), info in verified.items()
              if sig == N.path_hash(info["path"].replace("/", "\\"))}
    ext_count = Counter(Path(info["path"]).suffix for info in verified.values())
    rule_count = Counter(info["rule"] for info in verified.values())
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "gap_records": gap_total,
        "mappings": {"_unresolved/%08x%s" % (sig, ext_of_entry(ctx, sig, length)):
                     {"path": info["path"], "rule": info["rule"], "kind": info["kind"],
                      "evidence": evidence_for(ctx, sig, length, info)}
                     for (sig, length), info in sorted(verified.items())},
        "stats": {"gap_records": gap_total, "solved": len(verified),
                  "by_ext": dict(ext_count), "by_rule": dict(rule_count),
                  "verdict": dict(verdict), "collisions": collisions},
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("written %s (%d mappings)" % (args.out, len(payload["mappings"])), flush=True)
    return 0


def ext_of_entry(ctx, sig, length):
    for low, items, high in ctx["runs"]:
        for s, l in items:
            if s == sig and l == length:
                for anchor in (high, low):
                    if anchor and "." in anchor.rsplit("/", 1)[-1]:
                        return "." + anchor.rsplit(".", 1)[1]
    return ""


def evidence_for(ctx, sig, length, info):
    evidence = {"fnv": "0x%08x" % fnv_of(info["path"]), "len": len(info["path"]),
                "mmh3": "0x%08x" % sig, "rule": info["rule"]}
    for low, items, high in ctx["runs"]:
        for s, l in items:
            if s == sig and l == length:
                evidence["after"] = high or ""
                evidence["before"] = low or ""
                return evidence
    return evidence


if __name__ == "__main__":
    sys.exit(main())
