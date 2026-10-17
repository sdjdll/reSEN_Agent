import asyncio
import json
from functools import wraps
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar, cast
from pathlib import Path

from openai import AsyncOpenAI as openai
from openai.lib.streaming.chat import AsyncChatCompletionStreamManager as accsm
from openai.types.chat import ChatCompletionMessageParam as ccmp
from rich.markdown import Markdown
from textual.widgets import Static

from .agentapi import agent_api as agentapi, _from_message
from api.peer_api import NO_ACCESS_KEY
from file.dick import dick
"""
core是接入agentapi的兼容层，管理记忆/配置等
"""

class agent_error(Exception):
    """agentapi 的异常统一包装：由上层上屏给用户处理。"""

_P = ParamSpec("_P")
_R = TypeVar("_R")

def _guarded(fn: Callable[_P, Awaitable[_R]]) -> Callable[_P, Awaitable[_R]]:
    """给会调 agentapi 的协程方法套一层：任何异常都转成 agent_error（带异常类型名）。"""
    @wraps(fn)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return await fn(*args, **kwargs)
        except agent_error:
            raise
        except Exception as e:
            raise agent_error(f"{type(e).__name__}: {e}") from e
    return wrapper

class core:
    _client: agentapi
    _sessions: dict[str, list[ccmp]] = {}   # memory：{会话名: [ccmp]}
    _current: str = ""                      # 当前会话名（落盘在 config.json 的 current）
    _notice: str = ""                       # 启动时的一次性提醒（会话状态不对之类）
    _config: dict[str, Any] = {}

    @property
    def _memory(self) -> list[ccmp]:
        """当前会话的消息链；memory 是 {会话名: [ccmp]}，这里只暴露当前那一条。

        `_current` 指不到会话时（把最后的会话删了、新会话还没建起来）就地兜一个默认名，
        免得这一段窗口里追加消息/压缩拿到 KeyError。
        """
        if self._current not in self._sessions:
            self._current = self._current or "会话1"
            self._sessions.setdefault(self._current, [])
        return self._sessions[self._current]

    @_memory.setter
    def _memory(self, chain: list[ccmp]) -> None:
        self._sessions[self._current] = chain

    _used: int | None = None    # 上一次请求的上下文用量（token），未知为 None
    _extra_index: int = 0       # 当前 extra_body 预设下标（运行时状态）
    _stopped: bool = False      # 本轮是否已被用户中止（中止后不再执行任何工具）

    _dick: dick          # config.json（配置）
    _memfile: dick       # memory.json（会话内容 + current）

    def usage(self) -> tuple[int | None, int]:
        """当前上下文用量与上限（token）；用量未知时第一个值为 None。"""
        return self._used, self._config.get("context_limit", 128000)

    def stop(self) -> None:
        """中止本轮对话：工具执行器置位后不再执行任何工具（流由上层取消）。"""
        self._stopped = True
        self._client.stop()

    def stopped(self) -> bool:
        """本轮是否已被中止。"""
        return self._stopped

    def peer_serve(self) -> bool:
        """开关键 `config.peer_serve`（缺省 True）：TUI 起来时要不要顺手拉起 peer 服务。

        与 `config.peer_tools` 不同：那个管「远端能不能驱动本地工具」，这个管「服务开不开」。
        """
        return bool(self._config.get("peer_serve", True))

    def sessions(self) -> list[str]:
        """所有会话名（顺序同 memory.json 里的 memory）。"""
        return list(self._sessions)

    def count(self, name: str) -> int:
        """某个会话里的消息条数（删除确认时先让人看清要扔多少东西）；会话不存在就是 0。"""
        return len(self._sessions.get(name, []))

    def current(self) -> str:
        """当前会话名。"""
        return self._current

    def notice(self) -> str:
        """启动时的一次性提醒（比如 current 指不到会话）；没有就是空串。"""
        return self._notice

    def switch(self, name: str) -> None:
        """切换会话：只把 current 落盘（会话内容照旧不在运行期写回 memory.json）。"""
        if name == self._current or name not in self._sessions:
            return
        self._current = name
        self._used = None        # 上一轮的用量是别的会话的：页脚先显示未知
        self._memfile.set("current", name)
        self._memfile.update()

    def _save_sessions(self) -> None:
        """把会话表与当前会话一起写回 memory.json（memory + current）。

        跟 config.json 分家就是为了这个：改个开关/模型不再把整份对话重写一遍。
        """
        self._memfile.set("memory", self._sessions)
        self._memfile.set("current", self._current)
        self._memfile.update()

    def flush(self) -> None:
        """把运行期状态落盘：config 段写 config.json，会话表/current 写 memory.json。

        退出路径（^q/^c/^z、崩溃、关终端）由上层调一次；退出时写不动也没处报，
        所以这里吞掉异常（filerw 是「临时文件 + fsync + os.replace」的原子写，写坏了也留不下半截文件）。
        """
        try:
            self._dick.set("config", self._config)
            self._dick.update()
        except Exception:
            pass
        try:
            self._save_sessions()
        except Exception:
            pass

    @_guarded
    async def new_session(self, name: str) -> None:
        """新建会话并切过去：会话名当键写进 memory.json，同时落盘 current。

        写回的是整个会话表（含其他会话在当前进程里新聊的内容），
        所以这一次落盘之后，文件里的内容和内存是一致的。
        """
        name = name.strip()
        if not name:
            raise ValueError("会话名不能为空")
        if name in self._sessions:
            raise ValueError(f"会话 {name!r} 已存在")
        self._sessions[name] = []
        self._current = name
        self._used = None
        self._save_sessions()

    @_guarded
    async def delete_session(self, name: str) -> None:
        """删除会话（连同里面的对话），并落盘。

        - 删的正好是当前会话：顺位切到剩下的第一个（顺序同文件里的 memory）
        - 删的是最后一个：`_current` 变成空串（`sessions()` 也空了），由上层立刻要求新建一个；
          上层若没接上，下次启动时 `_load_sessions` 会补一个默认会话
        """
        if name not in self._sessions:
            raise ValueError(f"会话 {name!r} 不存在")
        del self._sessions[name]
        if self._current == name:
            self._current = next(iter(self._sessions), "")
            self._used = None       # 换会话了，上一轮的用量是别人的
        self._save_sessions()

    @_guarded
    async def compress(self) -> None:
        """压缩上下文：用现成的 _compact_history 把当前记忆摘成一条 system 消息，替换掉原记忆。"""
        summary = cast(dict[str, Any], await self._compact_history(self._memory)).get("content") or ""
        self._memory = [cast(ccmp, {"role": "system", "content": f"以下是此前对话的摘要：\n{summary}"})]
        self._used = None   # 压缩后的真实用量要等下一轮的 usage 才知道

    def model(self) -> str:
        """当前模型名。"""
        return self._client.model_name

    async def models(self) -> list[str]:
        """可用模型列表：API 现取（失败就退回当前模型，列表不入 config.json）。"""
        try:
            return await self._client.models()
        except Exception:
            return [self.model()]

    def set_model(self, name: str) -> None:
        """切换模型，并写回 config.json。"""
        self._client.model_name = name
        self._dick.set("modelname", name)
        self._dick.update()

    def effort(self) -> str:
        """当前思考强度。"""
        return self._config.get("reasoning_effort", "medium")

    def set_effort(self, effort: str) -> None:
        """切换思考强度，并写回 config.json。"""
        self._config["reasoning_effort"] = effort
        self._dick.set("config", self._config)
        self._dick.update()

    def extras(self) -> list[dict[str, Any]]:
        """extra_body 预设列表（兼容旧版单个 dict）。"""
        extra = self._config.get("extra", {"thinking": {"type": "enabled"}})
        extras = extra if isinstance(extra, list) else [extra]
        return extras or [{}]

    def extra(self) -> dict[str, Any]:
        """当前生效的 extra_body 预设。"""
        return self.extras()[self.extra_index()]

    def extra_index(self) -> int:
        """当前 extra_body 预设下标（越界自动取模）。"""
        return self._extra_index % len(self.extras())

    def set_extra_index(self, index: int) -> None:
        """切换 extra_body 预设（仅运行时状态，不写 config.json）。"""
        self._extra_index = index % len(self.extras())

    def settings(self) -> list[tuple[str, Any]]:
        """除 memory 外所有配置项：(点分路径, 当前值)；extra 已经是 list[dict]。"""
        return [
            ("apikey", self._dick.str("apikey")),
            ("baseurl", self._dick.str("baseurl")),
            ("modelname", self.model()),
            ("access_key", self._dick.str("access_key", NO_ACCESS_KEY)),   # 与远端 peer 互认的 key
            ("config.reasoning_effort", self.effort()),
            ("config.context_limit", self._config.get("context_limit", 128000)),
            ("config.peer_serve", self.peer_serve()),                     # 开关键：TUI 启动时是否拉起 peer 服务
            ("config.peer_tools", self._config.get("peer_tools", True)),   # 开关键：远端请求能否驱动本地工具
            ("config.extra", self.extras()),
        ]

    def _reconnect(self) -> None:
        """apikey/baseurl 变了要重建客户端 —— AsyncOpenAI 构造时就把它们吃进去了。"""
        name = self.model()
        self._client = agentapi(openai(base_url=self._dick.str("baseurl"), api_key=self._dick.str("apikey")), name)

    @staticmethod
    def _parse_extra(raw: str) -> list[dict[str, Any]]:
        """extra 的输入是 JSON 文本，必须是 [dict, ...]（非法就抛 ValueError，由上层上屏）。"""
        try:
            extra = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"extra 不是合法 JSON：{e}") from e
        if not isinstance(extra, list) or not all(isinstance(item, dict) for item in extra):
            raise ValueError('extra 需要 [dict, ...] 形式的 JSON，例如 [{"thinking": {"type": "enabled"}}]')
        return extra

    @staticmethod
    def _parse_bool(raw: str) -> bool:
        """开关键的值：true/false、1/0、yes/no、on/off、开/关（大小写随意）。"""
        low = raw.strip().lower()
        if low in ("true", "1", "yes", "on", "开"):
            return True
        if low in ("false", "0", "no", "off", "关"):
            return False
        raise ValueError(f"开关键只认 true/false（1/0、yes/no、on/off、开/关也行），收到 {raw!r}")

    @_guarded
    async def set_setting(self, path: str, raw: str) -> None:
        """按路径写回一项配置并落盘；raw 是界面上输入的原文，按该项类型解析（非法值抛 agent_error）。"""
        value = raw.strip()
        if not value and path != "access_key":
            raise ValueError(f"{path} 不能为空")   # access_key 允许空串：表示对端不需要鉴权
        if path in ("apikey", "baseurl"):
            self._dick.set(path, value)
            self._reconnect()
            self._dick.update()
        elif path == "access_key":
            self._dick.set("access_key", value)     # 只存着给 peer_agent 用，不用重建客户端
            self._dick.update()
        elif path == "modelname":
            self.set_model(value)                   # 自带落盘
        elif path == "config.reasoning_effort":
            self.set_effort(value)                  # 自带落盘
        elif path == "config.context_limit":
            if not value.isdigit():
                raise ValueError(f"context_limit 需要正整数，收到 {value!r}")
            self._config["context_limit"] = int(value)
            self._dick.set("config", self._config)
            self._dick.update()
        elif path in ("config.peer_serve", "config.peer_tools"):   # 两个开关键：服务开不开 / 远端能否驱动本地工具
            self._config[path.removeprefix("config.")] = self._parse_bool(value)
            self._dick.set("config", self._config)
            self._dick.update()
        elif path == "config.extra":
            self._config["extra"] = self._parse_extra(value)
            self._extra_index = 0                   # 列表换了，下标归零免得指到不存在的预设
            self._dick.set("config", self._config)
            self._dick.update()
        else:
            raise ValueError(f"未知配置项：{path}")

    def _load_sessions(self) -> None:
        """按 memory.json 的 memory/current 装载会话。

        memory 是 {会话名: [ccmp]}，current 是当前会话名；只在启动时读，运行期不主动回写。
        旧版把这两项写在 config.json 里：这里会自动搬过来（并归并两边都有的会话，不丢数据），
        搬完把 config.json 里那两项删掉，之后 config.json 只管配置。
        current 指不到任何会话时就退回第一个（一个都没有就先造个空的），并留一句提醒。
        """
        memory = self._memfile.any("memory", {})
        current = self._memfile.str("current", "")
        legacy = self._dick.any("memory", None)     # None = config.json 里已经没有这项了
        if isinstance(legacy, dict) and legacy:
            fresh = {name: chain for name, chain in memory.items() if chain} if isinstance(memory, dict) else {}
            fresh.update(legacy)                    # 旧位置的才是真数据：以它为准
            memory, current = fresh, self._dick.str("current", "") or current
            self._memfile.set("memory", memory)
            self._memfile.set("current", current)
            self._memfile.update()
            self._drop_legacy_memory()
        self._sessions = memory if isinstance(memory, dict) else {}
        if current in self._sessions:
            self._current = current
            return
        self._current = next(iter(self._sessions), "会话1")
        self._sessions.setdefault(self._current, [])
        if current:      # 空串只是“还没选过会话”（新建的 memory.json 就是这样），不用提醒
            self._notice = f"memory 里没有会话 {current!r}，已按 {self._current!r} 继续"

    def _drop_legacy_memory(self) -> None:
        """把 config.json 里遗留的 memory/current 删掉（已经搬进 memory.json，两边各存一份迟早不一致）。"""
        try:
            self._dick.pop("memory")
            self._dick.pop("current")
            self._dick.update()
        except Exception:
            pass    # 删不掉不影响用，只是文件里多留一份旧副本

    def __init__(self, apikey: str, baseurl: str, modelname: str, id:str|None=None) -> None:
        self._client = agentapi(openai(
            base_url=baseurl,
            api_key=apikey
        ), modelname)
        here = Path(__file__).resolve().parent.parent / "configure"
        self._dick = dick(str(here / "config.json"))
        self._memfile = dick(str(here / "memory.json"))   # 会话单独一个文件：它很大、又跟配置不同频改
        self._load_sessions()
        self._config = self._dick.any("config",{})
        self._extra_index = self._config.get("extra_index", 0)   # 配置里写了就读，但不主动写回去


    def _analyze_usrmsg(self, usrmsg: str) -> ccmp:# 待完善
        return {"role":"user", "content":usrmsg}

    @_guarded
    async def stream(self, usrmsg: str) -> accsm:
        self._stopped = False   # 新一轮：清掉上一次的中止标记
        self._client.resume()
        self._memory.append(self._analyze_usrmsg(usrmsg))
        return await self._client.chat_stream(
            usrmsg=self._memory,
            reasoning_effort=self._config.get("reasoning_effort", "medium"),
            extra=self.extra()
        )

    async def tool(self, toolmsg: ccmp, msgchain: list[ccmp] | None = None, rtime: int = 0) -> accsm:
        """提升为工具调用：执行 toolmsg 中的工具调用并把结果并回消息链，返回模型的续写流。

        toolmsg   update_screen 返回的带 tool_calls 的助手消息
        msgchain  可选的消息链（默认取 self._memory 的副本），完成后写回 self._memory
        rtime     已进行的工具调用轮数，多轮时由上层递增传入

        上层以「update_screen ↔ tool」循环即可按需持续执行工具；
        返回的 accsm 未消费，需原样交给 update_screen 上屏。

        工具执行器是同步的，放到线程里跑：事件循环不会被卡住（Cancel 才点得动），
        中止标记也能在批量工具调用中途生效（工具执行器每次执行前都会检查）。
        """
        results = await asyncio.to_thread(self._client.tool, toolmsg)
        return await self._continue(toolmsg, results, msgchain)

    async def deny(self, toolmsg: ccmp, msgchain: list[ccmp] | None = None) -> accsm:
        """用户拒绝工具调用：不执行工具，统一回一句拒绝并回消息链，返回模型的续写流。"""
        return await self._continue(toolmsg, self._client.deny(toolmsg), msgchain)

    @_guarded
    async def _continue(self, toolmsg: ccmp, results: list[ccmp], msgchain: list[ccmp] | None = None) -> accsm:
        """把 toolmsg 与工具结果并回消息链，返回模型的续写流。"""
        if msgchain is None:
            msgchain = self._memory.copy()
        msgchain.append(toolmsg)
        msgchain.extend(results)
        self._memory = msgchain
        return await self._client.chat_stream(
            usrmsg=self._memory,
            reasoning_effort=self._config.get("reasoning_effort", "medium"),
            extra=self.extra()
        )


    @_guarded
    async def update_screen(self,think:Static,context:Static,stream:accsm)->ccmp|None:
        async with stream as events:
            async for event in events:
                if event.type == "chunk":
                    think.update((event.snapshot.choices[0].message.model_extra or {}).get("reasoning_content", ""))
                elif event.type == "content.delta":
                    context.update(Markdown(event.snapshot))   # 正文是 Markdown，交给 rich 渲染
            completion = await events.get_final_completion()
            message = completion.choices[0].message
        if completion.usage:
            self._used = completion.usage.total_tokens
        reply = _from_message(message)
        return reply if reply.get("tool_calls") else None

    @_guarded
    async def _compact_history(self, history:list[ccmp])->ccmp:
        extra: list[str] = []

        for sig in history:
            temp = sig.get("content")
            if isinstance(temp, list):
                for cont in temp: extra += f"{sig.get('role')}:{cont.get('type') if cont.get('type') != 'text' else cont.get('text')}"
            elif isinstance(temp, str): extra += f"{sig.get('role')}:{sig.get('content')}"

        exstr: str = ""
        for strs in extra:
            exstr += strs
        return await self._client.chat(cast(list[ccmp],[{"role":"system","content":"总结以下对话，压缩到300字左右，保留关键信息"},{"role":"user", "content":f"{exstr}"}]))