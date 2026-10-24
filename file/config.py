"""全局共享的 `configure/config.json` 句柄。

`dick` 每次构造都会读一份内存快照，而 `update()` 又是整份覆写文件——
进程内若各自 `dick(...)`，后写的一方会把别人新增/修改的键回滚，
所以这里只创建一次 `config`，其它模块统一 `from file.config import config`。
"""

from __future__ import annotations

import json
from pathlib import Path

from .dick import dick

CONFIG_PATH = Path(__file__).resolve().parent.parent / "configure" / "config.json"

NO_ACCESS_KEY = "NO-ACCESS_KEY"

DEFAULT_CONFIG: dict[str, object] = {
    "apikey": "",
    "baseurl": "https://api.deepseek.com",
    "modelname": "deepseek-chat",
    "access_key": NO_ACCESS_KEY,
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


def _bootstrap() -> tuple[dick, bool]:
    """确保 config.json 存在并返回共享句柄；第二个返回值表示是否本次新建。"""
    created = False
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=4) + "\n"
        CONFIG_PATH.write_text(payload, encoding="utf-8")
        created = True
    return dick(str(CONFIG_PATH)), created


config, config_created = _bootstrap()
