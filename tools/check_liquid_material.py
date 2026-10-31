"""验证液体、玻璃和瀑布材质的纹理语义及零值参数。"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import npk_browser


def material(surface, params=(), states=None):
    # 直接构造解析后的对象树，专门验证材质解释而不重复二进制读取测试。
    names = ["Root", "MaterialGroup", "Material_0", "Material", "Technique"]
    objects = [{}, {}, {}, {"Name": "测试材质"}, {"Surface": surface}]
    for name, value in params:
        names.append(name)
        objects.append({"Value": value})
    if states is not None:
        names.append("RenderStates")
        objects.append(states)
    meta = [{"x": i, "y": 1 if i < 3 else (len(objects) - 4 if i == 3 else 0)}
            for i in range(len(objects))]
    return npk_browser.material_tree({"table": names, "objects": objects, "meta": meta})[0]


class LiquidMaterialRegression(unittest.TestCase):
    def test_water_texture_slots_and_zero_values(self):
        result = material("shader/surf/object_water.surf", [
            ("Tex0", "gradient.kta"), ("Tex1", "normal.kta"),
            ("Tex2", "noise.kta"), ("Tex3", "foam.kta"),
            ("Intensity", [0.2, 0.0, 0.0, 0.0]),
            ("Variable", [1, -0.7, 0.008, 0.007]), ("Speed", [0.2, 0.1, 0.1, 0]),
        ])
        self.assertEqual(result["shader_kind"], "water")
        self.assertEqual([result[f"tex{i}"] for i in range(4)],
                         ["gradient.kta", "normal.kta", "noise.kta", "foam.kta"])
        self.assertEqual(result["normal_slot"], "tex1")
        self.assertFalse(result["mix_map"])
        self.assertEqual((result["roughness"], result["metalness"]), (0, 0))
        self.assertEqual(result["variable"], [1, -0.7, 0.008, 0.007])
        self.assertEqual(result["speed"], [0.2, 0.1, 0.1, 0])

    def test_swirl_normals_are_not_deleted(self):
        result = material("shader/surf/object_water_simple.surf", [
            ("Tex0", "water_normal.kta"), ("Tex1", "swirl_normal.kta"),
            ("Tex2", "matcap.kta"), ("Intensity", [0, 1, 0.6, 0.25]),
            ("Parameter", [0, 2, 0, -1]), ("OpacityEnable", 0), ("Tiling", 0),
        ])
        self.assertEqual(result["shader_kind"], "water_simple")
        self.assertEqual(result["tex0"], "water_normal.kta")
        self.assertEqual(result["tex1"], "swirl_normal.kta")
        self.assertEqual(result["normal_slot"], "tex0")
        self.assertEqual(result["matcap_slot"], "tex2")
        self.assertEqual(result["roughness"], 0.25)
        self.assertEqual(result["water_tiling"], 0)
        self.assertEqual(result["parameter"], [0, 2, 0, -1])

    def test_glass_material_and_engine_opacity_stay_separate(self):
        result = material("shader/surf/standard_glass.surf", [
            ("BaseColor", [0.1, 0.3, 0.5, 0.4]), ("Extra_Op", 0),
            ("Tex0", "old_matcap.kta"), ("Tex1", "normal.kta"),
            ("Tex2", "orm.kta"), ("Intensity", [0, 0, 0, 0]),
        ], {"AlphaVal": 127})
        self.assertEqual(result["shader_kind"], "glass")
        self.assertEqual(result["glass_opacity"], 0)
        self.assertEqual(result["alpha_val"], 127 / 255)
        self.assertEqual(result["base_color"][3], 0.4)
        self.assertEqual(result["matcap_slot"], "")
        self.assertTrue(result["mix_map"])
        default = material("shader/surf/standard_glass.surf", states={"AlphaVal": 0})
        self.assertEqual(default["glass_opacity"], 1)
        self.assertEqual(default["alpha_val"], 0)

    def test_jelly_keeps_matcap_and_normals(self):
        result = material("shader/surf/unlit_jelly_obj_gets.surf", [
            ("Tex0", "matcap.kta"), ("Tex1", "normal.kta"), ("Reflection", 0),
        ])
        self.assertEqual(result["shader_kind"], "jelly")
        self.assertEqual(result["matcap_slot"], "tex0")
        self.assertEqual(result["normal_slot"], "tex1")
        self.assertFalse(result["lighting"])
        self.assertFalse(result["mix_map"])

    def test_waterfall_slots_and_zero_values(self):
        result = material("shader/surf/standard_pubu2.surf", [
            ("Tex3", "main.kta"), ("Tex4", "noise.kta"), ("Tex5", "mask.kta"),
            ("Speed_MainTexU", 0), ("Speed_MainTexV", -0.2),
            ("ClipLayer_EmisMult", 0), ("Rough_High", 0), ("AlphaStepMax", 0.75),
            ("Color_Low", [0.1, 0.2, 0.3, 1]), ("Color_High", [0.4, 0.5, 0.6, 1]),
        ])
        self.assertEqual(result["shader_kind"], "waterfall")
        self.assertEqual(result["waterfall_textures"], ["main.kta", "noise.kta", "mask.kta"])
        self.assertEqual(result["tex5"], "mask.kta")
        self.assertEqual(result["normal_slot"], "")
        self.assertFalse(result["mix_map"])
        self.assertEqual(result["speed_main_tex_u"], 0)
        self.assertEqual(result["speed_main_tex_v"], -0.2)
        self.assertEqual(result["clip_layer_emis_mult"], 0)
        self.assertEqual(result["rough_high"], 0)
        self.assertEqual(result["alpha_step_max"], 0.75)
        self.assertEqual(result["colors"][0], [0.1, 0.2, 0.3, 1])
        self.assertEqual(result["colors"][1], [0.4, 0.5, 0.6, 1])


def check_resources(unpacked):
    from check_neox_render import ResourceHandler
    handler = ResourceHandler(unpacked=unpacked)
    waterfall = handler.request("props/decoration/pubu_new/pubu_new_1.gim")["materials"][0]
    assert waterfall["shader_kind"] == "waterfall"
    assert len(waterfall["waterfall_textures"]) == 3
    assert all(waterfall["waterfall_textures"][:2])
    # 当前样本没有指定 Tex5，预览器应使用 Surface 的默认白色遮罩。
    assert waterfall["waterfall_textures"][2] == ""
    assert waterfall["colors"][0][2] > 0.8
    assert waterfall["colors"][1][2] == 1
    assert waterfall["speed_main_tex_v"] == -0.5
    for model, kind in (("props/water/water01.gim", "water"),
                        ("props/water/water_cylinder.gim", "water_color")):
        result = handler.request(model)["materials"][0]
        assert result["shader_kind"] == kind
        assert result["normal_slot"] == "tex1"
        assert result["tex1"].endswith("water_normal00.kta")
        assert result["roughness"] == result["metalness"] == 0
        assert result["variable"][1] < 0
    glass = handler.request("props/ground/guandao_zhi/guandao_zhi.gim")["materials"][0]
    assert glass["shader_kind"] == "glass" and glass["matcap_slot"] == ""
    assert glass["glass_opacity"] == 1
    assert abs(glass["base_color"][3] - 0.784314) < 1e-6
    print("通过：真实糖果瀑布、水柱、水池、玻璃管道材质")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unpacked", type=Path)
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(LiquidMaterialRegression))
    if args.unpacked:
        check_resources(args.unpacked)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
