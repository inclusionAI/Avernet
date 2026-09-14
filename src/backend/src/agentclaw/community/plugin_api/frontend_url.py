"""FrontendUrlProvider — task_discovery 前端 workbench URL 取数 Plugin Protocol.

Narrow port for task discovery's frontend workbench address resolution. Mirrors
``BcsBotTokenProvider`` / ``NotifyMessagesProvider``: the community core declares
only the neutral Protocol + ``NullFrontendUrlProvider``; the corp column binds the
env-aware ``CorpFrontendUrlProvider`` via DI.

Rule 20 — every Plugin Protocol has ≥1 local + ≥1 prod impl:
- local : ``NullFrontendUrlProvider`` (plugins/local) — returns "" (fallback).
- prod  : ``CorpFrontendUrlProvider`` (corp/plugins/prod) — env-aware YAML value.

Priority contract stays identical to the legacy ``FrontendUrlHolder`` chain:
runtime injection (``POST /discovery/dingtalk-config``) wins, then the static
env-aware value, then "" → downstream falls back to ``http://localhost:8000``.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class FrontendUrlProvider(Plugin, Protocol):
    """``get() -> frontend_url`` 前端 workbench 地址取数端口。

    使用方式::

        provider: FrontendUrlProvider = injector.get(FrontendUrlProvider)
        base = (provider.get() or "http://localhost:8000").rstrip("/")
    """

    def get(self) -> str: ...


class NullFrontendUrlProvider:
    """空实现 (singlebox/test/未配置): 恒返回空串, 降级不阻断.

    Mirror ``NullBcsBotTokenProvider`` / ``NullNotifyMessagesProvider``: corp 列
    未注入 corp provider 或纯内核测试列时由 DI 侧兜底; 下游拿到空串走构造默认值。
    """

    def get(self) -> str:
        return ""


__all__ = ["FrontendUrlProvider", "NullFrontendUrlProvider"]
