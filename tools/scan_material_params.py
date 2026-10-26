"""Scan MTG parameter objects for non-default UV/color values."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import neox_object
import npk_browser


DEFAULT_DB = Path.home() / "AppData" / "Local" / "EggyNpkBrowser" / "catalog.sqlite"
WATCH = {
    "TexturePara": [1.0, 1.0, 0.0, 0.0],
    "RotationAngle": 0.0,
    "RepeatScale": 1.0,
    "RepeatScaleY": 1.0,
    "BaseColor": [1.0, 1.0, 1.0, 1.0],
    "TintValue": 1.0,
    "Intensity": [1.0, 1.0, 1.0, 1.0],
    "AlphaVal": 255,
    "AlphaRef": 0,
    "UVSampleKey": 0,
    "EnableUVChange": 0,
}


def scan(row) -> dict:
    result = {"path": row["path"], "values": {}, "ok": False}
    try:
        data = neox_object.parse(npk_browser.read_entry_bytes(row["package_path"], row))
        table = data.get("table") or []
        meta = data.get("meta") or []
        for index, obj in enumerate(data.get("objects") or []):
            if not isinstance(obj, dict):
                continue
            table_index = meta[index].get("x") if index < len(meta) and isinstance(meta[index], dict) else None
            name = table[table_index] if isinstance(table_index, int) and 0 <= table_index < len(table) else ""
            if name in WATCH and "Value" in obj:
                value = obj["Value"]
                if isinstance(value, list):
                    result["values"].setdefault(name, []).append([round(float(x), 4) for x in value])
                elif isinstance(value, (int, float)):
                    result["values"].setdefault(name, []).append(round(float(value), 4))
        result["ok"] = True
    except Exception as error:
        result["error"] = str(error)
    return result


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
            "FROM files f JOIN packages p ON f.package=p.name WHERE lower(f.ext)='.mtg'"
        ).fetchall()
    finally:
        connection.close()
    print(f"mtg: {len(rows)}", flush=True)
    counts = {name: Counter() for name in WATCH}
    examples = {name: [] for name in WATCH}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for item in pool.map(scan, rows):
            if not item["ok"]:
                continue
            for name, values in item["values"].items():
                for value in values:
                    key = tuple(value) if isinstance(value, list) else value
                    counts[name][key] += 1
                    if len(examples[name]) < 8 and key not in examples[name]:
                        examples[name].append((key, item["path"]))
    for name, counter in counts.items():
        default = WATCH[name]
        default_key = tuple(default) if isinstance(default, list) else default
        non_default = {key: count for key, count in counter.items() if key != default_key}
        total = sum(counter.values())
        print(f"{name}: total={total} non_default={sum(non_default.values())} top={sorted(non_default.items(), key=lambda kv: -kv[1])[:8]}")
        if name in ("RepeatScale", "TexturePara", "RotationAngle", "BaseColor", "TintValue"):
            print(f"  examples={examples[name][:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
