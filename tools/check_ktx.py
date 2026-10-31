"""验证 KTX1 的 ASTC 块尺寸、像素通道和 DXT 后备解码。"""

from __future__ import annotations

import argparse
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ktx_to_png


def _ktx(width, height, internal, blocks, image_size=None):
    header = ktx_to_png.KTX1_IDENTIFIER + struct.pack(
        "<13I", 0x04030201, 0, 1, 0, internal, 0x1908,
        width, height, 0, 0, 1, 1, 0,
    )
    return header + struct.pack("<I", len(blocks) if image_size is None else image_size) + blocks


def _astc_color(red, green, blue, alpha):
    # ASTC 的常量颜色块：首部标识无边界的 LDR 常量区，后面是四个 16 位通道。
    return bytes.fromhex("fcfdffffffffffff") + struct.pack("<4H", red, green, blue, alpha)


class KtxRegression(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "texture.png"

    def test_all_astc_block_sizes_and_color_channels(self):
        # 多出一像素，保证每种块尺寸都必须向上取整，并且覆盖边缘像素。
        dimensions = ((4, 4), (5, 4), (5, 5), (6, 5), (6, 6), (8, 5), (8, 6),
                      (8, 8), (10, 5), (10, 6), (10, 8), (10, 10), (12, 10), (12, 12))
        block = _astc_color(65535, 32768, 0, 16448)
        for base in (0x93B0, 0x93D0):
            for index, (block_width, block_height) in enumerate(dimensions):
                with self.subTest(internal=hex(base + index)):
                    width, height = block_width + 1, block_height + 1
                    blob = _ktx(width, height, base + index, block * 4)
                    result = ktx_to_png.convert_bytes(blob, self.output)
                    self.assertEqual(result["compressed_bytes"], 64)
                    with Image.open(self.output) as image:
                        self.assertEqual(image.size, (width, height))
                        self.assertEqual(image.mode, "RGBA")
                        self.assertEqual(image.tobytes(), bytes([255, 128, 0, 64]) * width * height)

    def test_astc_smaller_than_a_block(self):
        blob = _ktx(2, 3, 0x93BD, _astc_color(0, 65535, 65535, 65535))
        ktx_to_png.convert_bytes(blob, self.output)
        with Image.open(self.output) as image:
            self.assertEqual(image.size, (2, 3))
            self.assertEqual(image.tobytes(), bytes([0, 255, 255, 255]) * 6)

    def test_invalid_sizes_and_truncated_payload(self):
        block = _astc_color(0, 0, 0, 65535)
        cases = (
            (_ktx(17, 17, 0x93B7, block), "图像大小"),
            (_ktx(8, 8, 0x93B7, block)[:-1], "图像数据越界"),
            (_ktx(8, 8, 0x93B7, block, image_size=32), "图像数据越界"),
            (_ktx(0, 8, 0x93B7, block), "宽高必须大于零"),
            (_ktx(8, 0, 0x93B7, block), "宽高必须大于零"),
            (_ktx(8, 8, 0x93BE, block), "不支持的 glInternalFormat"),
        )
        for blob, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    ktx_to_png.convert_bytes(blob, self.output)
        self.assertFalse(self.output.exists())

    def test_astc_without_decoder_fails_explicitly(self):
        blob = _ktx(4, 4, 0x93B0, _astc_color(0, 0, 0, 65535))
        with patch.object(ktx_to_png, "texture2ddecoder", None):
            with self.assertRaisesRegex(ValueError, "ASTC.*texture2ddecoder"):
                ktx_to_png.convert_bytes(blob, self.output)
        self.assertFalse(self.output.exists())

    def test_dxt_native_and_python_fallback(self):
        red = struct.pack("<HHI", 0xF800, 0, 0)
        samples = (
            (ktx_to_png.GL_COMPRESSED_RGB_S3TC_DXT1_EXT, red),
            (ktx_to_png.GL_COMPRESSED_RGBA_S3TC_DXT5_EXT, bytes([255, 0]) + bytes(6) + red),
        )
        for decoder in (ktx_to_png.texture2ddecoder, None):
            for internal, block in samples:
                with self.subTest(native=decoder is not None, internal=hex(internal)):
                    with patch.object(ktx_to_png, "texture2ddecoder", decoder):
                        ktx_to_png.convert_bytes(_ktx(3, 2, internal, block), self.output)
                    with Image.open(self.output) as image:
                        self.assertEqual(image.size, (3, 2))
                        self.assertEqual(image.tobytes(), bytes([255, 0, 0, 255]) * 6)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="可选的真实 KTX1 或 KTA 文件")
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(KtxRegression)
    )
    with tempfile.TemporaryDirectory() as temporary:
        for index, path in enumerate(args.paths):
            output = Path(temporary) / f"texture-{index}.png"
            data = ktx_to_png.convert_bytes(path.read_bytes(), output, path)
            with Image.open(output) as image:
                image.load()
                if image.size != (data["width"], data["height"]) or image.mode != "RGBA":
                    raise ValueError(f"输出图像尺寸或模式错误：{path}")
            print(f"{path.name}: {data['width']} × {data['height']}，"
                  f"格式 {data['format']}，PNG {data['png_bytes']} 字节")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
