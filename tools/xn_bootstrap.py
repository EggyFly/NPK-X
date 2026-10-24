"""Build XN opcode and module-name indexes from the local game catalog."""

import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import mmh3
import zstandard

import xn_crypto
import xn_parse

OPCODE_SIGNATURE = 0xDC830DF0
SEEDS = (
    "opcode", "game", "init", "redirect",
    "client_consts", "common.mobilecommon", "custom.client", "public.consts",
    "patch.patch_mgr", "version", "utils", "random", "json",
)


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


def build_names(db_path: Path, output: Path, budget: float = 240.0, limit: int = 20000, seed_path: Path | None = None, log=print) -> int:
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
    queue = []
    scheduled = set()
    tested = set()

    def schedule(name: str) -> None:
        if name in tested or not (2 <= len(name) <= 90):
            return
        tested.add(name)
        relative = name.replace(".", "\\") + ".nxs"
        forms = (
            (relative, name),
            (relative + "\\__init__.nxs", name + ".__init__"),
            ("clients\\" + relative, name),
            ("clients\\" + relative + "\\__init__.nxs", name + ".__init__"),
        )
        for path, salt in forms:
            signature = mmh3.hash(path, 0x9747B28C, signed=False)
            if signature in entries and signature not in name_map and signature not in scheduled:
                scheduled.add(signature)
                queue.append((signature, salt))

    if seed_path and seed_path.is_file():
        try:
            seeded = json.loads(seed_path.read_text("utf-8"))
            for key, value in seeded.get("map", {}).items():
                name_map[int(key)] = value
        except (OSError, ValueError):
            pass
    for seed in SEEDS:
        schedule(seed)

    started = time.time()
    decrypted = 0
    while queue and decrypted < limit and time.time() - started < budget:
        signature, salt = queue.pop(0)
        if signature in name_map:
            continue
        row = entries[signature][0]
        try:
            payload = _read_entry(packages[row["package"]], row["offset"], row["packed"], row["unpacked"], row["compression"])
            plain = xn_crypto.decrypt(payload, salt)
        except Exception:
            continue
        if not plain:
            continue
        name_map[signature] = salt
        decrypted += 1
        if decrypted % 100 == 0:
            log("XN name index: %d modules, queue %d, %.0fs" % (decrypted, len(queue), time.time() - started))
        try:
            for candidate in xn_parse.candidates(plain):
                schedule(candidate)
        except Exception:
            continue
    _write_json(output, {"map": {str(key): value for key, value in name_map.items()}, "decrypted": decrypted})
    log("XN name index generated: %s (%d modules)" % (output, len(name_map)))
    return len(name_map)


def ensure(db_path: Path, cache_root: Path, seed_path: Path | None = None, budget: float = 240.0, log=print) -> tuple[Path, Path]:
    opmap_path = cache_root / "xn_opmap.json"
    names_path = cache_root / "xn_names.json"
    if not opmap_path.is_file():
        try:
            build_opmap(db_path, opmap_path, log)
        except Exception as exc:
            log("XN opcode index failed: %s" % exc)
    need_build = not names_path.is_file()
    if need_build and seed_path and seed_path.is_file():
        try:
            _write_json(names_path, json.loads(seed_path.read_text("utf-8")))
            log("XN name index seeded from %s" % seed_path)
        except (OSError, ValueError):
            pass
    if need_build:
        thread = threading.Thread(
            target=build_names,
            args=(db_path, names_path, budget, 20000, names_path if names_path.is_file() else seed_path, log),
            name="xn-name-index",
            daemon=True,
        )
        thread.start()
    return opmap_path, names_path
