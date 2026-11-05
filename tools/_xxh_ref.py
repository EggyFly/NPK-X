import struct

M32 = 0xFFFFFFFF
M64 = 0xFFFFFFFFFFFFFFFF
P32_1, P32_2, P32_3, P32_4, P32_5 = 2654435761, 2246822519, 3266489917, 668265263, 374761393
P64_1, P64_2, P64_3, P64_4, P64_5 = (11400714785074694791, 14029467366897019727, 1609587929392839161,
                                     9650029242287828579, 2870177450012600261)


def _rotl(x, r, mask):
    return ((x << r) | (x >> (32 if mask == M32 else 64) - r)) & mask


def _rnd32(acc, val):
    acc = (acc + val * P32_2) & M32
    acc = _rotl(acc, 13, M32)
    return (acc * P32_1) & M32


def xxh32(b: bytes, seed: int = 0) -> int:
    n = len(b)
    i = 0
    if n >= 16:
        v1 = (seed + P32_1 + P32_2) & M32
        v2 = (seed + P32_2) & M32
        v3 = seed & M32
        v4 = (seed - P32_1) & M32
        while i + 16 <= n:
            v1 = _rnd32(v1, struct.unpack_from("<I", b, i)[0])
            v2 = _rnd32(v2, struct.unpack_from("<I", b, i + 4)[0])
            v3 = _rnd32(v3, struct.unpack_from("<I", b, i + 8)[0])
            v4 = _rnd32(v4, struct.unpack_from("<I", b, i + 12)[0])
            i += 16
        h = (_rotl(v1, 1, M32) + _rotl(v2, 7, M32) + _rotl(v3, 12, M32) + _rotl(v4, 18, M32)) & M32
    else:
        h = (seed + P32_5) & M32
    h = (h + n) & M32
    while i + 4 <= n:
        h = (h + struct.unpack_from("<I", b, i)[0] * P32_3) & M32
        h = (_rotl(h, 17, M32) * P32_4) & M32
        i += 4
    while i < n:
        h = (h + b[i] * P32_5) & M32
        h = (_rotl(h, 11, M32) * P32_1) & M32
        i += 1
    h ^= h >> 15
    h = (h * P32_2) & M32
    h ^= h >> 13
    h = (h * P32_3) & M32
    h ^= h >> 16
    return h


def _rnd64(acc, val):
    acc = (acc + val * P64_2) & M64
    acc = _rotl(acc, 31, M64)
    return (acc * P64_1) & M64


def _merge64(acc, val):
    acc ^= _rnd64(0, val)
    return ((acc * P64_1) + P64_4) & M64


def xxh64(b: bytes, seed: int = 0) -> int:
    n = len(b)
    i = 0
    if n >= 32:
        v1 = (seed + P64_1 + P64_2) & M64
        v2 = (seed + P64_2) & M64
        v3 = seed & M64
        v4 = (seed - P64_1) & M64
        while i + 32 <= n:
            v1 = _rnd64(v1, struct.unpack_from("<Q", b, i)[0])
            v2 = _rnd64(v2, struct.unpack_from("<Q", b, i + 8)[0])
            v3 = _rnd64(v3, struct.unpack_from("<Q", b, i + 16)[0])
            v4 = _rnd64(v4, struct.unpack_from("<Q", b, i + 24)[0])
            i += 32
        h = (_rotl(v1, 1, M64) + _rotl(v2, 7, M64) + _rotl(v3, 12, M64) + _rotl(v4, 18, M64)) & M64
        h = _merge64(h, v1)
        h = _merge64(h, v2)
        h = _merge64(h, v3)
        h = _merge64(h, v4)
    else:
        h = (seed + P64_5) & M64
    h = (h + n) & M64
    while i + 8 <= n:
        k = _rnd64(0, struct.unpack_from("<Q", b, i)[0])
        h = ((h ^ k) * P64_1) & M64
        h = (_rotl(h, 27, M64) * P64_1 + P64_4) & M64
        i += 8
    if i + 4 <= n:
        h = (h ^ ((struct.unpack_from("<I", b, i)[0] * P64_1) & M64)) & M64
        h = ((h * P64_2) + P64_3) & M64
        i += 4
    while i < n:
        h = (h ^ ((b[i] * P64_5) & M64)) & M64
        h = (_rotl(h, 11, M64) * P64_1) & M64
        i += 1
    h ^= h >> 33
    h = (h * P64_2) & M64
    h ^= h >> 29
    h = (h * P64_3) & M64
    h ^= h >> 32
    return h
