"""reSEN TCP 加密协议 v1 的可复用零件。

分层（从下到上）：

| 模块 | 职责 |
| --- | --- |
| ``pack`` | 帧布局 ``[len][counter][fcode][AEAD]``、``freshcode``、长度常量 |
| ``crypto`` | ``fp``、HKDF 密钥树、nonce、AEAD（AES-GCM / ChaCha20-Poly1305） |
| ``counter`` | 发送 counter 的预留式持久化、接收水位 ``last_rx`` |
| ``session`` | 握手、帧收发、防重放、熔断、``connect/request/serve`` |

自检：``python -m api.connect.selftest``
"""

from .counter import COUNTER_RESERVE, CounterState
from .crypto import (
    FP_LEN,
    Keys,
    algo_name,
    available_algo,
    derive,
    fingerprint,
    install_uuid,
    negotiate_algo,
)
from .pack import (
    FCODE_LEN,
    FRESH_LEN,
    HDR_LEN,
    MAX_DATA,
    MAX_FRAME,
    MAX_LEN,
    MIN_LEN,
    TAG_LEN,
    FrameError,
    aad,
    datapack,
    freshcode,
)
from .session import (
    FT_BYE,
    FT_DATA,
    FT_HELLO,
    FT_PING,
    MAX_FAILS,
    PORT,
    AuthError,
    Channel,
    ClosedError,
    PeerServer,
    ProtocolError,
    ReplayError,
    backoff_delay,
    connect,
    local_fingerprint,
    request,
    serve,
)

__all__ = [
    "AuthError",
    "COUNTER_RESERVE",
    "Channel",
    "ClosedError",
    "CounterState",
    "FCODE_LEN",
    "FP_LEN",
    "FRESH_LEN",
    "FT_BYE",
    "FT_DATA",
    "FT_HELLO",
    "FT_PING",
    "FrameError",
    "HDR_LEN",
    "Keys",
    "MAX_DATA",
    "MAX_FAILS",
    "MAX_FRAME",
    "MAX_LEN",
    "MIN_LEN",
    "PORT",
    "PeerServer",
    "ProtocolError",
    "ReplayError",
    "TAG_LEN",
    "aad",
    "algo_name",
    "available_algo",
    "backoff_delay",
    "connect",
    "datapack",
    "derive",
    "fingerprint",
    "freshcode",
    "install_uuid",
    "local_fingerprint",
    "negotiate_algo",
    "request",
    "serve",
]
