"""Probe NeoX skeleton/rawanimation chunk internals."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def chunks(data: bytes):
    for offset in range(8, len(data) - 7):
        if data[offset:offset + 4] in (b"HEAD", b"DATA", b"NAME"):
            size = u32(data, offset + 4)
            yield data[offset:offset + 4].decode("ascii"), offset, size


def print_strings(data: bytes, start: int, end: int):
    pos = start
    index = 0
    while pos + 4 <= end:
        size = u32(data, pos)
        pos += 4
        if size <= 0 or size > 200 or pos + size > end:
            break
        raw = data[pos:pos + size]
        text = raw.split(b"\x00")[0].decode("latin1", errors="replace")
        print(f"  string[{index}] @{pos - 4} len={size}: {text!r}")
        pos += size
        index += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()

    data = args.path.read_bytes()
    print(f"{args.path.name}: {len(data)} bytes head={data[:16]!r}")
    for name, offset, size in chunks(data):
        print(f"chunk {name} @{offset} size={size} data=@{offset + 8} end={offset + 8 + size}")
        if name == "HEAD":
            print(f"  first bytes: {data[offset + 8:offset + 40].hex(' ')}")
            if data[offset + 8:offset + 16].startswith(b"\x00"):
                print(f"  bone_count(u16@+12)={u16(data, offset + 20)}")
            print_strings(data, offset + 24, offset + 8 + size)
        elif name == "NAME":
            start = offset + 8
            count = u32(data, start)
            print(f"  names={count}")
            print_strings(data, start + 4, offset + 8 + size)
        elif name == "DATA":
            end = min(offset + 8 + size, len(data))
            start = offset + 8
            floats = (end - start) // 4
            print(f"  floats={floats} first={[round(x, 4) for x in struct.unpack_from(f'<{min(16, floats)}f', data, start)]}")
            for group in range(start, end - 15, 16):
                values = struct.unpack_from("<4f", data, group)
                if any(abs(value) > 1e-8 for value in values):
                    print(f"  @{group - start:3d}: {[round(x, 4) for x in values]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
