"""Runtime proxy used only by singlebox coverage mode.

Business services should not import coverage recorders. Infrastructure
providers can wrap plugin or repository objects with this proxy so runtime
evidence is collected at the boundary where the dependency is injected.
"""
from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Mapping
from typing import Any

from agentclaw.community.utils.singlebox_coverage_recorder import enabled, record_plugin_hit


AttrBuilder = Callable[[str, tuple[Any, ...], dict[str, Any], Any], dict[str, Any]]


class SingleboxCoverageProxy:
    """Proxy selected method calls and record a hit after successful execution."""

    def __init__(
        self,
        target: Any,
        method_keys: Mapping[str, str],
        *,
        attrs: AttrBuilder | None = None,
    ) -> None:
        self._target = target
        self._method_keys = dict(method_keys)
        self._attrs = attrs

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._target, name)
        key = self._method_keys.get(name)
        if key is None or not callable(attr):
            return attr

        @functools.wraps(attr)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = attr(*args, **kwargs)
            if inspect.isawaitable(result):
                return self._await_and_record(name, key, args, kwargs, result)
            self._record(name, key, args, kwargs, result)
            return result

        return wrapper

    async def _await_and_record(
        self,
        method_name: str,
        key: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        awaitable: Any,
    ) -> Any:
        result = await awaitable
        self._record(method_name, key, args, kwargs, result)
        return result

    def _record(
        self,
        method_name: str,
        key: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        result: Any,
    ) -> None:
        extra = self._attrs(method_name, args, kwargs, result) if self._attrs else {}
        record_plugin_hit(key, method=method_name, **extra)


def wrap_for_singlebox_coverage(
    target: Any,
    method_keys: Mapping[str, str],
    *,
    attrs: AttrBuilder | None = None,
) -> Any:
    """Return target normally, or a recording proxy in coverage mode."""
    if not enabled():
        return target
    return SingleboxCoverageProxy(target, method_keys, attrs=attrs)
