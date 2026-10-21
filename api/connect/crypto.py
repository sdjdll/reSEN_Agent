"""reSEN TCP 协议 v1 的密钥与算法层。

对应 ``skills/resen-tcp-v1.md`` 第 3.1 / 3.2 / 3.5 / 3.6 节：

- ``fp``：机器特征（只做域分离与连接一致性校验，不是机密）
- ``Keys``：由 ``access_key`` + 双方 ``fp`` + 方向 经 HKDF-SHA256 派生
- ``nonce``：4B 零占位 + 8B 单调 counter
- AEAD：优先 AES-256-GCM（需 ``cryptography``），否则内置 ChaCha20-Poly1305
"""

from __future__ import annotations

import hashlib
import hmac
import platform
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from file.jsonfile import jsonfile

try:  # 可选依赖：有就优先用 AES-256-GCM
    from cryptography.hazmat.primitives.ciphers.aead import (  # type: ignore[import-not-found]
        AESGCM as _AESGCM,
    )
except Exception:  # pragma: no cover - 取决于运行环境
    _AESGCM = None

from . import chacha

ROOT = Path(__file__).resolve().parents[2]
IDENTITY_PATH = ROOT / "configure" / "peer_identity.json"

SALT = b"reSEN-v1"
FP_PREFIX = b"reSEN-fp-v1"
FP_LEN = 16
KEY_LEN = 32

ALGO_AES_GCM = 1
ALGO_CHACHA20_POLY1305 = 2

_NAMES = {
    ALGO_AES_GCM: "aes-256-gcm",
    ALGO_CHACHA20_POLY1305: "chacha20-poly1305",
}


def machine_id() -> str:
    """尽量稳定的本机标识：Linux machine-id / Windows MachineGuid / 回退到网卡地址。"""
    system = platform.system()
    try:
        if system == "Linux":
            for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                path = Path(candidate)
                if path.is_file():
                    value = path.read_text(encoding="utf-8", errors="replace").strip()
                    if value:
                        return value
        elif system == "Windows":  # pragma: no cover - 仅 Windows
            import importlib

            # Pylance 在非 Windows 主机上不提供 winreg 的成员，用动态导入避开
            winreg: Any = importlib.import_module("winreg")
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            )
            try:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
            finally:
                key.Close()
            if value:
                return str(value)
    except Exception:
        pass
    return f"{uuid.getnode():012x}"


def install_uuid(path: str | Path | None = None) -> str:
    """安装实例标识：同一台机器上多实例互不干扰 counter 空间。"""
    target = Path(path) if path is not None else IDENTITY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    store = jsonfile(str(target))
    data = store.read()
    value = data.get("install_uuid") if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        value = uuid.uuid4().hex
        store.write({"install_uuid": value, "created": int(time.time())})
    return value


def fingerprint(
    uid: str | None = None, path: str | Path | None = None
) -> bytes:
    """``fp = SHA256("reSEN-fp-v1" || machine_id || hostname || platform || install_uuid)[:16]``"""
    raw = b"".join(
        (
            FP_PREFIX,
            machine_id().encode(),
            platform.node().encode(),
            platform.system().encode(),
            (uid or install_uuid(path)).encode(),
        )
    )
    return hashlib.sha256(raw).digest()[:FP_LEN]


def hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int = KEY_LEN) -> bytes:
    out = b""
    block = b""
    index = 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([index]), hashlib.sha256).digest()
        out += block
        index += 1
    return out[:length]


def psk(access_key: str | bytes) -> bytes:
    """``K_psk = HKDF-Extract(salt="reSEN-v1", IKM=access_key)``"""
    ikm = access_key.encode() if isinstance(access_key, str) else access_key
    return hkdf_extract(SALT, ikm)


@dataclass(frozen=True)
class Keys:
    """一条连接上两个方向的密钥与 fcode 种子。"""

    k_tx: bytes
    k_rx: bytes
    seed_tx: bytes
    seed_rx: bytes


def derive(access_key: str | bytes, fp_local: bytes, fp_remote: bytes) -> Keys:
    """方向绑定派生：密钥只由 ``(发送方 fp, 接收方 fp)`` 决定。

    ``A.tx == B.rx``、``A.rx == B.tx``；fp 顺序不同即天然方向分离，
    把对端发来的帧回射回去时解密/fcode 必然失败。
    """
    if len(fp_local) != FP_LEN or len(fp_remote) != FP_LEN:
        raise ValueError(f"fp must be {FP_LEN} bytes")
    base = psk(access_key)
    return Keys(
        k_tx=hkdf_expand(base, b"key" + fp_local + fp_remote),
        k_rx=hkdf_expand(base, b"key" + fp_remote + fp_local),
        seed_tx=hkdf_expand(base, b"fcode" + fp_local + fp_remote),
        seed_rx=hkdf_expand(base, b"fcode" + fp_remote + fp_local),
    )


def hello_key(access_key: str | bytes) -> bytes:
    """hello 阶段专用密钥。

    hello 是连接的第一帧，此时还不知道对端 ``fp``，无法派生方向密钥；
    用一把与方向无关的固定密钥完成 "交换 fp + 协商算法"。
    它只加密 16B fp 与 1B 算法号，不承载业务数据。
    """
    return hkdf_expand(psk(access_key), b"hello")


def nonce(counter: int) -> bytes:
    """96-bit nonce = 4B 零占位 || counter(8B 大端)。"""
    if not 0 <= counter < 1 << 64:
        raise ValueError("counter out of range")
    return b"\x00" * 4 + counter.to_bytes(8, "big")


def available_algo() -> int:
    """本机首选算法号（双方协商后取共同可用的）。"""
    return ALGO_AES_GCM if _AESGCM is not None else ALGO_CHACHA20_POLY1305


def algo_name(algo: int) -> str:
    return _NAMES.get(algo, f"unknown-{algo}")


def negotiate_algo(local: int, remote: int) -> int:
    """优先 AES-GCM；任一方不支持就退回 ChaCha20-Poly1305。"""
    if local == ALGO_AES_GCM and remote == ALGO_AES_GCM:
        return ALGO_AES_GCM
    return ALGO_CHACHA20_POLY1305


def seal(algo: int, key: bytes, nonce_: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    if algo == ALGO_AES_GCM:
        if _AESGCM is None:
            raise RuntimeError("AES-256-GCM requested but 'cryptography' is not installed")
        return _AESGCM(key).encrypt(nonce_, plaintext, aad)
    if algo == ALGO_CHACHA20_POLY1305:
        return chacha.seal(key, nonce_, plaintext, aad)
    raise ValueError(f"unknown AEAD algorithm {algo}")


def open_(algo: int, key: bytes, nonce_: bytes, data: bytes, aad: bytes = b"") -> bytes:
    """解密并验 tag；任何失败统一抛 ``ValueError``。"""
    if algo == ALGO_AES_GCM:
        if _AESGCM is None:
            raise RuntimeError("AES-256-GCM requested but 'cryptography' is not installed")
        try:
            return _AESGCM(key).decrypt(nonce_, data, aad)
        except Exception as exc:
            raise ValueError("AEAD authentication failed") from exc
    if algo == ALGO_CHACHA20_POLY1305:
        return chacha.unseal(key, nonce_, data, aad)
    raise ValueError(f"unknown AEAD algorithm {algo}")
