"""Print material_tree output for selected catalog .mtg files."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import neox_object
import npk_browser
import scan_materials


DEFAULT_DB = Path.home() / "AppData" / "Local" / "EggyNpkBrowser" / "catalog.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    paths = scan_materials.load_paths(args.db)
    resolve = scan_materials.make_resolver(paths)
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for path in args.paths:
            row = connection.execute(
                "SELECT f.path, f.package, f.offset, f.packed, f.unpacked, "
                "f.compression, p.path AS package_path "
                "FROM files f JOIN packages p ON f.package=p.name WHERE f.path=?",
                (path,),
            ).fetchone()
            if not row:
                print(f"{path}: not found")
                continue
            data = neox_object.parse(npk_browser.read_entry_bytes(row["package_path"], row))
            materials = npk_browser.material_tree(data, lambda ref: resolve(path, ref))
            print(f"{path}: {len(materials)} materials")
            for mat in materials:
                print(f"  [{mat['index']}] {mat['surface']}")
                print(f"    mode={mat['color_mode']} normal={mat['normal_mode']} opacity={mat['opacity_tex']} "
                      f"transparent={mat['transparent']} cull={mat['cull_back']} lighting={mat['lighting']}")
                print(f"    tex0={mat['tex0']} tex1={mat['tex1']} tex2={mat['tex2']} "
                      f"pattern={mat['pattern_tex']} base={mat['base_normal']}")
                print(f"    colors={mat['colors']} design={mat['design_color']} "
                      f"pattern_para={mat['pattern_para']} key={mat['pattern_color_key']} "
                      f"rot={mat['pattern_rotation']} repeat={mat['repeat_scale']} tiling={mat['tiling']}")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
