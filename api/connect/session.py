"""TCP 会话层：握手、帧收发、防重放与熔断。

把 ``pack``（字节布局）、``crypto``（密钥/AEAD）、``counter``（持久计数）
拼成一条可直接用的双向加密通道：

```python
async with await session.connect("10.0.0.2", 1124, access_key) as ch:
    await ch.send(session.FT_DATA, b'{"messages": [...]}')
    ftype, data = await ch.recv()
```

对端用 ``serve(handler, ...)`` 起服务即可。两边完全对等（C/S 用同一套派生公式）。

握手只交换 17B（``fp 16B || 算法号 1B``），用固定 hello 密钥加密；
随后所有业务帧都走方向绑定的正式密钥，无证书、无明文可探测字段。
"""

from __future__ import annotations

import asyncio
import hmac
import inspect
import logging
import socket
import struct
from typing import Awaitable, Callable, cast

from . import crypto, pack
from .counter import CounterState, shared_state
from .pack import FrameError, datapack, freshcode

log = logging.getLogger(__name__)

FT_DATA = 0x01
FT_HELLO = 0x02
FT_PING = 0x03
FT_BYE = 0x04

PORT = 1124
MAX_FAILS = 3
HELLO_ALGO = crypto.ALGO_CHACHA20_POLY1305  # hello 固定用纯 Python 实现，处处可解


class ProtocolError(Exception):
    """协议层错误：非法帧、密钥不符、被熔断等。"""


class ClosedError(ProtocolError):
    """连接已关闭 / 对端提前断开。"""


class AuthError(ProtocolError):
    """fcode 或 AEAD tag 校验失败（密钥错、fp 已变或伪造）。"""


class ReplayError(ProtocolError):
    """``counter <= last_rx``：重放或乱序旧帧。"""


def backoff_delay(fails: int, base: float = 5.0, cap: float = 300.0) -> float:
    """熔断退避：5s 起，指数增长，封顶 300s。"""
    return min(cap, base * (2 ** max(0, fails - 1)))


def _as_fp(value: str | bytes | None) -> bytes:
    if value is None:
        return b""
    raw = bytes.fromhex(value) if isinstance(value, str) else bytes(value)
    if len(raw) != crypto.FP_LEN:
        raise ValueError(f"fp must be {crypto.FP_LEN} bytes")
    return raw


_DEFAULT_FP: bytes | None = None


def local_fingerprint() -> bytes:
    global _DEFAULT_FP
    cached = _DEFAULT_FP
    if cached is None:
        cached = crypto.fingerprint()
        _DEFAULT_FP = cached
    return cached


class Channel:
    """一条已经建立 TCP 连接的加密帧通道。"""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        access_key: str | bytes,
        *,
        local_fp: bytes | None = None,
        remote_fp: str | bytes | None = None,
        state: CounterState | None = None,
        algo: int | None = None,
        timeout: float | None = None,
        max_fails: int = MAX_FAILS,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._access_key = access_key
        self._local_fp = _as_fp(local_fp) if local_fp else local_fingerprint()
        self._remote_fp = _as_fp(remote_fp)
        self._expect_fp = bool(self._remote_fp)
        self._state = state if state is not None else shared_state()
        self._algo_local = algo if algo is not None else crypto.available_algo()
        self._algo: int | None = None
        self._keys: crypto.Keys | None = None
        self._timeout = timeout
        self._max_fails = max(1, max_fails)
        self._fails = 0
        self._closed = False
        self._hello_sent = False
        self._ready_lock = asyncio.Lock()
        self._hello_key = crypto.hello_key(access_key)
        self._hello_seed = crypto.hkdf_expand(crypto.psk(access_key), b"fcode-hello")
        self._tx_key = CounterState.key_for(self._local_fp)
        self._rx_key = CounterState.key_for(self._remote_fp)

    # ------------------------------------------------------------- 基本属性
    @property
    def local_fp(self) -> bytes:
        return self._local_fp

    @property
    def remote_fp(self) -> bytes:
        return self._remote_fp

    @property
    def algorithm(self) -> str:
        return crypto.algo_name(self._algo) if self._algo else "negotiating"

    @property
    def fails(self) -> int:
        return self._fails

    @property
    def closed(self) -> bool:
        return self._closed

    def peer_name(self) -> str:
        try:
            host, port = self._writer.get_extra_info("peername")[:2]
            return f"{host}:{port}"
        except Exception:
            return "?"

    # ------------------------------------------------------------- 建立
    @classmethod
    async def open(
        cls,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        access_key: str | bytes,
        *,
        local_fp: bytes | None = None,
        remote_fp: str | bytes | None = None,
        state: CounterState | None = None,
        algo: int | None = None,
        timeout: float | None = None,
        max_fails: int = MAX_FAILS,
    ) -> "Channel":
        """包装一条已连通的 TCP 流，并立即发送 hello。"""
        channel = cls(
            reader,
            writer,
            access_key,
            local_fp=local_fp,
            remote_fp=remote_fp,
            state=state,
            algo=algo,
            timeout=timeout,
            max_fails=max_fails,
        )
        await channel._send_hello()
        return channel

    async def __aenter__(self) -> "Channel":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close(send_bye=False)

    # ------------------------------------------------------------- 发送
    async def send(self, ftype: int, data: bytes = b"") -> None:
        if not 1 <= ftype <= 0xFF:
            raise ValueError(f"frame type out of range: {ftype}")
        if self._closed:
            raise ClosedError("channel closed")
        await self._ensure_ready()
        await self._write_frame(
            self._keys.k_tx,  # type: ignore[union-attr]
            self._keys.seed_tx,  # type: ignore[union-attr]
            self._algo,  # type: ignore[arg-type]
            ftype,
            bytes(data),
        )

    async def ping(self) -> None:
        await self.send(FT_PING)

    async def _send_hello(self) -> None:
        payload = self._local_fp + bytes([self._algo_local])
        await self._write_frame(
            self._hello_key, self._hello_seed, HELLO_ALGO, FT_HELLO, payload
        )
        self._hello_sent = True

    async def _write_frame(
        self, key: bytes, seed: bytes, algo: int, ftype: int, data: bytes
    ) -> None:
        counter = self._state.take_tx(self._tx_key)
        code = freshcode(seed, counter)
        length = pack.frame_len(len(data))
        aad = pack.aad(length, counter, code)
        plaintext = bytes([ftype]) + data
        ciphertext = crypto.seal(algo, key, crypto.nonce(counter), plaintext, aad)
        frame = datapack.build(counter, code, ciphertext).topack()
        self._writer.write(frame)
        await self._writer.drain()

    # ------------------------------------------------------------- 接收
    async def recv(self) -> tuple[int, bytes]:
        """读取一个业务帧，返回 ``(type, data)``；遇到攻击帧按熔断规则处理。"""
        if self._closed:
            raise ClosedError("channel closed")
        await self._ensure_ready()
        while True:
            try:
                ftype, data = await self._read_frame(
                    self._keys.k_rx,  # type: ignore[union-attr]
                    self._keys.seed_rx,  # type: ignore[union-attr]
                    self._algo,  # type: ignore[arg-type]
                    check_replay=True,
                )
            except (AuthError, ReplayError) as exc:
                self._fails += 1
                log.warning("peer %s frame rejected (%s), fail=%d", self.peer_name(), exc, self._fails)
                if self._fails >= self._max_fails:
                    await self.abort()
                    raise ProtocolError(
                        f"连续 {self._fails} 次认证/重放失败，已熔断（{self.peer_name()}）"
                    ) from exc
                continue
            self._fails = 0
            if ftype == FT_HELLO:
                continue
            return ftype, data

    async def _ensure_ready(self) -> None:
        if self._keys is not None and self._algo is not None:
            return
        async with self._ready_lock:
            if self._keys is not None and self._algo is not None:
                return
            await self._recv_hello()

    async def _recv_hello(self) -> None:
        ftype, data = await self._read_frame(
            self._hello_key, self._hello_seed, HELLO_ALGO, check_replay=False
        )
        if ftype != FT_HELLO:
            raise ProtocolError(f"expected hello frame, got type {ftype}")
        if len(data) != crypto.FP_LEN + 1:
            raise ProtocolError(f"malformed hello payload: {len(data)} bytes")
        remote_fp = data[: crypto.FP_LEN]
        remote_algo = data[crypto.FP_LEN]
        if self._expect_fp and remote_fp != self._remote_fp:
            raise AuthError(
                f"peer fingerprint changed: expected {self._remote_fp.hex()}, got {remote_fp.hex()}"
            )
        self._remote_fp = remote_fp
        self._rx_key = CounterState.key_for(remote_fp)
        self._keys = crypto.derive(self._access_key, self._local_fp, remote_fp)
        self._algo = crypto.negotiate_algo(self._algo_local, remote_algo)

    async def _read_frame(
        self, key: bytes, seed: bytes, algo: int, *, check_replay: bool
    ) -> tuple[int, bytes]:
        header = await self._read_exact(pack.HDR_LEN)
        (length,) = struct.unpack(pack.LEN_FMT, header)
        if length < pack.MIN_LEN or length > pack.MAX_LEN:
            raise FrameError(f"frame length out of range: {length}")
        body = await self._read_exact(length)
        try:
            frame = datapack(header + body).unpack()
        except FrameError as exc:
            raise ProtocolError(str(exc)) from exc
        counter = frame.counter
        if check_replay and not self._state.accept_rx(self._rx_key, counter):
            raise ReplayError(f"counter {counter} <= last_rx {self._state.rx_last(self._rx_key)}")
        expected = freshcode(seed, counter)
        if not hmac.compare_digest(frame.fcode, expected):
            raise AuthError("fcode mismatch (wrong access_key or fingerprint)")
        try:
            plaintext = crypto.open_(
                algo, key, crypto.nonce(counter), frame.ciphertext, pack.aad(length, counter, frame.fcode)
            )
        except ValueError as exc:
            raise AuthError(str(exc)) from exc
        if not plaintext:
            raise FrameError("empty plaintext")
        return plaintext[0], plaintext[1:]

    async def _read_exact(self, count: int) -> bytes:
        try:
            if self._timeout is None:
                return await self._reader.readexactly(count)
            return await asyncio.wait_for(self._reader.readexactly(count), self._timeout)
        except asyncio.IncompleteReadError as exc:
            raise ClosedError(f"peer closed after {len(exc.partial)}/{count} bytes") from exc
        except (ConnectionError, OSError) as exc:
            raise ClosedError(f"connection lost: {exc}") from exc

    # ------------------------------------------------------------- 关闭
    async def abort(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:
            pass

    async def close(self, *, send_bye: bool = True) -> None:
        if self._closed:
            return
        if send_bye and self._keys is not None:
            try:
                await asyncio.wait_for(self.send(FT_BYE), 1.0)
            except Exception:
                pass
        try:
            self._state.flush()
        except Exception:
            log.exception("failed to persist counter state")
        await self.abort()


# --------------------------------------------------------------------- 客户端
async def connect(
    host: str,
    port: int = PORT,
    access_key: str = "",
    *,
    remote_fp: str | bytes | None = None,
    state: CounterState | None = None,
    timeout: float | None = None,
    connect_timeout: float = 10.0,
) -> Channel:
    """建立到 ``host:port`` 的加密通道。"""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), connect_timeout
    )
    try:
        return await Channel.open(
            reader, writer, access_key, remote_fp=remote_fp, state=state, timeout=timeout
        )
    except BaseException:
        writer.close()
        raise


async def request(
    host: str,
    port: int = PORT,
    access_key: str = "",
    payload: bytes = b"",
    *,
    remote_fp: str | bytes | None = None,
    state: CounterState | None = None,
    timeout: float | None = 60.0,
    connect_timeout: float = 10.0,
) -> bytes:
    """一问一答：发一个 DATA 帧，等第一个 DATA 帧回复。"""
    channel = await connect(
        host,
        port,
        access_key,
        remote_fp=remote_fp,
        state=state,
        timeout=timeout,
        connect_timeout=connect_timeout,
    )
    try:
        await channel.send(FT_DATA, payload)
        while True:
            ftype, data = await channel.recv()
            if ftype == FT_DATA:
                return data
            if ftype == FT_BYE:
                raise ClosedError("peer closed before reply")
    finally:
        await channel.close()


# --------------------------------------------------------------------- 服务端
Handler = Callable[[bytes], Awaitable[bytes | None] | bytes | None]


async def _call(handler: Handler, data: bytes) -> bytes | None:
    """容许 handler 写同步函数：await 可等待对象，直接返回普通值。"""
    result = handler(data)
    if inspect.isawaitable(result):
        return await cast(Awaitable[bytes | None], result)
    return result


async def serve(
    handler: Handler,
    host: str = "0.0.0.0",
    port: int = PORT,
    access_key: str = "",
    *,
    state: CounterState | None = None,
    timeout: float | None = None,
    local_fp: bytes | None = None,
) -> asyncio.AbstractServer:
    """起一个 peer 服务；每个 DATA 帧交给 ``handler``，返回 bytes 即回包。"""
    shared = state if state is not None else shared_state()

    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        channel: Channel | None = None
        try:
            channel = await Channel.open(
                reader,
                writer,
                access_key,
                local_fp=local_fp,
                state=shared,
                timeout=timeout,
            )
            while True:
                ftype, data = await channel.recv()
                if ftype == FT_BYE:
                    break
                if ftype == FT_PING:
                    await channel.send(FT_PING)
                    continue
                if ftype != FT_DATA:
                    continue
                reply = await _call(handler, data)
                if reply is not None:
                    await channel.send(FT_DATA, reply)
        except (ClosedError, ProtocolError, ConnectionError, OSError):
            pass
        except Exception:
            log.exception("peer connection failed")
        finally:
            if channel is not None:
                await channel.abort()
            else:
                try:
                    writer.close()
                except Exception:
                    pass

    return await asyncio.start_server(client, host, port)


class PeerServer:
    """``serve`` 的同步包装（端口在构造时就绑定，便于上层立即发现占用）。

    ```python
    srv = PeerServer(handler, "0.0.0.0", 1124, access_key)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.shutdown(); srv.server_close()
    ```
    """

    def __init__(
        self,
        handler: Handler,
        host: str = "0.0.0.0",
        port: int = PORT,
        access_key: str = "",
        *,
        state: CounterState | None = None,
        timeout: float | None = None,
    ) -> None:
        self.handler = handler
        self.host = host
        self.access_key = access_key
        self.state = state if state is not None else shared_state()
        self.timeout = timeout
        self._sock = socket.create_server((host, port), backlog=128)
        self._sock.setblocking(False)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.AbstractServer | None = None

    @property
    def port(self) -> int:
        return int(self._sock.getsockname()[1])

    def serve_forever(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._server = loop.run_until_complete(
                asyncio.start_server(self._client, sock=self._sock)
            )
            loop.run_forever()
        finally:
            server, self._server = self._server, None
            if server is not None:
                server.close()
                loop.run_until_complete(server.wait_closed())
            self._close_sock()
            loop.close()
            self._loop = None

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        channel: Channel | None = None
        try:
            channel = await Channel.open(
                reader, writer, self.access_key, state=self.state, timeout=self.timeout
            )
            while True:
                ftype, data = await channel.recv()
                if ftype == FT_BYE:
                    break
                if ftype == FT_PING:
                    await channel.send(FT_PING)
                    continue
                if ftype != FT_DATA:
                    continue
                reply = await _call(self.handler, data)
                if reply is not None:
                    await channel.send(FT_DATA, reply)
        except (ClosedError, ProtocolError, ConnectionError, OSError):
            pass
        except Exception:
            log.exception("peer connection failed")
        finally:
            if channel is not None:
                await channel.abort()
            else:
                try:
                    writer.close()
                except Exception:
                    pass

    def shutdown(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        else:
            self._close_sock()

    def server_close(self) -> None:
        self.shutdown()
        if self._loop is None:
            self._close_sock()

    def _close_sock(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass
