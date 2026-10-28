#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-contained decoder for Eggy Party PC NXPK mode-3 indexes.

Format (little-endian)::

    NXPK header (24 bytes)
      +0x00  magic "NXPK"
      +0x04  u32 entry_count
      +0x08  u32 index_offset_high
      +0x0c  u32 index_mode
      +0x10  u32 hash_mode
      +0x14  u32 index_offset_low
      index_offset = (index_offset_high << 32) | index_offset_low

    Entry (28 bytes)
      +0x00  u32 signature      MurmurHash3_x86_32(path, seed=0x9747B28C)
      +0x04  u32 offset_low      true offset = offset_low | (flags & 0x100) << 24
      +0x08  u32 packed_size
      +0x0c  u32 unpacked_size
      +0x10  u32 packed_hash     MurmurHash3_x86_32(packed bytes, same seed)
      +0x14  u32 unpacked_hash   MurmurHash3_x86_32(unpacked bytes, same seed)
      +0x18  u16 compression     0 = stored, 3 = Zstandard
      +0x1a  u16 flags           0x100 = offset bit 32 (container > 4 GiB)

Entries are sorted by signature; the client binary-searches them (0x141180740).

Mode-3 index cipher
-------------------
The index table is decrypted in 16-byte blocks; a trailing partial block is
left in place.  The cipher is an AES-128-shaped inverse cipher with a custom
S-box and T-tables.  The key comes from client getter A (0x142ec53d0), which
joins these two ASCII constants (each 8 hex bytes)::

    14d20d2f8634b0c3   e775617be3e3accb

The getter's hex parser writes the bytes backwards within each half, so the
runtime key is::

    c3b034862f0dd214cbace3e37b6175e7

This was verified by emulating the getter and by decrypting every shipped
NPK: all entry bounds, sizes, hashes and paths validate.  The key schedule
function (0x141185420) reverses the AES round keys per 16-byte group and
passes middle groups through the client's inverse-MixColumns tables; the
block function is 0x141184840.  ``mode3_tables.py`` holds the fixed tables
extracted from the verified implementation.
"""

from __future__ import annotations

import gzip
import json
import mmap
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import mmh3

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import mode3_tables as _t

MASK32 = 0xFFFFFFFF
HEADER = struct.Struct("<4s5I")
ENTRY = struct.Struct("<6I2H")
HEADER_SIZE = HEADER.size
ENTRY_SIZE = ENTRY.size

PATH_HASH_SEED = 0x9747B28C
FLAG_OFFSET_32 = 0x100
COMPRESSION_STORED = 0
COMPRESSION_ZSTD = 3

_KEY_HALVES = ("14d20d2f8634b0c3", "e775617be3e3accb")


def runtime_key() -> bytes:
    """Return the client's runtime mode-3 key for this PC build."""
    raw = bytes.fromhex(_KEY_HALVES[0] + _KEY_HALVES[1])
    return raw[:8][::-1] + raw[8:][::-1]


MODE3_KEY = runtime_key()


def _neo(v: int) -> int:
    ror = ((v >> 8) | (v << 24)) & MASK32
    rol = ((v << 8) | (v >> 24)) & MASK32
    return (ror ^ ((ror ^ rol) & 0x00FF00FF)) & MASK32


def expand_key(key: bytes) -> list[int]:
    """AES-128 key expansion as done by client 0x141185690."""
    if len(key) != 16:
        raise ValueError("mode-3 key must be 16 bytes")
    sbox = _t.SBOX
    words = [_neo(v) for v in struct.unpack("<4I", key)]
    rcon = 1
    for i in range(4, 44):
        previous = words[i - 1]
        if i % 4 == 0:
            b0, b1, b2, b3 = (
                (previous >> 24) & 0xFF,
                (previous >> 16) & 0xFF,
                (previous >> 8) & 0xFF,
                previous & 0xFF,
            )
            g = ((sbox[b1] << 24) | (sbox[b2] << 16) | (sbox[b3] << 8) | sbox[b0]) ^ (rcon << 24)
            words.append((words[i - 4] ^ g) & MASK32)
            rcon = ((rcon << 1) ^ (0x11B if rcon & 0x80 else 0)) & 0xFF
        else:
            words.append((words[i - 4] ^ previous) & MASK32)
    return words


def prepare_schedule(key: bytes) -> list[int]:
    """Equivalent-inverse schedule as produced by client 0x141185420."""
    words = expand_key(key)
    words = [w for group in reversed(range(11)) for w in words[group * 4:group * 4 + 4]]
    e0, e1, e2, e3 = _t.E0, _t.E1, _t.E2, _t.E3
    out = list(words)
    for group in range(1, 10):
        for i in range(4):
            w = words[group * 4 + i]
            out[group * 4 + i] = (
                e1[(w >> 16) & 0xFF]
                ^ e2[(w >> 8) & 0xFF]
                ^ e0[(w >> 24) & 0xFF]
                ^ e3[w & 0xFF]
            ) & MASK32
    return out + [0] * 16 + [10]


def decrypt_block(block: bytes, schedule: list[int]) -> bytes:
    """Decrypt one 16-byte block (client 0x141184840)."""
    if len(block) != 16:
        raise ValueError("block must be 16 bytes")
    t0, t1, t2, t3 = _t.T0, _t.T1, _t.T2, _t.T3
    inv_sbox = _t.INV_SBOX
    k = schedule
    x = struct.unpack("<4I", block)
    s = [_neo(x[i]) ^ k[i] for i in range(4)]

    a = (k[4] ^ t0[s[1] & 255] ^ t1[s[0] >> 24] ^ t2[(s[3] >> 16) & 255] ^ t3[(s[2] >> 8) & 255]) & MASK32
    b = (k[5] ^ t0[s[2] & 255] ^ t1[s[1] >> 24] ^ t3[(s[3] >> 8) & 255] ^ t2[(s[0] >> 16) & 255]) & MASK32
    c = (k[6] ^ t0[s[3] & 255] ^ t1[s[2] >> 24] ^ t3[(s[0] >> 8) & 255] ^ t2[(s[1] >> 16) & 255]) & MASK32
    d = (k[7] ^ t0[s[0] & 255] ^ t1[s[3] >> 24] ^ t2[(s[2] >> 16) & 255] ^ t3[(s[1] >> 8) & 255]) & MASK32

    j = 8
    for _ in range((k[60] >> 1) - 1):
        w = (k[j + 3] ^ t0[a & 255] ^ t1[d >> 24] ^ t2[(c >> 16) & 255] ^ t3[(b >> 8) & 255]) & MASK32
        x2 = (k[j + 2] ^ t0[d & 255] ^ t1[c >> 24] ^ t2[(b >> 16) & 255] ^ t3[(a >> 8) & 255]) & MASK32
        y = (k[j + 1] ^ t0[c & 255] ^ t1[b >> 24] ^ t3[(d >> 8) & 255] ^ t2[(a >> 16) & 255]) & MASK32
        z = (k[j] ^ t0[b & 255] ^ t1[a >> 24] ^ t2[(d >> 16) & 255] ^ t3[(c >> 8) & 255]) & MASK32
        a = (k[j + 4] ^ t0[y & 255] ^ t1[z >> 24] ^ t2[(w >> 16) & 255] ^ t3[(x2 >> 8) & 255]) & MASK32
        b = (k[j + 5] ^ t0[x2 & 255] ^ t1[y >> 24] ^ t3[(w >> 8) & 255] ^ t2[(z >> 16) & 255]) & MASK32
        c = (k[j + 6] ^ t0[w & 255] ^ t1[x2 >> 24] ^ t2[(y >> 16) & 255] ^ t3[(z >> 8) & 255]) & MASK32
        d = (k[j + 7] ^ t0[z & 255] ^ t1[w >> 24] ^ t2[(x2 >> 16) & 255] ^ t3[(y >> 8) & 255]) & MASK32
        j += 8

    r = [
        (k[j] ^ inv_sbox[b & 255] ^ (inv_sbox[(c >> 8) & 255] << 8)
         ^ (inv_sbox[(d >> 16) & 255] << 16) ^ (inv_sbox[a >> 24] << 24)) & MASK32,
        (k[j + 1] ^ inv_sbox[c & 255] ^ (inv_sbox[(d >> 8) & 255] << 8)
         ^ (inv_sbox[(a >> 16) & 255] << 16) ^ (inv_sbox[b >> 24] << 24)) & MASK32,
        (k[j + 2] ^ inv_sbox[d & 255] ^ (inv_sbox[(a >> 8) & 255] << 8)
         ^ (inv_sbox[(b >> 16) & 255] << 16) ^ (inv_sbox[c >> 24] << 24)) & MASK32,
        (k[j + 3] ^ inv_sbox[a & 255] ^ (inv_sbox[(b >> 8) & 255] << 8)
         ^ (inv_sbox[(c >> 16) & 255] << 16) ^ (inv_sbox[d >> 24] << 24)) & MASK32,
    ]
    return struct.pack("<4I", *[_neo(v) for v in r])


def decrypt_index(data: bytes | bytearray, key: bytes = MODE3_KEY) -> bytes:
    """Decrypt a mode-3 index table; a trailing partial block is preserved."""
    schedule = prepare_schedule(key)
    buffer = bytearray(data)
    for offset in range(0, len(buffer) & ~15, 16):
        buffer[offset:offset + 16] = decrypt_block(bytes(buffer[offset:offset + 16]), schedule)
    return bytes(buffer)


@dataclass(frozen=True)
class Header:
    magic: bytes
    entry_count: int
    index_offset_high: int
    index_mode: int
    hash_mode: int
    index_offset_low: int
    index_offset: int


@dataclass(frozen=True)
class Entry:
    signature: int
    stored_offset: int
    packed_size: int
    unpacked_size: int
    packed_hash: int
    unpacked_hash: int
    compression: int
    flags: int

    @property
    def offset(self) -> int:
        return self.stored_offset | ((self.flags & FLAG_OFFSET_32) << 24)

    @property
    def offset_high(self) -> bool:
        return bool(self.flags & FLAG_OFFSET_32)


def read_header(data: bytes) -> Header:
    if len(data) < HEADER_SIZE:
        raise ValueError("file shorter than NXPK header")
    magic, count, high, mode, hash_mode, low = HEADER.unpack_from(data)
    if magic != b"NXPK":
        raise ValueError(f"not an NXPK container: {magic!r}")
    return Header(magic, count, high, mode, hash_mode, low, (high << 32) | low)


def parse_entries(table: bytes, count: int | None = None) -> list[Entry]:
    if count is None:
        count = len(table) // ENTRY_SIZE
    if len(table) < count * ENTRY_SIZE:
        raise ValueError("index table shorter than entry_count * 28")
    return [Entry(*ENTRY.unpack_from(table, i * ENTRY_SIZE)) for i in range(count)]




class Mode3FormatError(RuntimeError):
    """Raised when a decrypted index does not look like a valid NPK index."""


def load_profile(filename: str | Path | None = None) -> dict:
    """Load a JSON version profile (key, hash seed, path dictionary)."""
    profile: dict = {
        "name": "embedded-pc-2026",
        "key_hex": MODE3_KEY.hex(),
        "path_hash_seed": PATH_HASH_SEED,
        "known_packages": {},
    }
    if filename:
        path = Path(filename)
        if not path.is_file():
            return profile
        data = json.loads(path.read_text(encoding="utf-8"))
        profile.update(data)
        if "path_hash_seed" in data and isinstance(data["path_hash_seed"], str):
            profile["path_hash_seed"] = int(data["path_hash_seed"], 0)
    return profile


def profile_key(profile: dict | None, key_hex: str | None = None) -> bytes:
    if key_hex:
        key = bytes.fromhex(key_hex.strip())
        if len(key) != 16:
            raise ValueError("key must be 16 bytes (32 hex chars)")
        return key
    if profile and profile.get("key_hex"):
        key = bytes.fromhex(str(profile["key_hex"]).strip())
        if len(key) != 16:
            raise ValueError("profile key must be 16 bytes")
        return key
    return MODE3_KEY


def profile_seed(profile: dict | None) -> int:
    if not profile:
        return PATH_HASH_SEED
    value = profile.get("path_hash_seed", PATH_HASH_SEED)
    return int(value, 0) if isinstance(value, str) else int(value)


HEX_KEY = re.compile(rb"(?<![0-9a-f])[0-9a-f]{16}(?![0-9a-f])")
_CLIENT_KEY_CACHE: dict = {}


def client_key_candidates(executable: str | Path) -> list:
    """Recover reversed 8-byte key halves stored as nearby ASCII strings."""
    path = Path(executable)
    try:
        stat = path.stat()
        stamp = (str(path.resolve()), stat.st_mtime, stat.st_size)
    except OSError:
        return []
    cached = _CLIENT_KEY_CACHE.get(stamp)
    if cached is not None:
        return cached
    try:
        data = path.read_bytes()
    except OSError:
        return []
    matches = [(match.start(), match.group().decode()) for match in HEX_KEY.finditer(data)]
    keys = []
    for index, (start, first) in enumerate(matches):
        for next_index in range(index + 1, min(index + 5, len(matches))):
            next_start, second = matches[next_index]
            if next_start - start > 96:
                break
            raw = bytes.fromhex(first + second)
            keys.append(raw[:8][::-1] + raw[8:][::-1])
    result = list(dict.fromkeys(keys))
    if len(_CLIENT_KEY_CACHE) > 4:
        _CLIENT_KEY_CACHE.clear()
    _CLIENT_KEY_CACHE[stamp] = result
    return result


def discover_package_key(package: str | Path, executable: str | Path) -> bytes | None:
    """Find the index key by validating client.exe candidates against a package."""
    path = Path(package)
    try:
        with open(path, "rb") as handle:
            header = read_header(handle.read(HEADER_SIZE))
            if not header.entry_count:
                return None
            samples = []
            middle = (header.entry_count // 2) // 4 * 4
            for start_index in sorted({0, middle}):
                count = min(64, header.entry_count - start_index)
                if count <= 0:
                    continue
                handle.seek(header.index_offset + start_index * ENTRY_SIZE)
                chunk = handle.read(count * ENTRY_SIZE)
                if len(chunk) == count * ENTRY_SIZE:
                    samples.append((chunk, count))
    except (OSError, ValueError, struct.error):
        return None
    if not samples:
        return None
    for key in client_key_candidates(executable):
        matched = True
        for chunk, count in samples:
            try:
                index = decrypt_index(chunk, key) if header.index_mode == 3 else chunk
                entries = parse_entries(index, count)
            except (ValueError, struct.error):
                matched = False
                break
            signatures = [entry.signature for entry in entries]
            if any(signatures[i] > signatures[i + 1] for i in range(len(signatures) - 1)):
                matched = False
                break
            if any(entry.offset < HEADER_SIZE or entry.offset + entry.packed_size > header.index_offset for entry in entries):
                matched = False
                break
            if any(entry.compression not in (COMPRESSION_STORED, COMPRESSION_ZSTD) for entry in entries):
                matched = False
                break
            if any(entry.compression == COMPRESSION_STORED and entry.packed_size != entry.unpacked_size for entry in entries):
                matched = False
                break
        if matched:
            return key
    return None


def install_tables(filename: str | Path) -> None:
    """Replace the built-in table module with a profile-specific module."""
    global _t
    import importlib.util

    spec = importlib.util.spec_from_file_location("mode3_tables_profile", filename)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load tables module: {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _t = module


def validate_entries(
    header: Header,
    entries: list[Entry],
    path: str | Path | None = None,
    resolver: "PathResolver | None" = None,
    sample: int = 32,
) -> dict:
    """Validate decrypted index metadata; raise on a likely wrong key/version."""
    errors: list[str] = []
    warnings: list[str] = []
    if header.index_mode not in (0, 3):
        errors.append(f"unsupported index_mode {header.index_mode}")
    if not entries:
        errors.append("empty index")
    if len(entries) != header.entry_count:
        errors.append(f"entry_count mismatch: header={header.entry_count} parsed={len(entries)}")
    signatures = [entry.signature for entry in entries]
    if any(signatures[i] > signatures[i + 1] for i in range(len(signatures) - 1)):
        errors.append("entries are not sorted by signature")
    allowed_flags = 0x100
    flag_bits = 0
    for entry in entries:
        flag_bits |= entry.flags & ~allowed_flags
        if entry.offset < HEADER_SIZE or entry.offset + entry.packed_size > header.index_offset:
            errors.append(f"entry out of bounds: signature={entry.signature:08x} offset={entry.offset}")
            break
        if entry.compression not in (COMPRESSION_STORED, COMPRESSION_ZSTD):
            errors.append(f"unsupported compression {entry.compression}")
            break
        if entry.compression == COMPRESSION_STORED and entry.packed_size != entry.unpacked_size:
            errors.append(f"stored entry size mismatch: signature={entry.signature:08x}")
            break
    if flag_bits:
        warnings.append(f"unknown flag bits 0x{flag_bits:x}")
    magic_hits = 0
    magic_checked = 0
    resolution_hits = 0
    resolution_checked = 0
    if path and entries:
        candidates = [entry for entry in entries if entry.compression == COMPRESSION_ZSTD][:sample]
        try:
            with open(path, "rb") as handle:
                for entry in candidates:
                    handle.seek(entry.offset)
                    head = handle.read(4)
                    magic_checked += 1
                    if head == b"\x28\xb5\x2f\xfd":
                        magic_hits += 1
        except OSError:
            pass
        for entry in entries[:sample]:
            resolution_checked += 1
            if resolver and resolver.resolve(entry.signature)[0]:
                resolution_hits += 1
    if magic_checked and magic_hits < max(1, magic_checked // 2):
        errors.append(f"only {magic_hits}/{magic_checked} sampled Zstd frames have a frame header")
    if resolver and resolution_checked and resolution_hits == 0:
        warnings.append("no sampled signature matched the path dictionary (new version or new assets?)")
    report = {
        "index_mode": header.index_mode,
        "entry_count": header.entry_count,
        "sorted": not any("sorted" in error for error in errors),
        "zstd_sample": f"{magic_hits}/{magic_checked}",
        "path_sample": f"{resolution_hits}/{resolution_checked}",
        "flag_bits": f"0x{flag_bits:x}",
        "errors": errors,
        "warnings": warnings,
    }
    if errors:
        raise Mode3FormatError(
            "NPK index validation failed: "
            + "; ".join(errors[:4])
            + ". The game build may have changed; update data/mode3_profile.json "
              "(key/tables/path hash) instead of trusting the output."
        )
    return report


def probe_package(
    path: str | Path,
    key: bytes = MODE3_KEY,
    resolver: "PathResolver | None" = None,
    profile: dict | None = None,
) -> dict:
    """Decrypt and validate one NPK without writing anything."""
    path = Path(path)
    header, entries = read_index(path, key, validate=False)
    report = validate_entries(header, entries, path, resolver)
    report.update(
        {
            "file": path.name,
            "size": path.stat().st_size,
            "index_offset": header.index_offset,
            "hash_mode": header.hash_mode,
            "first_signature": f"{entries[0].signature:08x}" if entries else None,
            "last_signature": f"{entries[-1].signature:08x}" if entries else None,
        }
    )
    if profile:
        known = profile.get("known_packages", {}).get(path.name, {})
        if known.get("entry_count") is not None:
            report["known_package"] = {
                "expected_entries": known.get("entry_count"),
                "match": known.get("entry_count") == header.entry_count,
            }
    return report


def read_index(
    path: str | Path,
    key: bytes = MODE3_KEY,
    validate: bool = False,
    resolver: "PathResolver | None" = None,
) -> tuple[Header, list[Entry]]:
    """Read only the header and decrypted index of an NPK file."""
    with open(path, "rb") as handle:
        header = read_header(handle.read(HEADER_SIZE))
        table_size = header.entry_count * ENTRY_SIZE
        handle.seek(header.index_offset)
        table = handle.read(table_size)
    if len(table) != table_size:
        raise ValueError("truncated index table")
    if header.index_mode == 3:
        table = decrypt_index(table, key)
    elif header.index_mode != 0:
        raise ValueError(f"unsupported index_mode {header.index_mode}")
    entries = parse_entries(table, header.entry_count)
    if validate:
        validate_entries(header, entries, path, resolver)
    return header, entries


def load(path: str | Path, key: bytes = MODE3_KEY) -> tuple[Header, list[Entry], bytes]:
    """Convenience loader that returns the whole file in memory."""
    header, entries = read_index(path, key)
    return header, entries, Path(path).read_bytes()


def path_hash(path: str, seed: int = PATH_HASH_SEED) -> int:
    """Client path hash: MurmurHash3 x86_32 over backslash-separated paths."""
    return mmh3.hash(path.replace("/", "\\"), seed, signed=False)


class PathResolver:
    """Resolve entry signatures to logical paths using a newline path list."""

    def __init__(self, paths: Iterable[str], seed: int = PATH_HASH_SEED):
        self.seed = seed
        self._map: dict[int, list[str]] = {}
        for raw in paths:
            path = raw.strip()
            if not path:
                continue
            self._map.setdefault(path_hash(path, seed), []).append(path)

    @classmethod
    def from_file(cls, filename: str | Path, seed: int = PATH_HASH_SEED) -> "PathResolver":
        filename = Path(filename)
        if filename.suffix.lower() == ".gz":
            with gzip.open(filename, "rt", encoding="utf-8", errors="ignore") as handle:
                return cls(handle, seed)
        with open(filename, "r", encoding="utf-8", errors="ignore") as handle:
            return cls(handle, seed)

    def resolve(self, signature: int) -> tuple[str | None, int]:
        matches = self._map.get(signature)
        if not matches:
            return None, 0
        return matches[0], len(matches)


class Package:
    """Memory-mapped, read-only NPK view."""

    def __init__(self, path: str | Path, key: bytes = MODE3_KEY):
        self.path = Path(path)
        self._file = open(self.path, "rb")
        self.data = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        self.header, self.entries = read_index(self.path, key)

    def read(self, offset: int, size: int) -> bytes:
        return self.data[offset:offset + size]

    def close(self) -> None:
        self.data.close()
        self._file.close()

    def __enter__(self) -> "Package":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def audit(path: str | Path, resolver: PathResolver | None = None, key: bytes = MODE3_KEY, hash_seed: int = PATH_HASH_SEED) -> dict:
    """Validate every index entry and payload of one NPK (read-only)."""
    import zstandard

    path = Path(path)
    dctx = zstandard.ZstdDecompressor()
    with Package(path, key) as pkg:
        header, entries = pkg.header, pkg.entries
        table_size = header.entry_count * ENTRY_SIZE
        result: dict = {
            "file": path.name,
            "size": len(pkg.data),
            "entry_count": header.entry_count,
            "index_mode": header.index_mode,
            "hash_mode": header.hash_mode,
            "index_offset": header.index_offset,
            "index_offset_high": header.index_offset_high,
            "table_bytes": table_size,
            "table_end": header.index_offset + table_size,
        }
        errors: list[str] = []
        if result["table_end"] != len(pkg.data):
            result["table_tail_bytes"] = len(pkg.data) - result["table_end"]
        if not entries:
            errors.append("empty index")
        if any(entries[i].signature > entries[i + 1].signature for i in range(len(entries) - 1)):
            errors.append("entries not sorted by signature")

        compression_flags: dict[int, int] = {}
        offset_flags: dict[int, int] = {}
        flag_bits: dict[int, int] = {}
        ranges: list[tuple[int, int]] = []
        offset_seen: dict[int, int] = {}
        packed_ok = raw_ok = crc_packed_ok = crc_raw_ok = 0
        stored_payload = zstd_payload = 0
        failures: list[dict] = []
        path_stats = {"resolved": 0, "ambiguous": 0, "unresolved": 0, "unresolved_samples": [], "prefixes": {}}
        total_packed = 0
        decompressed = 0

        for number, entry in enumerate(entries):
            compression_flags[entry.compression] = compression_flags.get(entry.compression, 0) + 1
            offset_flags[entry.flags] = offset_flags.get(entry.flags, 0) + 1
            bits = entry.flags & ~FLAG_OFFSET_32
            if bits:
                flag_bits[bits] = flag_bits.get(bits, 0) + 1
            if resolver is not None:
                logical, count = resolver.resolve(entry.signature)
                if logical is None:
                    path_stats["unresolved"] += 1
                    if len(path_stats["unresolved_samples"]) < 8:
                        path_stats["unresolved_samples"].append(hex(entry.signature))
                else:
                    if count > 1:
                        path_stats["ambiguous"] += 1
                    path_stats["resolved"] += 1
                    prefix = logical.split("/", 1)[0]
                    path_stats["prefixes"][prefix] = path_stats["prefixes"].get(prefix, 0) + 1

            if entry.packed_size == 0 and entry.unpacked_size == 0:
                continue
            if entry.offset < HEADER_SIZE or entry.offset + entry.packed_size > header.index_offset:
                failures.append({"entry": number, "reason": "out_of_bounds", "offset": entry.offset,
                                 "packed_size": entry.packed_size})
                continue
            ranges.append((entry.offset, entry.offset + entry.packed_size))
            offset_seen[entry.offset] = offset_seen.get(entry.offset, 0) + 1
            total_packed += entry.packed_size
            packed = pkg.read(entry.offset, entry.packed_size)

            if entry.compression == COMPRESSION_ZSTD:
                if not packed.startswith(b"\x28\xb5\x2f\xfd"):
                    failures.append({"entry": number, "reason": "missing_zstd_magic", "offset": entry.offset})
                    continue
                try:
                    payload = dctx.decompress(packed)
                except Exception:
                    try:
                        obj = dctx.decompressobj()
                        payload = obj.decompress(packed)
                        if not obj.eof or obj.unused_data:
                            raise ValueError("frame does not end at packed_size")
                    except Exception as exc:
                        failures.append({"entry": number, "reason": "zstd_error", "offset": entry.offset,
                                         "detail": str(exc)[:80]})
                        continue
                if len(payload) != entry.unpacked_size:
                    failures.append({"entry": number, "reason": "unpacked_size", "offset": entry.offset,
                                     "expected": entry.unpacked_size, "actual": len(payload)})
                    continue
                zstd_payload += 1
                packed_ok += 1
                decompressed += len(payload)
                if mmh3.hash(packed, hash_seed, signed=False) == entry.packed_hash:
                    crc_packed_ok += 1
                else:
                    failures.append({"entry": number, "reason": "packed_hash", "offset": entry.offset})
                if mmh3.hash(payload, hash_seed, signed=False) == entry.unpacked_hash:
                    crc_raw_ok += 1
                    raw_ok += 1
                else:
                    failures.append({"entry": number, "reason": "unpacked_hash", "offset": entry.offset})
            elif entry.compression == COMPRESSION_STORED:
                if entry.packed_size != entry.unpacked_size:
                    failures.append({"entry": number, "reason": "stored_size", "offset": entry.offset})
                    continue
                stored_payload += 1
                packed_ok += 1
                raw_ok += 1
                decompressed += len(packed)
                if mmh3.hash(packed, hash_seed, signed=False) == entry.packed_hash:
                    crc_packed_ok += 1
                else:
                    failures.append({"entry": number, "reason": "packed_hash", "offset": entry.offset})
                if mmh3.hash(packed, hash_seed, signed=False) == entry.unpacked_hash:
                    crc_raw_ok += 1
                else:
                    failures.append({"entry": number, "reason": "unpacked_hash", "offset": entry.offset})
            else:
                failures.append({"entry": number, "reason": f"unknown_compression_{entry.compression}",
                                 "offset": entry.offset})

        ranges.sort()
        gaps = 0
        gap_count = 0
        overlaps = 0
        for (start, end), (next_start, next_end) in zip(ranges, ranges[1:]):
            if next_start < end:
                overlaps += 1
            elif next_start > end:
                gaps += next_start - end
                gap_count += 1
        if ranges:
            if ranges[0][0] > HEADER_SIZE:
                gaps += ranges[0][0] - HEADER_SIZE
                gap_count += 1
            if ranges[-1][1] < header.index_offset:
                gaps += header.index_offset - ranges[-1][1]
                gap_count += 1

        duplicate_offsets = sum(1 for count in offset_seen.values() if count > 1)
        if overlaps:
            errors.append(f"{overlaps} overlapping entry ranges")
        if duplicate_offsets:
            errors.append(f"{duplicate_offsets} duplicated offsets")

        result.update(
            {
                "compression_flags": {str(k): v for k, v in sorted(compression_flags.items())},
                "offset_flags": {hex(k): v for k, v in sorted(offset_flags.items())},
                "unknown_flag_bits": {hex(k): v for k, v in sorted(flag_bits.items())},
                "paths": path_stats,
                "payload": {
                    "zstd_entries": zstd_payload,
                    "stored_entries": stored_payload,
                    "packed_verified": packed_ok,
                    "unpacked_verified": raw_ok,
                    "packed_hash_ok": crc_packed_ok,
                    "unpacked_hash_ok": crc_raw_ok,
                    "decompressed_bytes": decompressed,
                    "failure_count": len(failures),
                    "failures": failures[:20],
                },
                "coverage": {
                    "nonempty_entries": len(ranges),
                    "packed_bytes": total_packed,
                    "data_start": HEADER_SIZE,
                    "data_end": header.index_offset,
                    "gaps": gap_count,
                    "gap_bytes": gaps,
                    "overlaps": overlaps,
                    "duplicate_offsets": duplicate_offsets,
                },
                "errors": errors,
            }
        )
        result["valid"] = not errors and not failures and flag_bits == {}
        return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Decode and validate an Eggy Party NXPK mode-3 index")
    parser.add_argument("path", type=Path)
    parser.add_argument("--paths", type=Path, default=Path("_pathdict.txt"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    resolver = PathResolver.from_file(args.paths) if args.paths.exists() else None
    report = audit(args.path, resolver)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
