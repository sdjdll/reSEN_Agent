"""网络环境自愈：处理代理环境变量与 httpx 不兼容的情况。

背景（真实踩坑）：代理软件常把 ``ALL_PROXY`` 设成 ``socks://127.0.0.1:7897``，
但 httpx 只认 ``http`` / ``https`` / ``socks5`` / ``socks5h`` 四种 scheme，
于是 ``AsyncOpenAI(...)`` 在构造阶段就抛::

    ValueError: Unknown scheme for proxy URL URL('socks://127.0.0.1:7897')

这个异常出现在创建客户端的瞬间，与模型、密钥、业务逻辑都无关，极难定位。
另外即使写成 ``socks5://``，在没装 ``socksio`` 时也会抛::

    ImportError: Using SOCKS proxy, but the 'socksio' package is not installed.

本模块在导入时做一次**尽量无痛**的修正：

* ``socks://`` -> ``socks5://``（scheme 别名，语义等价）
* SOCKS 代理但缺少 ``socksio`` -> 警告并临时清空该变量（宁可连接失败给出正常报错，
  也不要启动瞬间崩溃）
* 无法识别的 scheme -> 警告并清空该变量

只影响当前进程的环境变量，不写任何配置文件。
"""

from __future__ import annotations

import os
import sys

# httpx 允许的代理 scheme
_OK_SCHEMES = ("http", "https", "socks5", "socks5h")
_SOCKS_SCHEMES = ("socks5", "socks5h")
_PROXY_VARS = (
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
)

_warned = False


def _warn(message: str) -> None:
    global _warned
    sys.stderr.write(f"[reSEN] 网络环境提示：{message}\n")
    _warned = True


def _scheme_of(value: str) -> str:
    """取出 ``scheme://...`` 里的 scheme；没有 ``://`` 时返回空串。"""
    head, sep, _ = value.partition("://")
    return head.strip().lower() if sep else ""


def _has_socksio() -> bool:
    try:
        import socksio  # noqa: F401
    except ImportError:
        return False
    return True


def sanitize_proxy_env() -> bool:
    """修正不兼容的代理环境变量。返回 ``True`` 表示做过改动。"""
    changed = False
    socks_ok: bool | None = None  # 惰性检测，避免多余 import

    for name in _PROXY_VARS:
        raw = os.environ.get(name)
        if not raw:
            continue
        scheme = _scheme_of(raw)

        # 1) socks:// 是常见笔误/别名，直接归一化成 socks5://
        if scheme == "socks":
            fixed = "socks5://" + raw.partition("://")[2]
            os.environ[name] = fixed
            sys.stderr.write(
                f"[reSEN] 网络环境提示：{name}={raw!r} 的 scheme 不是 httpx 认可的写法，"
                f"已自动改为 {fixed!r}\n"
            )
            changed = True
            scheme = "socks5"

        # 2) 没有 :// 的写法（如 ALL_PROXY=127.0.0.1:7897）httpx 也会报错
        if not scheme:
            _warn(f"{name}={raw!r} 缺少 scheme（应形如 http://host:port），已忽略该项")
            os.environ.pop(name, None)
            changed = True
            continue

        # 3) SOCKS 代理需要 socksio
        if scheme in _SOCKS_SCHEMES:
            if socks_ok is None:
                socks_ok = _has_socksio()
            if not socks_ok:
                _warn(
                    f"{name}={os.environ.get(name)!r} 是 SOCKS 代理，但未安装 socksio，"
                    "已忽略该项以免启动时崩溃。"
                    "如需使用 SOCKS 代理，请安装："
                    "pip install \"httpx[socks]\"（openai 2.x 及以下）"
                    "或 pip install socksio（openai 3.x 自带 httpx2 时）"
                )
                os.environ.pop(name, None)
                changed = True
            continue

        # 4) 其它无法识别的 scheme
        if scheme not in _OK_SCHEMES:
            _warn(
                f"{name}={raw!r} 的 scheme {scheme!r} 不受支持"
                f"（只支持 {'/'.join(_OK_SCHEMES)}），已忽略该项"
            )
            os.environ.pop(name, None)
            changed = True

    return changed


# 导入即生效：只需在创建任何 openai 客户端之前 import 一次本模块。
sanitize_proxy_env()
