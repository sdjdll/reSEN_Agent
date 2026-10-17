import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Coroutine, TypeVar, cast

from openai.types.chat import ChatCompletionMessageParam as ccmp

from api.peer_api import NO_ACCESS_KEY, chat, remember
from file.dick import dick

from .interface.meta_tool import meta_tool

_CONFIG = Path(__file__).resolve().parents[2] / "configure" / "config.json"

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
                    "Ask a remote peer agent for help (another reSEN-Agent service). Sends a conversation "
                    "to that peer and returns its reply. The peer host is decided at run time, so 'url' "
                    "is required; omit the port to use the peer's default port. Whatever you pass is sent "
                    "to that remote service: only point it at hosts you actually trust."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Peer endpoint to call, e.g. http://192.168.1.7/chat (http/https; the port defaults to the peer's default port when omitted). You choose the host at run time.",
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
                            "description": "Optional access key override; defaults to config.json's access_key. Pass an empty string when the peer needs no auth.",
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
            return cast(list[ccmp], messages)
        content = kwargs.get("content")
        if not content:
            raise ValueError("'content' (or 'messages') is required.")
        if not isinstance(content, str):
            raise TypeError("'content' must be a string.")
        return [cast(ccmp, {"role": "user", "content": content})]

    @staticmethod
    def _url(kwargs: dict[str, Any]) -> str:
        url = kwargs.get("url")
        if not url:
            raise ValueError("'url' is required: pass the peer endpoint, e.g. 'http://192.168.1.7/chat'.")
        if not isinstance(url, str):
            raise TypeError("'url' must be a string.")
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"'url' must be an http(s) URL, got {url!r}.")
        return url

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
        url = self._url(kwargs)
        raw_key = kwargs.get("access_key")
        access_key = dick(str(_CONFIG)).str("access_key", NO_ACCESS_KEY) if raw_key is None else str(raw_key)
        reply = _sync(chat(messages, access_key=access_key, url=url))
        out: dict[str, Any] = {
            "url": url,
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
