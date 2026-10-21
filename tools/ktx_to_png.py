#!/usr/bin/env python3
"""把本研究中恢复出的标准 KTX1/DXT 纹理转换为 PNG。

这个工具只处理已经可以正常读取的 KTX 文件，不处理 NPK/PPK 的索引保护层。
KTX 是一个带有小型头部的纹理容器；真正的像素数据在头部之后，按 4x4 像素
的 DXT 压缩块排列。DXT1 每块占 8 字节，DXT5 每块占 16 字节。这里先解码
每个块，再交给 Pillow 写 PNG，因此 PNG 是无损保存解码后像素的结果；但
DXT 本身是有损压缩，PNG 不会恢复压缩前已经丢失的细节。
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from PIL import Image

try:
    import texture2ddecoder
except ImportError:  # pragma: no cover - 纯 Python 后备路径仍可独立工作
    texture2ddecoder = None


KTX1_IDENTIFIER = b"\xABKTX 11\xBB\r\n\x1A\n"
GL_COMPRESSED_RGB_S3TC_DXT1_EXT = 0x83F0
GL_COMPRESSED_RGBA_S3TC_DXT5_EXT = 0x83F3


def _rgb565(value: int) -> tuple[int, int, int]:
    """将一个 16 位 RGB565 端点展开为 8 位 RGB。"""
    r = ((value >> 11) & 0x1F) * 255 // 31
    g = ((value >> 5) & 0x3F) * 255 // 63
    b = (value & 0x1F) * 255 // 31
    return r, g, b


def _decode_color_block(block: bytes, allow_transparent: bool) -> list[tuple[int, int, int, int]]:
    """解码一个 DXT1 颜色块，返回按行排列的 16 个 RGBA 像素。"""
    c0, c1, selectors = struct.unpack_from("<HHI", block)
    colors = [(*_rgb565(c0), 255), (*_rgb565(c1), 255)]
    if c0 > c1 or not allow_transparent:
        colors.extend(
            [
                tuple((2 * colors[0][i] + colors[1][i]) // 3 for i in range(4)),
                tuple((colors[0][i] + 2 * colors[1][i]) // 3 for i in range(4)),
            ]
        )
    else:
        colors.extend(
            [
                tuple((colors[0][i] + colors[1][i]) // 2 for i in range(4)),
                (0, 0, 0, 0),
            ]
        )
    return [colors[(selectors >> (2 * i)) & 3] for i in range(16)]


def _decode_dxt1(block: bytes) -> list[tuple[int, int, int, int]]:
    return _decode_color_block(block, allow_transparent=True)


def _decode_dxt5(block: bytes) -> list[tuple[int, int, int, int]]:
    """解码一个 DXT5 块：前 8 字节是 alpha，后 8 字节是 DXT1 颜色。"""
    a0, a1 = block[0], block[1]
    alpha_bits = int.from_bytes(block[2:8], "little")
    alphas = [a0, a1]
    if a0 > a1:
        alphas.extend([(6 * a0 + a1) // 7, (5 * a0 + 2 * a1) // 7,
                       (4 * a0 + 3 * a1) // 7, (3 * a0 + 4 * a1) // 7,
                       (2 * a0 + 5 * a1) // 7, (a0 + 6 * a1) // 7])
    else:
        alphas.extend([(4 * a0 + a1) // 5, (3 * a0 + 2 * a1) // 5,
                       (2 * a0 + 3 * a1) // 5, (a0 + 4 * a1) // 5, 0, 255])
    pixels = _decode_color_block(block[8:16], allow_transparent=False)
    return [(*pixel[:3], alphas[(alpha_bits >> (3 * i)) & 7]) for i, pixel in enumerate(pixels)]


def read_ktx1_bytes(blob: bytes, source: object = "<memory>") -> tuple[int, int, int, bytes]:
    """读取内存中的 KTX1 首个 mip，返回宽、高、压缩格式和块数据。"""
    if len(blob) < 64 or blob[:12] != KTX1_IDENTIFIER:
        raise ValueError(f"不是 KTX1 文件: {source}")
    fields = struct.unpack_from("<13I", blob, 12)
    endianness, gl_type, gl_type_size, gl_format, internal, _base, width, height, depth, array_count, faces, mip_count, kv_size = fields
    if endianness != 0x04030201:
        raise ValueError("只支持小端 KTX1")
    if gl_type != 0 or gl_format != 0 or depth != 0 or array_count != 0 or faces != 1:
        raise ValueError("不是本工具支持的二维块压缩 KTX1")
    if gl_type_size != 1:
        raise ValueError("只支持块压缩 KTX1")
    if internal not in (GL_COMPRESSED_RGB_S3TC_DXT1_EXT, GL_COMPRESSED_RGBA_S3TC_DXT5_EXT):
        raise ValueError(f"不支持的 glInternalFormat: 0x{internal:04X}")
    data_offset = 64 + kv_size
    if data_offset + 4 > len(blob):
        raise ValueError("KTX key/value 区越界")
    # KTX 的每个 mip 都有一个 imageSize；这里输出第一级（原始尺寸），
    # 后续缩小级别由游戏运行时按需使用，写 PNG 时无需一并保存。
    image_size = struct.unpack_from("<I", blob, data_offset)[0]
    data_offset += 4
    if data_offset + image_size > len(blob):
        raise ValueError("KTX 图像数据越界")
    expected_block_size = 8 if internal == GL_COMPRESSED_RGB_S3TC_DXT1_EXT else 16
    block_count = ((width + 3) // 4) * ((height + 3) // 4)
    if image_size != block_count * expected_block_size:
        raise ValueError(f"图像大小 {image_size} 与尺寸/格式不符，预期 {block_count * expected_block_size}")
    return width, height, internal, blob[data_offset:data_offset + image_size]


def read_ktx1(path: Path) -> tuple[int, int, int, bytes]:
    """读取 KTX1 文件的第一个 mip 级别。"""
    return read_ktx1_bytes(path.read_bytes(), path)


def convert_bytes(blob: bytes, output: Path, source: object = "<memory>") -> dict[str, object]:
    """把一个已解压的 KTX1 载荷直接转换成 PNG。

    解包器使用这个入口逐帧转换，因此不会先把整个 NPK 的 KTX 文件写到磁盘。
    """
    width, height, internal, compressed = read_ktx1_bytes(blob, source)
    if texture2ddecoder is not None:
        # 原生实现返回 BGRA；Pillow 的 raw 解码器把它转换成 RGBA，
        # 避免在大批量导出时执行数百万次 Python 像素循环。
        if internal == GL_COMPRESSED_RGB_S3TC_DXT1_EXT:
            pixels = texture2ddecoder.decode_bc1(compressed, width, height)
        else:
            pixels = texture2ddecoder.decode_bc3(compressed, width, height)
        image = Image.frombytes("RGBA", (width, height), pixels, "raw", "BGRA")
    else:
        block_size = 8 if internal == GL_COMPRESSED_RGB_S3TC_DXT1_EXT else 16
        decode = _decode_dxt1 if block_size == 8 else _decode_dxt5
        blocks_w = (width + 3) // 4
        blocks_h = (height + 3) // 4
        pixels = bytearray(width * height * 4)
        for by in range(blocks_h):
            for bx in range(blocks_w):
                start = (by * blocks_w + bx) * block_size
                block_pixels = decode(compressed[start:start + block_size])
                for py in range(4):
                    y = by * 4 + py
                    if y >= height:
                        continue
                    for px in range(4):
                        x = bx * 4 + px
                        if x >= width:
                            continue
                        dst = (y * width + x) * 4
                        pixels[dst:dst + 4] = bytes(block_pixels[py * 4 + px])
        image = Image.frombytes("RGBA", (width, height), bytes(pixels))
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG")
    return {
        "input": str(source),
        "output": str(output),
        "width": width,
        "height": height,
        "format": f"0x{internal:04X}",
        "compressed_bytes": len(compressed),
        "png_bytes": output.stat().st_size,
    }


def convert(path: Path, output: Path) -> dict[str, object]:
    """把磁盘上的 KTX1 文件转换成 PNG。"""
    return convert_bytes(path.read_bytes(), output, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="将 KTX1 DXT1/DXT5 纹理转换为 PNG")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(convert(args.input, args.output))


if __name__ == "__main__":
    main()
