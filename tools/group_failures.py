"""Group failed mesh entries by header version/flag and error."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import npk_browser


DEFAULT_DB = Path.home() / "AppData" / "Local" / "EggyNpkBrowser" / "catalog.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scan")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    data = json.loads(Path(args.scan).read_text(encoding="utf-8"))
    failed = [item for item in data["items"] if not item.get("ok")]
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    groups = Counter()
    samples = {}
    try:
        for item in failed:
            row = connection.execute(
                "SELECT f.path, f.package, f.offset, f.packed, f.unpacked, "
                "f.compression, p.path AS package_path "
                "FROM files f JOIN packages p ON f.package=p.name WHERE f.path=?",
                (item["path"],),
            ).fetchone()
            try:
                payload = npk_browser.read_entry_bytes(row["package_path"], row)
                version = int.from_bytes(payload[4:6], "little")
                flag = int.from_bytes(payload[8:10], "little")
            except Exception:
                version, flag = -1, -1
            key = (version, flag, item["error"].split(":")[-1].strip())
            groups[key] += 1
            samples.setdefault(key, item["path"])
    finally:
        connection.close()
    for key, count in groups.most_common():
        print(f"{count:4d} version={key[0]} flag={key[1]} {key[2]}  e.g. {samples[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
