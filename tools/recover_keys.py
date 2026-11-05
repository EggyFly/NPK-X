#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recover Eggy Party package keys from client binaries, nothing hardcoded.

PC client.exe
    Each key is stored as two ASCII hex tokens (16 chars each) in the NeoX
    filesystem constant table; the runtime key reverses the bytes of each
    8-byte half of the concatenation (the getter behavior documented in
    nxpk_mode3.py).  The npk mode-3 key is the pair whose decryption of a
    local NPK index yields sorted signatures and sane bounds; the ppk shard
    key is the pair that decrypts a local shard index plus one zstd body.

Android libclient.so
    The .rodata region is obfuscated with plain = rol8(c, 3) ^ 0x37; inside
    it 16-hex tokens sit in 17-byte stride runs next to the string
    "N4neox10filesystem15NXZipStubOpenerE" (docs/unresolved-sweep/17).  Token
    pairs from that run validate against the script.npk index (NXPK mode-3
    structure) and against PZ$E manifest heads (zstd magic after decryption).

Every key is verified against real game files before it is reported; a failed
verification is an error, never a silent wrong value.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
import zstandard
from pathlib import Path

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import nxpk_mode3 as N

HEX_TOKEN = N.HEX_KEY
TOKEN_GAP = 96
SAMPLE_ENTRIES = 64
SHARD_SAMPLE = 4096
MAX_BODY = 4 * 1024 * 1024
MARKER = b"N4neox10filesystem15NXZipStubOpenerE"
# libclient.so .rodata obfuscation: plain = rol8(c, 3) ^ 0x37
RODATA_TABLE = bytes((((c << 3) | (c >> 5)) & 0xFF) ^ 0x37 for c in range(256))


class RecoverError(RuntimeError):
    pass


def pair_key(first: bytes, second: bytes) -> bytes:
    raw = bytes.fromhex((first + second).decode("ascii"))
    return raw[:8][::-1] + raw[8:][::-1]


def exe_key_candidates(data: bytes) -> list[dict]:
    matches = [(match.start(), match.group()) for match in HEX_TOKEN.finditer(data)]
    seen: dict[bytes, dict] = {}
    for index, (start, first) in enumerate(matches):
        for next_index in range(index + 1, len(matches)):
            next_start, second = matches[next_index]
            if next_start - start > TOKEN_GAP:
                break
            key = pair_key(first, second)
            if key not in seen:
                seen[key] = {
                    "key": key,
                    "token_pair": [first.decode("ascii"), second.decode("ascii")],
                    "token_offsets": [start, next_start],
                }
    if not seen:
        raise RecoverError("client.exe holds no adjacent hex token pair; the key storage scheme has changed")
    return list(seen.values())


def scan_token_runs(plain: bytes) -> list[dict]:
    runs: list[dict] = []
    current = None
    for match in HEX_TOKEN.finditer(plain):
        start = match.start()
        if current is not None and start == current["end"] + 1:
            current["tokens"].append(match.group())
            current["end"] = start + 16
            continue
        if current is not None:
            runs.append(current)
        current = {"start": start, "end": start + 16, "tokens": [match.group()]}
    if current is not None:
        runs.append(current)
    return runs


def merge_runs(runs: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for run in runs:
        if merged and run["start"] - merged[-1]["end"] <= TOKEN_GAP:
            merged[-1]["end"] = run["end"]
            merged[-1]["tokens"] += run["tokens"]
        else:
            merged.append(dict(run))
    return merged


def lib_key_candidates(data: bytes) -> tuple[list[dict], dict]:
    decoded = data.translate(RODATA_TABLE)
    marker_at = decoded.find(MARKER)
    best = None
    source = None
    for label, blob in (("rodata-decoded", decoded), ("raw", data)):
        qualified = [run for run in merge_runs(scan_token_runs(blob)) if len(run["tokens"]) >= 6]
        near = [run for run in qualified if marker_at >= 0 and abs(run["start"] - marker_at) <= 4096]
        pool = near or qualified
        if pool:
            best = max(pool, key=lambda run: len(run["tokens"]))
            source = label
            break
    if best is None:
        raise RecoverError("libclient.so holds no 17-byte-stride hex token run (>=6 tokens); the .rodata scheme has changed")
    tokens = best["tokens"]
    seen: dict[bytes, dict] = {}
    for i in range(len(tokens)):
        for j in range(i + 1, len(tokens)):
            key = pair_key(tokens[i], tokens[j])
            if key not in seen:
                seen[key] = {
                    "key": key,
                    "token_pair": [tokens[i].decode("ascii"), tokens[j].decode("ascii")],
                    "token_offsets": [best["start"] + i * 17, best["start"] + j * 17],
                }
    info = {"token_source": source, "run_start": best["start"], "token_count": len(tokens), "marker_offset": marker_at}
    return list(seen.values()), info


def check_npk_sample(path: Path, key: bytes):
    try:
        with open(path, "rb") as handle:
            header = N.read_header(handle.read(N.HEADER_SIZE))
            if header.index_mode != 3:
                return False, f"index_mode {header.index_mode}"
            middle = (header.entry_count // 2) // 4 * 4
            windows = []
            for start in sorted({0, middle}):
                count = min(SAMPLE_ENTRIES, header.entry_count - start)
                count -= count % 4
                if count <= 0:
                    continue
                handle.seek(header.index_offset + start * N.ENTRY_SIZE)
                chunk = handle.read(count * N.ENTRY_SIZE)
                if len(chunk) != count * N.ENTRY_SIZE:
                    return False, "short index read"
                windows.append((chunk, count))
    except (OSError, ValueError, struct.error) as exc:
        return False, str(exc)
    checked = 0
    for chunk, count in windows:
        table = N.decrypt_index(chunk, key)
        entries = N.parse_entries(table, count)
        signatures = [entry.signature for entry in entries]
        if any(signatures[i] > signatures[i + 1] for i in range(len(signatures) - 1)):
            return False, "signatures not sorted"
        for entry in entries:
            if entry.offset < N.HEADER_SIZE or entry.offset + entry.packed_size > header.index_offset:
                return False, f"entry out of bounds at {entry.signature:#x}"
            if entry.compression not in (N.COMPRESSION_STORED, N.COMPRESSION_ZSTD):
                return False, f"compression {entry.compression}"
            if entry.compression == N.COMPRESSION_STORED and entry.packed_size != entry.unpacked_size:
                return False, "stored size mismatch"
        checked += count
    return True, {"windows": len(windows), "entries_checked": checked}


def check_shard_sample(path: Path, key: bytes):
    size = path.stat().st_size
    try:
        with open(path, "rb") as handle:
            count = struct.unpack("<I", handle.read(4))[0]
            if not count or count > 4000000:
                return False, f"bad entry count {count}"
            take = min(count, SHARD_SAMPLE)
            raw = handle.read(take * 24)
    except (OSError, struct.error) as exc:
        return False, str(exc)
    if len(raw) != take * 24:
        return False, "short shard index"
    body = None
    checked = 0
    for start in range(0, take, SAMPLE_ENTRIES):
        # 24-byte entries: even spans keep the chunk 16-byte aligned for decrypt_index
        span = min(SAMPLE_ENTRIES, take - start)
        span -= span % 2
        if span <= 0:
            break
        table = N.decrypt_index(raw[start * 24:(start + span) * 24], key)
        for pos in range(0, span * 24, 24):
            _sig, offset, packed, unpacked, _content_hash, compression, _flags = struct.unpack_from("<5I2H", table, pos)
            checked += 1
            if compression not in (0, 1, 3):
                return False, f"compression {compression}"
            if packed and offset + packed > size:
                return False, "entry out of bounds"
            if unpacked > (1 << 31):
                return False, "unpacked size overflow"
            if compression == 3 and 0 < packed and 0 < unpacked <= MAX_BODY and body is None:
                body = (offset, packed, unpacked)
        if body is not None:
            break
    if body is None:
        return False, "no small zstd entry in the sampled shard index"
    with open(path, "rb") as handle:
        handle.seek(body[0])
        payload = handle.read(body[1])
    if len(payload) != body[1]:
        return False, "short payload read"
    try:
        out = zstandard.ZstdDecompressor().decompress(payload, max_output_size=body[2])
    except zstandard.ZstdError as exc:
        return False, f"zstd body: {exc}"
    if len(out) != body[2]:
        return False, "zstd body size mismatch"
    return True, {"entries_checked": checked, "zstd_body": {"offset": body[0], "packed": body[1], "unpacked": body[2]}}


def check_android_npk_sample(head: N.Header, index: bytes, key: bytes):
    if head.index_mode != 3:
        return False, f"index_mode {head.index_mode}"
    take = min(SAMPLE_ENTRIES, head.entry_count)
    take -= take % 4
    if take <= 0:
        return False, "entry_count too small to sample"
    chunk = index[:take * N.ENTRY_SIZE]
    table = N.decrypt_index(chunk, key)
    entries = N.parse_entries(table, take)
    signatures = [entry.signature for entry in entries]
    if any(signatures[i] > signatures[i + 1] for i in range(len(signatures) - 1)):
        return False, "signatures not sorted"
    for entry in entries:
        if entry.offset < N.HEADER_SIZE or entry.offset + entry.packed_size > head.index_offset:
            return False, f"entry out of bounds at {entry.signature:#x}"
        if entry.compression not in (N.COMPRESSION_STORED, N.COMPRESSION_ZSTD):
            return False, f"compression {entry.compression}"
    return True, {"entries_checked": len(entries)}


def check_android_npk_full(head: N.Header, index: bytes, key: bytes):
    if len(index) != head.entry_count * N.ENTRY_SIZE:
        return False, f"index holds {len(index) // N.ENTRY_SIZE} entries, header says {head.entry_count}"
    table = N.decrypt_index(index, key)
    entries = N.parse_entries(table, head.entry_count)
    compressions: dict[int, int] = {}
    data_end = 0
    previous = -1
    for entry in entries:
        if entry.signature < previous:
            return False, f"signatures not sorted at {entry.signature:#x}"
        previous = entry.signature
        if entry.offset < N.HEADER_SIZE or entry.offset + entry.packed_size > head.index_offset:
            return False, f"entry out of bounds at {entry.signature:#x}"
        if entry.compression not in (N.COMPRESSION_STORED, N.COMPRESSION_ZSTD):
            return False, f"compression {entry.compression}"
        if entry.compression == N.COMPRESSION_STORED and entry.packed_size != entry.unpacked_size:
            return False, "stored size mismatch"
        compressions[entry.compression] = compressions.get(entry.compression, 0) + 1
        data_end = max(data_end, entry.offset + entry.packed_size)
    return True, {
        "entries": len(entries),
        "first_signature": f"{entries[0].signature:08x}",
        "last_signature": f"{entries[-1].signature:08x}",
        "data_end": data_end,
        "index_offset": head.index_offset,
        "compressions": {str(k): v for k, v in sorted(compressions.items())},
    }


def check_pze_files(paths: list[Path], key: bytes):
    for path in paths:
        with open(path, "rb") as handle:
            head = handle.read(4 + 64)
        if head[:4] != b"PZ$E":
            return False, f"{path.name}: magic {head[:4]!r}"
        plain = N.decrypt_index(head[4:], key)
        if not plain.startswith(b"\x28\xb5\x2f\xfd"):
            return False, f"{path.name}: no zstd magic after decrypt"
    return True, {"samples": [path.name for path in paths], "zstd_magic": "28b52ffd"}


def recover_one(label: str, candidates: list[dict], checker) -> dict:
    hits = []
    for candidate in candidates:
        ok, detail = checker(candidate["key"])
        if ok:
            hits.append((candidate, detail))
    if not hits:
        raise RecoverError(f"{label}: none of {len(candidates)} candidate keys validated against real game files")
    distinct = {candidate["key"] for candidate, _ in hits}
    if len(distinct) > 1:
        raise RecoverError(f"{label}: {len(distinct)} different keys validated, the storage scheme needs review")
    record = dict(hits[0][0])
    record["verify"] = hits[0][1]
    return record


def pick_sample_npk(game_root: Path) -> Path:
    for path in sorted(game_root.glob("*.npk"), key=lambda item: item.stat().st_size):
        try:
            with open(path, "rb") as handle:
                header = N.read_header(handle.read(N.HEADER_SIZE))
        except (OSError, ValueError, struct.error):
            continue
        if header.entry_count >= 8 and header.index_mode == 3:
            return path
    raise RecoverError(f"no non-empty mode-3 npk under {game_root}")


def pick_shard(game_root: Path) -> Path:
    folder = game_root / "ppk"
    files = [item for item in folder.glob("*") if item.is_file() and item.name not in {"assets.ppk", "assets.ppkn"}]
    if not files:
        raise RecoverError(f"no ppk shards under {folder}")
    return max(files, key=lambda item: item.stat().st_size)


def describe(record: dict, name: str, usage: str, source: Path, pattern: str) -> dict:
    return {
        "name": name,
        "usage": usage,
        "key_hex": record["key"].hex(),
        "source_file": str(source),
        "token_pair": record["token_pair"],
        "token_offsets": [f"0x{offset:x}" for offset in record["token_offsets"]],
        "pattern": pattern,
        "verify": record["verify"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover Eggy Party npk/ppk keys from client.exe and libclient.so")
    parser.add_argument("--game-root", help="folder holding bin/client.exe and *.npk")
    parser.add_argument("--client", help="path to client.exe")
    parser.add_argument("--lib", help="path to libclient.so (arm64)")
    parser.add_argument("--android-dir", help="folder holding script_npk_head/index and PZ$E samples")
    parser.add_argument("--output", help="write the JSON report to this file")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent.parent
    default_root = Path(r"D:\FeverApps\party_pc")
    if args.game_root:
        game_root = Path(args.game_root)
    elif default_root.joinpath("bin", "client.exe").is_file():
        game_root = default_root
    else:
        raise RecoverError("pass --game-root (the folder holding bin/client.exe and *.npk)")
    client = Path(args.client) if args.client else game_root / "bin" / "client.exe"
    lib = Path(args.lib) if args.lib else repo / "t3_audit" / "tmp_share" / "libclient_arm64.so"
    android_dir = Path(args.android_dir) if args.android_dir else repo / "_android-pathtable" / "dl"

    if not client.is_file():
        raise RecoverError(f"client.exe not found: {client}")
    if not lib.is_file():
        raise RecoverError(f"libclient.so not found: {lib} (extract lib/arm64-v8a/libclient.so from the APK or pass --lib)")
    head_path = android_dir / "script_npk_head.bin"
    index_path = android_dir / "script_npk_index_enc.bin"
    if not head_path.is_file() or not index_path.is_file():
        raise RecoverError(f"android script.npk sample missing under {android_dir} (script_npk_head.bin / script_npk_index_enc.bin)")
    pze_paths = [item for item in (android_dir / "assets.ppk.map.bin", android_dir / "script_file_list.bin") if item.is_file()]
    if not pze_paths:
        raise RecoverError(f"no PZ$E sample under {android_dir} (assets.ppk.map.bin / script_file_list.bin)")

    npk_sample = pick_sample_npk(game_root)
    shard = pick_shard(game_root)
    print(f"client: {client}", file=sys.stderr)
    print(f"lib: {lib}", file=sys.stderr)
    print(f"npk sample: {npk_sample.name}", file=sys.stderr)
    print(f"ppk shard: {shard.name}", file=sys.stderr)

    exe_data = client.read_bytes()
    lib_data = lib.read_bytes()
    exe_cands = exe_key_candidates(exe_data)
    lib_cands, lib_info = lib_key_candidates(lib_data)
    print(f"client.exe token pairs: {len(exe_cands)}", file=sys.stderr)
    print(f"libclient.so token run: {lib_info}", file=sys.stderr)

    head = N.read_header(head_path.read_bytes())
    index_bytes = index_path.read_bytes()

    npk_record = recover_one("pc npk mode-3", exe_cands, lambda key: check_npk_sample(npk_sample, key))
    print(f"pc npk mode-3 key: {npk_record['key'].hex()}", file=sys.stderr)
    ppk_record = recover_one("pc ppk shard", exe_cands, lambda key: check_shard_sample(shard, key))
    print(f"pc ppk shard key: {ppk_record['key'].hex()}", file=sys.stderr)
    android_npk_record = recover_one("android npk", lib_cands, lambda key: check_android_npk_sample(head, index_bytes, key))
    ok, detail = check_android_npk_full(head, index_bytes, android_npk_record["key"])
    if not ok:
        raise RecoverError(f"android npk full index check failed: {detail}")
    android_npk_record["verify"] = detail
    print(f"android npk key: {android_npk_record['key'].hex()}", file=sys.stderr)
    android_ppk_record = recover_one("android ppk manifest", lib_cands, lambda key: check_pze_files(pze_paths, key))
    print(f"android ppk manifest key: {android_ppk_record['key'].hex()}", file=sys.stderr)

    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "inputs": {
            "client": str(client),
            "client_size": len(exe_data),
            "lib": str(lib),
            "lib_size": len(lib_data),
            "npk_sample": str(npk_sample),
            "ppk_shard": str(shard),
            "android_head": str(head_path),
            "android_index": str(index_path),
            "pze_samples": [str(item) for item in pze_paths],
            "lib_token_run": lib_info,
        },
        "keys": [
            describe(npk_record, "pc_npk_mode3", "PC npk index (NXPK mode-3)", client,
                     "two ASCII hex tokens in client.exe, 8-byte halves reversed at runtime; validated against a local npk index"),
            describe(ppk_record, "pc_ppk_index", "PC ppk shard index", client,
                     "two ASCII hex tokens in client.exe, 8-byte halves reversed at runtime; validated against a local ppk shard index plus one zstd body"),
            describe(android_npk_record, "android_npk", "Android npk index (script.npk, NXPK mode-3)", lib,
                     "libclient.so .rodata (plain = rol8(c,3)^0x37) hex token run near N4neox10filesystem15NXZipStubOpenerE; token pair validated against the script.npk index"),
            describe(android_ppk_record, "android_ppk_manifest", "Android PZ$E manifest (assets.ppk.map, *_file_list)", lib,
                     "same token run; token pair decrypts PZ$E heads to a zstd frame"),
        ],
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"report: {args.output}", file=sys.stderr)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RecoverError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
