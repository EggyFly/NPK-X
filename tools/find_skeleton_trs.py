"""Find the TRS record layout in a NeoX skeleton DATA chunk."""

from __future__ import annotations

import argparse
import math
import struct
from pathlib import Path


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--bones", type=int, default=0)
    args = parser.parse_args()
    data = args.path.read_bytes()
    chunks = []
    for offset in range(8, len(data) - 7):
        if data[offset:offset + 4] == b"DATA":
            chunks.append((offset, u32(data, offset + 4)))
    if not chunks:
        raise SystemExit("no DATA chunk")
    offset, size = chunks[0]
    start = offset + 8
    end = start + size
    floats = struct.unpack_from(f"<{(end-start)//4}f", data, start)
    print(f"DATA start={start} floats={len(floats)}")
    best = []
    for record_offset in range(0, min(200, len(floats) - 10)):
        for stride in (10, 12):
            count = 0
            good = 0
            while record_offset + count * stride + 10 <= len(floats):
                record = floats[record_offset + count * stride:record_offset + count * stride + 10]
                q = record[3:7]
                scale = record[7:10]
                qnorm = math.sqrt(sum(x * x for x in q))
                if 0.8 <= qnorm <= 1.2 and all(0.3 <= abs(x) <= 3.0 for x in scale):
                    good += 1
                count += 1
            if count:
                best.append((good / count, record_offset, stride, count, good))
    best.sort(reverse=True)
    for score, record_offset, stride, count, good in best[:15]:
        print(f"score={score:.3f} offset={record_offset} stride={stride} records={count} good={good}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
