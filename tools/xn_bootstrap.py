"""Build XN opcode and module-name indexes from the local game catalog."""

import json
import re
import sqlite3
import sys
import time
from collections import deque
from pathlib import Path

import mmh3
import zstandard

import xn_crypto
import xn_parse

OPCODE_SIGNATURE = 0xDC830DF0
INDEX_VERSION = 3
BUNDLED_SEED = Path(__file__).with_name("xn_seed.json")
SEEDS = (
    "opcode", "game", "init", "redirect",
    "client_consts", "common.mobilecommon", "custom.client", "public.consts",
    "patch.patch_mgr", "version", "utils", "random", "json",
)
CLIENT_LOG_HINT = re.compile(r"module\s+(?:\[')?([A-Za-z_][\w.]+)")
HOTFIX_HINT = re.compile(r"hotfix_module_begin:([\w.]+)")


def _read_entry(package_path: str, offset: int, packed: int, unpacked: int, compression: int) -> bytes:
    with open(package_path, "rb") as handle:
        handle.seek(offset)
        raw = handle.read(packed)
    if compression == 3:
        data = zstandard.ZstdDecompressor().decompress(raw, max_output_size=unpacked)
    elif compression == 0:
        data = raw
    else:
        raise ValueError("unsupported compression %s" % compression)
    if len(data) != unpacked:
        raise ValueError("entry size mismatch")
    return data


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(path)


def _client_hint_names(game_root: Path) -> list[str]:
    names = set()
    try:
        logs = [game_root / "log.txt"] + sorted(game_root.glob("log_old_*.txt"), key=lambda path: path.stat().st_mtime, reverse=True)
        for log in logs[:4]:
            text = log.read_text("utf-8", errors="ignore")
            names.update(CLIENT_LOG_HINT.findall(text))
            names.update(HOTFIX_HINT.findall(text))
    except OSError:
        return []
    return [name for name in names if 2 <= len(name) <= 90 and not name.endswith(".py")]


def build_opmap(db_path: Path, output: Path, log=print) -> bool:
    connection = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "select package, path, offset, packed, unpacked, compression from files where signature=? and ext='.xn'",
            (OPCODE_SIGNATURE,),
        ).fetchone()
        if not row:
            log("XN opcode module not found; disassembly disabled")
            return False
        package_path = connection.execute(
            "select path from packages where name=?", (row["package"],)
        ).fetchone()[0]
        payload = _read_entry(package_path, row["offset"], row["packed"], row["unpacked"], row["compression"])
        plain = xn_crypto.decrypt(payload, "opcode")
        if plain is None:
            log("XN opcode module decrypt failed; disassembly disabled")
            return False
        data = xn_parse.build_opmap(plain)
        _write_json(output, data)
        log("XN opcode index generated: %s (%d base, %d OPEXT)" % (output, len(data["base"]), len(data["opext"])))
        return True
    finally:
        connection.close()


def build_names(db_path: Path, output: Path, budget: float = 240.0, limit: int = 20000, seed_path: Path | None = None, game_root: Path | None = None, log=print) -> int:
    connection = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "select signature, package, path, offset, packed, unpacked, compression from files where ext='.xn'"
        ).fetchall()
        packages = {row[0]: row[1] for row in connection.execute("select name, path from packages")}
    finally:
        connection.close()
    entries = {}
    for row in rows:
        entries.setdefault(row["signature"], []).append(row)
    name_map = {}
    new_queue = deque()
    known_queue = deque()
    scheduled = set()
    tested = set()

    def schedule(name: str) -> None:
        if not (2 <= len(name) <= 90):
            return
        parts = name.split(".")
        for index in range(1, len(parts) + 1):
            prefix = ".".join(parts[:index])
            if not (2 <= len(prefix) <= 90) or prefix in tested:
                continue
            tested.add(prefix)
            base = prefix.replace(".", "\\")
            forms = (
                (base + ".nxs", prefix),
                (base + "\\__init__.nxs", prefix + ".__init__"),
                ("clients\\" + base + ".nxs", prefix),
                ("clients\\" + base + "\\__init__.nxs", prefix + ".__init__"),
            )
            for path, salt in forms:
                signature = mmh3.hash(path, 0x9747B28C, signed=False)
                if signature in entries and signature not in scheduled:
                    scheduled.add(signature)
                    if signature in name_map and name_map[signature] != salt:
                        continue
                    target = known_queue if signature in name_map else new_queue
                    target.append((signature, salt))

    if seed_path and seed_path.is_file():
        try:
            seeded = json.loads(seed_path.read_text("utf-8"))
            for key, value in seeded.get("map", {}).items():
                name_map[int(key)] = value
        except (OSError, ValueError):
            pass
    for seed in SEEDS:
        schedule(seed)
    for name in set(name_map.values()):
        schedule(name)
    if game_root is not None:
        for name in _client_hint_names(game_root):
            schedule(name)
    if len(name_map) > 1000:
        known_queue.clear()
    # 已索引模块的任意父包都可能是 __init__.nxs, 对未命中签名单独重放这些 salt.
    package_salts = set()
    for name in set(name_map.values()):
        parts = name.split(".")
        for index in range(1, len(parts) + 1):
            prefix = ".".join(parts[:index])
            if 2 <= len(prefix) <= 90:
                package_salts.add(prefix)
    tested.clear()
    for salt in sorted(package_salts):
        schedule(salt)

    started = time.time()
    decrypted = 0
    scanned = set()
    while (new_queue or known_queue) and decrypted < limit and time.time() - started < budget:
        signature, salt = new_queue.popleft() if new_queue else known_queue.popleft()
        if signature in scanned:
            continue
        scanned.add(signature)
        row = entries[signature][0]
        try:
            payload = _read_entry(packages[row["package"]], row["offset"], row["packed"], row["unpacked"], row["compression"])
            sizes = xn_crypto.header(payload)
            if not sizes or sizes[0] > 256 * 1024:
                continue
            plain = xn_crypto.decrypt(payload, salt)
        except Exception:
            continue
        if not plain:
            continue
        if signature not in name_map:
            name_map[signature] = salt
            decrypted += 1
            if decrypted % 500 == 0:
                _write_json(output, {"version": INDEX_VERSION, "complete": False, "map": {str(key): value for key, value in name_map.items()}, "decrypted": decrypted})
                log("XN name index: %d modules, queue %d, %.0fs" % (decrypted, len(new_queue) + len(known_queue), time.time() - started))
        # 大于 64KB 的脚本解析候选名太慢, 只索引它本身, 不继续展开导入关系.
        if sizes[1] > 64 * 1024:
            continue
        try:
            for candidate in xn_parse.candidates(plain):
                schedule(candidate)
        except Exception:
            continue
    complete = not new_queue and not known_queue
    _write_json(output, {"version": INDEX_VERSION, "complete": complete, "map": {str(key): value for key, value in name_map.items()}, "decrypted": decrypted})
    log("XN name index generated: %s (%d modules%s)" % (output, len(name_map), "" if complete else ", partial"))
    return len(name_map)


def _map_count(path: Path) -> int:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return 0
    return len(data.get("map", {})) if isinstance(data, dict) else 0


def _index_version(path: Path) -> int:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return 0
    try:
        return int(data.get("version") or 0) if isinstance(data, dict) else 0
    except (TypeError, ValueError):
        return 0


def _index_complete(path: Path) -> bool:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return bool(data.get("complete")) if isinstance(data, dict) else False


def _merged_map(paths: list[Path]) -> dict:
    merged = {}
    for path in paths:
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key, value in data.get("map", {}).items():
            merged[int(key)] = value
    return merged


def ensure(db_path: Path, cache_root: Path, seed_path: Path | None = None, budget: float = 600.0, game_root: Path | None = None, log=print) -> tuple[Path, Path]:
    opmap_path = cache_root / "xn_opmap.json"
    names_path = cache_root / "xn_names.json"
    if seed_path is None or not seed_path.is_file():
        seed_path = BUNDLED_SEED if BUNDLED_SEED.is_file() else seed_path
    if not opmap_path.is_file():
        try:
            build_opmap(db_path, opmap_path, log)
        except Exception as exc:
            log("XN opcode index failed: %s" % exc)
    need_build = (
        not names_path.is_file()
        or _index_version(names_path) < INDEX_VERSION
        or not _index_complete(names_path)
    )
    if not need_build and seed_path and seed_path.is_file() and _map_count(seed_path) > _map_count(names_path):
        need_build = True
    if need_build and not names_path.is_file() and seed_path and seed_path.is_file():
        try:
            _write_json(names_path, json.loads(seed_path.read_text("utf-8")))
            log("XN name index seeded from %s" % seed_path)
        except (OSError, ValueError):
            pass
    if need_build:
        sources = [path for path in (names_path, seed_path, BUNDLED_SEED) if path and path.is_file()]
        merged = _merged_map(sources)
        if merged:
            _write_json(names_path, {"version": INDEX_VERSION, "complete": False, "map": {str(key): value for key, value in merged.items()}})
            seed_for_build = names_path
        else:
            seed_for_build = names_path if names_path.is_file() else seed_path
        log("XN name index building before startup...")
        try:
            build_names(db_path, names_path, budget, 50000, seed_for_build, game_root, log)
        except Exception as exc:
            log("XN name index failed: %s" % exc)
    return opmap_path, names_path
