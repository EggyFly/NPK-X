"""NeoX XN script container: key derivation and block decryption."""

import hashlib
import hmac
import struct
import zlib

import mode3_tables as _t

MASK32 = 0xFFFFFFFF
KEY_SEED = bytes.fromhex("e1ac77de1abccc930269932985bbf16ab19472f93060e16b30f0a98052c947ff")
KEY_TAG = b"neox-py-encrypt-v2"
HEADER = 10


def _neo(value: int) -> int:
    ror = ((value >> 8) | (value << 24)) & MASK32
    rol = ((value << 8) | (value >> 24)) & MASK32
    return (ror ^ ((ror ^ rol) & 0x00FF00FF)) & MASK32


def derive_key(salt: str) -> bytes:
    first = hmac.new(salt.encode("utf-8"), KEY_SEED, hashlib.sha256).digest()
    return hmac.new(first, KEY_TAG + b"\x01", hashlib.sha256).digest()


def expand_key(key: bytes) -> tuple[list[int], int]:
    nk = len(key) // 4
    if nk not in (4, 6, 8):
        raise ValueError("invalid key size")
    rounds = nk + 6
    total = 4 * (rounds + 1)
    sbox = _t.SBOX
    words = [_neo(v) for v in struct.unpack("<%dI" % nk, key)]
    rcon = 1
    for i in range(nk, total):
        prev = words[i - 1]
        if i % nk == 0:
            b0 = (prev >> 24) & 0xFF
            b1 = (prev >> 16) & 0xFF
            b2 = (prev >> 8) & 0xFF
            b3 = prev & 0xFF
            g = ((sbox[b1] << 24) | (sbox[b2] << 16) | (sbox[b3] << 8) | sbox[b0]) ^ (rcon << 24)
            words.append((words[i - nk] ^ g) & MASK32)
            rcon = ((rcon << 1) ^ (0x11B if rcon & 0x80 else 0)) & 0xFF
        elif nk > 6 and i % nk == 4:
            b0 = (prev >> 24) & 0xFF
            b1 = (prev >> 16) & 0xFF
            b2 = (prev >> 8) & 0xFF
            b3 = prev & 0xFF
            g = (sbox[b0] << 24) | (sbox[b1] << 16) | (sbox[b2] << 8) | sbox[b3]
            words.append((words[i - nk] ^ g) & MASK32)
        else:
            words.append((words[i - nk] ^ prev) & MASK32)
    return words, rounds


def prepare_schedule(key: bytes) -> list[int]:
    words, rounds = expand_key(key)
    groups = rounds + 1
    words = [w for group in reversed(range(groups)) for w in words[group * 4:group * 4 + 4]]
    e0, e1, e2, e3 = _t.E0, _t.E1, _t.E2, _t.E3
    out = list(words)
    for group in range(1, groups - 1):
        for i in range(4):
            w = words[group * 4 + i]
            out[group * 4 + i] = (
                e1[(w >> 16) & 0xFF]
                ^ e2[(w >> 8) & 0xFF]
                ^ e0[(w >> 24) & 0xFF]
                ^ e3[w & 0xFF]
            ) & MASK32
    return out + [0] * 16 + [rounds]


def decrypt_block(block: bytes, schedule: list[int]) -> bytes:
    t0, t1, t2, t3 = _t.T0, _t.T1, _t.T2, _t.T3
    inv_sbox = _t.INV_SBOX
    k = schedule
    rounds = k[-1]
    s = [_neo(v) ^ k[i] for i, v in enumerate(struct.unpack("<4I", block))]

    a = (k[4] ^ t0[s[1] & 255] ^ t1[s[0] >> 24] ^ t2[(s[3] >> 16) & 255] ^ t3[(s[2] >> 8) & 255]) & MASK32
    b = (k[5] ^ t0[s[2] & 255] ^ t1[s[1] >> 24] ^ t3[(s[3] >> 8) & 255] ^ t2[(s[0] >> 16) & 255]) & MASK32
    c = (k[6] ^ t0[s[3] & 255] ^ t1[s[2] >> 24] ^ t3[(s[0] >> 8) & 255] ^ t2[(s[1] >> 16) & 255]) & MASK32
    d = (k[7] ^ t0[s[0] & 255] ^ t1[s[3] >> 24] ^ t2[(s[2] >> 16) & 255] ^ t3[(s[1] >> 8) & 255]) & MASK32

    j = 8
    for _ in range(rounds // 2 - 1):
        w = (k[j + 3] ^ t0[a & 255] ^ t1[d >> 24] ^ t2[(c >> 16) & 255] ^ t3[(b >> 8) & 255]) & MASK32
        x2 = (k[j + 2] ^ t0[d & 255] ^ t1[c >> 24] ^ t2[(b >> 16) & 255] ^ t3[(a >> 8) & 255]) & MASK32
        y = (k[j + 1] ^ t0[c & 255] ^ t1[b >> 24] ^ t3[(d >> 8) & 255] ^ t2[(a >> 16) & 255]) & MASK32
        z = (k[j] ^ t0[b & 255] ^ t1[a >> 24] ^ t2[(d >> 16) & 255] ^ t3[(c >> 8) & 255]) & MASK32
        a = (k[j + 4] ^ t0[y & 255] ^ t1[z >> 24] ^ t2[(w >> 16) & 255] ^ t3[(x2 >> 8) & 255]) & MASK32
        b = (k[j + 5] ^ t0[x2 & 255] ^ t1[y >> 24] ^ t3[(w >> 8) & 255] ^ t2[(z >> 16) & 255]) & MASK32
        c = (k[j + 6] ^ t0[w & 255] ^ t1[x2 >> 24] ^ t2[(y >> 16) & 255] ^ t3[(z >> 8) & 255]) & MASK32
        d = (k[j + 7] ^ t0[z & 255] ^ t1[w >> 24] ^ t2[(x2 >> 16) & 255] ^ t3[(y >> 8) & 255]) & MASK32
        j += 8

    r = [
        (k[j] ^ inv_sbox[b & 255] ^ (inv_sbox[(c >> 8) & 255] << 8)
         ^ (inv_sbox[(d >> 16) & 255] << 16) ^ (inv_sbox[a >> 24] << 24)) & MASK32,
        (k[j + 1] ^ inv_sbox[c & 255] ^ (inv_sbox[(d >> 8) & 255] << 8)
         ^ (inv_sbox[(a >> 16) & 255] << 16) ^ (inv_sbox[b >> 24] << 24)) & MASK32,
        (k[j + 2] ^ inv_sbox[d & 255] ^ (inv_sbox[(a >> 8) & 255] << 8)
         ^ (inv_sbox[(b >> 16) & 255] << 16) ^ (inv_sbox[c >> 24] << 24)) & MASK32,
        (k[j + 3] ^ inv_sbox[a & 255] ^ (inv_sbox[(b >> 8) & 255] << 8)
         ^ (inv_sbox[(c >> 16) & 255] << 16) ^ (inv_sbox[d >> 24] << 24)) & MASK32,
    ]
    return struct.pack("<4I", *[_neo(v) for v in r])


def header(payload: bytes) -> tuple[int, int] | None:
    if len(payload) < HEADER + 16 or payload[:2] != b"XN":
        return None
    compressed = int.from_bytes(payload[2:6], "little")
    original = int.from_bytes(payload[6:10], "little")
    if compressed <= 0 or original <= 0 or compressed > len(payload) - HEADER:
        return None
    return compressed, original


def decrypt(payload: bytes, salt: str) -> bytes | None:
    sizes = header(payload)
    if not sizes:
        return None
    compressed, original = sizes
    body = payload[HEADER:]
    schedule = prepare_schedule(derive_key(salt))
    out = bytearray()
    for offset in range(0, len(body) - len(body) % 16, 16):
        out += decrypt_block(body[offset:offset + 16], schedule)
    try:
        plain = zlib.decompress(bytes(out[:compressed]))
    except Exception:
        return None
    return plain if len(plain) == original else None
