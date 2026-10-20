import asyncio
import json
import os
import signal
import sys
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, cast
from types import FunctionType

from openai.lib.streaming.chat import AsyncChatCompletionStreamManager as accsm
from openai.types.chat import ChatCompletionMessageParam as ccmp
from textual import events
from textual.app import App, SystemCommand
from textual.screen import Screen
from textual.widgets import Header, Static, Button, Input, Collapsible
from textual.containers import Container, Horizontal, VerticalScroll
from textual.worker import Worker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import api.peer_api as peer_api
from agentapi.core import agent_error, core
from file.dick import dick
from file.jsonfile import jsonfile
from setpanel import (Confirm, PeerProvider, SessionProvider, SettingsScreen,
                      prompt_new_session)

def _toolmsg(reply: ccmp) -> str:
    calls = cast(list[dict[str, Any]], reply.get("tool_calls") or [])
    return "\n".join(
        f"{call['function']['name']}({call['function']['arguments']})"
        for call in calls
    )

def _ktok(n: int | None) -> str:
    if n is None:
        return "?"
    return str(n) if n < 1000 else f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"

def _extralabel(extra: dict[str, Any]) -> str:
    return json.dumps(extra, ensure_ascii=False, separators=(",", ":"))

_DEFAULT_CONFIG: dict[str, Any] = {
    "apikey": "",
    "baseurl": "https://api.deepseek.com",
    "modelname": "deepseek-chat",
    "access_key": peer_api.NO_ACCESS_KEY,
    "reasoning_effort": "medium",
    "think_collapsed": True,
    "context_limit": 128000,
    "peer_serve": False,
    "peer_tools": True,
    "extra": [
        {"thinking": {"type": "enabled"}},
        {"thinking": {"type": "disabled"}},
    ],
}

class Clickable(Static):

    DEFAULT_CSS = """
    Clickable:hover { background: $boost; }
    """

    def __init__(self, content: str, action: Callable[[], None], id: str) -> None:
        super().__init__(content, id=id)
        self._action = action

    def on_click(self, event: events.Click) -> None:
        self._action()

class main(App):
    CSS = """
    #Ipt_Message { width: 1fr; }
    #InputRow { height: auto; }
    #FooterRow { height: 1; background: $panel; }
    #FooterRow > Static { width: auto; padding: 0 1; }
    #FooterRow > #Ft_Extra { width: 1fr; text-overflow: ellipsis; }
    .error { color: $error; }
    """

    BINDINGS = [
        ("up", "history_scroll(True)", "历史上滚"),
        ("down", "history_scroll(False)", "历史下滚"),
        ("pageup", "history_scroll(True, True)", "历史上翻页"),
        ("pagedown", "history_scroll(False, True)", "历史下翻页"),
        ("f2", "settings", "设置"),
        ("ctrl+z", "suspend_process", "挂起"),
    ]

    COMMANDS = {*App.COMMANDS, SessionProvider, PeerProvider}

    _core: core
    _cfg: dick
    _EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")
    _allow_all: bool = False
    _allow_turn: bool = False
    _count: int = 0
    _busy: bool = False
    _worker: Worker[None] | None = None
    _peer_server: ThreadingHTTPServer | None = None

    def __init__(self):
        super().__init__()
        cfgpath = Path(__file__).resolve().parent.parent / "configure" / "config.json"
        if not cfgpath.exists():
            cfgpath.parent.mkdir(parents=True, exist_ok=True)
            jsonfile(str(cfgpath)).write(_DEFAULT_CONFIG)
            raise agent_error(f"配置文件不存在，已创建默认配置：{cfgpath}（请填写 apikey/baseurl/modelname 后重新启动）")
        self._cfg = dick(str(cfgpath))
        self._core = core(self._cfg.str("apikey"), self._cfg.str("baseurl"), self._cfg.str("modelname"))
        self._sync_title()

    def _sync_title(self) -> None:
        self.title = self._core.current() or "（无会话）"

    @property
    def core(self) -> core:
        return self._core

    @property
    def busy(self) -> bool:
        return self._busy

    def compose(self):
        yield Header()
        yield HistoryScroll(
            id="History"
        )
        yield Horizontal(
            Input(placeholder="Message...", id="Ipt_Message"),
            Button("Send", id="Btn_Send"),
            id="InputRow"
        )
        yield Horizontal(
            Static("^q 退出  ^p 命令  f2 设置  ↑↓ 历史", id="Ft_Keys"),            Clickable(self._core.model(), self._cycle_model, id="Ft_Model"),
            Clickable(self._core.effort(), self._cycle_effort, id="Ft_Effort"),
            Clickable(self._think_label(), self._toggle_think, id="Ft_Think"),
            Clickable(_extralabel(self._core.extra()), self._cycle_extra, id="Ft_Extra"),
            Static("余额 …", id="Ft_Balance"),
            Clickable("", self._compress, id="Ft_Context"),
            id="FooterRow",
        )

    async def on_mount(self) -> None:
        self._show_usage()
        self._watch_signals()
        if self._core.peer_serve():
            await self._note(self._start_peer())
        else:
            await self._note("peer 服务未启动（peer_serve = false）—— f2 设置里能改，^p 的 peer 栏里能临时启动")
        if self._core.notice():
            await self._note(f"⚠ {self._core.notice()}")

    def _start_peer(self) -> str:
        try:
            srv = peer_api.make_server("0.0.0.0", peer_api.PORT)
        except peer_api.api_error as e:
            return f"⚠ peer 服务没起来（{e}）—— 改完 access_key 重启即可（f2 设置里能改）"
        except OSError as e:
            return f"⚠ peer 服务没起来（{type(e).__name__}: {e}）—— 1124 可能被别的进程占了（ss -ltnp | grep 1124）"
        threading.Thread(target=srv.serve_forever, name="peer_api", daemon=True).start()
        self._peer_server = srv
        tools = "远端可驱动本地工具" if srv.use_tools else "远端不能驱动本地工具"
        return f"peer 服务已监听 0.0.0.0:{peer_api.PORT}（access_key 取自 config.json；{tools}）"

    def _stop_peer(self) -> None:
        srv, self._peer_server = self._peer_server, None
        if srv is not None:
            srv.shutdown()
            srv.server_close()

    @property
    def peer_running(self) -> bool:
        return self._peer_server is not None

    def toggle_peer(self) -> None:
        self.run_worker(self._toggle_peer())

    async def _toggle_peer(self) -> None:
        if self._peer_server is None:
            await self._note(self._start_peer())
        else:
            self._stop_peer()
            state = "开" if self._core.peer_serve() else "关"
            await self._note(f"peer 服务已关闭（开机自启还是「{state}」，{peer_api.PORT} 已释放）")

    async def on_unmount(self) -> None:
        self._core.flush()
        self._stop_peer()

    def _watch_signals(self) -> None:
        # 按名字取信号：Windows 没有 SIGHUP，直接写属性会在 try 之外抛 AttributeError
        for name in ("SIGHUP", "SIGTERM"):
            try:
                signal.signal(getattr(signal, name), self._flush_and_die)
            except (AttributeError, ValueError, OSError):
                pass

    def _flush_and_die(self, signum: int, frame: object) -> None:
        self._core.flush()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    def session_switched(self, name: str, created: bool = False) -> None:
        self.run_worker(self._note(f"— {'已新建并切到' if created else '已切换到'}会话「{name}」 —"))
        self._show_usage()
        self._sync_title()

    def delete_session(self, name: str) -> None:
        detail = f"里面有 {self._core.count(name)} 条消息，删掉不可恢复。"
        if len(self._core.sessions()) <= 1:
            detail += "这是最后一个会话：删完会立刻让你给新会话起个名字。"
        self.push_screen(Confirm(f"删除会话「{name}」？", detail), partial(self._confirmed_delete, name))

    def _confirmed_delete(self, name: str, ok: bool | None) -> None:
        if ok:
            self.run_worker(self._delete_session(name))

    async def _delete_session(self, name: str) -> None:
        was_current = self._core.current() == name
        try:
            await self._core.delete_session(name)
        except agent_error as e:
            await self._error(e)
            return
        left = self._core.sessions()
        tail = f"，已顺位切到「{self._core.current()}」" if was_current and left else ""
        await self._note(f"— 已删除会话「{name}」{tail} —")
        self._show_usage()
        self._sync_title()
        if not left:
            prompt_new_session(self)

    def fallback_session(self) -> None:
        if not self._core.sessions():
            self.run_worker(self._make_fallback_session())

    async def _make_fallback_session(self) -> None:
        try:
            await self._core.new_session("会话1")
        except agent_error as e:
            await self._error(e)
            return
        self.session_switched("会话1", created=True)

    def _show_usage(self) -> None:
        used, limit = self._core.usage()
        self.query_one("#Ft_Context", Static).update(f"ctx {_ktok(used)}/{_ktok(limit)}")
        self.run_worker(self._refresh_balance())

    async def _refresh_balance(self) -> None:
        try:
            text = await self._core.balance()
        except agent_error:
            text = "?"
        self.query_one("#Ft_Balance", Static).update(f"余额 {text}")

    def refresh_settings(self) -> None:
        self.query_one("#Ft_Model", Static).update(self._core.model())
        self.query_one("#Ft_Effort", Static).update(self._core.effort())
        self.query_one("#Ft_Think", Static).update(self._think_label())
        self.query_one("#Ft_Extra", Static).update(_extralabel(self._core.extra()))
        self._show_usage()

    def action_settings(self) -> None:
        self.push_screen(SettingsScreen(self._core))

    def action_suspend_process(self) -> None:
        self._core.flush()
        super().action_suspend_process()

    def action_help_quit(self) -> None:
        self._core.flush()
        super().action_help_quit()

    def get_system_commands(self, screen: Screen[Any]) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        yield SystemCommand("设置", "表格列出 config.json 的项与值，回车可改（f2）", self.action_settings)

    def _compress(self) -> None:
        self.run_worker(self._do_compress())

    async def _do_compress(self) -> None:
        self.query_one("#Ft_Context", Static).update("ctx 压缩中…")
        try:
            await self._core.compress()
        except agent_error as e:
            await self._error(e)
        finally:
            self._show_usage()

    def _cycle_model(self) -> None:
        self.run_worker(self._rotate_model())

    async def _rotate_model(self) -> None:
        models = await self._core.models()
        cur = self._core.model()
        if cur not in models:
            models.insert(0, cur)
        name = models[(models.index(cur) + 1) % len(models)]
        self._core.set_model(name)
        self.query_one("#Ft_Model", Static).update(name)

    def _cycle_effort(self) -> None:
        cur = self._core.effort()
        name = self._EFFORTS[(self._EFFORTS.index(cur) + 1) % len(self._EFFORTS)] if cur in self._EFFORTS else self._EFFORTS[0]
        self._core.set_effort(name)
        self.query_one("#Ft_Effort", Static).update(name)

    def _cycle_extra(self) -> None:
        index = (self._core.extra_index() + 1) % len(self._core.extras())
        self._core.set_extra_index(index)
        self.query_one("#Ft_Extra", Static).update(_extralabel(self._core.extra()))

    def _think_label(self) -> str:
        return "思考: 折叠" if self._core.think_collapsed() else "思考: 展开"

    def _toggle_think(self) -> None:
        self._core.set_think_collapsed(not self._core.think_collapsed())
        self.query_one("#Ft_Think", Static).update(self._think_label())

    def action_history_scroll(self, up: bool, page: bool = False) -> None:
        hist = self.query_one("#History", HistoryScroll)
        if up:
            hist.sticky = False
        {
            (True, False): hist.scroll_up,
            (False, False): hist.scroll_down,
            (True, True): hist.scroll_page_up,
            (False, True): hist.scroll_page_down,
        }[up, page]()
        if not up:
            # scroll_to 本身延后到下次刷新，这里也要在其后再判断是否到底
            hist.call_after_refresh(hist.sync_sticky)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "Btn_Send":
            return
        if self._busy:
            self._cancel()
        else:
            self._start(self.query_one("#Ipt_Message", Input))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "Ipt_Message":
            self._start(event.input)

    def _start(self, ipt: Input) -> None:
        if self._busy or not ipt.value:
            return
        usrmsg, ipt.value = ipt.value, ""
        self._allow_turn = False
        self._set_busy(True)
        self._launch(self._turn(self._watch(self._core.stream(usrmsg))))

    def _cancel(self) -> None:
        self._core.stop()
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
        else:
            self._launch(self._abort())

    def _launch(self, work: Awaitable[Any]) -> None:
        self._worker = self.run_worker(work, group="turn")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        btn = self.query_one("#Btn_Send", Button)
        btn.label = "Cancel" if busy else "Send"
        btn.variant = "error" if busy else "primary"

    async def _turn(self, work: Awaitable[Any]) -> None:
        try:
            await work
        except asyncio.CancelledError:
            if self.is_running:
                await self._abort()
            raise
        finally:
            self._worker = None
            if self.is_running and not any(card.pending for card in self.query(ToolCall)):
                self._set_busy(False)

    async def _abort(self) -> None:
        for card in list(self.query(ToolCall)):
            await card.remove()
        await self._note("⏹ 已停止：本轮对话中止，未执行的工具已丢弃")
        self._set_busy(False)

    async def _note(self, message: str, classes: str = "") -> None:
        hist = self.query_one("#History", HistoryScroll)
        await hist.mount(Static(message, id=self._newid("note"), markup=False, classes=classes))
        hist.sticky = True
        hist.scroll_end(animate=False)

    async def _error(self, message: object) -> None:
        await self._note(f"⚠ {message}", "error")

    async def _bubble(self) -> "tuple[ThinkStatic, Static]":
        msgid = self._newid("msg")
        box = ComplateMessage("", "", id=msgid, collapsed=self._core.think_collapsed())
        hist = self.query_one("#History", HistoryScroll)
        await hist.mount(box)
        hist.sticky = True
        hist.scroll_end(animate=False)
        return box.query_one(f"#{msgid}think", ThinkStatic), box.query_one(f"#{msgid}context", Static)

    def _newid(self, prefix: str) -> str:
        self._count += 1
        return f"{prefix}{self._count}"

    async def _watch(self, stream: Awaitable[accsm]) -> None:
        think, context = await self._bubble()
        try:
            reply = await self._core.update_screen(think, context, await stream)
        except agent_error as e:
            await self._error(e)
            return
        finally:
            think.finish()
        self._show_usage()
        if reply is None:
            return
        if self._allow_all or self._allow_turn:
            await self._tools(reply)
            return
        def act(allow: bool, turn: bool = False, all_: bool = False) -> FunctionType:
            def action(_args: list) -> None:
                self._allow_turn = self._allow_turn or turn
                self._allow_all = self._allow_all or all_
                self._launch(self._turn(self._tools(reply, not allow)))
            return action
        await self.query_one("#History", HistoryScroll).mount(ToolCall(
            _toolmsg(reply), [act(True), act(True, turn=True), act(True, all_=True), act(False)],
            [], id=self._newid("tc"), markup=False,
        ))
        self.query_one("#History", HistoryScroll).follow_end()

    async def _tools(self, reply: ccmp, deny: bool = False) -> None:
        await self._watch(self._core.deny(reply) if deny else self._core.tool(reply))

class ToolCall(Container):
    DEFAULT_CSS = """
    ToolCall { height: auto; }
    ToolCall Horizontal { height: auto; }
    ToolCall Button {
        width: auto;
        min-width: 0;
        height: 1;
        border: none !important;
        padding: 0 1;
        margin: 0 2 0 0;
    }
    """

    _action: list[FunctionType]
    _args: list
    _buttons: Horizontal
    _decided: bool = False

    def __init__(self, toolcall_msg:str,action: list[FunctionType], args:list, name: str | None = None, id: str | None = None, classes: str | None = None, disabled: bool = False, markup: bool = True) -> None:
        buttons = Horizontal(
            Button("Allow",id=f"{id}y"),
            Button("Allow This",id=f"{id}t"),
            Button("Allow All",id=f"{id}a"),
            Button("Deny", id=f"{id}n")
        )
        super().__init__(
            Static(toolcall_msg, markup=markup),
            buttons
            ,name=name, id=id, classes=classes, disabled=disabled, markup=markup
        )
        self._action = action
        self._args = args
        self._buttons = buttons
    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == f"{self.id}y":
            self._action[0](self._args)
        elif event.button.id == f"{self.id}t":
            self._action[1](self._args)
        elif event.button.id == f"{self.id}a":
            self._action[2](self._args)
        elif event.button.id == f"{self.id}n":
            self._action[3](self._args)
        else:
            return
        self._decided = True
        # 只收起按钮，保留工具调用内容（参数）在历史里可查
        await self._buttons.remove()

    @property
    def pending(self) -> bool:
        return not self._decided

class HistoryScroll(VerticalScroll):

    sticky: bool = True

    def sync_sticky(self) -> None:
        self.sticky = self.scroll_target_y >= self.max_scroll_y

    def follow_end(self) -> None:
        if self.is_vertical_scrollbar_grabbed:
            self.sticky = False
            return
        if not self.sticky and self.is_vertical_scroll_end:
            self.sticky = True
        if self.sticky:
            self.scroll_end(animate=False)

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.sticky = False

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self.call_after_refresh(self.sync_sticky)


class FollowStatic(Static):

    def update(self, content: Any = "", *, layout: bool = True) -> None:
        super().update(content, layout=layout)
        if (history := self._history()) is not None:
            history.follow_end()

    def _history(self) -> HistoryScroll | None:
        for node in self.ancestors:
            if isinstance(node, HistoryScroll):
                return node
        return None


class ThinkStatic(FollowStatic):
    """思考链内容；把字数同步到外层 Collapsible 的标题上。"""

    _state: str = "Thinking"
    _count: int = 0

    def update(self, content: Any = "", *, layout: bool = True) -> None:
        super().update(content, layout=layout)
        self._count = len(content) if isinstance(content, str) else len(str(content))
        self._sync_title()

    def finish(self) -> None:
        self._state = "Done"
        self._sync_title()

    def _sync_title(self) -> None:
        for node in self.ancestors:
            if isinstance(node, Collapsible):
                node.title = f"{self._state}({self._count})"
                return


class ComplateMessage(Container):
    DEFAULT_CSS = """
    ComplateMessage { height: auto; }
    """

    def __init__(self,think:str, context:str, name: str | None = None, id: str | None = None, classes: str | None = None, disabled: bool = False, markup: bool = True, collapsed: bool = True) -> None:
        super().__init__(
            Collapsible(ThinkStatic(think, id=f"{id}think", markup=markup), title="Thinking(0)", collapsed=collapsed),
            FollowStatic(context, id=f"{id}context", markup=markup)
            , name=name, id=id, classes=classes, disabled=disabled, markup=markup)


if __name__ == "__main__":
    try:
        mainapp = main()
    except agent_error as e:
        sys.exit(f"⚠ {e}")
    try:
        mainapp.run()
    finally:
        mainapp.core.flush()