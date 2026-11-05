"""Name unresolved non-ppk hash entries (_unresolved/char|char_loading|gui2|props|res|scene).

目标: catalog 中 _unresolved/<包名>/<sig:08x>.<ext> 的 303 条小目录条目.
文件名十六进制即路径哈希: MurmurHash3_x86_32(反斜杠路径, seed=0x9747B28C).
每条映射都必须通过 path_hash(candidate) == signature 验证 (content-identical 除外),
规则与证据随 JSON 一并输出. 参考 tools/ppk_content_names.py 的结构.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
import neox_object
import npk_browser as B
import nxpk_mode3
import xn_crypto
from nxpk_mode3 import path_hash

try:
    import msgpack
except ImportError:
    msgpack = None

DEFAULT_DB = B.default_cache_dir() / "catalog.sqlite"
SEED = 0x9747B28C
TARGET_DIRS = {"_unresolved/char", "_unresolved/char_loading", "_unresolved/gui2",
               "_unresolved/props", "_unresolved/res", "_unresolved/scene"}
NON_PPK_PACKAGES = ["char.npk", "char_loading.npk", "gui2.npk", "props.npk",
                    "props_ui_model.npk", "props_zhucheng.npk", "res.npk", "scene.npk"]
CFG_EXT = (".animconfig", ".animresmap", ".animgraph", ".blendspace", ".blendspace1d", ".trackgroup")
NEOX_EXT = {".gim", ".mtg", ".scn"}
MSGPACK_EXT = {".eggimonbin", ".bin"}
SRC_EXT = (".mtg", ".gim", ".scn", ".csb", ".json", ".xml", ".plist", ".animation", ".animresmap",
           ".blendspace", ".blendspace1d", ".animgraph", ".trackgroup", ".animconfig", ".postprocess",
           ".sfxmusic", ".stb", ".nstb", ".efx", ".sfx", ".meta", ".txt", ".lod", ".trk", ".gmp",
           ".eggimonbin", ".luac", ".mesh", ".skeleton", ".rawanimation", ".bin")
IMG_SRC_EXT = {"tga", "psd", "png", "jpg", "jpeg", "bmp"}
TOKEN = re.compile(rb"[A-Za-z0-9_\-.\\/]{3,220}[.][A-Za-z0-9]{1,12}")
XML_NAME = re.compile(rb"<Name\s+Name=\"([^\"]+)\"")
XML_FILENAME = re.compile(rb"<FileName\s+Value=\"([^\"]+)\"")
CFG_FILENAME = re.compile(rb"FileName=\"([^\"]+)\"")
SKELETON_REF = re.compile(rb"([A-Za-z0-9_\-./\\]+[.]skeleton)")
MAX_SOURCE_BYTES = 64 * 1024 * 1024
HASH_RULES = {"refs-token", "refs-neox", "refs-msg", "ppkrefs-token", "ppkrefs-neox", "ppkrefs-msg",
              "xn-refs", "xn-animname+direnum", "xml-name+dir-enum", "animation-dependency",
              "skeleton-dir+name-pool", "eggimonbin-uuid", "bin-selfid+direnum"}


def load_targets(db_path: Path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=120)
    con.row_factory = sqlite3.Row
    targets = {r["signature"]: dict(r) for r in con.execute(
        "SELECT path, signature, ext, unpacked, unpacked_hash FROM files "
        "WHERE dir IN (%s)" % ",".join("'%s'" % d for d in sorted(TARGET_DIRS)))}
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    return con, targets, packages


class Namer:
    def __init__(self, targets: dict):
        self.targets = targets
        self.hits: dict[str, dict] = {}
        self.conflicts: dict[str, set] = defaultdict(set)

    def try_path(self, cand: str, rule: str, evidence: str) -> bool:
        cand = cand.replace("\\", "/").strip("/")
        if ".." in cand or "//" in cand or "." not in cand:
            return False
        target = self.targets.get(path_hash(cand))
        if target is None:
            return False
        if cand.rsplit(".", 1)[-1].lower() != target["ext"].lstrip(".").lower():
            return False
        prev = self.hits.get(target["path"])
        if prev:
            prev_is_hash = prev["rule"] in HASH_RULES
            if prev["path"] == cand:
                return True
            if prev_is_hash and rule not in HASH_RULES:
                return False
            if prev_is_hash and rule in HASH_RULES:
                self.conflicts[target["path"]].update([prev["path"], cand])
                return False
        self.hits[target["path"]] = {"rule": rule, "path": cand, "evidence": evidence}
        return True

    def variants(self, raw: str, source_dir: str, roots: set) -> list:
        text = raw.replace("\\", "/").strip()
        text = re.sub(r"^[.]/+", "", text)
        stem, dot, ext = text.rpartition(".")
        if not dot:
            return []
        low = ext.lower()
        bases = []
        if text.split("/", 1)[0] in roots:
            bases.append(text)
        else:
            bases.append("gui2/" + text)
            for cut in range(1, 4):
                trimmed = text[cut:]
                if trimmed and trimmed.split("/", 1)[0] in roots:
                    bases.append(trimmed)
            if source_dir:
                norm, rel = source_dir, text
                while rel.startswith("../"):
                    if "/" not in norm:
                        norm = ""
                        break
                    norm = norm.rsplit("/", 1)[0]
                    rel = rel[3:]
                if norm:
                    bases.append(norm + "/" + rel)
        out = []
        for base in bases:
            out.append(base)
            if low in IMG_SRC_EXT:
                out.append(base[:base.rindex(".")] + ".kta")
            if low == "blendspace":
                out.append(base[:base.rindex(".")] + ".blendspace1d")
        return out


def extract_strings(data: bytes, ext: str):
    for m in set(TOKEN.findall(data)):
        yield "tok", m.decode("latin1")
    if ext in NEOX_EXT:
        try:
            obj = neox_object.parse(data)
        except Exception:
            return
        for o in obj.get("objects", []):
            for k, v in o.items():
                if isinstance(v, str) and v:
                    yield "neox", v
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and item:
                            yield "neox", item
    elif ext in MSGPACK_EXT and msgpack is not None:
        try:
            obj = msgpack.unpackb(data, raw=False, strict_map_key=False)
        except Exception:
            return
        stack = [obj]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                stack.extend(cur.keys())
                stack.extend(cur.values())
            elif isinstance(cur, list):
                stack.extend(cur)
            elif isinstance(cur, str) and cur:
                yield "msg", cur


def rule_twin(con, namer: Namer, started):
    twin = {}
    for r in con.execute("SELECT path, unpacked, unpacked_hash FROM files "
                         "WHERE path NOT LIKE '_unresolved/%' AND unpacked_hash != 0"):
        twin.setdefault((r["unpacked"], r["unpacked_hash"]), r["path"])
    n = 0
    for t in namer.targets.values():
        if not t["unpacked_hash"] or t["path"] in namer.hits:
            continue
        tp = twin.get((t["unpacked"], t["unpacked_hash"]))
        if tp and path_hash(tp) != t["signature"]:
            namer.hits[t["path"]] = {"rule": "content-identical", "path": tp,
                                     "evidence": "same unpacked size and content hash as named entry"}
            n += 1
    print("rule content-identical: %d  %.0fs" % (n, time.time() - started))


def rule_refs(con, namer: Namer, packages: dict, roots: set, started, ppk: bool = True):
    pkg_names = list(NON_PPK_PACKAGES)
    if ppk:
        pkg_names += [r["package"] for r in con.execute(
            "SELECT DISTINCT package FROM files WHERE package LIKE 'ppk/%' ORDER BY package")]
    src_list = ",".join("'%s'" % e for e in SRC_EXT)
    prefix = "" if ppk else "nonppk-"
    n = 0
    for pkg in pkg_names:
        rows = con.execute(
            "SELECT path, offset, packed, unpacked, compression, package, ext FROM files "
            "WHERE package=? AND ext IN (%s)" % src_list, (pkg,)).fetchall()
        for row in rows:
            if row["unpacked"] > MAX_SOURCE_BYTES:
                continue
            try:
                data = B.read_entry_bytes(packages[pkg], row)
            except Exception:
                continue
            source_dir = "/".join(row["path"].split("/")[:-1])
            for kind, raw in extract_strings(data, row["ext"]):
                if kind != "tok" and not ("/" in raw or "\\" in raw or "." in raw):
                    continue
                for cand in namer.variants(raw, source_dir, roots):
                    namer.try_path(cand, "%srefs-%s" % (prefix, kind),
                                   "%s references %r" % (row["path"], raw))
            n += 1
        print("scanned %s (%d)  hits %d  %.0fs" % (pkg, len(rows), len(namer.hits), time.time() - started))


def read_entry(con, packages: dict, path: str) -> bytes | None:
    r = con.execute(
        "SELECT f.*, p.path AS package_path FROM files f JOIN packages p ON f.package=p.name WHERE f.path=?",
        (path,)).fetchone()
    if r is None:
        return None
    try:
        return B.read_entry_bytes(r["package_path"], r)
    except Exception:
        return None


def rule_anim_names(con, namer: Namer, packages: dict, started):
    dirs = [r["path"] for r in con.execute(
        "SELECT path FROM dirs WHERE file_count > 0 AND (path LIKE 'char/%' OR path LIKE 'char_loading/%')")]
    n = 0
    for r in con.execute("SELECT path FROM files WHERE dir='_unresolved/char' AND ext='.animation'"):
        data = read_entry(con, packages, r["path"])
        if data is None:
            continue
        m = XML_NAME.search(data)
        if not m:
            continue
        name = m.group(1).decode("latin1")
        for d in dirs:
            if namer.try_path(f"{d}/{name}.animation", "xml-name+dir-enum",
                              f'animation XML <Name Name="{name}"> + directory enumeration + hash match'):
                n += 1
    print("rule xml-name+dir-enum: %d  %.0fs" % (n, time.time() - started))


def rule_anim_deps(con, namer: Namer, packages: dict, started):
    sources = [r["path"] for r in con.execute(
        "SELECT path FROM files WHERE ext IN ('.animation','.animresmap') AND path NOT LIKE '_unresolved/%'")]
    sources += [v["path"] for v in namer.hits.values() if v["path"].endswith((".animation", ".animresmap"))]
    n = 0
    for src in sources:
        data = read_entry(con, packages, src)
        if data is None:
            continue
        sdir = "/".join(src.split("/")[:-1])
        for m in XML_FILENAME.findall(data):
            dep = m.decode("latin1").replace("\\", "/")
            if "/" in dep or ".." in dep:
                continue
            if namer.try_path(f"{sdir}/{dep}", "animation-dependency",
                              f'{src} <FileName Value="{dep}"> in same directory'):
                n += 1
    print("rule animation-dependency: %d  %.0fs" % (n, time.time() - started))


def rule_skeleton_names(con, namer: Namer, packages: dict, started):
    resolved_raw = {v["path"] for v in namer.hits.values() if v["path"].endswith(".rawanimation")}
    stems = {}
    for r in con.execute("SELECT path FROM files WHERE dir LIKE '_unresolved/%' AND ext='.rawanimation'"):
        if r["path"] in resolved_raw:
            continue
        data = read_entry(con, packages, r["path"])
        if data is None:
            continue
        m = SKELETON_REF.search(data)
        if not m:
            continue
        stem = m.group(1).decode("latin1").replace("\\", "/").split("/")[-1].rsplit(".", 1)[0]
        stems.setdefault(stem, []).append(r["path"])
    name_pool = set()
    for r in con.execute("SELECT name FROM files WHERE ext IN ('.animation','.rawanimation') "
                         "AND path NOT LIKE '_unresolved/%'"):
        name_pool.add(r["name"].rsplit(".", 1)[0])
    for v in namer.hits.values():
        base = v["path"].rsplit("/", 1)[-1]
        if "." in base:
            name_pool.add(base.rsplit(".", 1)[0])
    src_list = ",".join("'%s'" % e for e in CFG_EXT)
    for r in con.execute("SELECT path, package, offset, packed, unpacked, compression FROM files "
                         "WHERE ext IN (%s) AND unpacked < 4000000" % src_list):
        data = read_entry(con, packages, r["path"])
        if data is None:
            continue
        for m in CFG_FILENAME.findall(data):
            base = m.decode("latin1").replace("\\", "/").split("/")[-1]
            if "." in base:
                name_pool.add(base.rsplit(".", 1)[0])
    print("skeleton stems: %s  name pool: %d" % ({s: len(v) for s, v in stems.items()}, len(name_pool)))
    n = 0
    for stem, lst in stems.items():
        dirs = set()
        for r in con.execute("SELECT path FROM files WHERE name=? AND ext='.skeleton' "
                             "AND path NOT LIKE '_unresolved/%'", (stem + ".skeleton",)):
            dirs.add("/".join(r["path"].split("/")[:-1]))
        for r in con.execute("SELECT path FROM dirs WHERE file_count > 0 AND path LIKE ?",
                             ("%" + f"animations_{stem}",)):
            dirs.add(r["path"])
        dirs = {d + f"/animations_{stem}" for d in dirs} | dirs
        for src in lst:
            for d in sorted(dirs):
                for name in name_pool:
                    if namer.try_path(f"{d}/{name}.rawanimation", "skeleton-dir+name-pool",
                                      f"rawanimation references ../{stem}.skeleton; dir from named skeleton "
                                      f"parent or animations_{stem} dir; name from config/file name pool; hash match"):
                        n += 1
    print("rule skeleton-dir+name-pool: %d  %.0fs" % (n, time.time() - started))


def rule_eggimon(con, namer: Namer, packages: dict, started):
    if msgpack is None:
        return
    groups = [r["path"].split("/", 1)[1] for r in con.execute(
        "SELECT path FROM dirs WHERE path LIKE 'mont/%' AND path NOT LIKE 'mont/%/%'")]
    n = 0
    for r in con.execute("SELECT path FROM files WHERE dir='_unresolved/res' AND ext='.eggimonbin'"):
        data = read_entry(con, packages, r["path"])
        if data is None:
            continue
        try:
            obj = msgpack.unpackb(data, raw=False, strict_map_key=False)
        except Exception:
            continue
        uuid = obj.get("uuid")
        if not uuid:
            continue
        for g in groups:
            if namer.try_path(f"mont/{g}/{uuid.lower()}.eggimonbin", "eggimonbin-uuid",
                              f"msgpack top uuid={uuid}; mont/<group> dirs enumerated; hash match"):
                n += 1
    print("rule eggimonbin-uuid: %d  %.0fs" % (n, time.time() - started))


def rule_xn(con, namer: Namer, packages: dict, started):
    seed_path = Path(__file__).resolve().parent / "xn_seed.json"
    if not seed_path.is_file():
        return
    salt_map = {int(k): v for k, v in json.loads(seed_path.read_text("utf-8")).get("map", {}).items()}
    anim_names = set()
    for r in con.execute("SELECT path FROM files WHERE dir='_unresolved/char' AND ext='.animation'"):
        data = read_entry(con, packages, r["path"])
        if data is None:
            continue
        m = XML_NAME.search(data)
        if m:
            anim_names.add(m.group(1).decode("latin1"))
    anim_dirs = [r["path"] for r in con.execute(
        "SELECT path FROM dirs WHERE file_count > 0 AND path LIKE 'char/%animations_%'")]
    n = 0
    decrypted = 0
    rows = con.execute("SELECT path, signature, offset, packed, unpacked, compression FROM files "
                       "WHERE package='script.npk' AND ext='.xn'").fetchall()
    for row in rows:
        salt = salt_map.get(row["signature"])
        if not salt:
            continue
        try:
            plain = xn_crypto.decrypt(B.read_entry_bytes(packages["script.npk"], row), salt)
        except Exception:
            continue
        if not plain:
            continue
        decrypted += 1
        src = row["path"]
        for m in set(TOKEN.findall(plain)):
            raw = m.decode("latin1")
            for cand in namer.variants(raw, "", {"char", "char_loading", "gui2", "props", "res",
                                                 "scene", "effect", "scene_w", "mont"} | _roots(con)):
                if namer.try_path(cand, "xn-refs", f"{src} (module {salt}) references {raw!r}"):
                    n += 1
        for an in anim_names:
            if an.encode("latin1") in plain:
                for d in anim_dirs:
                    for ext in (".animation", ".rawanimation"):
                        if namer.try_path(f"{d}/{an}{ext}", "xn-animname+direnum",
                                          f"{src} (module {salt}) contains animation name {an!r}"):
                            n += 1
    print("rule xn: decrypted %d, hits %d  %.0fs" % (decrypted, n, time.time() - started))


_root_cache = set()


def _roots(con) -> set:
    if not _root_cache:
        for r in con.execute("SELECT dir, count(*) n FROM files WHERE path NOT LIKE '_unresolved/%' GROUP BY dir"):
            head = r["dir"].split("/")[0] if r["dir"] else ""
            if r["n"] >= 20 and head:
                _root_cache.add(head)
    return _root_cache


def rule_scene_grids(con, namer: Namer, started):
    """scene_w 音频 scn 网格 + scene_w/gui2 kta 邻居词干 + scene mtg/mesh 邻居."""
    n = 0
    for r in con.execute("SELECT path FROM dirs WHERE path LIKE 'scene_w/%' AND file_count > 0"):
        d = r["path"]
        base = d.rsplit("/", 1)[-1]
        for nm in ("music.scn", "road.scn", "scene.scn"):
            if namer.try_path(f"{d}/{nm}", "scn-dir-grid",
                              f"scene_w content dir {d} + standard audio scene name + hash match"):
                n += 1
        if base.endswith(("_loading", "_content")):
            if namer.try_path(f"{d}/{base}.scn", "scn-dir-grid",
                              f"dir {d} + self-named scn + hash match"):
                n += 1
    print("rule scn-dir-grid: %d  %.0fs" % (n, time.time() - started))

    n = 0
    for r in con.execute("SELECT path FROM dirs WHERE path LIKE 'scene_w/%' AND file_count > 0"):
        d = r["path"]
        base = d.rsplit("/", 1)[-1]
        for stem in (base, base + "_d", base + "_n", base + "_m"):
            if namer.try_path(f"{d}/{stem}.kta", "scene-dirstem",
                              f"dir {d} + self-named kta + hash match"):
                n += 1
    print("rule scene-dirstem: %d  %.0fs" % (n, time.time() - started))

    n = 0
    for r in con.execute("SELECT dir, name FROM files WHERE ext='.kta' AND dir LIKE 'gui2/%' "
                         "AND path NOT LIKE '_unresolved/%'"):
        d, stem = r["dir"], r["name"].rsplit(".", 1)[0]
        for suf in ("", "_round", "_1", "_2", "_3", "_d", "_n", "_m", "_mask", "_convert",
                    "_press", "_hover", "_dis", "_gray"):
            if namer.try_path(f"{d}/{stem}{suf}.kta", "gui2-kta-sibling",
                              f"gui2 kta stem {stem} in {d} + suffix variant + hash match"):
                n += 1
    print("rule gui2-kta-sibling: %d  %.0fs" % (n, time.time() - started))

    n = 0
    for r in con.execute("SELECT dir, name FROM files WHERE ext='.kta' AND (dir LIKE 'scene_w/%' "
                         "OR dir LIKE 'effect/%' OR dir LIKE 'props/%') AND path NOT LIKE '_unresolved/%'"):
        d, stem = r["dir"], r["name"].rsplit(".", 1)[0]
        for suf in ("_1", "_2", "_3", "_01", "_02", "_03", "_v01", "_v02", "_v03",
                    "_d", "_n", "_m", "_mask", "_convert", "_low"):
            if namer.try_path(f"{d}/{stem}{suf}.kta", "scene-kta-sibling",
                              f"kta stem {stem} in {d} + suffix variant + hash match"):
                n += 1
        for m in re.finditer(r"\d{1,4}", stem):
            width = len(m.group(0))
            base_num = int(m.group(0))
            for delta in range(-6, 7):
                if delta == 0 or base_num + delta < 0:
                    continue
                text = stem[:m.start()] + str(base_num + delta).zfill(width) + stem[m.end():]
                for suf in ("", "_d", "_n", "_m"):
                    if namer.try_path(f"{d}/{text}{suf}.kta", "scene-kta-sibling",
                                      f"kta stem {stem} in {d} + number {delta:+d} + hash match"):
                        n += 1
    print("rule scene-kta-sibling: %d  %.0fs" % (n, time.time() - started))

    n = 0
    for r in con.execute("SELECT dir, name FROM files WHERE ext IN ('.mtg','.mesh','.gim') "
                         "AND dir LIKE 'scene_w/%' AND path NOT LIKE '_unresolved/%'"):
        d, stem = r["dir"], r["name"].rsplit(".", 1)[0]
        for suf in ("", "_lod1", "_lod2", "_low", "_1", "_2", "_d", "_n"):
            for ext in (".mtg", ".mesh"):
                if namer.try_path(f"{d}/{stem}{suf}{ext}", "scene-mtg-mesh-sibling",
                                  f"model stem {stem} in {d} + hash match"):
                    n += 1
    print("rule scene-mtg-mesh-sibling: %d  %.0fs" % (n, time.time() - started))


def rule_neox_gim_names(con, namer: Namer, packages: dict, started):
    """未识别 .gim 的 NeoX 子网格 Name → 前缀近邻目录枚举."""
    n = 0
    dir_prefix = {}
    for r in con.execute("SELECT path FROM dirs WHERE file_count > 0 AND path LIKE 'scene_w/%'"):
        leaf = r["path"].rsplit("/", 1)[-1]
        dir_prefix.setdefault(leaf[:10], []).append(r["path"])
    for r in con.execute("SELECT path FROM files WHERE dir LIKE '_unresolved/%' AND ext='.gim'"):
        data = read_entry(con, packages, r["path"])
        if data is None:
            continue
        try:
            obj = neox_object.parse(data)
        except Exception:
            continue
        names = []
        for o in obj.get("objects", []):
            for k, v in o.items():
                if k == "Name" and isinstance(v, str) and len(v) >= 4:
                    names.append(v)
        for nm in set(names):
            for d in dir_prefix.get(nm[:10], []):
                if namer.try_path(f"{d}/{nm}.gim", "neox-gim-name",
                                  f"gim submesh Name={nm} + prefix-nearby scene_w dir {d} + hash match"):
                    n += 1
    print("rule neox-gim-name: %d  %.0fs" % (n, time.time() - started))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep" / "small-dirs-names.json")
    parser.add_argument("--skip-refs", action="store_true", help="skip whole-corpus reference scan")
    parser.add_argument("--skip-xn", action="store_true", help="skip XN decrypt scan (slow)")
    parser.add_argument("--no-ppk", action="store_true", help="do not scan ppk shard sources")
    args = parser.parse_args()

    started = time.time()
    con, targets, packages = load_targets(args.db)
    namer = Namer(targets)
    roots = _roots(con)
    print("targets: %d  roots: %d" % (len(targets), len(roots)))

    rule_twin(con, namer, started)
    rule_anim_names(con, namer, packages, started)
    rule_anim_deps(con, namer, packages, started)
    rule_eggimon(con, namer, packages, started)
    rule_scene_grids(con, namer, started)
    rule_neox_gim_names(con, namer, packages, started)
    if not args.skip_refs:
        rule_refs(con, namer, packages, roots, started, ppk=not args.no_ppk)
    rule_skeleton_names(con, namer, packages, started)
    if not args.skip_xn:
        rule_xn(con, namer, packages, started)

    resolved_by_dir = Counter((k.split("/")[1], k.rsplit(".", 1)[-1]) for k in namer.hits)
    print("resolved: %d / %d" % (len(namer.hits), len(targets)))
    for k, n in sorted(resolved_by_dir.items()):
        print("  ", k, n)
    for k, v in sorted(namer.conflicts.items()):
        print("CONFLICT", k, v)

    remaining = sorted({t["path"] for t in targets.values()} - set(namer.hits))
    result = {
        "generated_by": "tools/small_dirs_names.py",
        "seed": f"0x{SEED:08x}",
        "total_unresolved": len(targets),
        "named": len(namer.hits),
        "remaining": remaining,
        "mappings": namer.hits,
        "conflicts": {k: sorted(v) for k, v in namer.conflicts.items()},
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s (%.0fs)" % (args.out, time.time() - started))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
