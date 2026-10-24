"""`peer_agent` 元工具：把一段对话发给远端 reSEN peer。

底层走 [`api/peer_api.chat()`](../../api/peer_api.py)（reSEN TCP v1 加密帧）：

- `url` 是远端 **host[:port]**，不是 HTTP 路径；未写端口时用 `peer_api.PORT`（1124）
- `access_key` 就是会话密钥，**不会随请求发送**；对端必须配同一个 key
- 远端可以是任何跑了 `python -m api.peer_api` 的 reSEN-Agent
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Coroutine, TypeVar, cast
from urllib.parse import urlsplit

from openai.types.chat import ChatCompletionMessageParam as ccmp

from api.peer_api import NO_ACCESS_KEY, PORT, chat, remember
from file.config import config

from .interface.meta_tool import meta_tool

_DEFAULT_TIMEOUT = 120.0
_SCHEMES = ("tcp", "http", "https")

_T = TypeVar("_T")


def _sync(coro: Coroutine[Any, Any, _T]) -> _T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class peer_agent(meta_tool):

    def __init__(self) -> None:
        self.schema += [{
            "type": "function",
            "function": {
                "name": "peer_agent",
                "description": (
                    "Ask a remote reSEN-Agent peer for help over its encrypted TCP transport "
                    "(reSEN TCP v1). Sends a conversation to that peer and returns its reply. "
                    "'url' is the peer's host[:port] (default port 1124); the peer must be running "
                    "the reSEN peer service and share the same access_key. Whatever you send reaches "
                    "that remote model, which may run tools on its own machine: only point it at "
                    "hosts you actually trust."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Peer TCP endpoint: 'host', 'host:port' or 'tcp://host:port' (default port 1124). Paths are not used; 'http(s)://host' is accepted for convenience and treated as a plain TCP endpoint.",
                        },
                        "content": {
                            "type": "string",
                            "description": "A single request; wrapped into one user message. Use it for a quick question.",
                        },
                        "messages": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "A full conversation (list of chat messages) to send instead of content; takes precedence.",
                        },
                        "access_key": {
                            "type": "string",
                            "description": "Shared secret used as the peer session key; defaults to config.json's access_key. It is never sent over the wire. Pass an empty string to use an empty key.",
                        },
                        "timeout": {
                            "type": "number",
                            "description": "Seconds to wait for the peer's reply (default 120). The peer may run its own model and tools, so allow a generous timeout.",
                        },
                        "name": {
                            "type": "string",
                            "description": "Optional friendly name for that device, recorded in connect.json (defaults to the host).",
                        },
                        "desc": {
                            "type": "string",
                            "description": "Optional one-line description of that device, recorded in connect.json.",
                        },
                        "tag": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional tags for that device, recorded in connect.json.",
                        },
                    },
                    "required": ["url"],
                },
            },
        }]

    @staticmethod
    def _messages(kwargs: dict[str, Any]) -> list[ccmp]:
        messages = kwargs.get("messages")
        if messages is not None:
            if not isinstance(messages, list) or not all(isinstance(msg, dict) for msg in messages):
                raise TypeError("'messages' must be an array of chat message objects.")
            if not messages:
                raise ValueError("'messages' must contain at least one message.")
            return cast(list[ccmp], messages)
        content = kwargs.get("content")
        if not content:
            raise ValueError("'content' (or 'messages') is required.")
        if not isinstance(content, str):
            raise TypeError("'content' must be a string.")
        return [cast(ccmp, {"role": "user", "content": content})]

    @staticmethod
    def _endpoint(kwargs: dict[str, Any]) -> tuple[str, str, int]:
        raw = kwargs.get("url")
        if not raw:
            raise ValueError(
                "'url' is required: pass the peer host, e.g. '192.168.1.7' or '192.168.1.7:1124'."
            )
        if not isinstance(raw, str):
            raise TypeError("'url' must be a string.")
        text = raw.strip()
        if "://" not in text:
            text = f"tcp://{text}"
        try:
            parts = urlsplit(text)
            port = parts.port
        except ValueError as e:
            raise ValueError(f"'url' has an invalid port: {raw!r}（{e}）") from e
        if parts.scheme.lower() not in _SCHEMES:
            raise ValueError(f"'url' must be host[:port] or tcp://host[:port], got {raw!r}.")
        if not parts.hostname:
            raise ValueError(f"'url' has no host: {raw!r}.")
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError(
                f"'url' should not contain a path/query: {raw!r}（peer 是裸 TCP 端点，不是 HTTP 接口）"
            )
        return text, str(parts.hostname), port or PORT

    @staticmethod
    def _timeout(kwargs: dict[str, Any]) -> float:
        value = kwargs.get("timeout", _DEFAULT_TIMEOUT)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("'timeout' must be a number of seconds.")
        if value <= 0:
            raise ValueError("'timeout' must be > 0.")
        return float(value)

    @staticmethod
    def _tags(kwargs: dict[str, Any]) -> list[str]:
        tag = kwargs.get("tag")
        if isinstance(tag, str):
            return [tag] if tag else []
        if isinstance(tag, list):
            return [item for item in tag if isinstance(item, str) and item]
        return []

    def run(self, *args, **kwargs) -> dict[str, Any]:
        messages = self._messages(kwargs)
        url, host, port = self._endpoint(kwargs)
        raw_key = kwargs.get("access_key")
        access_key = config.str("access_key", NO_ACCESS_KEY) if raw_key is None else str(raw_key)
        reply = _sync(chat(messages, access_key=access_key, url=url, timeout=self._timeout(kwargs)))
        shown = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        out: dict[str, Any] = {
            "endpoint": shown,
            "url": url,
            "protocol": "reSEN-tcp-v1",
            "messages_sent": len(messages),
            "reply": reply,
        }
        try:
            fresh, device = remember(url, str(kwargs.get("name") or ""),
                                     str(kwargs.get("desc") or ""), self._tags(kwargs))
            out["device"] = device
            out["device_new"] = fresh
        except Exception as e:
            out["device_error"] = f"{type(e).__name__}: {e}"
        return out

tool = peer_agent()
