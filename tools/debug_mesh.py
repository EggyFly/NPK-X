"""Dump NeoX mesh header/layout candidates for hard cases."""

from __future__ import annotations

import argparse
import math
import sqlite3
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mesh_parse
import npk_browser


DEFAULT_DB = npk_browser.default_cache_dir() / "catalog.sqlite"


def u16(data, offset):
    return int.from_bytes(data[offset:offset + 2], "little")


def u32(data, offset):
    return int.from_bytes(data[offset:offset + 4], "little")


def load(path: str, db: Path) -> bytes:
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT f.path, f.package, f.offset, f.packed, f.unpacked, "
            "f.compression, p.path AS package_path "
            "FROM files f JOIN packages p ON f.package=p.name WHERE f.path=?",
            (path,),
        ).fetchone()
    finally:
        connection.close()
    if not row:
        raise SystemExit(f"not found: {path}")
    return npk_browser.read_entry_bytes(row["package_path"], row)


def probe_face(data: bytes, start: int, vcount: int, fcount: int) -> tuple[int, int]:
    size = 6 * fcount
    if start < 0 or start + size > len(data):
        return 0, 0
    return mesh_parse._decode_faces(data[start:start + size], vcount)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--hex", type=int, default=96)
    args = parser.parse_args()

    data = load(args.path, args.db)
    print(f"path={args.path} size={len(data)}")
    print(f"magic=0x{u32(data,0):08x} version={u16(data,4)} flag={u16(data,8)}")
    print(f"head={data[:args.hex].hex(' ')}")
    for offset in range(0, min(64, len(data)), 4):
        print(f"  u32@{offset}={u32(data,offset)}")
    if u16(data, 8) == 0:
        data_end = u32(data, 12)
        print(f"data_end={data_end}")
        print(f"u16@data_end={u16(data,data_end)} u32@data_end+2={u32(data,data_end+2)}")
        table_off = 16
        if data_end + 6 <= len(data) and u16(data, data_end) >= 1:
            table_off = u32(data, data_end + 2)
        print(f"table_off={table_off}")
        table = mesh_parse._read_table(data, table_off)
        print(f"table={table}")
        if not table:
            return 0
        print(f"table_end={table['end']} vertex_flag={data[table['end']]}")
        if u16(data, 4) == 3:
            vcount = table["total_vcount"]
            fcount = table["total_fcount"]
            uv_bytes = 8 * sum(item[0] * item[2] for item in table["records"])
            for sep in (0, 2, 4, 8):
                uv_off = table["end"] + 24 * vcount + sep
                face_off = uv_off + uv_bytes
                if face_off + 6 * fcount > len(data):
                    continue
                values = struct.unpack_from(f"<{3*fcount}H", data, face_off)
                valid = sum(1 for value in values if value < vcount)
                uv_ok = 0
                for index in range(min(vcount, 64)):
                    try:
                        u, v = struct.unpack_from("<2f", data, uv_off + index * 8)
                        if math.isfinite(u) and math.isfinite(v) and abs(u) < 64 and abs(v) < 64:
                            uv_ok += 1
                    except struct.error:
                        break
                print(f"  v3 sep={sep} uv@{uv_off} uv_ok={uv_ok}/{min(vcount,64)} "
                      f"face@{face_off} abs_valid={valid}/{len(values)} end={face_off + 6*fcount} data_end={data_end}")
                if face_off + 6 * fcount == data_end:
                    face_cursor = 0
                    vertex_cursor = 0
                    for item in table["records"]:
                        block = values[face_cursor * 3:(face_cursor + item[1]) * 3]
                        if block:
                            local = sum(1 for value in block if value < item[0])
                            print(f"    sub v={item[0]} f={item[1]} max={max(block)} "
                                  f"local={local}/{len(block)}")
                        face_cursor += item[1]
                        vertex_cursor += item[0]
            for sep in (0, 2, 4, 8):
                face_off = table["end"] + 24 * vcount + sep
                uv_off = face_off + 6 * fcount
                if uv_off + uv_bytes > len(data):
                    continue
                values = struct.unpack_from(f"<{3*fcount}H", data, face_off)
                valid = sum(1 for value in values if value < vcount)
                reset_valid = 0
                cursor = 0
                for item in table["records"]:
                    current = 0
                    for (delta,) in struct.iter_unpack("<h", data[face_off + cursor * 6:face_off + (cursor + item[1]) * 6]):
                        current += delta
                        if 0 <= current < item[0]:
                            reset_valid += 1
                    cursor += item[1]
                if valid > len(values) * 0.9 or reset_valid > len(values) * 0.9:
                    print(f"  v3 face-first sep={sep} face@{face_off} abs={valid}/{len(values)} "
                          f"delta_reset={reset_valid}/{len(values)} uv@{uv_off} "
                          f"end={uv_off + uv_bytes} data_end={data_end}")
                    if valid == len(values):
                        cursor = 0
                        for item in table["records"]:
                            block = values[cursor * 3:(cursor + item[1]) * 3]
                            if block:
                                local = sum(1 for value in block if value < item[0])
                                print(f"    face-first sub v={item[0]} f={item[1]} "
                                      f"max={max(block)} local={local}/{len(block)}")
                            cursor += item[1]
        return 0

    info = mesh_parse._parse_bone_header(data)
    print(f"bones={u16(data,12)}")
    print("bone_header=", {k: v for k, v in info.items() if k != "records"})
    print(f"records={info['records']}")
    vcount = info["vcount"]
    fcount = info["fcount"]
    data_start = info["data_start"]
    data_end = info["data_end"]
    print(f"vcount={vcount} fcount={fcount} data_start={data_start} data_end={data_end}")
    print(f"span={data_end-data_start} expected_by_variants="
          f"115={(115*vcount+16)//8 + 6*fcount} 20={(8*vcount+1)+3*vcount+6*fcount} 12={(12*vcount+1)+3*vcount+6*fcount}")
    print(f"vertex_flag={data[data_start]} next={data[data_start:data_start+16].hex(' ')}")
    for geom in (20, 24, 28, 36, 40, 48):
        face_abs = data_start + geom * vcount
        if face_abs + 6 * fcount > len(data):
            continue
        values = struct.unpack_from(f"<{3*fcount}H", data, face_abs)
        valid = sum(1 for value in values if value < vcount)
        reset_valid = 0
        cursor = 0
        for item in info["records"]:
            current = 0
            for (delta,) in struct.iter_unpack("<h", data[face_abs + cursor * 6:face_abs + (cursor + item[1]) * 6]):
                current += delta
                if 0 <= current < item[0]:
                    reset_valid += 1
            cursor += item[1]
        print(f"  geom={geom}B face@{face_abs} abs_valid={valid}/{len(values)} "
              f"delta_reset={reset_valid}/{len(values)} first={values[:9]}")
    face_abs = data_start + 24 * vcount
    if face_abs + 6 * fcount <= len(data):
        values = struct.unpack_from(f"<{3*fcount}H", data, face_abs)
        print(f"abs face@{face_abs} valid={sum(1 for v in values if v < vcount)}/{len(values)} max={max(values)}")
    for uv in (1, 2, 3, 4):
        for color in (0, 1, 2, 4):
            for tail_name, tail in (
                ("none", 0),
                ("12V", 12 * vcount),
                ("12V+32", 12 * vcount + 32),
                ("4V", 4 * vcount),
                ("8V", 8 * vcount),
                ("16V", 16 * vcount),
                ("12V+4V", 16 * vcount),
                ("12V+32+4V", 16 * vcount + 32),
            ):
                post = 4 * uv * vcount + 4 * color * vcount
                face_start = data_end - tail - post - 6 * fcount
                if face_start < data_start:
                    continue
                indices, valid = probe_face(data, face_start, vcount, fcount)
                if valid >= fcount * 3 * 0.999:
                    print(f"  MATCH uv={uv} color={color} tail={tail_name} face_start={face_start} "
                          f"geometry={face_start-data_start}")
    hits = []
    step = 2
    for pos in range(data_start, min(data_end, data_start + 200000), step):
        probe = data[pos:pos + 96]
        valid, total = mesh_parse._face_probe(probe, vcount)
        if total and valid * 10 >= total * 9:
            hits.append(pos)
            if len(hits) >= 8:
                break
    print(f"face_probe_hits={hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
