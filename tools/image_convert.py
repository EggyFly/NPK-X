#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Image decoders used by the NPK mode-3 extractor.

KTX1 DXT1/DXT5 is delegated to ``ktx_to_png``.  This module adds DDS
support (legacy fourCC and DX10 headers) and simple Pillow-backed formats.
Only the first mip / base image is exported.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import ktx_to_png

try:
    import texture2ddecoder
except ImportError:  # pragma: no cover
    texture2ddecoder = None

KTX1_ID = ktx_to_png.KTX1_IDENTIFIER
KTX2_ID = b"\xABKTX 20\xBB\r\n\x1A\n"
PNG_ID = b"\x89PNG\r\n\x1a\n"
JPEG_ID = b"\xff\xd8\xff"

DDS_MAGIC = b"DDS "
DDPF_FOURCC = 0x4
DDSCAPS2_CUBEMAP = 0x0000FE00

# DXGI formats
DXGI_R32G32B32A32_FLOAT = 2
DXGI_R16G16B16A16_FLOAT = 10
DXGI_R8G8B8A8_UNORM = 28
DXGI_R8G8B8A8_UNORM_SRGB = 29
DXGI_BC1_UNORM = 71
DXGI_BC2_UNORM = 74
DXGI_BC3_UNORM = 77
DXGI_BC5_UNORM = 83
DXGI_BC7_UNORM = 98
DXGI_B8G8R8A8_UNORM = 87
DXGI_B8G8R8X8_UNORM = 88


def classify(payload: bytes, suffix: str = "") -> str:
    suffix = suffix.lower()
    if payload.startswith(KTX1_ID):
        return "ktx1"
    if payload.startswith(KTX2_ID):
        return "ktx2"
    if payload.startswith(DDS_MAGIC):
        return "dds"
    if payload.startswith(PNG_ID):
        return "png"
    if payload.startswith(JPEG_ID):
        return "jpeg"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WAVE":
        return "wem"
    if suffix in (".tga", ".bmp", ".webp", ".gif"):
        return suffix[1:]
    return "other"


def _decode_bc(data: bytes, width: int, height: int, kind: int) -> Image.Image:
    if texture2ddecoder is None:
        raise ValueError("texture2ddecoder is required for BC textures")
    if kind == 1:
        raw = texture2ddecoder.decode_bc1(data, width, height)
    elif kind == 3:
        raw = texture2ddecoder.decode_bc3(data, width, height)
    elif kind == 5:
        raw = texture2ddecoder.decode_bc5(data, width, height)
    elif kind == 7:
        raw = texture2ddecoder.decode_bc7(data, width, height)
    else:
        raise ValueError(f"unsupported BC kind {kind}")
    return Image.frombytes("RGBA", (width, height), raw, "raw", "BGRA")


def _decode_dxt3(data: bytes, width: int, height: int) -> Image.Image:
    """Decode BC2/DXT3: 8-byte explicit 4-bit alpha + 8-byte DXT1 color."""
    out = bytearray(width * height * 4)
    bw = (width + 3) // 4
    bh = (height + 3) // 4
    offset = 0
    for by in range(bh):
        for bx in range(bw):
            block = data[offset:offset + 16]
            offset += 16
            if len(block) < 16:
                raise ValueError("truncated DXT3 block")
            alpha_bits = int.from_bytes(block[0:8], "little")
            colors = ktx_to_png._decode_color_block(block[8:16], allow_transparent=False)
            for py in range(4):
                y = by * 4 + py
                if y >= height:
                    continue
                for px in range(4):
                    x = bx * 4 + px
                    if x >= width:
                        continue
                    i = py * 4 + px
                    a = (alpha_bits >> (4 * i)) & 0xF
                    r, g, b, _ = colors[i]
                    p = (y * width + x) * 4
                    out[p:p + 4] = bytes((r, g, b, a * 17))
    return Image.frombytes("RGBA", (width, height), bytes(out))


def _half_to_byte(value: int) -> int:
    try:
        f = struct.unpack("<e", struct.pack("<H", value))[0]
    except Exception:
        return 0
    if f != f:
        return 0
    f = max(0.0, min(1.0, f))
    return int(f * 255.0 + 0.5)


def _decode_float_rgba16(data: bytes, width: int, height: int, stride: int | None = None) -> Image.Image:
    bpp = 8
    stride = stride or width * bpp
    out = bytearray(width * height * 4)
    for y in range(height):
        row = data[y * stride:y * stride + width * bpp]
        o = y * width * 4
        for x in range(width):
            p = x * bpp
            out[o + x * 4 + 0] = _half_to_byte(struct.unpack_from("<H", row, p)[0])
            out[o + x * 4 + 1] = _half_to_byte(struct.unpack_from("<H", row, p + 2)[0])
            out[o + x * 4 + 2] = _half_to_byte(struct.unpack_from("<H", row, p + 4)[0])
            out[o + x * 4 + 3] = _half_to_byte(struct.unpack_from("<H", row, p + 6)[0])
    return Image.frombytes("RGBA", (width, height), bytes(out))


def dds_to_image(payload: bytes) -> Image.Image:
    if len(payload) < 128 or not payload.startswith(DDS_MAGIC):
        raise ValueError("not a DDS file")
    height, width, pitch, depth, mips = struct.unpack_from("<5I", payload, 12)
    pf_flags = struct.unpack_from("<I", payload, 80)[0]
    fourcc = payload[84:88]
    bit_count = struct.unpack_from("<I", payload, 88)[0]
    caps2 = struct.unpack_from("<I", payload, 112)[0]
    if depth > 1:
        raise ValueError("volume DDS not supported")
    if caps2 & DDSCAPS2_CUBEMAP:
        raise ValueError("cubemap DDS not supported")
    if width == 0 or height == 0 or width > 16384 or height > 16384:
        raise ValueError("invalid DDS dimensions")

    if fourcc == b"DX10":
        if len(payload) < 148:
            raise ValueError("truncated DX10 header")
        dxgi = struct.unpack_from("<I", payload, 128)[0]
        data = payload[148:]
        if dxgi == DXGI_R16G16B16A16_FLOAT:
            return _decode_float_rgba16(data, width, height)
        if dxgi in (DXGI_R8G8B8A8_UNORM, DXGI_R8G8B8A8_UNORM_SRGB):
            return Image.frombytes("RGBA", (width, height), data[:width * height * 4])
        if dxgi in (DXGI_B8G8R8A8_UNORM, DXGI_B8G8R8X8_UNORM):
            return Image.frombytes("RGBA", (width, height), data[:width * height * 4], "raw", "BGRA")
        if dxgi in (DXGI_BC1_UNORM, DXGI_BC2_UNORM, DXGI_BC3_UNORM, DXGI_BC5_UNORM, DXGI_BC7_UNORM):
            bw = (width + 3) // 4
            bh = (height + 3) // 4
            if dxgi == DXGI_BC1_UNORM:
                size = bw * bh * 8
                return _decode_bc(data[:size], width, height, 1)
            if dxgi == DXGI_BC2_UNORM:
                return _decode_dxt3(data[:bw * bh * 16], width, height)
            if dxgi == DXGI_BC3_UNORM:
                return _decode_bc(data[:bw * bh * 16], width, height, 3)
            if dxgi == DXGI_BC5_UNORM:
                return _decode_bc(data[:bw * bh * 16], width, height, 5)
            return _decode_bc(data[:bw * bh * 16], width, height, 7)
        raise ValueError(f"unsupported DXGI format {dxgi}")

    data = payload[128:]
    if fourcc == b"DXT1":
        bw = (width + 3) // 4
        bh = (height + 3) // 4
        return _decode_bc(data[:bw * bh * 8], width, height, 1)
    if fourcc == b"DXT3":
        bw = (width + 3) // 4
        bh = (height + 3) // 4
        return _decode_dxt3(data[:bw * bh * 16], width, height)
    if fourcc == b"DXT5":
        bw = (width + 3) // 4
        bh = (height + 3) // 4
        return _decode_bc(data[:bw * bh * 16], width, height, 3)
    if fourcc == b"DX10":
        raise ValueError("handled above")
    # A16B16G16R16F uses fourCC 0x71 (113) in this client's DDS writer.
    if int.from_bytes(fourcc, "little") == 0x71 or bit_count == 64:
        return _decode_float_rgba16(data, width, height)
    if pf_flags & 0x20000 or bit_count == 8:
        stride = pitch if pitch >= width else width
        return Image.frombytes("L", (width, height), data[:stride * height], "raw", "L", stride, 1)
    if bit_count == 32:
        stride = pitch if pitch >= width * 4 else width * 4
        # Most legacy 32-bit DDS files in this build are BGRA/RGBA masks.
        return Image.frombytes("RGBA", (width, height), data[:stride * height], "raw", "BGRA", stride, 1)
    raise ValueError(f"unsupported legacy DDS fourCC={fourcc!r} bits={bit_count}")


def convert_image(payload: bytes, src_suffix: str, output: Path) -> tuple[str, str]:
    """Convert a payload to PNG when possible.

    Returns (status, final_suffix).  On unsupported content raises ValueError
    so the caller can preserve the original bytes.
    """
    kind = classify(payload, src_suffix)
    output = Path(output)
    if kind == "ktx1":
        ktx_to_png.convert_bytes(payload, output)
        return "converted_png", ".png"
    if kind == "dds":
        image = dds_to_image(payload)
        image.save(output, "PNG")
        return "converted_png", ".png"
    if kind == "png":
        if output.suffix.lower() == ".png":
            output.write_bytes(payload)
        else:
            output.with_suffix(".png").write_bytes(payload)
        return "copied_png", ".png"
    if kind in ("jpeg", "tga", "bmp", "webp", "gif"):
        image = Image.open(io.BytesIO(payload))
        image.load()
        image.save(output.with_suffix(".png") if output.suffix.lower() != ".png" else output, "PNG")
        return "converted_png", ".png"
    raise ValueError(f"payload is not a supported image ({kind})")


def image_extension(payload: bytes, src_suffix: str) -> str:
    """Best-effort extension for an unnamed payload."""
    kind = classify(payload, src_suffix)
    if kind in ("ktx1", "ktx2"):
        return ".kta"
    if kind == "dds":
        return ".dds"
    if kind == "png":
        return ".png"
    if kind == "jpeg":
        return ".jpg"
    if kind == "wem":
        return ".wem"
    if payload[:4] == b"BKHD":
        return ".bnk"
    if payload[:4] == b"\x34\x80\xc8\xbb":
        return ".mesh"
    if payload[:4] == b"AKPK":
        return ".npk"
    return ".bin"
