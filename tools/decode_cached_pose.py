"""Decode NeoX .animation CachedPose base64 matrices."""

from __future__ import annotations

import argparse
import base64
import re
import struct
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    text = args.path.read_text(encoding="utf-8", errors="replace")
    names_match = re.search(r'JointNames[^>]*Value="([^"]+)"', text)
    names = names_match.group(1).split(",") if names_match else []
    poses = re.findall(r'<Pose[^>]*Time="([^"]+)"[^>]*Value="([^"]+)"', text)
    print(f"{args.path.name}: joints={len(names)} poses={len(poses)}")
    for time, value in poses[:2]:
        raw = base64.b64decode(value)
        floats = struct.unpack(f"<{len(raw)//4}f", raw)
        per = len(floats) / max(1, len(names))
        print(f"  time={time} bytes={len(raw)} floats={len(floats)} per_joint={per:.2f}")
        for index in range(min(2, len(names))):
            start = int(index * per)
            matrix = floats[start:start + int(per)]
            print(f"    {names[index]} first16={[round(x, 4) for x in matrix[:16]]}")
        print(f"    last16={[round(x, 4) for x in floats[-16:]]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
