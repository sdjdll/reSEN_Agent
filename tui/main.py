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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 让 python tui/main.py 也能找到同级的 agentapi/file
sys.path.insert(0, str(Path(__file__).resolve().parent))         # 以及同目录的 setpanel

import api.peer_api as peer_api
from agentapi.core import agent_error, core
from file.dick import dick
from setpanel import (Confirm, PeerProvider, SessionProvider, SettingProvider, SettingsScreen,
                      prompt_new_session)

def _toolmsg(reply: ccmp) -> str:
    """把 tool_calls 压成多行文本，作为工具卡的说明。"""
    calls = cast(list[dict[str, Any]], reply.get("tool_calls") or [])
    return "\n".join(
        f"{call['function']['name']}({call['function']['arguments']})"
        for call in calls
    )

def _ktok(n: int | None) -> str:
    """token 数压成 k 记法（不足 1k 显示原值）；未知显示 ?。"""
    if n is None:
        return "?"
    return str(n) if n < 1000 else f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"

def _extralabel(extra: dict[str, Any]) -> str:
    """extra_body 预设压成一行短标签，例如 {"thinking": {"type": "enabled"}} -> thinking:on"""
    return json.dumps(extra, ensure_ascii=False, separators=(",", ":"))

class Clickable(Static):
    """可点击的页脚项：点一下执行一次回调。"""

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
    #Ipt_Message { width: 1fr; }              /* 与 Btn_Send 并列：输入框吃掉剩余宽度 */
    #InputRow { height: auto; }               /* 只占自身高度，剩下的都给 #History，于是被顶到屏幕下方 */
    #FooterRow { height: 1; background: $panel; }
    #FooterRow > Static { width: auto; padding: 0 1; }
    #FooterRow > #Ft_Extra { width: 1fr; text-overflow: ellipsis; }   /* 预设可能很长：吃掉剩余宽度并省略 */
    .error { color: $error; }                /* 接口错误上屏 */
    """

    BINDINGS = [
        ("up", "history_scroll(True)", "历史上滚"),
        ("down", "history_scroll(False)", "历史下滚"),
        ("pageup", "history_scroll(True, True)", "历史上翻页"),
        ("pagedown", "history_scroll(False, True)", "历史下翻页"),
        ("f2", "settings", "设置"),
        ("ctrl+z", "suspend_process", "挂起"),
    ]

    COMMANDS = {*App.COMMANDS, SettingProvider, SessionProvider, PeerProvider}   # ^p 命令面板多三栏：set（配置项）/ 会话 / peer（服务开关）

    _core: core
    _cfg: dick
    _EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")   # 与 agentapi 的 Literal 对齐（不要 none）
    _allow_all: bool = False    # 点过 Allow All 后不再询问
    _count: int = 0             # 气泡/工具卡编号
    _busy: bool = False                    # 本轮是否进行中（流里 或 有待决的工具卡）
    _worker: Worker[None] | None = None    # 当前在跑的 worker（Cancel 时取消它）
    _peer_server: ThreadingHTTPServer | None = None   # 随 TUI 一起起来的 peer 服务（0.0.0.0:1124）

    def __init__(self):
        super().__init__()
        self._cfg = dick(str(Path(__file__).resolve().parent.parent / "configure" / "config.json"))
        self._core = core(self._cfg.str("apikey"), self._cfg.str("baseurl"), self._cfg.str("modelname"))
        self._sync_title()      # 标题栏（Header）那行字 = 当前会话名；这里是首屏，后续会话变了再刷

    def _sync_title(self) -> None:
        """把当前会话名写到标题栏。

        Header 显示的是 `App.title`（屏幕没自己盖的话），而它 watch 了这个响应式变量，
        所以赋值就会自己重画 —— 切会话/新建/删除后各调一次即可。
        """
        self.title = self._core.current() or "（无会话）"

    @property
    def core(self) -> core:
        """agentapi 兼容层：命令面板的 set 栏、设置界面都要用它（setpanel 里的 Host 协议）。"""
        return self._core

    @property
    def busy(self) -> bool:
        """本轮对话是否进行中（会话栏靠它挡住在流式期间切会话）。"""
        return self._busy

    def compose(self):
        yield Header()
        yield VerticalScroll(
            id="History"
        )
        yield Horizontal(
            Input(placeholder="Message...", id="Ipt_Message"),
            Button("Send", id="Btn_Send"),
            id="InputRow"
        )
        # Textual 的 Footer 会忽略子控件，所以用 Horizontal 自己排一行页脚
        yield Horizontal(
            Static("^q 退出  ^p 命令  f2 设置  ↑↓ 历史", id="Ft_Keys"),            Clickable(self._core.model(), self._cycle_model, id="Ft_Model"),                    # 点击切换模型
            Clickable(self._core.effort(), self._cycle_effort, id="Ft_Effort"),                 # 点击切换思考强度
            Clickable(_extralabel(self._core.extra()), self._cycle_extra, id="Ft_Extra"),       # 点击切换 extra_body
            Clickable("", self._compress, id="Ft_Context"),                                     # 点击压缩上下文
            id="FooterRow",
        )

    async def on_mount(self) -> None:
        self._show_usage()
        self._watch_signals()            # 关终端（SIGHUP）/ 被 kill（SIGTERM）也要先落盘
        if self._core.peer_serve():      # 开关键 config.peer_serve（^p 的 peer 栏里可以关）
            await self._note(self._start_peer())
        else:
            await self._note("peer 服务未启动（config.peer_serve = false）—— ^p 的 peer 栏里可以打开")
        if self._core.notice():          # 会话状态不对（current 指不到）之类：开局说一声
            await self._note(f"⚠ {self._core.notice()}")

    def _start_peer(self) -> str:
        """拉起 peer 服务（daemon 线程里跑 server_forever，不阻塞 TUI）并返回一句提示。

        access_key 与“远端能不能驱动本地工具”都看 config.json（`access_key` / `config.peer_tools`）；
        起不来（access_key 还是占位值 / 1124 被占 / 权限）不抛异常，只上屏提醒。
        """
        try:
            srv = peer_api.make_server("0.0.0.0", peer_api.PORT)
        except peer_api.api_error as e:
            return f"⚠ peer 服务没起来（{e}）—— 改完 access_key 重启即可（^p 的 set 栏或 f2 都能改）"
        except OSError as e:
            return f"⚠ peer 服务没起来（{type(e).__name__}: {e}）—— 1124 可能被别的进程占了（ss -ltnp | grep 1124）"
        threading.Thread(target=srv.serve_forever, name="peer_api", daemon=True).start()
        self._peer_server = srv
        tools = "远端可驱动本地工具" if srv.use_tools else "远端不能驱动本地工具"
        return f"peer 服务已监听 0.0.0.0:{peer_api.PORT}（access_key 取自 config.json；{tools}）"

    def _stop_peer(self) -> None:
        """关掉 peer 服务（shutdown 会等 serve_forever 退出，所以不会把进程留成僵尸）。"""
        srv, self._peer_server = self._peer_server, None
        if srv is not None:
            srv.shutdown()
            srv.server_close()

    @property
    def peer_running(self) -> bool:
        """peer 服务现在开着没有（^p 的 peer 栏靠它显示状态、决定开关方向）。"""
        return self._peer_server is not None

    @property
    def peer_autostart(self) -> bool:
        """开关键 config.peer_serve：TUI 起来时要不要顺手拉起 peer 服务。"""
        return self._core.peer_serve()

    def toggle_peer(self) -> None:
        """peer 栏第一条：现在开着就关，关着就拉起来（真正的工作跑 worker，别卡消息循环）。"""
        self.run_worker(self._toggle_peer())

    async def _toggle_peer(self) -> None:
        if self._peer_server is None:
            await self._note(self._start_peer())
        else:
            self._stop_peer()
            state = "开" if self._core.peer_serve() else "关"
            await self._note(f"peer 服务已关闭（开机自启还是「{state}」，{peer_api.PORT} 已释放）")

    async def on_unmount(self) -> None:
        """应用退出（^q / 崩溃 / run() 收尾）：把内存里的配置与会话落盘，关掉 peer 服务。"""
        self._core.flush()
        self._stop_peer()

    def _watch_signals(self) -> None:
        """装上 SIGHUP/SIGTERM 的处理器（只有主线程能装，装不上就算了）。"""
        for sig in (signal.SIGHUP, signal.SIGTERM):
            try:
                signal.signal(sig, self._flush_and_die)
            except (ValueError, OSError):
                pass

    def _flush_and_die(self, signum: int, frame: object) -> None:
        """关终端窗口 / 被 kill：先落盘，再按默认方式把这个信号重新发给自己。"""
        self._core.flush()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    def session_switched(self, name: str, created: bool = False) -> None:
        """会话栏切会话/新建会话：历史里说一声，把页脚用量重置（换会话后用量未知），标题栏也跟着换。"""
        self.run_worker(self._note(f"— {'已新建并切到' if created else '已切换到'}会话「{name}」 —"))
        self._show_usage()
        self._sync_title()

    def delete_session(self, name: str) -> None:
        """会话栏的删除：先确认（删了就找不回来），确认后才动手。"""
        detail = f"里面有 {self._core.count(name)} 条消息，删掉不可恢复。"
        if len(self._core.sessions()) <= 1:
            detail += "这是最后一个会话：删完会立刻让你给新会话起个名字。"
        self.push_screen(Confirm(f"删除会话「{name}」？", detail), partial(self._confirmed_delete, name))

    def _confirmed_delete(self, name: str, ok: bool | None) -> None:
        """确认弹窗的回调：跳过了就什么都不做，真确认了就交给 worker 删（别卡消息循环）。"""
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
        self._sync_title()   # 删的可能就是当前会话：标题栏跟着换（删光了先显示“（无会话）”）
        if not left:      # 删光了：立刻要个新名字（名字当键落盘），别停在“没有会话”的状态里
            prompt_new_session(self)

    def fallback_session(self) -> None:
        """新建会话的弹窗被 esc 跳过：自己兜一个默认名（只在会话真被删光时才动手）。"""
        if not self._core.sessions():
            self.run_worker(self._make_fallback_session())

    async def _make_fallback_session(self) -> None:
        try:
            await self._core.new_session("会话1")
        except agent_error as e:
            await self._error(e)
            return
        self.session_switched("会话1", created=True)

    def toggle_peer_autostart(self) -> None:
        """peer 栏第二条：翻一下 config.peer_serve 并落盘（下次启动生效）。"""
        self.run_worker(self._flip_peer_serve())

    async def _flip_peer_serve(self) -> None:
        want = not self._core.peer_serve()
        try:
            await self._core.set_setting("config.peer_serve", "true" if want else "false")
        except agent_error as e:
            await self._error(e)
            return
        self.refresh_settings()   # 页脚/设置面板里的值跟着变
        now = "运行中" if self.peer_running else "没在跑"
        await self._note(f"peer 开机自启已设为「{'开' if want else '关'}」（重启 TUI 生效；现在{now}）")

    def _show_usage(self) -> None:
        """把上下文用量刷到页脚。"""
        used, limit = self._core.usage()
        self.query_one("#Ft_Context", Static).update(f"ctx {_ktok(used)}/{_ktok(limit)}")

    def refresh_settings(self) -> None:
        """配置值被改过（设置界面/命令面板）：把页脚上跟着变的那几项重读一遍。"""
        self.query_one("#Ft_Model", Static).update(self._core.model())
        self.query_one("#Ft_Effort", Static).update(self._core.effort())
        self.query_one("#Ft_Extra", Static).update(_extralabel(self._core.extra()))
        self._show_usage()

    def action_settings(self) -> None:
        """f2（或命令面板里的「设置」）：打开设置表格。"""
        self.push_screen(SettingsScreen(self._core))

    def action_suspend_process(self) -> None:
        """^z 挂起前先落盘 —— 挂起后进程可能直接被 kill，那就来不及写了。"""
        self._core.flush()
        super().action_suspend_process()

    def action_help_quit(self) -> None:
        """^c：Textual 只拿它提醒「^q 才是退出」，我们顺手先落盘（反正你多半是想退出）。"""
        self._core.flush()
        super().action_help_quit()

    def get_system_commands(self, screen: Screen[Any]) -> Iterable[SystemCommand]:
        """在命令面板的系统命令里多挂一条「设置」，让它能搜到。"""
        yield from super().get_system_commands(screen)
        yield SystemCommand("设置", "表格列出 config.json 的项与值，回车可改（f2）", self.action_settings)

    def _compress(self) -> None:
        """点 Ft_Context：压缩上下文（真正的工作跑在 worker 上）。"""
        self.run_worker(self._do_compress())

    async def _do_compress(self) -> None:
        """把当前记忆压成摘要，完了刷新页脚用量。"""
        self.query_one("#Ft_Context", Static).update("ctx 压缩中…")
        try:
            await self._core.compress()
        except agent_error as e:
            await self._error(e)
        finally:
            self._show_usage()

    def _cycle_model(self) -> None:
        """点 Ft_Model：向 API 现取模型列表并切到下一个（列表不入 config.json）。"""
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
        """点 Ft_Effort：按固定顺序切到下一个思考强度。"""
        cur = self._core.effort()
        name = self._EFFORTS[(self._EFFORTS.index(cur) + 1) % len(self._EFFORTS)] if cur in self._EFFORTS else self._EFFORTS[0]
        self._core.set_effort(name)
        self.query_one("#Ft_Effort", Static).update(name)

    def _cycle_extra(self) -> None:
        """点 Ft_Extra：切到 config.json 里 extra 列表的下一个预设。"""
        index = (self._core.extra_index() + 1) % len(self._core.extras())
        self._core.set_extra_index(index)
        self.query_one("#Ft_Extra", Static).update(_extralabel(self._core.extra()))
            
    def action_history_scroll(self, up: bool, page: bool = False) -> None:
        """焦点在输入框时，上下键/翻页键会冒泡到这里，用来滚动历史区。"""
        hist = self.query_one("#History", VerticalScroll)
        {
            (True, False): hist.scroll_up,
            (False, False): hist.scroll_down,
            (True, True): hist.scroll_page_up,
            (False, True): hist.scroll_page_down,
        }[up, page]()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Send：发一轮；本轮进行中时这个按钮是 Cancel。"""
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
        """回车/点 Send：取走输入，把这一轮丢到 worker 上流式上屏。

        必须放 worker：textual 的消息循环是顺序 await 处理器的，在处理器里直接 await
        整轮对话会把界面（含 Cancel 按钮）一起冻住，点了也没反应。
        """
        if self._busy or not ipt.value:
            return
        usrmsg, ipt.value = ipt.value, ""
        self._set_busy(True)
        self._launch(self._turn(self._watch(self._core.stream(usrmsg))))

    def _cancel(self) -> None:
        """点 Cancel：停流 + 停工具链，界面收尾交给被取消的那段协程。"""
        self._core.stop()                   # 工具执行器置位：批量工具调用中途也不再执行任何工具
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()                 # CancelledError 打断流 -> async with 关掉响应
        else:
            self._launch(self._abort())     # 停在「等工具决定」：没有流在跑，直接收尾

    def _launch(self, work: Awaitable[Any]) -> None:
        """把一段协程挂到 worker 上跑，并留个引用供 Cancel 取消。"""
        self._worker = self.run_worker(work, group="turn")

    def _set_busy(self, busy: bool) -> None:
        """本轮状态 -> 按钮：进行中显示 Cancel，结束显示 Send。"""
        self._busy = busy
        btn = self.query_one("#Btn_Send", Button)
        btn.label = "Cancel" if busy else "Send"
        btn.variant = "error" if busy else "primary"

    async def _turn(self, work: Awaitable[Any]) -> None:
        """本轮的外壳：被取消时撤掉待决工具卡并提示，收尾时复位按钮。"""
        try:
            await work
        except asyncio.CancelledError:
            if self.is_running:         # 应用退出时也会取消，那不是用户中止，且控件已拆掉
                await self._abort()
            raise                       # 交回 textual，worker 记为 CANCELLED
        finally:
            self._worker = None
            if self.is_running and not list(self.query(ToolCall)):   # 还有待决工具卡 = 本轮还没结束
                self._set_busy(False)

    async def _abort(self) -> None:
        """中止收尾：撤掉待决的工具卡（点了也执行不了工具），上屏一条提示。"""
        for card in list(self.query(ToolCall)):
            await card.remove()
        await self._note("⏹ 已停止：本轮对话中止，未执行的工具已丢弃")
        self._set_busy(False)

    async def _note(self, message: str, classes: str = "") -> None:
        """往历史上屏一条普通文本（不解析 markup：机器文本可能含 [ ]）。"""
        hist = self.query_one("#History", VerticalScroll)
        await hist.mount(Static(message, id=self._newid("note"), markup=False, classes=classes))
        hist.scroll_end(animate=False)

    async def _error(self, message: object) -> None:
        """把接口错误作为一条消息上屏，交给用户处理。"""
        await self._note(f"⚠ {message}", "error")

    async def _bubble(self) -> tuple[Static, Static]:
        """挂一个空气泡进历史，返回其中的思考/正文 Static 供流式刷新。"""
        msgid = self._newid("msg")
        box = ComplateMessage("", "", id=msgid)
        await self.query_one("#History", VerticalScroll).mount(box)
        self.query_one("#History", VerticalScroll).scroll_end()
        return box.query_one(f"#{msgid}think", Static), box.query_one(f"#{msgid}context", Static)

    def _newid(self, prefix: str) -> str:
        """取一个不会重复的控件 id；id 必须当场取当场用，流式期间可能还有别的轮次在编号。"""
        self._count += 1
        return f"{prefix}{self._count}"

    async def _watch(self, stream: Awaitable[accsm]) -> None:
        """开气泡、取流并消费；接口报错就上屏成一条错误消息，不往上抛。"""
        think, context = await self._bubble()
        try:
            reply = await self._core.update_screen(think, context, await stream)
        except agent_error as e:
            await self._error(e)
            return
        self._show_usage()
        if reply is None:
            return
        if self._allow_all:
            await self._tools(reply)
            return
        def act(allow: bool, all_: bool = False) -> FunctionType:
            def action(_args: list) -> None:
                self._allow_all = self._allow_all or all_
                self._launch(self._turn(self._tools(reply, not allow)))   # Deny 也要回绝并继续会话
            return action
        await self.query_one("#History", VerticalScroll).mount(ToolCall(
            _toolmsg(reply), [act(True), act(True, True), act(False)], [], id=self._newid("tc"),
            markup=False,   # 工具参数是 JSON，含 [a-z] / [/] 之类会被 markup 吃字甚至报错
        ))

    async def _tools(self, reply: ccmp, deny: bool = False) -> None:
        """执行工具调用；deny=True 时不执行，统一回一句拒绝，然后把他续写的流继续上屏。"""
        await self._watch(self._core.deny(reply) if deny else self._core.tool(reply))

class ToolCall(Container):
    DEFAULT_CSS = """
    ToolCall { height: auto; }
    ToolCall Horizontal { height: auto; }
    ToolCall Button {
        width: auto;
        min-width: 0;      /* 默认 16，太宽 */
        height: 1;         /* 默认 auto = 1 行文字 + 上下 tall 边框 = 3 行 */
        border: none !important;   /* 不压掉边框的话，高度会被边框吃光，文字看不见 */
        padding: 0 1;
        margin: 0 2 0 0;
    }
    """

    _action: list[FunctionType]
    _args: list
    def __init__(self, toolcall_msg:str,action: list[FunctionType], args:list, name: str | None = None, id: str | None = None, classes: str | None = None, disabled: bool = False, markup: bool = True) -> None:
        super().__init__(
            Static(toolcall_msg, markup=markup),
            Horizontal(
                Button("Allow",id=f"{id}y"),
                Button("Allow All",id=f"{id}a"),
                Button("Deny", id=f"{id}n")
            )
            ,name=name, id=id, classes=classes, disabled=disabled, markup=markup
        )
        self._action = action
        self._args = args
    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == f"{self.id}y":
            self._action[0](self._args)
        elif event.button.id == f"{self.id}a":
            self._action[1](self._args)
        elif event.button.id == f"{self.id}n":
            self._action[2](self._args)
        else:
            return
        await self.remove()

class ComplateMessage(Container):
    DEFAULT_CSS = """
    ComplateMessage { height: auto; }   /* Container 默认 1fr，会让历史区永远撑满视口、滚不动 */
    """

    def __init__(self,think:str, context:str, name: str | None = None, id: str | None = None, classes: str | None = None, disabled: bool = False, markup: bool = True) -> None:
        super().__init__(
            Collapsible(Static(think, id=f"{id}think", markup=markup)),
            Static(context, id=f"{id}context", markup=markup)
            , name=name, id=id, classes=classes, disabled=disabled, markup=markup)


if __name__ == "__main__":
    mainapp = main()
    try:
        mainapp.run()
    finally:
        mainapp.core.flush()    # run() 自己异常/被中断时兜一下；flush 幂等，重复写没关系