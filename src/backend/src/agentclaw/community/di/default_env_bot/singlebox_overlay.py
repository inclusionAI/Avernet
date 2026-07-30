"""Singlebox overlay 模块注册表。

singlebox 列可能需要一组 corp 模块（default-env-bot router + DI），
通过本 registry 供给，使得 ``profile_modules.py`` 不直接引用
任何 corp 模块（B8 规则）。

社区构建（Avernet CI）中 corp 包不存在，provider 永远不会被注册，
``get_singlebox_overlay_modules()`` 返回空列表，singlebox 列保持
corp-free。
"""
from __future__ import annotations

from collections.abc import Callable

from injector import Module

_singlebox_overlay_provider: Callable[[], list[Module]] | None = None


def register_singlebox_overlay_provider(provider: Callable[[], list[Module]]) -> None:  # pragma: no cover
    """注册 corp overlay 模块供给函数（由 corp_bootstrap 调用）。"""
    global _singlebox_overlay_provider  # pragma: no cover
    _singlebox_overlay_provider = provider  # pragma: no cover


def get_singlebox_overlay_modules() -> list[Module]:
    """返回已注册的 singlebox overlay 模块（未注册时返回空列表）。

    社区构建中永远不注册 provider，因此返回空列表 — singlebox 列
    保持 corp-free。
    """
    if _singlebox_overlay_provider is None:
        return []
    return _singlebox_overlay_provider()  # pragma: no cover