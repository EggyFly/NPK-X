"""验证 GMP 普通组件和批次渲染记录的合并。"""

from __future__ import annotations

import argparse
import struct
import sys
import unittest
from pathlib import Path

import zstandard

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gmp_parse


def _pack(value):
    # 仅编码回归用例需要的 MessagePack 类型，不依赖额外的安装包。
    if value is None:
        return b"\xc0"
    if isinstance(value, bool):
        return b"\xc3" if value else b"\xc2"
    if isinstance(value, int):
        return b"\xd3" + struct.pack(">q", value)
    if isinstance(value, float):
        return b"\xcb" + struct.pack(">d", value)
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return b"\xdb" + struct.pack(">I", len(raw)) + raw
    if isinstance(value, bytes):
        return b"\xc6" + struct.pack(">I", len(value)) + value
    if isinstance(value, list):
        return b"\xdd" + struct.pack(">I", len(value)) + b"".join(_pack(item) for item in value)
    if isinstance(value, dict):
        return b"\xdf" + struct.pack(">I", len(value)) + b"".join(_pack(key) + _pack(item) for key, item in value.items())
    raise TypeError(type(value))


def _parse(*chunks):
    compressor = zstandard.ZstdCompressor()
    payload = b"DZ" + b"".join(compressor.compress(_pack(chunk)) for chunk in chunks)
    return gmp_parse.parse(payload)


class GmpRegression(unittest.TestCase):
    def test_native_sections_are_reported_without_guessing_messagepack(self):
        compressor = zstandard.ZstdCompressor()
        sections = [
            (1, 1, _pack({"map_name": "原生数据不能猜成普通地图信息"})),
            (3, 1, b"\x82\x02\x00\x00\x00" + b"native bindict data"),
            (58, 0, _pack({"batch_loader_data": {}})),
        ]
        header = bytearray(34)
        header[:2] = b"DZ"
        struct.pack_into("!I", header, 22, 2)
        struct.pack_into("!H", header, 32, len(sections))
        bodies = [compressor.compress(raw) for _, _, raw in sections]
        directory = b"".join(struct.pack("!HHI", sid, native, len(body))
                             for (sid, native, _), body in zip(sections, bodies))
        payload = bytes(header) + directory + b"".join(bodies)
        data = gmp_parse.parse(payload)
        self.assertEqual(data["unit_count"], 0)
        self.assertEqual(data["unsupported_chunks"], 2)
        self.assertTrue(data["unit_data_unsupported"])
        self.assertEqual(data["parsed_chunks"], 1)
        self.assertEqual(data["binary_chunks"], 0)
        self.assertEqual(data["chunks"][1]["encoding"], "bindict")
        self.assertEqual(data["chunks"][1]["section_id"], 3)
        self.assertIsNone(gmp_parse.read_map_name(payload))

    def test_partial_messagepack_is_not_a_successful_section(self):
        raw = _pack({"map_name": "误判名称", "unit": {1: {"position": [0, 0, 0]}}}) + b"extra"
        payload = b"DZ" + zstandard.ZstdCompressor().compress(raw)
        data = gmp_parse.parse(payload)
        self.assertEqual(data["unit_count"], 0)
        self.assertEqual(data["parsed_chunks"], 0)
        self.assertEqual(data["unsupported_chunks"], 0)
        self.assertIsNone(gmp_parse.read_map_name(payload))

    def test_batch_overrides_rendering_without_losing_component_identity(self):
        normal = {b"unit": {17: {
            b"unit_id": 17, b"name": "测试组件", b"eid": 1202261, b"type": 256,
            b"parent_unit_id": 42, b"camp_id": 2, b"position": [1, 2, 3],
            b"real_scale": [2, 2, 2], b"model_angle": [0, 10, 0],
            b"trans_model_angle": [0, 20, 0], b"model_alpha": 1.0,
        }}}
        batch = {"batch_loader_data": {"batch_unit_data": {17: {
            "name": "批次占位名称", "type": 0, "parent_unit_id": 0,
            "model_id": 100016, "ctx_model_path": "props/ground/dk_gjbb/dk_gjbb.gim",
            "ctx_world_position": [4, 5, 6], "ctx_real_scale": [3, 4, 5],
            "ctx_model_angle": [0, 90, 0], "ctx_model_alpha": 127,
            "ctx_model_visible": False, "mat_path": "props/test.mtg",
            "paint_area1": [0, 0, 0, 255], "paint_area2": [25, 50, 75],
        }}}}
        for chunks in ((normal, batch), (batch, normal)):
            with self.subTest(batch_first=chunks[0] is batch):
                data = _parse(*chunks)
                self.assertEqual(data["unit_count"], 1)
                unit = data["units"][0]
                self.assertEqual((unit["name"], unit["eid"], unit["type"], unit["parent"], unit["camp"]),
                                 ("测试组件", 1202261, 256, 42, 2))
                self.assertEqual(unit["model_id"], 100016)
                self.assertEqual(unit["model"], "props/ground/dk_gjbb/dk_gjbb.gim")
                self.assertEqual(unit["material"], "props/test.mtg")
                self.assertEqual(unit["position"], [4, 5, 6])
                self.assertEqual(unit["scale"], [3, 4, 5])
                self.assertEqual(unit["angle"], [0, 90, 0])
                self.assertEqual(unit["alpha"], 0.498)
                self.assertFalse(unit["visible"])
                self.assertEqual(unit["paint"], [[0, 0, 0], [25, 50, 75], None])
                self.assertEqual(data["eids"], {"1202261": 1})
                self.assertEqual(data["types"], {"256": 1})

    def test_world_angle_and_partial_batch_vector_fallback(self):
        data = _parse({"unit": {7: {
            "position": [1, 2, 3], "scale": [2, 3, 4], "model_angle": [0, 10, 0],
            "trans_model_angle": [0, 80, 0], "model_alpha": 0.25,
        }}, "batch_unit_data": {7: {"ctx_real_scale": None, "ctx_model_angle": [1]}}})
        unit = data["units"][0]
        self.assertEqual(unit["angle"], [0, 80, 0])
        self.assertEqual(unit["scale"], [2, 3, 4])
        self.assertEqual(unit["alpha"], 0.25)

    def test_batch_model_id_is_not_a_component_eid(self):
        data = _parse({"batch_unit_data": {
            8: {"ctx_world_position": [0, 0, 0], "model_id": 100016,
                "ctx_model_path": "props/test.gim", "ctx_model_alpha": 0},
            9: {"ctx_world_position": [1, 0, 0], "model_id": 100017,
                "ctx_model_path": "props/test2.gim", "ctx_model_alpha": 255},
            10: {"position": [2, 0, 0], "model": 100018},
        }})
        units = {unit["id"]: unit for unit in data["units"]}
        self.assertIsNone(units[8]["eid"])
        self.assertEqual(units[8]["alpha"], 0)
        self.assertEqual(units[9]["alpha"], 1)
        self.assertEqual(units[10]["model"], "")
        self.assertEqual(units[10]["model_id"], 100018)
        self.assertEqual(data["eids"], {})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="可选的真实 GMP 文件")
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(GmpRegression))
    for path in args.paths:
        data = gmp_parse.parse(path.read_bytes())
        units = data["units"]
        print(f"{path.name}: 组件 {data['unit_count']}，有模型 {sum(bool(unit['model']) for unit in units)}，"
              f"隐藏 {sum(not unit['visible'] for unit in units)}，不支持的原生段 {data['unsupported_chunks']}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
