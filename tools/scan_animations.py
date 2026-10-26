"""Scan legacy .rawanimation ACL versions across the catalog."""

from __future__ import annotations

import argparse
import sqlite3
import struct
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import npk_browser


DEFAULT_DB = Path.home() / "AppData" / "Local" / "EggyNpkBrowser" / "catalog.sqlite"


def scan(row) -> dict:
    try:
        data = npk_browser.read_entry_bytes(row["package_path"], row)
        data_chunk = data.find(b"DATA", 20)
        if data_chunk < 0:
            return {"ok": False, "error": "no data"}
        payload = data_chunk + 8
        tag = data[payload + 8:payload + 12]
        version = struct.unpack_from("<I", data, payload + 12)[0] & 0xFFFF
        return {"ok": True, "tag": tag.hex(), "version": version, "path": row["path"]}
    except Exception as error:
        return {"ok": False, "error": str(error)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT f.path, f.package, f.offset, f.packed, f.unpacked, "
            "f.compression, p.path AS package_path "
            "FROM files f JOIN packages p ON f.package=p.name "
            "WHERE lower(f.ext)='.rawanimation'"
        ).fetchall()
    finally:
        connection.close()
    print(f"rawanimations: {len(rows)}", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for index, item in enumerate(pool.map(scan, rows), 1):
            results.append(item)
            if index % 2000 == 0:
                print(index, flush=True)
    ok = [item for item in results if item["ok"]]
    samples = {}
    for item in ok:
        key = (item["tag"], item["version"])
        samples.setdefault(key, [])
        if len(samples[key]) < 3:
            samples[key].append(item["path"])
    print("tags", Counter((item["tag"], item["version"]) for item in ok).most_common(20))
    print("samples", samples)
    print("errors", Counter(item.get("error") for item in results if not item["ok"]).most_common(10))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
