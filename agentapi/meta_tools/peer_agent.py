"""元工具：peer_agent —— 与远端 peer agent 协作。

把一份 `list[ccmp]` 交给远端 agent（走 api.peer_api 的网络调用），把它回的 ccmp 带回来。
连哪台主机由大模型在运行时决定（`url` 必填，端口不写就用默认端口）；
access_key 默认从 config.json 的 `access_key` 读，可覆盖。
**连上的设备会记一笔到 `configure/connect.json`**（按 host:port 去重，见 `peer_api.remember`）。
"""
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
    """把协程跑完并返回结果（工具接口是同步的）。

    平时（core 用 asyncio.to_thread 调工具）本线程没有事件循环，直接 asyncio.run；
    若本线程已经在循环里（从协程里直接调 run），就换个线程跑，
    免得 asyncio.run 报 “cannot be called from a running event loop”。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class peer_agent(meta_tool):
    """元工具：和远端 agent 协作 —— 发一份对话过去，把它回的 ccmp 带回来。"""

    def __init__(self) -> None:
        # 注册本工具的 OpenAI function calling schema 到共享 meta_tool.schema。
        # 模块底部 peer_agent() 在导入时执行一次 -> 列表元素 +1
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
        """把参数整理成一份 list[ccmp]：优先 messages，其次把 content 包成一条 user 消息。"""
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
        """远端地址：必须由调用方（大模型）在运行时给出，这里只做基本形状检查。

        端口交给 [`peer_api.chat`][api.peer_api.chat] 的默认端口逻辑处理（没写就补默认端口）。
        """
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
        """可选的 tag：字符串、字符串数组都收；不是这形状就当没传（连接已经成功了，不为此报错）。"""
        tag = kwargs.get("tag")
        if isinstance(tag, str):
            return [tag] if tag else []
        if isinstance(tag, list):
            return [item for item in tag if isinstance(item, str) and item]
        return []

    def run(self, *args, **kwargs) -> dict[str, Any]:
        """发给远端 peer agent 并返回它的回复。

        参数（都走关键字）：
        - url         远端地址（**必填**：连哪台主机由大模型在运行时决定；端口不写用默认端口）
        - content     一句话请求（会包成一条 user 消息）
        - messages    完整对话（list[ccmp]，优先于 content）
        - access_key  鉴权串；**不传**才用 config.json 的 access_key，显式传空串就是对端不需要鉴权
        - name/desc/tag  可选：给这台设备起个名/写句说明/打标签，连上后会记进 connect.json

        远端连不上/报错就抛 api_error，由 tool_run 那层包成工具结果交给模型。
        """
        messages = self._messages(kwargs)
        url = self._url(kwargs)
        # 注意：这里不能用 `raw or 默认值` —— 空串是合法取值（不代表“没传”），会被 or 吃掉变成占位值
        raw_key = kwargs.get("access_key")
        access_key = dick(str(_CONFIG)).str("access_key", NO_ACCESS_KEY) if raw_key is None else str(raw_key)
        reply = _sync(chat(messages, access_key=access_key, url=url))
        out: dict[str, Any] = {
            "url": url,
            "messages_sent": len(messages),
            "reply": reply,
        }
        try:    # 连上了才记：记不上（盘满/权限）也不该让这次成功的协作变成失败
            fresh, device = remember(url, str(kwargs.get("name") or ""),
                                     str(kwargs.get("desc") or ""), self._tags(kwargs))
            out["device"] = device
            out["device_new"] = fresh
        except Exception as e:
            out["device_error"] = f"{type(e).__name__}: {e}"
        return out

# 模块级单例：导入时注册 schema 一次，运行期复用
tool = peer_agent()
