"""纯 Python 的 ChaCha20-Poly1305（RFC 8439）。

项目不强依赖 ``cryptography``：装了就优先用 AES-256-GCM（多核/硬件加速更好），
没装就用这里的实现兜底。两者帧格式一致（32B key、12B nonce、16B tag），
因此协议层不需要区分算法细节，只需在 hello 阶段协商。

同时暴露 ``chacha20_xor`` 与 ``poly1305``，便于测试与复用。
"""

from __future__ import annotations

import hmac
import struct

_MASK = 0xFFFFFFFF
_P = (1 << 130) - 5
_CONST = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)


def _rotl(value: int, bits: int) -> int:
    return ((value << bits) & _MASK) | (value >> (32 - bits))


def _quarter(x: list[int], a: int, b: int, c: int, d: int) -> None:
    xa, xb, xc, xd = x[a], x[b], x[c], x[d]
    xa = (xa + xb) & _MASK
    xd = _rotl(xd ^ xa, 16)
    xc = (xc + xd) & _MASK
    xb = _rotl(xb ^ xc, 12)
    xa = (xa + xb) & _MASK
    xd = _rotl(xd ^ xa, 8)
    xc = (xc + xd) & _MASK
    xb = _rotl(xb ^ xc, 7)
    x[a], x[b], x[c], x[d] = xa, xb, xc, xd


def block(key: bytes, counter: int, nonce: bytes) -> bytes:
    """生成 64 字节 ChaCha20 密钥流块（counter 为 32 位块计数）。"""
    if len(key) != 32:
        raise ValueError("ChaCha20 key must be 32 bytes")
    if len(nonce) != 12:
        raise ValueError("ChaCha20 nonce must be 12 bytes")
    state = list(_CONST)
    state += list(struct.unpack("<8I", key))
    state += [counter & _MASK]
    state += list(struct.unpack("<3I", nonce))
    work = state[:]
    for _ in range(10):
        _quarter(work, 0, 4, 8, 12)
        _quarter(work, 1, 5, 9, 13)
        _quarter(work, 2, 6, 10, 14)
        _quarter(work, 3, 7, 11, 15)
        _quarter(work, 0, 5, 10, 15)
        _quarter(work, 1, 6, 11, 12)
        _quarter(work, 2, 7, 8, 13)
        _quarter(work, 3, 4, 9, 14)
    return struct.pack("<16I", *[(work[i] + state[i]) & _MASK for i in range(16)])


def chacha20_xor(key: bytes, nonce: bytes, counter: int, data: bytes) -> bytes:
    """ChaCha20 加/解密（同一操作）。"""
    out = bytearray(len(data))
    pos = 0
    while pos < len(data):
        stream = block(key, counter, nonce)
        end = min(pos + 64, len(data))
        chunk = data[pos:end]
        n = int.from_bytes(chunk, "little") ^ int.from_bytes(stream[: len(chunk)], "little")
        out[pos:end] = n.to_bytes(len(chunk), "little")
        counter = (counter + 1) & _MASK
        pos = end
    return bytes(out)


def poly1305(key: bytes, msg: bytes) -> bytes:
    """Poly1305 一次性认证（16B key，任意长度 msg，16B tag）。"""
    if len(key) != 32:
        raise ValueError("Poly1305 key must be 32 bytes")
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:], "little")
    acc = 0
    for pos in range(0, len(msg), 16):
        chunk = msg[pos : pos + 16]
        n = int.from_bytes(chunk + b"\x01", "little")
        acc = ((acc + n) * r) % _P
    acc = (acc + s) & ((1 << 128) - 1)
    return acc.to_bytes(16, "little")


def _pad16(data: bytes) -> bytes:
    return b"\x00" * (-len(data) % 16)


def _tag(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    polykey = block(key, 0, nonce)[:32]
    mac_data = (
        aad
        + _pad16(aad)
        + ciphertext
        + _pad16(ciphertext)
        + struct.pack("<QQ", len(aad), len(ciphertext))
    )
    return poly1305(polykey, mac_data)


def seal(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """ChaCha20-Poly1305 加密，返回 密文 || 16B tag。"""
    ciphertext = chacha20_xor(key, nonce, 1, plaintext)
    return ciphertext + _tag(key, nonce, ciphertext, aad)


def unseal(key: bytes, nonce: bytes, data: bytes, aad: bytes = b"") -> bytes:
    """ChaCha20-Poly1305 解密；认证失败抛 ValueError。"""
    if len(data) < 16:
        raise ValueError("ciphertext shorter than tag")
    ciphertext, tag = data[:-16], data[-16:]
    if not hmac.compare_digest(tag, _tag(key, nonce, ciphertext, aad)):
        raise ValueError("AEAD authentication failed")
    return chacha20_xor(key, nonce, 1, ciphertext)
