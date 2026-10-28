#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract verified model display names from bindict/common/editor_model.bin.

Tier 1 pairs a .gim/.mesh path with a Chinese name only when the row repeats the
path's own file stem (self-verified).  Tier 2 accepts rows that reference a
single .gim/.mesh, and drops any path whose name rivals a multi-path row.
The shard key is recovered from client.exe (two reversed 8-byte hex halves).
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import zstandard
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nxpk_mode3 as N

SHARD_KEY = bytes.fromhex("70b2215f04d6373f3a7c3ebe21a7ef76")
TABLE_PATH = "bindict/common/editor_model.bin"
ROW_FLAG = 40
ROW_MATERIAL = 417
ROW_NAME_INDEX = 4
SINGLE_PATH_WINDOW = 40
MAX_STRINGS = 400000
MAX_MAIN = 64 * 1024 * 1024
MAX_PAIRS = 300000


def read_leb(data: bytes, pos: int, end: int):
    value = 0
    shift = 0
    while pos < end:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos
    return None, pos


def leb_bytes(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def key_matches_shard(shard: Path, key: bytes) -> bool:
    try:
        size = shard.stat().st_size
        with open(shard, "rb") as handle:
            count = struct.unpack("<I", handle.read(4))[0]
            if not count or count > 4000000:
                return False
            head = handle.read(min(count, 4) * 24)
    except (OSError, struct.error):
        return False
    if len(head) < 24:
        return False
    head = N.decrypt_index(head, key)
    for pos in range(0, len(head), 24):
        _sig, offset, packed, unpacked, _hash, compression, _flags = struct.unpack_from("<5I2H", head, pos)
        if compression not in (0, 1, 3):
            return False
        if not (0 < packed <= size and 0 < unpacked <= (1 << 31)):
            return False
        if offset + packed > size:
            return False
    return True


def discover_shard_key(game_root: Path):
    shards = [
        candidate for candidate in (game_root / "ppk").glob("*")
        if candidate.is_file() and candidate.name not in {"assets.ppk", "assets.ppkn"}
    ]
    if not shards:
        return None
    order = shard_manifest_order(game_root)

    def rank(candidate: Path):
        try:
            return (order.get(candidate.name, -1), candidate.stat().st_mtime, candidate.stat().st_size)
        except OSError:
            return (order.get(candidate.name, -1), 0.0, 0)

    shard = max(shards, key=rank)
    for key in N.client_key_candidates(game_root / "bin" / "client.exe"):
        if key_matches_shard(shard, key):
            return key
    return None


def shard_manifest_order(game_root: Path) -> dict:
    ppk = game_root / "ppk"
    rank = {}
    try:
        raw = (ppk / "assets.ppk").read_bytes()
    except OSError:
        return rank
    names = {candidate.name for candidate in ppk.glob("*") if candidate.is_file()}
    position = 0
    for offset in range(0, len(raw) - 3, 4):
        name = raw[offset:offset + 4].hex()
        if name in names:
            rank[name] = position
            position += 1
    return rank


def load_shard_entry(game_root: Path, path: str, keys: list) -> bytes | None:
    signature = N.path_hash(path)
    ppk = game_root / "ppk"
    order = shard_manifest_order(game_root)

    def mtime(candidate: Path) -> float:
        try:
            return candidate.stat().st_mtime
        except OSError:
            return 0.0

    shards = [
        candidate for candidate in ppk.glob("*")
        if candidate.is_file() and candidate.name not in {"assets.ppk", "assets.ppkn"}
    ]
    shards.sort(key=lambda candidate: (order.get(candidate.name, -1), mtime(candidate)), reverse=True)
    best = None
    best_rank = None
    for shard in shards:
        try:
            with open(shard, "rb") as handle:
                count = struct.unpack("<I", handle.read(4))[0]
                if not count or count > 4000000:
                    continue
                raw = handle.read(count * 24)
                if len(raw) != count * 24:
                    continue
                for key in keys:
                    table = N.decrypt_index(raw, key)
                    found = None
                    for index in range(count):
                        sig, offset, packed, unpacked, _hash, compression, _flags = struct.unpack_from("<5I2H", table, index * 24)
                        if sig == signature:
                            found = (offset, packed, unpacked, compression)
                            break
                    if found is None:
                        continue
                    offset, packed, unpacked, compression = found
                    if not packed or unpacked > MAX_MAIN * 4:
                        continue
                    handle.seek(offset)
                    body = handle.read(packed)
                    if compression == 3:
                        body = zstandard.ZstdDecompressor().decompress(body, max_output_size=unpacked)
                    if len(body) < 12:
                        continue
                    rank = (order.get(shard.name, -1), mtime(shard), struct.unpack_from("<I", body, 0)[0])
                    if best_rank is None or rank > best_rank:
                        best, best_rank = body, rank
                    break
        except (OSError, ValueError, struct.error, zstandard.ZstdError):
            continue
    return best


def parse_table(data: bytes) -> dict:
    if len(data) < 12:
        return {}
    count = struct.unpack_from("<I", data, 0)[0]
    if not count or count > MAX_STRINGS:
        return {}
    table_start = 8 + count * 4
    if table_start > len(data):
        return {}
    strings = []
    begin = 0
    for index in range(count):
        end = struct.unpack_from("<I", data, 8 + index * 4)[0]
        if end < begin or table_start + end > len(data):
            return {}
        strings.append(data[table_start + begin:table_start + end].decode("utf-8", "replace"))
        begin = end
    row_start = table_start + begin
    if row_start + 4 > len(data):
        return {}
    main_len = struct.unpack_from("<I", data, row_start)[0]
    root = row_start + main_len
    if main_len > MAX_MAIN or root + 4 > len(data):
        return {}
    return {"strings": strings, "row_start": row_start, "main_len": main_len, "root": root}


def parse_rows(data: bytes, table: dict) -> list:
    root = table["root"]
    tag = data[root]
    if (tag & 0x0F) != 6 or (tag & 0x10) == 0 or (tag & 0x20) == 0:
        return []
    pos = root + 1
    if tag & 0x10:
        pos += 1
    if tag & 0x20:
        pos += 1
    count, pos = read_leb(data, pos, len(data))
    if count is None or count > MAX_PAIRS or pos + count * 8 > len(data):
        return []
    leaf = pos + count * 8
    rows = []
    pos = leaf
    while pos < len(data):
        key, pos = read_leb(data, pos, len(data))
        offset, pos = read_leb(data, pos, len(data))
        if key is None or offset is None:
            break
        rows.append((key, offset))
        if len(rows) > MAX_PAIRS:
            break
    return rows


def build_names(data: bytes) -> dict:
    table = parse_table(data)
    if not table:
        return {}
    strings = table["strings"]
    rows = parse_rows(data, table)
    if not rows:
        return {}
    body = data[table["row_start"] + 4:]
    total = table["main_len"]
    is_mesh = [value.endswith((".gim", ".mesh")) for value in strings]
    is_cjk = [any("\u4e00" <= char <= "\u9fff" for char in value) for value in strings]
    by_text = {}
    for index, value in enumerate(strings):
        by_text.setdefault(value, index)
    mesh_bytes = {}
    for index, flag in enumerate(is_mesh):
        if flag:
            mesh_bytes.setdefault(leb_bytes(index), []).append(index)
    stem_id = {}
    for index, flag in enumerate(is_mesh):
        if flag:
            stem = strings[index].rsplit("/", 1)[-1].rsplit(".", 1)[0]
            ref = by_text.get(stem)
            if ref is not None and ref != index:
                stem_id[index] = ref
    stem_bytes = {index: leb_bytes(ref) for index, ref in stem_id.items()}

    ends = [rows[index + 1][1] if index + 1 < len(rows) else total for index in range(len(rows))]
    options = {}
    single_options = {}
    multi_first = {}
    for index, (key, offset) in enumerate(rows):
        end = ends[index]
        if offset >= total or end <= offset:
            continue
        row = body[offset:end]
        pos = 0
        fields = []
        for _ in range(7):
            value, pos = read_leb(row, pos, len(row))
            if value is None:
                break
            fields.append(value)
        if len(fields) != 7 or fields[0] != ROW_FLAG or fields[1] != ROW_MATERIAL:
            continue
        name_index = fields[ROW_NAME_INDEX]
        if name_index >= len(strings) or not is_cjk[name_index]:
            continue
        name = strings[name_index]
        all_ids = []
        all_pos = {}
        verified = []
        scan = pos
        while scan < len(row):
            hit = None
            for length in (1, 2, 3, 4, 5):
                chunk = row[scan:scan + length]
                if len(chunk) != length:
                    break
                if chunk in mesh_bytes:
                    hit = chunk
                    break
            if hit is None:
                scan += 1
                continue
            for mesh_id in mesh_bytes[hit]:
                if mesh_id not in all_ids:
                    all_ids.append(mesh_id)
                    all_pos[mesh_id] = scan - pos
                stem = stem_bytes.get(mesh_id)
                if stem is not None and stem in row[scan + len(hit):scan + len(hit) + 24] and mesh_id not in verified:
                    verified.append(mesh_id)
            scan += len(hit)
        if len(verified) == 1:
            path = strings[verified[0]]
            options.setdefault(path, {}).setdefault(name, 0)
            options[path][name] += 1
        elif len(all_ids) == 1 and all_pos[all_ids[0]] < SINGLE_PATH_WINDOW:
            path = strings[all_ids[0]]
            single_options.setdefault(path, {}).setdefault(name, 0)
            single_options[path][name] += 1
        elif len(all_ids) > 1:
            multi_first.setdefault(strings[all_ids[0]], {}).setdefault(name, 0)
            multi_first[strings[all_ids[0]]][name] += 1

    names = {}
    for path, counter in options.items():
        if len(counter) == 1:
            names[path] = next(iter(counter))
    for path, counter in single_options.items():
        if len(counter) != 1 or path in options:
            continue
        name = next(iter(counter))
        rival = multi_first.get(path)
        if rival and any(other != name for other in rival):
            continue
        names[path] = name
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract verified .gim/.mesh display names from editor_model.bin")
    parser.add_argument("--game-root", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--key", default=None)
    args = parser.parse_args()
    game_root = Path(args.game_root) if args.game_root else None
    if game_root is None:
        try:
            import extract_mode3 as E
            game_root = E.find_game_root(None)
        except Exception:
            game_root = Path(r"D:\FeverApps\party_pc")
    output = Path(args.output) if args.output else Path(__file__).resolve().parent.parent / "data" / "model_names.json"
    if args.key:
        keys = [bytes.fromhex(args.key)]
        key_source = "--key"
    else:
        discovered = discover_shard_key(game_root)
        keys = [discovered, SHARD_KEY, N.MODE3_KEY] if discovered else [SHARD_KEY, N.MODE3_KEY]
        key_source = "client.exe" if discovered else "built-in"
    keys = list(dict.fromkeys(keys))
    data = load_shard_entry(game_root, TABLE_PATH, keys)
    if not data:
        print("editor_model bindict not found; the shard key may have changed, pass --key <hex>", file=sys.stderr)
        return 2
    names = build_names(data)
    if not names:
        print("no verified names extracted", file=sys.stderr)
        return 3
    payload = {"source": TABLE_PATH, "map": names}
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, output)
    print(f"wrote {output} ({len(names):,} names, key {key_source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
