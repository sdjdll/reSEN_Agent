"""counter 与接收状态持久化（协议第 3.3 节）。

单调性由三件事保证：

1. **预留式落盘**：内存里每帧自增，落盘时把 ``saved_next`` 直接推到
   ``c + 1 + COUNTER_RESERVE``（原子写），崩溃后从 ``saved_next`` 起跳，绝不重号。
2. **时钟兜底**：启动时 ``next = max(saved_next, now_ms)``，即使状态文件被回滚，
   真实时间仍在前进。
3. **接收端只要求 ``counter > last_rx``**：丢包、断线重连不会造成错位死锁。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from file.jsonfile import jsonfile

from . import crypto

COUNTER_RESERVE = 1024
COMMIT_EVERY = 256

_DEFAULT_PATH: Path | None = None
_DEFAULT_STATE: "CounterState | None" = None


def default_state_path() -> Path:
    """``configure/peer/<install_uuid>.json``：同机多实例互不干扰。"""
    global _DEFAULT_PATH
    if _DEFAULT_PATH is None:
        _DEFAULT_PATH = crypto.ROOT / "configure" / "peer" / f"{crypto.install_uuid()}.json"
    return _DEFAULT_PATH


def shared_state() -> "CounterState":
    """进程级单例：同一安装里的所有连接必须共用一套 counter 空间。"""
    global _DEFAULT_STATE
    if _DEFAULT_STATE is None:
        _DEFAULT_STATE = CounterState()
    return _DEFAULT_STATE


def _now_ms() -> int:
    return int(time.time() * 1000)


class CounterState:
    """发送计数（本机全局单调）与接收水位（按对端 ``fp`` 分开）。"""

    def __init__(
        self,
        path: str | Path | None = None,
        reserve: int = COUNTER_RESERVE,
        commit_every: int = COMMIT_EVERY,
    ) -> None:
        if reserve < 1:
            raise ValueError("reserve must be >= 1")
        self.path = Path(path) if path is not None else default_state_path()
        self.reserve = reserve
        self.commit_every = max(1, commit_every)
        self._store = jsonfile(str(self.path))
        data = self._store.read()
        data = data if isinstance(data, dict) else {}
        self._tx_saved = self._section(data.get("tx"))
        self._rx_last = self._section(data.get("rx"))
        self._tx_next: dict[str, int] = {}
        self._rx_bump = 0
        self._lock = threading.RLock()

    @staticmethod
    def _section(raw: Any) -> dict[str, int]:
        if not isinstance(raw, dict):
            return {}
        out: dict[str, int] = {}
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, int) and value >= 0:
                out[key] = value
        return out

    @staticmethod
    def key_for(fp: bytes) -> str:
        return fp.hex()

    # ------------------------------------------------------------------ 发送
    def take_tx(self, key: str) -> int:
        """取一个严格单调的发送 counter，并在需要时预留式落盘。"""
        with self._lock:
            saved = self._tx_saved.get(key, 0)
            c = max(self._tx_next.get(key, 0), saved, _now_ms())
            self._tx_next[key] = c + 1
            if c + 1 >= saved:
                self._tx_saved[key] = c + 1 + self.reserve
                self.commit()
            return c

    def tx_next(self, key: str) -> int:
        """当前内存里的下一个发送 counter（诊断用）。"""
        with self._lock:
            return max(self._tx_next.get(key, 0), self._tx_saved.get(key, 0), _now_ms())

    # ------------------------------------------------------------------ 接收
    def accept_rx(self, key: str, counter: int) -> bool:
        """``counter > last_rx`` 才接受；否则是重放/乱序旧帧。"""
        with self._lock:
            if counter <= self._rx_last.get(key, 0):
                return False
            self._rx_last[key] = counter
            self._rx_bump += 1
            if self._rx_bump >= self.commit_every:
                self.commit()
            return True

    def rx_last(self, key: str) -> int:
        with self._lock:
            return self._rx_last.get(key, 0)

    # ------------------------------------------------------------------ 落盘
    def commit(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._store.write({"tx": dict(self._tx_saved), "rx": dict(self._rx_last)})
            self._rx_bump = 0

    def flush(self) -> None:
        self.commit()
