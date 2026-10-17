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

    core: core
    busy: bool
    peer_running: bool
    peer_autostart: bool

    def toggle_peer(self) -> None: ...
    def toggle_peer_autostart(self) -> None: ...
    def delete_session(self, name: str) -> None: ...


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class Prompt(ModalScreen[bool]):

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
        if (error := await self._submit(event.value)) is not None:
            self.query_one("#Pmt_Err", Static).update(f"⚠ {error}")
            return
        self.dismiss(True)


class Confirm(ModalScreen[bool]):

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
    async def submit(text: str) -> str | None:
        try:
            await call(text)
        except agent_error as e:
            return str(e)
        return None
    return submit


def _refresh(host: App[object], done: bool | None) -> None:
    if not done:
        return
    hook = getattr(host, "refresh_settings", None)
    if hook is not None:
        hook()


def _switched(host: App[object], done: bool | None, created: bool = False) -> None:
    if not done:
        return
    hook = getattr(host, "session_switched", None)
    if hook is not None:
        hook(cast(Host, host).core.current(), created)


def prompt_new_session(host: App[object]) -> None:
    def after(ok: bool | None) -> None:
        if ok:
            _switched(host, ok, created=True)
            return
        hook = getattr(host, "fallback_session", None)
        if hook is not None:
            hook()

    host.push_screen(Prompt("新建会话", "", _submitter(cast(Host, host).core.new_session)), after)


class SettingsScreen(Screen[None]):

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
        table = self.query_one(DataTable)
        table.clear()
        for path, value in self._core.settings():
            table.add_row(Text(path), Text(_text(value)), type(value).__name__, key=path, height=None)

    @on(DataTable.RowSelected, "#Tbl_Set")
    def _edit(self, event: DataTable.RowSelected) -> None:
        path = str(event.row_key.value)
        self.app.push_screen(
            Prompt(f"set {path}", _text(dict(self._core.settings())[path]),
                   _submitter(lambda text: self._core.set_setting(path, text))),
            self._after,
        )

    def _after(self, changed: bool | None) -> None:
        if changed:
            _refresh(self.app, changed)
            self.action_reload()


class SettingProvider(Provider):

    def _lines(self) -> list[tuple[Text, str]]:
        items = cast(Host, self.app).core.settings()
        pad = max(len(path) for path, _ in items) + 2
        return [(Text(f"{path:<{pad}}{_text(value)}"), path) for path, value in items]

    def _run(self, path: str) -> Callable[[], None]:
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
        for line, path in self._lines():
            yield DiscoveryHit(line, self._run(path), text=f"set {path}")

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for line, path in self._lines():
            if (score := matcher.match(f"set {path} {line.plain}")) > 0:
                yield Hit(score, line, self._run(path), text=f"set {path}")


class SessionProvider(Provider):

    _NEW = "+ 新建会话"
    _DEL = "✗ 删除会话"

    def _lines(self) -> list[tuple[str, str]]:
        engine = cast(Host, self.app).core
        current = engine.current()
        return [(f"{'*' if name == current else ' '} {name}", name) for name in engine.sessions()]

    def _run(self, name: str) -> Callable[[], None]:
        def run() -> None:
            host = self.app
            cast(Host, host).core.switch(name)
            _switched(host, True)
        return run

    def _run_delete(self, name: str) -> Callable[[], None]:
        def run() -> None:
            cast(Host, self.app).delete_session(name)
        return run

    def _new(self) -> Callable[[], None]:
        def run() -> None:
            prompt_new_session(self.app)
        return run

    async def discover(self) -> Hits:
        if cast(Host, self.app).busy:
            return
        yield DiscoveryHit(Text(self._NEW), self._new(), text="新建会话")
        for line, name in self._lines():
            yield DiscoveryHit(Text(line), self._run(name), text=f"会话 {name}")
            yield DiscoveryHit(Text(f"{self._DEL}　{name}"), self._run_delete(name), text=f"删除会话 {name}")

    async def search(self, query: str) -> Hits:
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

    def _lines(self) -> list[tuple[str, Callable[[], None], str]]:
        host = cast(Host, self.app)
        serve = "peer 服务  运行中　回车关闭" if host.peer_running else "peer 服务  已停止　回车打开"
        autostart = f"peer 开机自启  {'开' if host.peer_autostart else '关'}　回车切换"
        return [
            (serve, host.toggle_peer, "peer 服务 开关 启动 停止 拉起 peer_api"),
            (autostart, host.toggle_peer_autostart, "peer 开机自启 配置 peer_serve 启动时"),
        ]

    async def discover(self) -> Hits:
        for line, run, text in self._lines():
            yield DiscoveryHit(Text(line), run, text=text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for line, run, text in self._lines():
            if (score := matcher.match(f"{text} {line}")) > 0:
                yield Hit(score, Text(line), run, text=text)
