# api
> `peer_api` 是 peer 协议的**两端**：收（服务端，本模块主体）+ 发（`peer_agent` 工具用的客户端）

与 [agentapi](../agentapi/readme.md) 的分工：
- `agentapi` 是本地 agent 核心（OpenAI 兼容接口 + 元工具/热插拔工具）
- `api` 只负责网络：[`serve()`](#接收端服务端) 收别人发来的对话交给本地 agent 答，[`chat()`](#await-chatusrmsg-access_keyno_access_key-urlno_apiurl-timeout600-clientnone---ccmp) 把对话发给别的 peer

## [peer_api.py](peer_api.py)

### `await chat(usrmsg, access_key=NO_ACCESS_KEY, url=NO_APIURL, timeout=60.0, client=None) -> ccmp`
- **usrmsg**：上下文，`list[ccmp]`，原样作为请求体的 `messages`
- **access_key**：鉴权串，默认占位值 `"NO-ACCESS_KEY"`；**还是占位值就当没配，直接报错**（不需要鉴权传空串）
- **url**：远端地址，由调用方传入（默认占位值 `"NO-APIURL"`，同样是没配就报错）；
  **没写端口就补默认端口 `PORT`（1124）**，写了就用写的（443/80 也要显式写）
- **timeout**：单次请求超时（秒）
- **client**：可选，复用自己的 `httpx2.AsyncClient`；不传就每次新建一个

```python
from api.peer_api import chat

reply = await chat(
    [{"role": "user", "content": "你好"}],
    access_key="my-key",
    url="http://192.168.1.7/chat",     # 实际请求 http://192.168.1.7:1124/chat
)
print(reply)   # {"role": "assistant", "content": "..."}
```

### `with_port(url) -> str`
把 url 里缺的端口补成 `PORT`，显式写了端口就原样返回；没有主机名则抛 `api_error`。

### 端口
- `PORT = 1124`：peer 服务的默认端口，客户端 `chat()` 内部对 `url` 走 `with_port()` 补齐，服务端默认监听它

## 接收端（服务端）

### `serve(host="0.0.0.0", port=PORT, key=None, use_tools=True)`
起服务并阻塞（Cmd-C/Ctrl-C 停）。命令行入口一样：
```bash
python -m api.peer_api                              # 0.0.0.0:1124，access_key 取自 config.json
python -m api.peer_api --port 9000 --key my-key     # 换端口/直接给 key
python -m api.peer_api --no-tools                   # 强制关闭（默认看 config.json 的 peer_tools）
python -m api.peer_api --probe http://host/chat --key k   # 只打一发请求看结果（诊断用，不起服务）
```
- 读 `configure/config.json` 的 `apikey`/`baseurl`/`modelname` 跑**本地模型**；`access_key` 用来校验来客
  （还是占位值 `NO_ACCESS_KEY` 就拒绝启动，退码 1）
- 收到请求后：把请求里的 `messages` 交给本地模型跑一轮（**要工具就地执行**，最多 `_MAX_ROUNDS = 8` 轮），
  把最后那条 ccmp 当响应返回；路径不参与判断（客户端写什么路径都收）
- **每次请求都是独立的**：用请求自带的那份对话答一轮，不碰本地 memory；要连续对话就由调用方把整段 `messages` 带上
- 请求进错（非 JSON、`messages` 不对）回 `400`，`access_key` 不对回 `403`，本地模型报错回 `500`，
  非 POST 回 `405` —— body 都是 `{"error": "..."}`（客户端会把它显示成 `HTTP 403：...`）
- **处理时无论出什么事都回响应**（连 `KeyboardInterrupt`/`CancelledError` 都兜住，并把完整堆栈写到日志）：
  不这样做的话连接会被默默关掉，客户端只能看到 `RemoteProtocolError: Server disconnected...`，极难定位

### `probe(url, key=None, text="ping") -> int`
按 peer 协议打**一发**请求并打印状态行/响应头/响应体（`--probe` 就是它，退码 2xx=0 其余=1）。
重点看 `Server:` 头：是本项目的服务就是 `reSEN-peer/1 ...` 开头，否则 1124 上跑的很可能是别的东西。

### 错误 → 先查什么
| 报错 | 含义 | 看哪里 |
|---|---|---|
| `api_error: access_key 还是占位值` | 客户端自己拦下的（没发请求） | 传一个真 key；“不需要鉴权”要显式传空串 |
| `ConnectError: All connection attempts failed` | host:port 上没人接受连接 | 对端服务起没起、端口对不对 |
| `ConnectError: ... Name or service not known` | 域名解析不了 | 主机名/DNS |
| `ReadError` / `RemoteProtocolError: Server disconnected...` | TCP 通了但远端没回响应就断开 | **远端服务的日志/堆栈**；路径与协议对不对 |
| `ConnectTimeout` | 包被丢（防火墙） | 放行规则、网段 |
| `HTTP 4xx/5xx：{...}` | 连上了，被对端拒/对端出错 | 响应体里的 `error` |

### `make_server(host, port, key, use_tools) -> _Server`
只搭好不启动（起停自己控制：`srv.serve_forever()` / `srv.shutdown()`，也能嵌到别的进程里）。

> ⚠️ 默认监听 `0.0.0.0`，而“远端能不能驱动本地工具”由 config.json 里的开关键决定：
> `"peer_tools": true | false`（缺省 **true**）。true 时拿到 `access_key` 的一方可以让本地模型
> 在你机器上执行工具，只在你信任的网络里跑；CLI 的 `--no-tools` 可以无视配置强制关。
> 这个键在 f2 的设置面板里能直接改（`core.settings()` 列了它），**改完要重启 TUI/服务才生效**（启动时读一次）。

> [TUI](../tui/main.py)（`python tui/main.py`）启动时会自动调 `make_server` 起一份带 `0.0.0.0:PORT` 的，
> 退出时关掉；`access_key` 还是占位值或 1124 被占就只在聊天区提一句、不影响用 TUI。
> （所以手动跑 CLI 起服务时，记得 TUI 那边已经把 1124 占了）
>
> 另有一个开关键 `"peer_serve": true | false`（缺省 **true**）决定 TUI 启动时起不起这份服务；
> 在 `^p` 的 **peer 栏**里可以当场开关服务（状态写在行里，回车就切，端口即时释放/占用），
> 而 `peer_serve`（下次启动生效）只在 `f2` 设置表格里改。两者都不影响本机自己聊天。

## 已连接过的设备（[`connect.json`](../configure/connect.json)）

`peer_agent` 每连上一台远端，就按下面这个格式记一笔；同一台（`host:port` 相同）只留一条。

```json
[
    {"name": "看板机", "desc": "车间的显示器", "host": "10.10.10.100", "port": "9999", "tag": ["lab"]}
]
```

- `name`/`desc`/`tag` 是给人看的：工具调用可以带上（`name`/`desc`/`tag` 参数），不传就只填 `host`/`port`，
  `name` 默认用主机名；重连时传了的字段会刷新、`tag` 取并集
- 地址里的端口走和 `chat()` 一样的默认端口逻辑（没写就是 [`PORT`](#端口)）
- 文件不存在/空/写坏了都当成空清单，不拦工具调用；清单里全是空的那条骨架（手写模板）会被就地填掉

```python
await chat(...)                                  # 真正发请求
peer_api.devices()                               # -> [{name, desc, host, port, tag}, ...]
peer_api.remember(url, name="客厅机", tag=["home"])  # -> (是不是新条目, 该条目)
```

### 协议
- 请求：`POST url`，JSON 体 = `{"messages": [...], "access_key": "..."}`
- 响应：一条 ccmp；也接受套了一层的 `{"message": {...}}`

### 错误
全部抛 `api_error`（原始异常挂在 `__cause__`），覆盖：url/access_key 是占位值、`usrmsg` 为空、
url 没有主机名、连不上/超时/协议错、HTTP 非 2xx、响应不是合法 JSON、响应不是一条 ccmp。

## 谁在用
- 发：[元工具 peer_agent](../agentapi/meta_tools/peer_agent.py) —— 把一份 `list[ccmp]` 发给远端 peer agent 协作，
  连上后把对方记进 `connect.json`
- 收：远端 peer 的 `peer_agent` 工具打到本机 `peer_api.serve()` 上，本地模型代你应答
