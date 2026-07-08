"""DeviceContextResolver — 全仓唯一 provider 解析点。

caller 用 (bot_id, user_id) 入参,resolver 查 binding.device_provider 拿 provider,
委托对应 ConnInfoBuilder 算 conn_info,包成 typed DeviceContext 出口。
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.devices.services.conn_info_builders.arca_builder import (
    ArcaConnInfoBuilder,
)
from agentclaw.community.core.devices.services.conn_info_builders.baas_builder import (
    BaasConnInfoBuilder,
)
from agentclaw.community.core.devices.services.conn_info_builders.local_builder import (
    LocalConnInfoBuilder,
)
from agentclaw.community.core.devices.services.conn_info_builders.teclaw_builder import (
    TeclawConnInfoBuilder,
)
from agentclaw.community.core.devices.services.device_context import (
    DeviceContext,
    DeviceNotBoundError,
    UnknownProviderError,
)


class DeviceContextResolver:
    """resolve bot → DeviceContext。

    全仓唯一 provider 解析点。底层 conn_info 算法委托各 ConnInfoBuilder
    (复用现有 plugin 内部逻辑,不重写)。
    """

    def __init__(
        self,
        binding_repository: DeviceBindingRepository,
        bot_repository: BotRepository,
        arca_builder: ArcaConnInfoBuilder,
        baas_builder: BaasConnInfoBuilder,
        teclaw_builder: TeclawConnInfoBuilder,
        local_builder: LocalConnInfoBuilder,
    ):
        self._binding_repository = binding_repository
        self._bot_repository = bot_repository
        self._builders = {
            "arca": arca_builder,
            "baas": baas_builder,
            "teclaw": teclaw_builder,
            "local": local_builder,
        }

    def resolve_for_bot(
        self, bot_id: str, user_id: str, *, device_uuid: str | None = None
    ) -> DeviceContext:
        """resolve bot → DeviceContext。

        Args:
            bot_id: 业务 bot ID
            user_id: 操作者身份(用于权限/审计,内部用作 device_affinity)
            device_uuid: 可选设备 UUID,用于多实例场景锁定特定实例;不传则由
                provider 自动选活跃实例(单实例 provider 忽略)。命不中时底层 builder
                抛 ``ConnInfoBuildError`` — **不静默回落**到其它实例。

        Raises:
            DeviceNotBoundError: bot 无 active binding(未 apply / 已 release)
            UnknownProviderError: binding.device_provider 是未知值(DB 异常)
            ConnInfoBuildError: 底层 conn_info 计算失败(含 device_uuid 命不中)
        """
        binding = self._binding_repository.get_active_by_bot_and_owner(bot_id, user_id)
        if binding is None:
            raise DeviceNotBoundError(
                f"DeviceContextResolver: no active binding for bot={bot_id}"
            )

        provider = binding.device_provider
        if provider not in self._builders:
            raise UnknownProviderError(
                f"DeviceContextResolver: unknown provider={provider!r} for bot={bot_id}"
            )

        builder = self._builders[provider]
        raw_conn_info = builder.build(binding, user_id, device_uuid=device_uuid)
        conn_info = self._normalize_schema(raw_conn_info, provider)

        bot = self._bot_repository.get_by_id_and_owner(bot_id, user_id)
        bot_type = (bot or {}).get("bot_type") or ""

        return DeviceContext(
            provider=provider,
            conn_info=conn_info,
            binding_id=binding.id,
            bot_id=bot_id,
            user_id=user_id,
            bot_type=bot_type,
        )

    def resolve_for_binding(
        self, binding_id: int, operator_id: str, *, bot_id: str,
        device_uuid: str | None = None,
    ) -> DeviceContext:
        """resolve binding_id → DeviceContext。

        给 caller 已经锁定 binding(不靠 (bot_id, owner_id) 反查)的场景用。
        典型场景:service bot 发布单 ``ext.binding.online`` 拿到 binding_id,
        而 ``ac_bots.binding_id`` 是发布前的 DRAFT binding,不能用 by-bot 入口
        (会查到错的 binding)。

        Args:
            binding_id: 已知 binding 主键
            operator_id: 操作者身份(用于 builder 内部 device_affinity / 审计)
            bot_id: caller 持有的 bot_id(keyword-only)。透传到 ``ctx.bot_id``,
                供下游(如 ``skill_center_module._build_binding_ctx``)做 owner
                校验 + ``LocalDeviceFileSystem`` BaaS-mode 的 binding context 构建。
                resolver 本身不读 — 但 ``DeviceContext`` 契约要求,caller 必传。
            device_uuid: 可选设备 UUID,用于多实例场景锁定特定实例;不传则由
                provider 自动选活跃实例(单实例 provider 忽略)。命不中时底层 builder
                抛 ``ConnInfoBuildError`` — **不静默回落**到其它实例。

        Raises:
            DeviceNotBoundError: binding_id 不存在
            UnknownProviderError: binding.device_provider 是未知值(DB 异常)
            ConnInfoBuildError: 底层 conn_info 计算失败(含 device_uuid 命不中)
        """
        binding = self._binding_repository.get_by_id(binding_id)
        if binding is None:
            raise DeviceNotBoundError(
                f"DeviceContextResolver: binding_id={binding_id} not found"
            )

        provider = binding.device_provider
        if provider not in self._builders:
            raise UnknownProviderError(
                f"DeviceContextResolver: unknown provider={provider!r} "
                f"for binding_id={binding_id}"
            )

        builder = self._builders[provider]
        raw_conn_info = builder.build(binding, operator_id, device_uuid=device_uuid)
        conn_info = self._normalize_schema(raw_conn_info, provider)

        bot = self._bot_repository.get_by_binding_id(binding_id)
        bot_type = (bot or {}).get("bot_type") or ""

        return DeviceContext(
            provider=provider,
            conn_info=conn_info,
            binding_id=binding.id,
            bot_id=bot_id,
            user_id=operator_id,
            bot_type=bot_type,
        )

    def _normalize_schema(
        self, raw_conn_info: dict[str, Any], provider: str
    ) -> dict[str, Any]:
        """统一字段命名 — 桥接 builder 输出与 caller 期望的 schema。

        当前对齐范围(Step 1.7 + 方案 X 后稳定):

        - ``device_provider`` 从 dict pop —— 由 ``ctx.provider`` 承载,单源。
          (生产链路上没有 caller 真的需要从 conn_info 读这字段;
          ``ArcaDeviceSyncPlugin.__init__`` 的 ``conn_info.get("device_provider", "local")``
          fallback 永远拿到 ``"local"``,但 only 走 Arca 分支,该字段的死防御
          ``if self._device_provider == "baas"`` 永远不命中,无 functional bug。
          残留 attr/分支属于历史防御代码,待单独 cleanup task 处理。)

        - ``bind_id`` → 加 ``binding_id`` alias(``bind_id`` 保留)。
          baas_builder 经 ``build_baas_conn_info_for_http`` 出 ``bind_id``,
          v2 desktop/local 分支(arca/local builder)出 ``binding_id``,
          两边命名分裂。alias 让 caller ``device_adapter_transport.invoke``
          (读 ``binding_id`` 决定 baas vs fallback 分流)对两种 builder 都生效。
          终态应让 baas_builder 直接写 ``binding_id`` 并删 alias,但这需要
          ``baas_device_filesystem`` / ``baas_device_sync`` 的 ``conn_info["bind_id"]``
          硬读改名,涉及多处 plugin 测试,留独立 task。

        Phase X 扩展:发现新的 caller 读不到字段(real functional gap)时,
        在此加 rename / alias。**不要** 为对齐而对齐 —— 改前先确认有 caller 在生产链路上真的读不到。
        """
        conn_info = dict(raw_conn_info)  # shallow copy,不污染源
        # device_provider 由 ctx.provider 承载,不再放 dict
        conn_info.pop("device_provider", None)
        # bind_id → binding_id(为兼容期保留 alias,本期两个都暴露)
        if "bind_id" in conn_info and "binding_id" not in conn_info:
            conn_info["binding_id"] = conn_info["bind_id"]
        return conn_info
