"""DeviceContext — DeviceContextResolver 的 typed 出口。

全仓唯一 provider 解析结果的数据契约。下游 dispatcher 用 ``provider``
选 plugin 实例,plugin 用 ``conn_info`` dict 拨号。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

ProviderType = Literal["arca", "baas", "teclaw", "local"]


@dataclass(frozen=True)
class DeviceContext:
    """容器访问上下文 — DeviceContextResolver 的 typed 出口。

    分流 caller 和 plugin 之间的数据契约:
    - dispatcher 用 ``(provider, bot_type)`` 选 plugin 实例(本期新增 bot_type 维度,
      原因见 docs/superpowers/specs/2026-06-17-baas-transport-bot-type-strategy-design.md)
    - plugin 用 ``conn_info`` 拨号(各 provider 字段子集不同,本期保 dict)

    Fields:
        provider: 来自 ``ac_entity_device_binding.device_provider`` 的事实值
        conn_info: 拨号字段(命名经 resolver 对齐)
        binding_id: 来自 binding 表
        bot_id: caller 透传。两条入口都必填 — ``resolve_for_bot`` 入参自带,
            ``resolve_for_binding`` 由 caller 显式 kwarg 传(downstream
            ``_build_binding_ctx`` 真用它去 bot_repo 做 owner 校验)。
        user_id: caller 透传(操作者身份;非 owner 时通过权限校验)
        bot_type: ``ac_bots.bot_type`` 字段值(``"desktop"`` / ``"personal"`` /
            ``"service"`` 等),resolver 内部从 ``bot_repo`` 拿。dispatcher 用
            ``(provider, bot_type)`` 二维分流(本期 baas 内部按 bot_type 二次分,
            desktop 走自拼 URL,其他走 invoke_http)。数据未知时为 ``""``。
    """
    provider: ProviderType
    conn_info: dict[str, Any]
    binding_id: int
    bot_id: str
    user_id: str
    bot_type: str = ""  # Task 1 默认 "" — Task 2 后 resolver 内部 bot_repo 注入真值
    device_id: str | None = None


class BotNotFoundError(RuntimeError):
    """bot_id 不存在,或 caller 无权访问该 bot。"""


class DeviceNotBoundError(RuntimeError):
    """bot 存在但无 active binding(未 apply / 已 release)。"""


class UnknownProviderError(RuntimeError):
    """binding.device_provider 是 resolver 不认识的值。

    不应在生产中发生 — 触发即说明 binding 表数据异常。
    """


class ConnInfoBuildError(RuntimeError):
    """ConnInfoBuilder 调底层(baas /http-info、arca proxy 等)失败。"""


class DeviceOfflineError(ConnInfoBuildError):
    """The provider confirms that the current device is offline."""


class DeviceConnectionUnavailableError(ConnInfoBuildError):
    """A transient provider connection failure with unknown device state."""


class RuntimeBindingChangedError(DeviceConnectionUnavailableError):
    """A caller's expected Runtime binding changed before a device request."""


@dataclass(frozen=True, slots=True)
class RuntimeBindingTarget:
    bot_id: str
    owner_id: str
    entity_id: str
    binding_id: int | None
    device_id: str | None


_EXPECTED_RUNTIME_BINDING: ContextVar[RuntimeBindingTarget | None] = ContextVar(
    "expected_runtime_binding", default=None
)


@contextmanager
def expect_runtime_binding(target: RuntimeBindingTarget) -> Iterator[None]:
    """Keep one operation on the device whose receipt it can acknowledge."""
    token = _EXPECTED_RUNTIME_BINDING.set(target)
    try:
        yield
    finally:
        _EXPECTED_RUNTIME_BINDING.reset(token)


def verify_runtime_binding(
    *, bot_id: str, user_id: str, binding_id: int, device_id: str | None,
) -> None:
    expected = _EXPECTED_RUNTIME_BINDING.get()
    if expected is None or bot_id != expected.bot_id or user_id not in (
        expected.owner_id, expected.entity_id
    ):
        return
    if binding_id != expected.binding_id or (
        expected.device_id is not None and device_id != expected.device_id
    ):
        raise RuntimeBindingChangedError("Bot Runtime binding changed before delivery")
