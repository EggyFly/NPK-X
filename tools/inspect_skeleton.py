"""Inspect NeoX skeleton/rawanimation chunk layout."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--hex", type=int, default=128)
    args = parser.parse_args()

    data = args.path.read_bytes()
    print(f"{args.path.name}: {len(data)} bytes")
    print(f"head: {data[:args.hex].hex(' ')}")
    for offset in range(0, len(data) - 4):
        if data[offset:offset + 4] in (b"HEAD", b"DATA", b"NAME", b"NODE", b"BONE"):
            size = u32(data, offset + 4) if offset + 8 <= len(data) else None
            print(f"chunk {data[offset:offset+4].decode()} @{offset} size={size} end={offset+8+size if size is not None else None}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
