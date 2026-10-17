"""api.peer_api：peer 协议的两端（同一个模块里既收也发）。

- 收（本模块的主体）：`serve()` 起 HTTP 服务（默认监听 [`PORT`][api.peer_api.PORT] = 1124），
  收别的 agent 的 peer_agent 工具发来的请求 -> 交给本地 agent 跑一轮 -> 把回复的 ccmp 返回去
- 发：`chat()` 是 peer_agent 工具用的客户端（协议的另一端），把 `list[ccmp]` POST 出去拿回一条 ccmp

约定（两端一致）：
- 请求：`POST <任意路径>`，JSON 体 = `{"messages": [...], "access_key": "..."}`
- 响应：一条 ccmp（JSON）；出错时 HTTP 非 2xx + `{"error": "..."}`
- 端口：[`PORT`][api.peer_api.PORT] = 1124（客户端 url 没写端口就补它，服务端默认监听它）
"""
import argparse
import asyncio
import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import urlsplit, urlunsplit

import httpx2
from openai import AsyncOpenAI as openai
from openai.types.chat import ChatCompletionMessageParam as ccmp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 让 python api/peer_api.py 也能找到 agentapi/file

from agentapi.agentapi import agent_api
from file.dick import dick
from file.jsonfile import jsonfile

_CONFIG = Path(__file__).resolve().parents[1] / "configure" / "config.json"
_CONNECT = Path(__file__).resolve().parents[1] / "configure" / "connect.json"
"""已连接过的设备清单（`peer_agent` 连上哪台就记一笔），格式见 [`devices`][api.peer_api.devices]。"""

NO_ACCESS_KEY = "NO-ACCESS_KEY"
"""access_key 的默认值：还是这个占位就说明没配，调用会直接报错。"""

NO_APIURL = "NO-APIURL"
"""url 的默认值：还是这个占位就说明没配，调用会直接报错。"""

PORT = 1124
"""peer 服务的默认端口：url 里没写端口时补这个（显式写了端口就用写的，443/80 也要显式写）。"""


class api_error(Exception):
    """网络 api 的异常（url/access_key 没配、请求失败、响应不对），由上层上屏。"""


def with_port(url: str) -> str:
    """把 url 里缺的端口补成默认端口（显式写了端口就原样返回）。"""
    parts = urlsplit(url)
    if parts.port is not None:
        return url
    if not parts.hostname:
        raise api_error(f"url 里没有主机名：{url!r}")
    # 直接往 netloc 尾巴上接：userinfo（user:pass@）与 IPv6 的方括号都能原样保留
    return urlunsplit((parts.scheme, f"{parts.netloc}:{PORT}", parts.path, parts.query, parts.fragment))


# ---------------------------------------------------------------- 已连接过的设备（connect.json）

BLANK_DEVICE: dict[str, Any] = {"name": "", "desc": "", "host": "", "port": "", "tag": []}
"""设备清单里一条的形状（`name`/`desc`/`tag` 是给人填的，`host`/`port` 是连过的地址）。"""


def devices() -> list[dict[str, Any]]:
    """读已连接过的设备清单（`configure/connect.json`）：`[{name, desc, host, port, tag}, ...]`。

    文件不存在/是空的/坏了都当成空清单 —— 这是本地的便利记录，坏了不该拦住工具调用。
    """
    try:
        book: Any = jsonfile(str(_CONNECT)).read()
    except Exception:
        return []
    return [entry for entry in book if isinstance(entry, dict)] if isinstance(book, list) else []


def _target(url: str) -> tuple[str, int]:
    """url -> (host, port)：端口走和 `chat()` 一样的默认端口逻辑。"""
    parts = urlsplit(with_port(url))
    if not parts.hostname:
        raise api_error(f"url 里没有主机名：{url!r}")
    return parts.hostname, parts.port or PORT


def remember(url: str, name: str = "", desc: str = "", tag: list[str] | None = None) -> tuple[bool, dict[str, Any]]:
    """把一台连上的设备记进 `configure/connect.json`，返回 `(是不是新条目, 该条目)`。

    - 按 `host:port` 去重：同一台只留一条，重复连不算新条目
    - 清单里全是空的那条骨架（手写的模板）会被就地填掉，不另起一行
    - `name`/`desc` 传了非空值就刷新（模型可以给设备起个名），`tag` 做并集；不传就只留地址
    """
    host, port = _target(url)
    book = devices()

    def matched(entry: dict[str, Any]) -> bool:
        """同一台设备：host 与 port 都一致（port 存的是字符串，顺手容错写成数字的那种）。"""
        return str(entry.get("host")) == host and str(entry.get("port")) == str(port)

    entry = next((entry for entry in book if matched(entry)), None)
    if entry is not None:
        if name:
            entry["name"] = name
        if desc:
            entry["desc"] = desc
        tags = entry.get("tag")
        if not isinstance(tags, list):
            tags = entry["tag"] = []
        tags.extend(item for item in (tag or []) if item not in tags)
        fresh = False
    else:
        blank = next((entry for entry in book if not str(entry.get("host")) and not str(entry.get("port"))), None)
        fresh = True
        if blank is None:
            entry = dict(BLANK_DEVICE)
            book.append(entry)
        else:
            entry = blank
        entry.update({"name": name or host, "desc": desc, "host": host, "port": str(port), "tag": list(tag or [])})
    jsonfile(str(_CONNECT)).write(book)      # 原子写（临时文件 + replace）：两个线程同时记也不会写坏
    return fresh, entry


def _check(usrmsg: list[ccmp], access_key: str, url: str) -> None:
    """调用前先挡住最明显的几种错，省得发一次注定失败的请求。"""
    if not url or url == NO_APIURL:
        raise api_error(f"没有可用的 url（当前 {url!r}）：把它作为参数传给 chat(url=...)")
    if access_key == NO_ACCESS_KEY:
        raise api_error(f"access_key 还是占位值 {NO_ACCESS_KEY!r}：请传入真正的 key（不需要鉴权就传空串）")
    if not usrmsg:
        raise api_error("usrmsg 是空的：至少要有一条消息")


def _parse(resp: httpx2.Response) -> ccmp:
    """响应体 -> ccmp：本身就是一条 ccmp，或者套在 message 里。"""
    try:
        data: Any = resp.json()
    except Exception as e:
        raise api_error(f"响应不是合法 JSON（{type(e).__name__}）：{resp.text[:200]}") from e
    if isinstance(data, dict) and isinstance(data.get("message"), dict):
        data = data["message"]
    if not isinstance(data, dict) or not any(k in data for k in ("role", "content", "tool_calls")):
        raise api_error(f"响应不是一条 ccmp：{str(data)[:200]}")
    return cast(ccmp, data)


async def chat(
    usrmsg: list[ccmp],
    access_key: str = NO_ACCESS_KEY,
    url: str = NO_APIURL,
    timeout: float = 60.0,
    client: httpx2.AsyncClient | None = None,
) -> ccmp:
    """把一份 `list[ccmp]` 发给远端，返回远端给的 ccmp（非流式）。

    - usrmsg      上下文（list[ccmp]），原样放进请求体的 `messages`
    - access_key  鉴权串，默认占位值 `NO-ACCESS_KEY`（占位值会被当成没配，直接报错）
    - url         远端地址，由调用方传；没写端口就用默认端口 [`PORT`][api.peer_api.PORT]
    - timeout     单次请求超时（秒）
    - client      可选：复用自己的 httpx2.AsyncClient（便于复用连接/注入 mock），
                  不传就每次新建一个

    失败一律抛 [`api_error`][api.peer_api.api_error]，原始异常挂在 `__cause__` 上。
    """
    _check(usrmsg, access_key, url)
    url = with_port(url)
    payload = {"messages": usrmsg, "access_key": access_key}
    try:
        if client is None:
            async with httpx2.AsyncClient(timeout=timeout) as own:
                resp = await own.post(url, json=payload)
        else:
            resp = await client.post(url, json=payload)
    except Exception as e:  # 连不上 / 超时 / 协议错……
        # ReadError / RemoteProtocolError = TCP 通了但远端没回响应就断开：
        # 多半是服务端处理时崩了，或者路径/协议不对（这种最难查，所以把提示写进错误里）
        tip = "（远端在返回响应前断开：看远端服务日志、确认路径与协议）" if isinstance(e, (httpx2.ReadError, httpx2.RemoteProtocolError)) else ""
        raise api_error(f"{type(e).__name__}: {e}（请求 {url}）{tip}") from e
    if resp.status_code // 100 != 2:
        raise api_error(f"HTTP {resp.status_code}：{resp.text[:200]}")
    return _parse(resp)


# ---------------------------------------------------------------- 接收端（服务端）

_MAX_ROUNDS = 8
"""一次请求里最多让本地模型做几轮工具调用（防死循环）。"""


async def _answer(api: agent_api, messages: list[ccmp], use_tools: bool, log: Callable[[str], None] = print) -> ccmp:
    """让本地模型把这份对话答完（要工具就本地执行），返回最后一条 ccmp。"""
    chain = list(messages)
    reply: ccmp = chain[-1]
    for _ in range(_MAX_ROUNDS):
        reply = await api.chat(chain)
        calls = reply.get("tool_calls") or []
        if not calls or not use_tools:
            return reply
        log("远端请求触发本地工具：" + ", ".join(str(c.get("function", {}).get("name")) for c in calls))
        chain.append(reply)
        chain.extend(await asyncio.to_thread(api.tool, reply))   # 工具执行器是同步的，别卡住事件循环
    return reply


class _Server(ThreadingHTTPServer):
    """把「谁来答 / 拿什么 key 校验」挂在 server 上，handler 通过 self.server 取。"""

    api: agent_api
    access_key: str
    use_tools: bool


class _Handler(BaseHTTPRequestHandler):
    """peer 协议的服务端：收一份 list[ccmp]，把本地 agent 的回复（一条 ccmp）返回去。"""

    server_version = "reSEN-peer/1"

    def do_POST(self) -> None:
        """POST 一份 list[ccmp] -> 本地 agent 答一条 ccmp。路径不参与判断（客户端给什么路径都收）。"""
        srv = cast(_Server, self.server)
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            data = json.loads(raw or b"{}")
        except Exception as e:
            self._reply(400, {"error": f"请求体不是合法 JSON：{type(e).__name__}: {e}"})
            return
        if not isinstance(data, dict):
            self._reply(400, {"error": "请求体必须是一个 JSON object"})
            return
        messages = data.get("messages")
        if not isinstance(messages, list) or not all(isinstance(msg, dict) for msg in messages):
            self._reply(400, {"error": "'messages' 必须是 [ccmp, ...]（至少一条）"})
            return
        if data.get("access_key") != srv.access_key:
            self.log_message("access_key 不对，拒绝 %s", self.client_address)
            self._reply(403, {"error": "access_key 不对"})
            return
        try:
            reply = asyncio.run(_answer(srv.api, cast(list[ccmp], messages), srv.use_tools, self._log))
        except BaseException as e:
            # 连 KeyboardInterrupt / CancelledError 也兜住：绝不因为处理出错就把连接无声掐断
            # （客户端只会看到 "Server disconnected without sending a response"，几乎无法定位）
            self.log_message("应答失败：%s: %s", type(e).__name__, e)
            traceback.print_exc()   # 服务端日志里留完整堆栈
            self._reply(500, {"error": f"{type(e).__name__}: {e}"})
            return
        self._reply(200, reply)

    def do_GET(self) -> None:
        """GET 只是给人看的：明确告诉对方该 POST 什么。"""
        self._reply(405, {"error": '请用 POST，JSON 体 = {"messages": [...], "access_key": "..."}'})

    def _log(self, message: str) -> None:
        self.log_message("%s", message)

    def _reply(self, code: int, payload: Any) -> None:
        """把 payload 序列化成 JSON 发回去（payload 可能是 ccmp，也可能是 {"error": ...}）。"""
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def tools_enabled() -> bool:
    """config.json 里的开关键 `config.peer_tools`（缺省 True）：远端请求能不能驱动本地工具。"""
    cfg = dick(str(_CONFIG)).any("config", {})
    return bool(cfg.get("peer_tools", True)) if isinstance(cfg, dict) else True


def make_server(host: str = "0.0.0.0", port: int = PORT, key: str | None = None,
                use_tools: bool | None = None) -> _Server:
    """搭好（但**不**启动）peer 服务：读配置、校验 access_key、组装 handler。

    - key       访问密钥，None 就取 config.json 的 `access_key`
    - use_tools 远端请求能不能驱动本地工具；None 就看 config.json 的 `config.peer_tools`（缺省 True）

    要自己控制生命周期（测试 / 嵌到别的进程里）就用它，起停分别 `srv.serve_forever()` / `srv.shutdown()`；
    命令行直接跑用 serve()。
    """
    cfg = dick(str(_CONFIG))
    access_key = key or cfg.str("access_key", NO_ACCESS_KEY)
    if access_key == NO_ACCESS_KEY:
        raise api_error(f"access_key 还是占位值 {NO_ACCESS_KEY!r}：用 --key 传一个，或把 key 写进 {_CONFIG}")
    srv = _Server((host, port), _Handler)
    srv.api = agent_api(openai(base_url=cfg.str("baseurl"), api_key=cfg.str("apikey")), cfg.str("modelname"))
    srv.access_key = access_key
    srv.use_tools = tools_enabled() if use_tools is None else use_tools
    return srv


def serve(host: str = "0.0.0.0", port: int = PORT, key: str | None = None,
          use_tools: bool | None = None) -> None:
    """起 peer 服务并阻塞（Ctrl-C 停）：读 config.json 的 apikey/baseurl/modelname 跑本地模型。

    - host/port  监听地址，默认对外可达的 `0.0.0.0:PORT`
    - key        访问密钥，默认取 config.json 的 `access_key`；还是占位值就拒绝启动
    - use_tools  是否允许远端请求驱动本地工具；默认看 config.json 的 `config.peer_tools`
                 （工具是真的在你机器上执行，想关就关）

    每次请求都是独立的（用请求里带的那份对话答一轮，不碰本地 memory），
    所以要连续对话时由调用方把整段 messages 带上。
    """
    srv = make_server(host, port, key, use_tools)
    print(f"peer 服务已启动：http://{host}:{port} （任一路径都收 POST）")
    print(f"  access_key：{'来自 --key' if key else '取自 config.json'}；"
          f"远端驱动本地工具：{'允许' if srv.use_tools else '禁止'}")
    print(f"  本地模型：{srv.api.model_name}（来自 config.json；每次请求独立应答，不碰本地 memory）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()


def probe(url: str, key: str | None = None, text: str = "ping") -> int:
    """按 peer 协议打**一发**请求，把原始结果（状态行/响应头/响应体）打出来。诊断用，不起服务。

    比 curl 好在：请求体和 `chat()` 完全一致（同一个端口补齐、同样的字段），
    能直接回答“对端到底是谁 / 回没回话 / 路径与协议对不对”。返回退出码：2xx -> 0，其余 -> 1。
    """
    target = with_port(url)
    if key is None:
        key = dick(str(_CONFIG)).str("access_key", NO_ACCESS_KEY)
    body = {"messages": [{"role": "user", "content": text}], "access_key": key}
    print(f"POST {target}")
    print(f"  body: {json.dumps(body, ensure_ascii=False)}")

    async def send() -> httpx2.Response:
        async with httpx2.AsyncClient(timeout=30.0) as client:
            return await client.post(target, json=body)

    try:
        resp = asyncio.run(send())
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e}")
        return 1
    print(f"  HTTP {resp.status_code}")
    for name in ("server", "content-type", "content-length"):
        print(f"  {name}: {resp.headers.get(name, '(无)')}")
    print(f"  body: {resp.text[:500]}")
    # BaseHTTPRequestHandler 会在 Server 头后面接 Python 版本，所以只比前缀
    if not resp.headers.get("server", "").startswith(_Handler.server_version):
        print(f"  提示：Server 头不是本项目的 peer 服务（应为 {_Handler.server_version} 开头）：1124 上跑的可能是别的东西")
    return 0 if resp.status_code // 100 == 2 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="peer 服务端：接收 peer_agent 工具发来的请求，交给本地 agent 应答")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0，对外可达）")
    parser.add_argument("--port", type=int, default=PORT, help=f"监听端口（默认 {PORT}）")
    parser.add_argument("--key", default=None, help="访问密钥（默认取 config.json 的 access_key）")
    parser.add_argument("--no-tools", action="store_true", help="禁止远端请求驱动本地工具（默认看 config.json 的 config.peer_tools）")
    parser.add_argument("--probe", metavar="URL", help="只对着 URL 打一发请求看结果（诊断用，不起服务）")
    parser.add_argument("--text", default="ping", help="--probe 时发过去的那句话")
    args = parser.parse_args()
    if args.probe:
        raise SystemExit(probe(args.probe, args.key, args.text))
    try:
        serve(args.host, args.port, args.key, False if args.no_tools else None)
    except (api_error, OSError) as e:
        print(f"启动失败：{type(e).__name__}: {e}")
        raise SystemExit(1)
