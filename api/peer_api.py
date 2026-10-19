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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentapi.agentapi import agent_api
from file.dick import dick
from file.jsonfile import jsonfile
""" todolist
    todo: http -> https
    todo: 自定义网段/端口
"""
_CONFIG = Path(__file__).resolve().parents[1] / "configure" / "config.json"
_CONNECT = Path(__file__).resolve().parents[1] / "configure" / "connect.json"

NO_ACCESS_KEY = "NO-ACCESS_KEY"

NO_APIURL = "NO-APIURL"

PORT = 1124


class api_error(Exception):
    pass


def with_port(url: str) -> str:
    parts = urlsplit(url)
    if parts.port is not None:
        return url
    if not parts.hostname:
        raise api_error(f"url 里没有主机名：{url!r}")
    return urlunsplit((parts.scheme, f"{parts.netloc}:{PORT}", parts.path, parts.query, parts.fragment))



BLANK_DEVICE: dict[str, Any] = {"name": "", "desc": "", "host": "", "port": "", "tag": []}


def devices() -> list[dict[str, Any]]:
    try:
        book: Any = jsonfile(str(_CONNECT)).read()
    except Exception:
        return []
    return [entry for entry in book if isinstance(entry, dict)] if isinstance(book, list) else []


def _target(url: str) -> tuple[str, int]:
    parts = urlsplit(with_port(url))
    if not parts.hostname:
        raise api_error(f"url 里没有主机名：{url!r}")
    return parts.hostname, parts.port or PORT


def remember(url: str, name: str = "", desc: str = "", tag: list[str] | None = None) -> tuple[bool, dict[str, Any]]:
    host, port = _target(url)
    book = devices()

    def matched(entry: dict[str, Any]) -> bool:
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
    jsonfile(str(_CONNECT)).write(book)
    return fresh, entry


def _check(usrmsg: list[ccmp], access_key: str, url: str) -> None:
    if not url or url == NO_APIURL:
        raise api_error(f"没有可用的 url（当前 {url!r}）：把它作为参数传给 chat(url=...)")
    if access_key == NO_ACCESS_KEY:
        raise api_error(f"access_key 还是占位值 {NO_ACCESS_KEY!r}：请传入真正的 key（不需要鉴权就传空串）")
    if not usrmsg:
        raise api_error("usrmsg 是空的：至少要有一条消息")


def _parse(resp: httpx2.Response) -> ccmp:
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
    _check(usrmsg, access_key, url)
    url = with_port(url)
    payload = {"messages": usrmsg, "access_key": access_key}
    try:
        if client is None:
            async with httpx2.AsyncClient(timeout=timeout) as own:
                resp = await own.post(url, json=payload)
        else:
            resp = await client.post(url, json=payload)
    except Exception as e:
        tip = "（远端在返回响应前断开：看远端服务日志、确认路径与协议）" if isinstance(e, (httpx2.ReadError, httpx2.RemoteProtocolError)) else ""
        raise api_error(f"{type(e).__name__}: {e}（请求 {url}）{tip}") from e
    if resp.status_code // 100 != 2:
        raise api_error(f"HTTP {resp.status_code}：{resp.text[:200]}")
    return _parse(resp)



_MAX_ROUNDS = 8


async def _answer(api: agent_api, messages: list[ccmp], use_tools: bool, log: Callable[[str], None] = print) -> ccmp:
    chain = list(messages)
    reply: ccmp = chain[-1]
    for _ in range(_MAX_ROUNDS):
        reply = await api.chat(chain)
        calls = reply.get("tool_calls") or []
        if not calls or not use_tools:
            return reply
        log("远端请求触发本地工具：" + ", ".join(str(c.get("function", {}).get("name")) for c in calls))
        chain.append(reply)
        chain.extend(await asyncio.to_thread(api.tool, reply))
    return reply


class _Server(ThreadingHTTPServer):

    api: agent_api
    access_key: str
    use_tools: bool


class _Handler(BaseHTTPRequestHandler):

    server_version = "reSEN-peer/1"

    def do_POST(self) -> None:
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
            self.log_message("应答失败：%s: %s", type(e).__name__, e)
            traceback.print_exc()
            self._reply(500, {"error": f"{type(e).__name__}: {e}"})
            return
        self._reply(200, reply)

    def do_GET(self) -> None:
        self._reply(405, {"error": '请用 POST，JSON 体 = {"messages": [...], "access_key": "..."}'})

    def _log(self, message: str) -> None:
        self.log_message("%s", message)

    def _reply(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def tools_enabled() -> bool:
    cfg = dick(str(_CONFIG)).any("config", {})
    return bool(cfg.get("peer_tools", True)) if isinstance(cfg, dict) else True


def make_server(host: str = "0.0.0.0", port: int = PORT, key: str | None = None,
                use_tools: bool | None = None) -> _Server:
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
