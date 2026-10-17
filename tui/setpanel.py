"""命令面板（^p）里挂的两栏 + 独立设置界面，都围绕 config.json：

- set 栏（SettingProvider）：config.json 的项与值（会话在 memory.json，不在这里），左项右值；选中弹输入框改值
- 会话栏（SessionProvider）：`+ 新建会话` + memory.json 里的会话列表（* 是当前会话）；
  切换只落盘 current，新建则会话名当键落盘 memory + current，删除走 Confirm 确认后连内容一起删
- 设置界面（SettingsScreen，f2）：DataTable 列出所有项，选中一行回车改值
- peer 栏（PeerProvider）：peer 服务开/关，以及 `config.peer_serve` 开机自启开/关

输入都走 Prompt 弹窗 -> core.set_setting() / core.new_session() / core.switch()。
"""
import json
from functools import partial
from typing import Any, Awaitable, Callable, Protocol, cast

from rich.text import Text
from textual import on
from textual.app import App
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Header, Input, Static

from agentapi.core import agent_error, core


class Host(Protocol):
    """宿主 App 需要提供的东西（tui/main.py 里的 App）。"""

    core: core
    busy: bool
    peer_running: bool      # peer 服务现在开着没有（PeerProvider 拿来显示状态、决定开关方向）
    peer_autostart: bool    # 开关键 config.peer_serve（下次启动还起不起）

    def toggle_peer(self) -> None: ...           # 开/关 peer 服务
    def toggle_peer_autostart(self) -> None: ...  # 翻 config.peer_serve
    def delete_session(self, name: str) -> None: ...   # 确认后删会话（确认弹窗在宿主里）


def _text(value: Any) -> str:
    """值 -> 一行文本：字符串原样，list/dict 走 JSON（Python repr 的引号和 True/False 太难看）。"""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class Prompt(ModalScreen[bool]):
    """通用输入弹窗：回车把文本交给 submit()，它返回错误文案就留在弹窗里接着改，返回 None 算成功。"""

    BINDINGS = [("escape", "dismiss(False)", "取消")]

    DEFAULT_CSS = """
    Prompt { align: center middle; }
    Prompt > Vertical { width: 70%; height: auto; padding: 1 2; background: $panel; border: round $primary; }
    Prompt #Pmt_Input { width: 1fr; }
    Prompt #Pmt_Tip { color: $text-muted; }
    Prompt #Pmt_Err { color: $error; }
    """

    def __init__(self, title: str, value: str, submit: Callable[[str], Awaitable[str | None]]) -> None:
        super().__init__()
        self._title = title
        self._value = value
        self._submit = submit

    def compose(self):
        yield Vertical(
            Static(self._title, id="Pmt_Title"),
            Input(value=self._value, id="Pmt_Input"),
            Static("回车确认　esc 取消", id="Pmt_Tip"),
            Static("", id="Pmt_Err", markup=False),
            id="Pmt_Box",
        )

    def on_mount(self) -> None:
        self.query_one("#Pmt_Input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """提交；不合法就把错误留在弹窗里，让人接着改。"""
        if (error := await self._submit(event.value)) is not None:
            self.query_one("#Pmt_Err", Static).update(f"⚠ {error}")
            return
        self.dismiss(True)


class Confirm(ModalScreen[bool]):
    """破坏性操作的确认弹窗：y/回车 确认，n/esc 取消（默认取消，手滑不会把东西删了）。"""

    BINDINGS = [
        ("y", "dismiss(True)", "确认"),
        ("enter", "dismiss(True)", "确认"),
        ("n", "dismiss(False)", "取消"),
        ("escape", "dismiss(False)", "取消"),
    ]

    DEFAULT_CSS = """
    Confirm { align: center middle; }
    Confirm > Vertical { width: auto; max-width: 80%; height: auto; padding: 1 2; background: $panel; border: round $error; }
    Confirm #Cfm_Detail { color: $text-muted; }
    Confirm #Cfm_Tip { color: $text-muted; }
    """

    def __init__(self, title: str, detail: str = "") -> None:
        super().__init__()
        self._title = title
        self._detail = detail

    def compose(self):
        yield Vertical(
            Static(self._title, id="Cfm_Title"),
            Static(self._detail, id="Cfm_Detail", markup=False),
            Static("y / 回车 确认　　n / esc 取消", id="Cfm_Tip"),
            id="Cfm_Box",
        )


def _submitter(call: Callable[[str], Awaitable[None]]) -> Callable[[str], Awaitable[str | None]]:
    """把 core 的写操作（可能抛 agent_error）包成 Prompt 要的「返回错误文案」形式。"""
    async def submit(text: str) -> str | None:
        try:
            await call(text)
        except agent_error as e:
            return str(e)
        return None
    return submit


def _refresh(host: App[object], done: bool | None) -> None:
    """弹窗成功改完配置：让宿主 App 刷新页脚（它没实现就算了）。"""
    if not done:
        return
    hook = getattr(host, "refresh_settings", None)
    if hook is not None:
        hook()


def _switched(host: App[object], done: bool | None, created: bool = False) -> None:
    """弹窗成功切/建了会话：让宿主 App 上屏一句（它没实现就算了）。"""
    if not done:
        return
    hook = getattr(host, "session_switched", None)
    if hook is not None:
        hook(cast(Host, host).core.current(), created)


def prompt_new_session(host: App[object]) -> None:
    """弹「新建会话」输入框（输入的名字当键落盘 memory + current），完事让宿主上屏一句。

    会话栏的「+ 新建会话」和「删光了会话之后的收尾」都用它；
    被 esc 跳过就调宿主的 `fallback_session`（可选的），免得停在“一个会话都没有”的状态里。
    """
    def after(ok: bool | None) -> None:
        if ok:
            _switched(host, ok, created=True)
            return
        hook = getattr(host, "fallback_session", None)
        if hook is not None:
            hook()

    host.push_screen(Prompt("新建会话", "", _submitter(cast(Host, host).core.new_session)), after)


class SettingsScreen(Screen[None]):
    """设置界面：表格列出 config.json 的项与值（会话在 memory.json），选中一行回车改值。"""

    BINDINGS = [("escape", "app.pop_screen", "返回"), ("r", "reload", "刷新")]

    DEFAULT_CSS = """
    SettingsScreen { background: $surface; }
    SettingsScreen #Tbl_Set { height: 1fr; }
    SettingsScreen #Set_Hint { height: 1; background: $panel; padding: 0 1; }
    """

    def __init__(self, engine: core) -> None:
        super().__init__()
        self._core = engine

    def compose(self):
        yield Header()
        yield DataTable(id="Tbl_Set", cursor_type="row", zebra_stripes=True)
        yield Static("↑↓ 选行　enter 改值　r 刷新　esc 返回", id="Set_Hint")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("项", "值", "类型")
        self.action_reload()

    def action_reload(self) -> None:
        """重新读一遍配置、重建表格（改完值也走这里）。"""
        table = self.query_one(DataTable)
        table.clear()
        for path, value in self._core.settings():
            # 单元格用 Text 装：DataTable 对 str 单元格会走 Text.from_markup，JSON 值里的 [ ] 不安全
            table.add_row(Text(path), Text(_text(value)), type(value).__name__, key=path, height=None)

    @on(DataTable.RowSelected, "#Tbl_Set")
    def _edit(self, event: DataTable.RowSelected) -> None:
        """选中一行 -> 弹输入框改值，改成功了刷新表格。"""
        path = str(event.row_key.value)
        self.app.push_screen(
            Prompt(f"set {path}", _text(dict(self._core.settings())[path]),
                   _submitter(lambda text: self._core.set_setting(path, text))),
            self._after,
        )

    def _after(self, changed: bool | None) -> None:
        if changed:
            _refresh(self.app, changed)     # 页脚上那几个也跟着变
            self.action_reload()


class SettingProvider(Provider):
    """命令面板里的 set 栏：一个配置项一条，左边项、右边值（对齐成表格样式）。"""

    def _lines(self) -> list[tuple[Text, str]]:
        """(显示行, 路径)：按最长项名对齐，等宽字体下就是一张两列表。"""
        items = cast(Host, self.app).core.settings()
        pad = max(len(path) for path, _ in items) + 2
        return [(Text(f"{path:<{pad}}{_text(value)}"), path) for path, value in items]

    def _run(self, path: str) -> Callable[[], None]:
        """选中后要执行的动作：读当前值，弹输入框（面板关掉后才会被调用）。"""
        def run() -> None:
            host = self.app
            engine = cast(Host, host).core
            host.push_screen(
                Prompt(f"set {path}", _text(dict(engine.settings())[path]),
                       _submitter(lambda text: engine.set_setting(path, text))),
                partial(_refresh, host),
            )
        return run

    async def discover(self) -> Hits:
        """还没输入时（^p 一打开）就把所有配置项列出来。"""
        for line, path in self._lines():
            yield DiscoveryHit(line, self._run(path), text=f"set {path}")

    async def search(self, query: str) -> Hits:
        """按「set 项名 值」模糊匹配 —— 输入 set 就只剩下这一栏。"""
        matcher = self.matcher(query)
        for line, path in self._lines():
            if (score := matcher.match(f"set {path} {line.plain}")) > 0:
                yield Hit(score, line, self._run(path), text=f"set {path}")


class SessionProvider(Provider):
    """命令面板里的会话栏：`+ 新建会话` + 已有会话（* 是当前的）+ 每会话一条删除，选中即执行。

    对话进行中（宿主 busy）就不列出来 —— 流式期间换会话/删会话，会把这一轮的续写写错地方。
    """

    _NEW = "+ 新建会话"
    _DEL = "✗ 删除会话"

    def _lines(self) -> list[tuple[str, str]]:
        """(显示行, 会话名)：当前会话前面打 *。"""
        engine = cast(Host, self.app).core
        current = engine.current()
        return [(f"{'*' if name == current else ' '} {name}", name) for name in engine.sessions()]

    def _run(self, name: str) -> Callable[[], None]:
        """选中会话后切过去（面板关掉后才会被调用）。"""
        def run() -> None:
            host = self.app
            cast(Host, host).core.switch(name)
            _switched(host, True)
        return run

    def _run_delete(self, name: str) -> Callable[[], None]:
        """选中删除：交给宿主弹确认（它才知道会话里有多少条），确认后连内容一起删。"""
        def run() -> None:
            cast(Host, self.app).delete_session(name)
        return run

    def _new(self) -> Callable[[], None]:
        """选中「新建会话」：弹输入框，输入的名字当键落盘（memory.json 的 memory + current）。"""
        def run() -> None:
            prompt_new_session(self.app)
        return run

    async def discover(self) -> Hits:
        """还没输入时就把「新建会话」、已有会话、各自的删除都列出来。"""
        if cast(Host, self.app).busy:
            return
        yield DiscoveryHit(Text(self._NEW), self._new(), text="新建会话")
        for line, name in self._lines():
            yield DiscoveryHit(Text(line), self._run(name), text=f"会话 {name}")
            yield DiscoveryHit(Text(f"{self._DEL}　{name}"), self._run_delete(name), text=f"删除会话 {name}")

    async def search(self, query: str) -> Hits:
        """按「会话 <名字>」/「删除会话 <名字>」模糊匹配（输入新建也能找到新建会话）。"""
        if cast(Host, self.app).busy:
            return
        matcher = self.matcher(query)
        if (score := matcher.match("新建会话")) > 0:
            yield Hit(score, Text(self._NEW), self._new(), text="新建会话")
        for line, name in self._lines():
            if (score := matcher.match(f"会话 {name}")) > 0:
                yield Hit(score, Text(line), self._run(name), text=f"会话 {name}")
            if (score := matcher.match(f"删除会话 {name}")) > 0:
                yield Hit(score, Text(f"{self._DEL}　{name}"), self._run_delete(name), text=f"删除会话 {name}")


class PeerProvider(Provider):
    """命令面板里的 peer 栏：一条开关 peer 服务，一条改“开机自启”。

    peer 服务是给别人连过来用的（对方的 peer_agent 打 1124），与本机自己聊不相关，
    所以单独一栏；状态直接写在行里，回车就切，不用记现在是开是关。
    """

    def _lines(self) -> list[tuple[str, Callable[[], None], str]]:
        """(显示行, 回车执行的动作, 搜索关键词)：关键词里塞满同义词，^p 里打「peer/服务/自启」都能搜到。"""
        host = cast(Host, self.app)
        serve = "peer 服务  运行中　回车关闭" if host.peer_running else "peer 服务  已停止　回车打开"
        autostart = f"peer 开机自启  {'开' if host.peer_autostart else '关'}　回车切换"
        return [
            (serve, host.toggle_peer, "peer 服务 开关 启动 停止 拉起 peer_api"),
            (autostart, host.toggle_peer_autostart, "peer 开机自启 配置 peer_serve 启动时"),
        ]

    async def discover(self) -> Hits:
        """还没输入时就把两条都列出来（状态摆在行里）。"""
        for line, run, text in self._lines():
            yield DiscoveryHit(Text(line), run, text=text)

    async def search(self, query: str) -> Hits:
        """按关键词 + 显示行模糊匹配。"""
        matcher = self.matcher(query)
        for line, run, text in self._lines():
            if (score := matcher.match(f"{text} {line}")) > 0:
                yield Hit(score, Text(line), run, text=text)
