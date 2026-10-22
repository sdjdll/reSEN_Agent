"""协议自检：RFC 8439 向量、帧布局、密钥对称性、回环端到端。

    python -m api.connect.selftest

不依赖 pytest，任何一台装了 Python 的机器都能跑。
"""

from __future__ import annotations

import asyncio
import os
import struct
import tempfile
from pathlib import Path
from typing import Any

from . import chacha, crypto, pack, session
from .counter import CounterState

ACCESS = "selftest-access-key"


def _check(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)
    print(f"  ok  {message}")


# --------------------------------------------------------------- RFC 8439 向量
def test_chacha20_block() -> None:
    key = bytes(range(32))
    nonce = bytes.fromhex("000000090000004a00000000")
    block = chacha.block(key, 1, nonce)
    _check(
        block[:16].hex() == "10f1e7e4d13b5915500fdd1fa32071c4",
        "ChaCha20 块向量（RFC 8439 §2.3.2）",
    )


def test_poly1305() -> None:
    key = bytes.fromhex("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b")
    msg = b"Cryptographic Forum Research Group"
    _check(
        chacha.poly1305(key, msg).hex() == "a8061dc1305136c6c22b8baf0c0127a9",
        "Poly1305 向量（RFC 8439 §2.5.2）",
    )


def test_aead() -> None:
    key = bytes(range(0x80, 0xA0))
    nonce = bytes.fromhex("070000004041424344454647")
    aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
    plaintext = (
        b"Ladies and Gentlemen of the class of '99: If I could offer you only "
        b"one tip for the future, sunscreen would be it."
    )
    expected = bytes.fromhex(
        "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d6"
        "3dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b36"
        "92ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc"
        "3ff4def08e4b7a9de576d26586cec64b6116"
    )
    tag = bytes.fromhex("1ae10b594f09e26a7e902ecbd0600691")
    sealed = chacha.seal(key, nonce, plaintext, aad)
    _check(sealed[:-16] == expected, "ChaCha20-Poly1305 密文向量（RFC 8439 §2.8.2）")
    _check(sealed[-16:] == tag, "ChaCha20-Poly1305 tag 向量")
    _check(chacha.unseal(key, nonce, sealed, aad) == plaintext, "AEAD 解密还原")
    try:
        chacha.unseal(key, nonce, sealed[:-1] + bytes([sealed[-1] ^ 1]), aad)
    except ValueError:
        print("  ok  篡改 tag 被拒绝")
    else:
        raise AssertionError("篡改 tag 未被拒绝")


# ------------------------------------------------------------------ 协议零件
def test_pack() -> None:
    code = pack.freshcode(b"seed", 42)
    _check(len(code) == pack.FCODE_LEN, "freshcode 长度 = 8")
    _check(code != pack.freshcode(b"seed", 43), "freshcode 随 counter 变化")
    _check(len(pack.aad(33, 42, code)) == 20, "AAD = len(4) + counter(8) + fcode(8)")

    frame = pack.datapack.build(42, code, b"\x00" * (1 + pack.TAG_LEN))
    parsed = pack.datapack(frame.topack()).unpack()
    _check(parsed.length == pack.MIN_LEN, "空 data 帧 len = 33")
    _check(parsed.counter == 42 and parsed.fcode == code, "帧字段往返一致")

    for bad in (b"", b"\x00\x00\x00\x20", b"\xff\xff\xff\xff" + b"\x00" * 16):
        try:
            pack.datapack(bad).unpack()
        except pack.FrameError:
            pass
        else:
            raise AssertionError(f"非法帧未被拒绝: {bad!r}")
    print("  ok  非法帧被拒绝")


def test_derive() -> None:
    fp_a = bytes(range(16))
    fp_b = bytes(range(16, 32))
    keys_a = crypto.derive(ACCESS, fp_a, fp_b)
    keys_b = crypto.derive(ACCESS, fp_b, fp_a)
    _check(keys_a.k_tx == keys_b.k_rx, "A 的 tx 密钥 = B 的 rx 密钥")
    _check(keys_a.seed_tx == keys_b.seed_rx, "A 的 tx 种子 = B 的 rx 种子")
    _check(keys_a.k_tx != keys_b.k_tx, "两个方向密钥不同（防反射）")
    other = crypto.derive("another-key", fp_a, fp_b)
    _check(other.k_tx != keys_a.k_tx, "换 access_key 密钥全变")
    _check(
        crypto.negotiate_algo(crypto.ALGO_AES_GCM, crypto.ALGO_CHACHA20_POLY1305)
        == crypto.ALGO_CHACHA20_POLY1305,
        "算法协商回退到 ChaCha20-Poly1305",
    )


def test_counter(tmp: Path) -> None:
    path = tmp / "counter.json"
    state = CounterState(path, reserve=8)
    first = [state.take_tx("k") for _ in range(3)]
    _check(first == sorted(set(first)), "发送 counter 严格递增")
    state.commit()

    restarted = CounterState(path, reserve=8)
    _check(restarted.take_tx("k") > first[-1], "重启后 counter 不回退")

    _check(restarted.accept_rx("peer", 10), "接收到更大的 counter")
    _check(not restarted.accept_rx("peer", 10), "相同 counter 判为重放")
    _check(not restarted.accept_rx("peer", 9), "更小的 counter 判为重放")
    _check(restarted.accept_rx("peer", 11), "乱序之后的更大 counter 仍接受")


# ------------------------------------------------------------------ 端到端
async def _echo(data: bytes) -> bytes:
    return data


async def _read_one_hello(reader: asyncio.StreamReader) -> bytes:
    """对端（服务端）建连时也会先发一帧 hello，测试里把它读掉。"""
    header = await asyncio.wait_for(reader.readexactly(pack.HDR_LEN), 1.0)
    (length,) = struct.unpack(pack.LEN_FMT, header)
    return header + await asyncio.wait_for(reader.readexactly(length), 1.0)


class _Spy:
    """拦截写入的帧字节，便于做重放/篡改测试。"""

    def __init__(self, writer: Any) -> None:
        self._writer = writer
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(bytes(data))
        self._writer.write(data)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._writer, name)


async def _e2e(tmp: Path) -> None:
    server_state = CounterState(tmp / "server.json")
    client_state = CounterState(tmp / "client.json")
    server = await session.serve(_echo, "127.0.0.1", 0, ACCESS, state=server_state)
    port = int(server.sockets[0].getsockname()[1])
    try:
        for label, payload in (
            ("空 data", b""),
            ("小消息", b"hello reSEN"),
            ("64KB", os.urandom(64 * 1024)),
            ("接近上限", os.urandom(pack.MAX_DATA - 32)),
        ):
            reply = await session.request(
                "127.0.0.1", port, ACCESS, payload, state=client_state
            )
            _check(reply == payload, f"echo 往返 {label}")

        channel = await session.connect(
            "127.0.0.1", port, ACCESS, state=client_state
        )
        try:
            for index in range(3):
                payload = f"multi-{index}".encode()
                await channel.send(session.FT_DATA, payload)
                _check(
                    await channel.recv() == (session.FT_DATA, payload),
                    f"长连接多帧 echo #{index}",
                )
            await channel.ping()
            _check((await channel.recv())[0] == session.FT_PING, "PING 保活往返")
            _check(len(channel.remote_fp) == crypto.FP_LEN, "握手拿到对端 fp")
        finally:
            await channel.close()

        # 错误密钥：hello 的 fcode/tag 就过不去
        try:
            await session.request(
                "127.0.0.1", port, "wrong-key", b"x", state=CounterState(tmp / "bad.json")
            )
        except (session.AuthError, session.ProtocolError, session.ClosedError):
            print("  ok  错误 access_key 被拒绝")
        else:
            raise AssertionError("错误 access_key 未被拒绝")

        # 抓一帧后在新连接上重放：应被 last_rx 拦下（不回包）
        raw_reader, raw_writer = await asyncio.open_connection("127.0.0.1", port)
        spy = _Spy(raw_writer)
        channel = await session.Channel.open(
            raw_reader, spy, ACCESS, state=client_state, timeout=1.0
        )
        await channel.send(session.FT_DATA, b"first")
        _check(await channel.recv() == (session.FT_DATA, b"first"), "抓帧连接可用")
        await channel.send(session.FT_DATA, b"second")
        _check(await channel.recv() == (session.FT_DATA, b"second"), "抓帧连接第二帧")
        await channel.close()
        hello_frame, data_frame, second_frame = spy.frames[0], spy.frames[1], spy.frames[2]

        replay_reader, replay_writer = await asyncio.open_connection("127.0.0.1", port)
        replay_writer.write(hello_frame + data_frame)
        await replay_writer.drain()
        await _read_one_hello(replay_reader)
        try:
            await asyncio.wait_for(replay_reader.read(1), 1.0)
        except asyncio.TimeoutError:
            print("  ok  重放帧被丢弃（无响应）")
        else:
            raise AssertionError("重放帧得到了业务响应")
        replay_writer.close()

        # 篡改密文：counter 更新、fcode 正确，但 tag 不匹配；三次后熔断断开
        tampered = bytearray(second_frame)
        tampered[-1] ^= 0x01
        tamper_reader, tamper_writer = await asyncio.open_connection("127.0.0.1", port)
        tamper_writer.write(hello_frame + bytes(tampered) * session.MAX_FAILS)
        await tamper_writer.drain()
        await _read_one_hello(tamper_reader)
        closed = False
        try:
            await asyncio.wait_for(tamper_reader.read(), 2.0)
            closed = tamper_reader.at_eof()
        except (asyncio.TimeoutError, ConnectionError):
            pass
        _check(closed, "连续篡改触发熔断并断开")
        tamper_writer.close()
    finally:
        server.close()
        await server.wait_closed()


async def _sync_server(tmp: Path) -> None:
    server = session.PeerServer(_echo, "127.0.0.1", 0, ACCESS, state=CounterState(tmp / "sync.json"))
    loop = asyncio.get_running_loop()
    thread = loop.run_in_executor(None, server.serve_forever)
    await asyncio.sleep(0.2)
    try:
        reply = await session.request(
            "127.0.0.1", server.port, ACCESS, b"sync", state=CounterState(tmp / "sync-client.json")
        )
        _check(reply == b"sync", "PeerServer 同步包装可用")
    finally:
        server.shutdown()
        await asyncio.sleep(0.2)
        server.server_close()
        del thread


def main() -> int:
    print("ChaCha20 / Poly1305 / AEAD")
    test_chacha20_block()
    test_poly1305()
    test_aead()
    print("帧与密钥")
    test_pack()
    test_derive()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        test_counter(tmp)
        print("端到端")
        asyncio.run(_e2e(tmp))
        asyncio.run(_sync_server(tmp))
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
