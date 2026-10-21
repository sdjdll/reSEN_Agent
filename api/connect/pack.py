"""协议帧的字节层：``[len 4B][counter 8B][fcode 8B][AEAD 密文+tag]``。

本模块**不持有密钥**，只做布局、校验与 ``freshcode`` 计算；
加密/解密与 counter 管理分别在 ``crypto`` / ``counter`` / ``session``。

对应 ``skills/resen-tcp-v1.md`` 第 2 节与第 6 节常量表。
"""

from __future__ import annotations

import hashlib
import hmac
import struct

HDR_LEN = 4
FRESH_LEN = 16
FCODE_LEN = 8
TAG_LEN = 16

MAX_FRAME = 1_024_000
MAX_LEN = MAX_FRAME - HDR_LEN
MIN_LEN = FRESH_LEN + 1 + TAG_LEN
MAX_CT = MAX_LEN - FRESH_LEN
MAX_DATA = MAX_CT - TAG_LEN - 1

LEN_FMT = ">I"
_COUNTER_BYTES = 8


class FrameError(Exception):
    """帧层结构错误（长度越界、截断、字段不一致）——收到即应断开。"""


def freshcode(seed: bytes, c: int) -> bytes:
    """``fcode = HMAC-SHA256(seed_dir, counter_be64)[:8]``。

    粗计数器明文可见，但 fcode 是密钥派生的校验值：
    不知道 ``seed`` 既无法预测下一帧，也无法由 fcode 反推种子。
    """
    if not 0 <= c < 1 << 64:
        raise ValueError("counter out of range")
    return hmac.new(seed, c.to_bytes(_COUNTER_BYTES, "big"), hashlib.sha256).digest()[:FCODE_LEN]


def aad(length: int, counter: int, code: bytes) -> bytes:
    """``AAD = len(4B) || counter(8B) || fcode(8B)``，把明文头部纳入认证。"""
    if len(code) != FCODE_LEN:
        raise ValueError(f"fcode must be {FCODE_LEN} bytes")
    return struct.pack(LEN_FMT, length) + counter.to_bytes(_COUNTER_BYTES, "big") + code


def ciphertext_len(data_len: int) -> int:
    """给定业务数据长度，返回密文段长度（含 1B type 与 16B tag）。"""
    if data_len < 0 or data_len > MAX_DATA:
        raise FrameError(f"data too large: {data_len} > {MAX_DATA}")
    return 1 + data_len + TAG_LEN


def frame_len(data_len: int) -> int:
    """给定业务数据长度，返回 ``len`` 字段值（不含自身 4B）。"""
    return FRESH_LEN + ciphertext_len(data_len)


class datapack:
    """一帧的结构化表示，可双向转换。

    - ``datapack(raw).unpack()``：把整帧字节解析到字段（不验密）
    - ``datapack.build(c, fcode, ct).topack()``：从字段生成整帧字节
    """

    raw: bytes
    length: int
    counter: int
    fcode: bytes
    ciphertext: bytes

    def __init__(self, rawdata: bytes | None = None) -> None:
        self.raw = bytes(rawdata) if rawdata else b""
        self.length = 0
        self.counter = 0
        self.fcode = b""
        self.ciphertext = b""

    @classmethod
    def build(cls, counter: int, fcode: bytes, ciphertext: bytes) -> "datapack":
        pack = cls()
        pack.counter = counter
        pack.fcode = fcode
        pack.ciphertext = bytes(ciphertext)
        pack.topack()
        return pack

    @property
    def fresh(self) -> bytes:
        """fresh 段 16B：counter || fcode。"""
        return self.counter.to_bytes(_COUNTER_BYTES, "big") + self.fcode

    def topack(self) -> bytes:
        """按字段生成整帧；长度越界抛 ``FrameError``。"""
        if len(self.fcode) != FCODE_LEN:
            raise FrameError(f"fcode must be {FCODE_LEN} bytes, got {len(self.fcode)}")
        if not 0 <= self.counter < 1 << 64:
            raise FrameError("counter out of range")
        ct_len = len(self.ciphertext)
        if ct_len > MAX_CT:
            raise FrameError(f"ciphertext too large: {ct_len} > {MAX_CT}")
        self.length = FRESH_LEN + ct_len
        raw = struct.pack(LEN_FMT, self.length) + self.fresh + self.ciphertext
        self.raw = raw
        return raw

    def unpack(self) -> "datapack":
        """解析 ``self.raw``；结构不合法抛 ``FrameError``。"""
        raw = self.raw
        if len(raw) < HDR_LEN:
            raise FrameError(f"frame shorter than header: {len(raw)}")
        (length,) = struct.unpack(LEN_FMT, raw[:HDR_LEN])
        if length < MIN_LEN or length > MAX_LEN:
            raise FrameError(f"frame length out of range: {length}")
        if len(raw) != HDR_LEN + length:
            raise FrameError(f"frame truncated: have {len(raw)}, need {HDR_LEN + length}")
        self.length = length
        start = HDR_LEN
        self.counter = int.from_bytes(raw[start : start + _COUNTER_BYTES], "big")
        start += _COUNTER_BYTES
        self.fcode = raw[start : start + FCODE_LEN]
        start += FCODE_LEN
        self.ciphertext = raw[start:]
        return self

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        return (
            f"datapack(length={self.length}, counter={self.counter}, "
            f"fcode={self.fcode.hex()}, ct={len(self.ciphertext)}B)"
        )