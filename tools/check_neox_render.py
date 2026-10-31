"""验证 NeoX 材质引用、覆盖材质和相对贴图，支持只读检查真实解包资源。"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))

import npk_browser


def _varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _object_bytes(objects, table=None, children=None):
    table = table or ["Root"] * len(objects)
    children = children or [0] * len(objects)
    fields = list(dict.fromkeys(key for obj in objects for key in obj))
    payload = _varint(len(table)) + b"".join(item.encode() + b"\0" for item in table)
    payload += _varint(len(fields)) + b"".join(item.encode() + b"\0" for item in fields)
    payload += b"\0" * 8 + _varint(len(objects))
    for index, count in enumerate(children):
        payload += _varint(index) + _varint(count)
    for obj in objects:
        payload += _varint(len(obj))
        for name, value in obj.items():
            payload += _varint(fields.index(name))
            if isinstance(value, str):
                payload += b"\1" + value.encode() + b"\0"
            else:
                payload += b"\2" + struct.pack("<I", value)
        payload += b"\1\0"
    return struct.pack(">I", 0xC159410D) + struct.pack("<II", len(payload) + 12, 0) + payload


def _material(name, texture):
    return _object_bytes(
        [{}, {"MaterialCount": 1}, {}, {"Name": name, "Surface": "standard_common.surf"}, {"Value": texture}],
        ["Root", "MaterialGroup", "Material_0", "Material", "Tex0"],
        [1, 1, 1, 1, 0],
    )


class ResourceHandler(npk_browser.Handler):
    def __init__(self, files=None, unpacked=None):
        self.files = files or {}
        self.packages = list(unpacked.iterdir()) if unpacked else []
        self.result = None
        self.status = None

    def _row(self, path):
        physical = None
        if path not in self.files:
            for package in self.packages:
                candidate = package / path
                if candidate.is_file():
                    physical = candidate
                    break
            if physical is None:
                return None
        return {
            "path": path, "ext": Path(path).suffix.lower(), "name": Path(path).name,
            "package": "check", "kind": "model", "unpacked": 0, "physical": physical,
        }

    def _entry_payload(self, row):
        return row["physical"].read_bytes() if row["physical"] else self.files[row["path"]]

    def _json(self, value, status=200):
        self.result, self.status = value, status

    def _sibling_bounds(self, path):
        return None, None

    def request(self, path, material=None):
        query = {"path": path, "pose": "0"}
        if material is not None:
            query["material"] = material
        self.path = "/api/neox?" + urlencode(query)
        self.result, self.status = None, None
        self.do_GET()
        assert self.status == 200, (self.status, self.result)
        return self.result


def check_fixtures():
    files = {
        "props/a/model.gim": _object_bytes([{"MtgPath": "red.mtg", "Tex0": "stale.tga"}]),
        "props/a/model.mtg": _material("sibling", "sibling.tga"),
        "props/a/red.mtg": _material("red", "red.tga"),
        "props/a/red.kta": b"",
        "props/b/blue.mtg": _material("blue", "blue.tga"),
        "props/b/blue.kta": b"",
        "props/a/fallback.gim": _object_bytes([{}]),
        "props/a/fallback.mtg": _material("fallback", "../b/blue.tga"),
    }
    handler = ResourceHandler(files)
    data = handler.request("props/a/model.gim")
    assert data["material_path"] == "props/a/red.mtg"
    assert data["materials"][0]["name"] == "red"
    assert data["materials"][0]["tex0"] == "props/a/red.kta"
    assert data["texture_images"] == ["props/a/red.kta"]
    data = handler.request("props/a/model.gim", "../b/blue.mtg")
    assert data["material_path"] == "props/b/blue.mtg"
    assert data["materials"][0]["name"] == "blue"
    assert data["materials"][0]["tex0"] == "props/b/blue.kta"
    assert data["texture_images"] == ["props/b/blue.kta"]
    data = handler.request("props/a/fallback.gim")
    assert data["material_path"] == "props/a/fallback.mtg"
    assert data["materials"][0]["tex0"] == "props/b/blue.kta"
    for material, status in (("missing.mtg", 404), ("red.kta", 415)):
        try:
            handler.request("props/a/model.gim", material)
        except AssertionError:
            assert handler.status == status, handler.result
        else:
            raise AssertionError("非法覆盖材质未被拒绝")
    print("通过：显式材质优先、材质覆盖隔离、相对贴图、同名回退和非法材质校验")


def check_resources(unpacked):
    handler = ResourceHandler(unpacked=unpacked)
    base = "props/dynamic/yutuzuoqi/"
    data = handler.request(base + "yutuzuoqi_lod2_2.gim")
    assert data["material_path"] == base + "yutuzuoqi_2.mtg"
    assert len(data["materials"]) == 2
    assert data["mesh_data"]["vertex_count"] > 0
    assert data["materials"][0]["tex0"] == base + "yutuzuoqi_d02.kta"
    data = handler.request(base + "yutuzuoqi_lod2_2.gim", base + "yutuzuoqi_3.mtg")
    assert data["material_path"] == base + "yutuzuoqi_3.mtg"
    assert data["materials"][0]["tex0"] == base + "yutuzuoqi_d03.kta"
    data = handler.request(base + "yutuzuoqi_lod2.mesh", base + "yutuzuoqi_2.mtg")
    assert data["vertex_count"] > 0
    assert data["materials"][0]["tex0"] == base + "yutuzuoqi_d02.kta"
    print("通过：真实玉兔 LOD 模型、两套皮肤材质切换和直接 MESH 材质")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unpacked", type=Path)
    args = parser.parse_args()
    check_fixtures()
    if args.unpacked:
        check_resources(args.unpacked)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
