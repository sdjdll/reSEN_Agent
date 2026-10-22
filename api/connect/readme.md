# api/connect

裸 TCP 上的加密帧通道，实现 [`skills/resen-tcp-v1.md`](../../skills/resen-tcp-v1.md) 的 v1 协议。

> 它只做**传输**：帧布局、密钥派生、counter、握手、防重放、熔断。
> 业务语义（`messages` JSON、模型调用、工具执行）仍由 [`../peer_api.py`](../peer_api.py) 负责。

## 分层

| 模块 | 内容 |
| --- | --- |
| [`pack.py`](pack.py) | `[len 4B][counter 8B][fcode 8B][AEAD 密文+tag]`、`freshcode()`、`datapack`、长度常量 |
| [`crypto.py`](crypto.py) | `fp`（机器特征）、HKDF 密钥树、nonce、AEAD 后端与算法协商 |
| [`chacha.py`](chacha.py) | 纯 Python ChaCha20-Poly1305（RFC 8439），无第三方依赖兜底 |
| [`counter.py`](counter.py) | 发送 counter 预留式持久化、接收水位 `last_rx`、进程级共享状态 |
| [`session.py`](session.py) | `Channel` 收发帧、hello 握手、熔断、`connect/request/serve/PeerServer` |

## 快速使用

### 一问一答

```python
from api.connect import session

reply = await session.request("10.0.0.2", 1124, "my-key", b"payload")
```

### 服务端

```python
async def handler(data: bytes) -> bytes:
    return data.upper()

server = await session.serve(handler, "0.0.0.0", 1124, "my-key")
try:
    async with server:
        await server.serve_forever()
finally:
    server.close()
```

同步包装（线程里跑、构造时就绑定端口）：

```python
srv = session.PeerServer(handler, "0.0.0.0", 1124, "my-key")
threading.Thread(target=srv.serve_forever, daemon=True).start()
...
srv.shutdown()
srv.server_close()
```

### 长连接

```python
async with await session.connect("10.0.0.2", 1124, "my-key") as ch:
    await ch.send(session.FT_DATA, b"first")
    ftype, data = await ch.recv()
    await ch.ping()                 # 保活，对端回一个 PING
```

帧类型：`FT_DATA=1`（业务）、`FT_HELLO=2`（握手，勿手动发）、`FT_PING=3`、`FT_BYE=4`。

### 身份钉扎（可选）

`connect()` / `Channel.open()` 支持 `remote_fp=<16B 或 hex>`：传了就会校验对端指纹，
不匹配直接 `AuthError`。指纹可以存进 `configure/connect.json` 供下次连接使用。

## 握手

1. 双方建连后各发一帧 `HELLO`：载荷 `fp(16) || 主算法号(1)`，用固定 `K_hello` 加密。
2. 收到后派生方向密钥（`access_key` × 双方 `fp` × 方向），并协商主算法：
   双方都有 `cryptography` → **AES-256-GCM**，否则 **ChaCha20-Poly1305**。
3. 之后的帧都是 `type || data` 放进 AEAD 密文，头部 `len/counter/fcode` 进 AAD。

hello 是建连即发，不占额外业务往返。

> 想用 AES-256-GCM 加速：`pip install cryptography`（可选；双方都装才会协商成功，否则一律 ChaCha20-Poly1305）。

## 状态与配置

| 文件 | 用途 |
| --- | --- |
| `configure/peer_identity.json` | 安装实例 UUID（隔离同机多实例的 counter 空间） |
| `configure/peer/<install_uuid>.json` | 每个方向的 `saved_next` / `last_rx`；预留 1024 个号后落盘 |

两个文件都在 `.gitignore` 的 `*/*.json` 覆盖范围内，不会进版本库。

## 自检

```bash
PYTHONPATH=. python -m api.connect.selftest
```

覆盖：RFC 8439 三个官方向量、帧往返/非法帧、派生对称性、重启不回退、
重放拦截、篡改熔断、1MB 边界与同步服务端。

## 明确不做（v2）

- 无 DH → 无前向保密；`access_key` 泄露即可解历史流量
- 不隐藏长度与时序（无 padding / 固定速率）
- 同一 `install_uuid` 的多进程不共享内存 counter（建议一进程一实例）
