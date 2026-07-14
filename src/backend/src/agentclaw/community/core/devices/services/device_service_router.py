"""设备服务路由器

支持多 Provider 动态路由：
1. 历史设备动态路由：根据 binding_id/device_id 查询 device_provider，路由到对应服务
2. 原 ARCA 新设备申请路由：根据 ARCA -> BaaS 灰度策略决定 Provider
3. 多实例支持：bot_id/binding_id 双入口的实例列表 + 容器信息 + 健康四态
"""

from typing import TYPE_CHECKING, Any, override


if TYPE_CHECKING:
    from agentclaw.community.plugin_api.passport import PassportPlugin
    from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.errors import (
    DeviceNotFoundError,
    DeviceServiceError,
)
from agentclaw.community.core.devices.models import (
    AllocatedDevice,
    DeviceBindingInfo,
    OperatorContext,
    SynlinkMappingInfo,
)
from agentclaw.community.core.devices.protocols import (
    BotQueryProtocol,
)
from agentclaw.community.core.devices.repository.protocol import (
    DeviceBindingRepository,
)
from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_policy import (
    ArcaBotCreateBaasRolloutDecision,
    ArcaBotCreateBaasRolloutPolicy,
)
from agentclaw.community.core.devices.services.device_instance_service import (
    BindingNotFoundError,
    BotPublishNotFoundError,
    DeviceInstanceService,
    InstanceHealthStatus,
)
from agentclaw.community.core.devices.services.device_service import (
    BAAS_DEVICE_PROVIDER,
    DeviceService,
)
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.log import get_logger


logger = get_logger()

# Provider 类型别名
DeviceProvider = DeviceService

# 多实例错误 / 健康四态从 device_instance_service 抽出，这里 re-export
# 以保持既有 import 路径（``from ...device_service_router import
# InstanceHealthStatus`` 等）不破坏。
__all__ = [
    "DeviceServiceRouter",
    "DeviceInstanceService",
    "InstanceHealthStatus",
    "BindingNotFoundError",
    "BotPublishNotFoundError",
]


# TODO: 待实现的外部服务占位符
# NOTE: These are fallback placeholders only used when no service is injected.
# The real implementations are injected via device_dep.py.


class _DisabledArcaBotCreateBaasRolloutPolicy:
    """ARCA -> BaaS 灰度策略未注入时使用的兜底占位。"""

    def decide(
        self,
        *,
        user_id: str,
        bot_type: str,
        engine_type: str,
        template_type: str,
    ) -> ArcaBotCreateBaasRolloutDecision:
        # 真实创建路由由 prod/local 组合根注入；占位策略不参与业务路由。
        logger.error("[ArcaBotCreateBaasRolloutPolicy] Missing injected policy")
        raise DeviceServiceError(
            "arca baas rollout policy is not injected; refuse to choose a create provider"
        )


class _PlaceholderClusterConfigService:
    """Fallback ClusterConfigService placeholder — only used when no real service is injected."""

    pass


class DeviceServiceRouter(DeviceService):
    """设备服务路由器 - 支持多 Provider 动态路由

    职责：
    1. 历史设备动态路由：根据 binding_id/device_id 查询 device_provider，路由到对应服务
    2. 原 ARCA 新设备申请路由：通过创建期灰度策略决定是否灰度到 BaaS
    3. 统一的 API 入口，对上层透明

    路由策略由组合根（DI module）通过传入的 ``providers`` 字典与
    ``default_provider_key`` 决定：
    - 本地组合根 (``TestingDevicesModule``)：providers 只放 ``local``，
      默认 Provider = local。
    - 生产组合根 (``DevicesModule``)：providers 放两个远端 provider
      (arca / baas)，默认 Provider = arca。``local`` 在生产组合根中
      不注册——本地设备只在测试组合根中存在。

    Rule 14：路由器自身不读取任何环境变量，selection 完全由 composition
    root 通过 ``providers`` / ``default_provider_key`` 完成。
    """

    def __init__(
        self,
        *,
        repository: DeviceBindingRepository,
        bot_query: BotQueryProtocol,
        providers: dict[str, DeviceService],
        default_provider_key: str,
        arca_baas_rollout_policy: ArcaBotCreateBaasRolloutPolicy | None = None,
        passport_plugin: "PassportPlugin | None" = None,
        sandbox_client: "SandboxRuntimeClient | None" = None,
        publish_repo: BotPublishRepositoryProtocol | None = None,
        bot_repo: BotRepository | None = None,
    ) -> None:
        """初始化设备服务路由器.

        路由器自身不构造任何 Provider —— 由 DI 组合根（``DevicesModule`` /
        ``TestingDevicesModule``) 完整组装好 ``providers`` 字典再注入。
        路由器只做 binding/device_id/staff_id → DeviceService 的纯派发，
        不读取任何环境变量（Rule 14：配置驱动装配）。

        Args:
            repository: 设备仓库接口（用于 binding/device_id 查询）。
            bot_query: Bot 查询 Protocol（用于 ``bootstrap_device_auth``）。
            providers: 由 DI 装配好的 ``device_provider`` → ``DeviceService``
                映射。本地组合根只放 ``local``；生产组合根放 arca/baas。
            default_provider_key: 历史 binding/device 查询缺失等非创建期场景的兜底键，
                必须存在于 ``providers`` 中（本地: ``local``；生产: ``arca``）。
                创建期 policy 返回未注册 provider 时不会使用该兜底，而是直接失败。
            arca_baas_rollout_policy: 原 ARCA personal/service 草稿 bot 创建期
                BaaS 灰度策略；缺省时使用直接抛错的失败关闭占位实现。
            passport_plugin: ``bootstrap_device_auth`` 使用的护照插件。
            publish_repo: 发布记录仓库；bot_id → 运行态 binding_id 解析
                （``ext.binding.online``）用，多实例 bot_id 入口需要。
            bot_repo: Bot 仓库；由运行态 binding 的 ``device_props.bolt_id``
                解析 ``active_engine`` 用，缺省时 engine_type 兜底 openclaw。
        """
        if default_provider_key not in providers:
            raise ValueError(
                f"default_provider_key {default_provider_key!r} not in providers "
                f"{list(providers.keys())!r}"
            )

        self._repo = repository
        self._bot_query = bot_query
        self._providers: dict[str, DeviceService] = dict(providers)
        self._arca_baas_rollout_policy = arca_baas_rollout_policy or _DisabledArcaBotCreateBaasRolloutPolicy()
        self._passport_plugin = passport_plugin
        # ARCA-proxy branch of get_device_connection_v2 (base method) uses this.
        self._sandbox_client = sandbox_client
        self._default_service: DeviceService = self._providers[default_provider_key]
        # 多实例入口解析用：bot_id → 运行态 binding_id（publish_repo），
        # binding → active_engine（bot_repo）。均可选，缺省时相关入口降级。
        self._publish_repo = publish_repo
        self._bot_repo = bot_repo
        # 惰性构造的多实例读取服务（§1）。
        self._instance_svc: DeviceInstanceService | None = None

        logger.info(
            f"[DeviceServiceRouter] Initialized with providers: {list(self._providers.keys())}, "
            f"default={default_provider_key}"
        )

    def _get_provider_for_binding(self, binding_id: int) -> DeviceService:
        """根据 binding_id 获取对应的 Provider 服务.

        Args:
            binding_id: 设备绑定 ID

        Returns:
            对应的 DeviceService 实例
        """
        record = self._repo.get_by_id(binding_id)
        if record is None:
            logger.warning(
                f"[_get_provider_for_binding] Binding {binding_id} not found, using default"
            )
            return self._default_service

        provider = record.device_provider
        if provider in self._providers:
            logger.info(
                f"[_get_provider_for_binding] binding_id={binding_id} -> provider={provider}"
            )
            return self._providers[provider]

        logger.warning(
            f"[_get_provider_for_binding] Unknown provider {provider}, using default"
        )
        return self._default_service

    def _get_provider_for_device_id(self, device_id: str) -> DeviceService:
        """根据 device_id 获取对应的 Provider 服务.

        Args:
            device_id: 设备 ID

        Returns:
            对应的 DeviceService 实例
        """
        record = self._repo.get_by_device_id(device_id)
        if record is None:
            logger.warning(
                f"[_get_provider_for_device_id] Device {device_id} not found, using default"
            )
            return self._default_service

        provider = record.device_provider
        if provider in self._providers:
            logger.info(
                f"[_get_provider_for_device_id] device_id={device_id} -> provider={provider}"
            )
            return self._providers[provider]

        logger.warning(
            f"[_get_provider_for_device_id] Unknown provider {provider}, using default"
        )
        return self._default_service

    def _get_provider_for_new_device(
        self,
        staff_id: str,
        *,
        engine_type: str | None = None,
        template_type: str | None = None,
        bot_type: str | None = None,
    ) -> DeviceService:
        """根据员工工号 + bot 属性获取新设备申请的 Provider.

        原 ARCA 新建 personal/service 草稿 bot 的 BaaS 灰度只看
        ``staff_id + bot_type + engine bucket``。DRM 平台自身按环境隔离；
        ``template_type`` 会用于
        ``claude_code`` coding 类模板归入 ``aicoding`` 桶。

        Args:
            staff_id: 员工工号
            engine_type: bot active engine（openclaw / claude_code / aicoding 等）
            template_type: bot 模板类型（personalCoding / applicationCoding 等）
            bot_type: bot 业务类型（personal / service / desktop）

        Returns:
            对应的 DeviceService 实例
        """
        # 未显式指定 provider 的创建请求，交给创建期灰度策略决定走 ARCA 还是 BaaS。
        decision = self._arca_baas_rollout_policy.decide(
            user_id=staff_id,
            bot_type=bot_type or "",
            engine_type=engine_type or "openclaw",
            template_type=template_type or "",
        )
        provider_name = decision.target_provider

        if provider_name in self._providers:
            logger.info(
                f"[_get_provider_for_new_device] staff_id={staff_id} "
                f"engine_type={engine_type} template_type={template_type} "
                f"bot_type={bot_type} -> provider={provider_name} "
                f"reason={decision.reason} rollout_version={decision.rollout_version} "
                f"engine_bucket={decision.engine_bucket}"
            )
            return self._providers[provider_name]

        logger.error(
            f"[_get_provider_for_new_device] Unknown create provider {provider_name}, "
            f"reason={decision.reason}, registered={list(self._providers.keys())}"
        )
        raise DeviceServiceError(
            f"unknown create provider {provider_name!r}; "
            f"reason={decision.reason}; registered={list(self._providers.keys())!r}"
        )

    # ============== DeviceService 接口代理实现 ==============

    @override
    def apply_device(
        self,
        *,
        apply_reason: str | None,
        entity_id: str,
        entity_type: str,
        operator: OperatorContext,
        bot_id: str | None = None,
        engine: str | None = None,
        bot_type: str = "",
        owner_id: str | None = None,
        device_provider: str | None = None,
        symbol: list[SynlinkMappingInfo] | None = None,
        force_nas: bool = False,
        extra_envs: dict[str, str] | None = None,
        admins: list[str] | None = None,
        template_type: str | None = None,
        template_config: dict | None = None,
    ):
        """申请新设备 - 根据员工工号 + bot 属性路由到对应 Provider.

        默认根据 staff_id + bot_type + engine bucket 自动选择 provider。
        如果指定了 device_provider，则必须按该 provider 重建；未注册时直接失败，
        避免 restart 误进入创建期灰度。

        Args:
            apply_reason: 申请原因
            entity_id: 实体 ID（用户或团队）
            entity_type: 实体类型（staff/team/proj）
            operator: 操作者上下文
            bot_id: Bot ID
            engine: 引擎类型
            owner_id: 所有者ID（可选，默认为 entity_id）
            device_provider: 本次 allocation 的显式 device_provider 事实。
                restart 会传入历史 binding.device_provider；新 BaaS-native 创建
                分支也应显式传入自己的 provider。有值时跳过 ARCA -> BaaS
                创建期灰度，未注册时直接失败。
            symbol: 软链接配置
            extra_envs: 额外环境变量（可选）
            template_type: bot 模板类型（personalCoding / applicationCoding 等），
                用于创建期 engine bucket 归一化并继续向 provider 透传
            bot_type: bot 业务类型（personal / service / desktop），用于创建期
                ``staff_id + bot_type + engine bucket`` 白名单判定

        Returns:
            设备绑定记录
        """
        staff_id = operator.staff_id

        if device_provider is not None:
            # restart 会传入历史 provider；显式 provider 有值时直接按该 provider 路由。
            if device_provider not in self._providers:
                raise DeviceServiceError(
                    f"device_provider {device_provider!r} is not registered; "
                    "refuse to re-run create rollout"
                )
            service = self._providers[device_provider]
            logger.info(
                f"[apply_device] explicit provider route: bot_id={bot_id}, "
                f"staff_id={staff_id}, engine={engine}, bot_type={bot_type}, "
                f"device_provider={device_provider}"
            )
        else:
            # 普通新建不带 provider，走创建期灰度策略。
            service = self._get_provider_for_new_device(
                staff_id,
                engine_type=engine,
                template_type=template_type,
                bot_type=bot_type,
            )
            logger.info(
                f"[apply_device] Routing to {service.__class__.__name__} for staff_id={staff_id}"
            )

        return service.apply_device(
            apply_reason=apply_reason,
            entity_id=entity_id,
            entity_type=entity_type,
            operator=operator,
            bot_id=bot_id,
            engine=engine,
            bot_type=bot_type,
            owner_id=owner_id,
            symbol=symbol,
            force_nas=force_nas,
            extra_envs=extra_envs,
            admins=admins,
            template_type=template_type,
            template_config=template_config,
        )

    @override
    def release_device(
        self,
        *,
        binding_id: int,
        release_reason: str | None,
        reset: bool = False,
        operator: OperatorContext,
    ):
        """释放设备 - 根据 binding_id 路由到对应 Provider.

        Args:
            binding_id: 设备绑定 ID
            release_reason: 释放原因
            reset: 是否重置设备
            operator: 操作者上下文

        Returns:
            更新后的设备绑定记录
        """
        service = self._get_provider_for_binding(binding_id)
        return service.release_device(
            binding_id=binding_id,
            release_reason=release_reason,
            reset=reset,
            operator=operator,
        )

    @override
    def get_device(self, *, binding_id: int):
        """获取设备信息 - 根据 binding_id 路由.

        Args:
            binding_id: 设备绑定 ID

        Returns:
            设备绑定记录
        """
        service = self._get_provider_for_binding(binding_id)
        return service.get_device(binding_id=binding_id)

    @override
    def get_device_by_device_id(self, *, device_id: str):
        """根据 device_id 获取设备信息.

        Args:
            device_id: 设备 ID

        Returns:
            设备绑定记录
        """
        service = self._get_provider_for_device_id(device_id)
        return service.get_device_by_device_id(device_id=device_id)

    @override
    def list_devices(
        self,
        *,
        entity_id: str | None,
        entity_type: str | None,
        env: str | None,
        status: str | None,
        page: int = 1,
        page_size: int = 20,
    ):
        """列出设备 - 直接查询数据库（所有设备在同一张表）.

        Args:
            entity_id: 实体 ID（可选）
            entity_type: 实体类型（可选）
            env: 环境标识（可选）
            status: 设备状态（可选）
            page: 页码
            page_size: 每页数量

        Returns:
            (总数, 设备列表)
        """
        # 直接使用默认服务实例查询即可（它们共享同一个 repository）
        return self._default_service.list_devices(
            entity_id=entity_id,
            entity_type=entity_type,
            env=env,
            status=status,
            page=page,
            page_size=page_size,
        )

    @override
    def get_provider_inventory(
        self,
        *,
        entity_id: str | None,
        entity_type: str | None,
        env: str | None,
        status: str | None,
        page_size: int = 500,
        max_pages: int = 20,
    ):
        """Aggregate provider counts through the default service.

        All concrete device services are backed by the same
        ``DeviceBindingRepository``. Delegating to the default service avoids
        coupling this read-only observation path to any specific provider.
        """
        return self._default_service.get_provider_inventory(
            entity_id=entity_id,
            entity_type=entity_type,
            env=env,
            status=status,
            page_size=page_size,
            max_pages=max_pages,
        )

    @override
    def list_connectable_devices(
        self,
        *,
        entity_id: str | None,
        entity_type: str | None,
        env: str | None,
        page: int = 1,
        page_size: int = 20,
        with_connection: bool = False,
        port: int | None = None,
        operator: OperatorContext | None = None,
    ):
        """列出可连接设备（ACTIVE 状态）.

        Args:
            entity_id: 实体 ID（可选）
            entity_type: 实体类型（可选）
            env: 环境标识（可选）
            page: 页码
            page_size: 每页数量
            with_connection: 是否包含连接信息
            port: 可选端口覆盖
            operator: 操作者上下文（with_connection=True 时必需）

        Returns:
            (总数, 设备列表)
        """
        # 列表阶段只查 binding；connection 统一由 router 按真实 provider 补齐。
        total, items = self._default_service.list_connectable_devices(
            entity_id=entity_id,
            entity_type=entity_type,
            env=env,
            page=page,
            page_size=page_size,
            with_connection=False,
            port=port,
            operator=operator,
        )

        if not with_connection:
            return total, items

        # with_connection=True: supplement cross-provider connection info
        results = []
        for item in items:
            binding_id = item.record.id if isinstance(item, DeviceBindingInfo) else item.id
            connection = None
            if operator is not None:
                try:
                    provider = self._get_provider_for_binding(binding_id)
                    connection = provider.get_device_connection(
                        binding_id=binding_id,
                        operator=operator,
                        port=port,
                    )
                except Exception as e:
                    logger.warning(f"[list_connectable_devices] Failed to get connection: {e}")

            if isinstance(item, DeviceBindingInfo):
                item.connection = connection
                results.append(item)
            else:
                results.append(DeviceBindingInfo(record=item, connection=connection))
        return total, results

    @override
    def report_device_alive(
        self,
        *,
        device_id: str,
        token: str,
        skip_token_check: bool = False,
    ):
        """设备上报 alive - 根据 device_id 路由.

        Provider 的 report_device_alive（DeviceService.report_device_alive）
        内部已包含 PENDING→ACTIVE 时的 MCP 同步，路由器只做路由。

        Args:
            device_id: 设备 ID
            token: 回调 Token
            skip_token_check: 是否跳过 token 校验，用于进程内调用

        Returns:
            更新后的设备绑定记录
        """
        service = self._get_provider_for_device_id(device_id)
        return service.report_device_alive(
            device_id=device_id,
            token=token,
            skip_token_check=skip_token_check,
        )

    @override
    def report_device_status(
        self,
        *,
        device_id: str,
        status: str,
        message: str | None,
        token: str,
    ):
        """设备上报启动状态 - 根据 device_id 路由.

        Args:
            device_id: 设备 ID
            status: 启动状态 (STARTING, FAILED, SUCCEEDED)
            message: 启动信息
            token: 回调 Token

        Returns:
            更新后的设备绑定记录
        """
        service = self._get_provider_for_device_id(device_id)
        return service.report_device_status(
            device_id=device_id,
            status=status,
            message=message,
            token=token,
        )

    @override
    def exec_shell(self, device_id: str, shell_cmd: str) -> str:
        """在设备上执行 shell 命令 - 根据 device_id 路由.

        Args:
            device_id: 设备 ID
            shell_cmd: Shell 命令

        Returns:
            命令执行结果
        """
        service = self._get_provider_for_device_id(device_id)
        return service.exec_shell(device_id, shell_cmd)

    @override
    def exec_shell_new(self, device_id: str, shell_cmd: str):
        """在设备上执行 shell 命令 - 根据 device_id 路由.

        Args:
            device_id: 设备 ID
            shell_cmd: Shell 命令

        Returns:
            命令执行结果
        """
        service = self._get_provider_for_device_id(device_id)
        return service.exec_shell_new(device_id, shell_cmd)

    @override
    def batch_set_env(self, *, binding_ids: list[int], env: str) -> tuple[int, list[int]]:
        """批量设置环境 - 使用默认 Provider.

        Args:
            binding_ids: 绑定 ID 列表
            env: 环境标识

        Returns:
            (更新的记录数量, 成功更新的 binding_id 列表)
        """
        return self._default_service.batch_set_env(binding_ids=binding_ids, env=env)

    def get_device_connection(
        self,
        *,
        binding_id: int,
        operator: OperatorContext,
        port: int | None = None,
        ttl: int | None = None,
        device_uuid: str | None = None,
    ):
        """获取设备连接信息 - 根据 binding_id 路由.

        ``device_uuid`` 透传给 provider,BaaS provider 用它锁定多实例中的特定实例;
        不传则由 BaaS 自动选活跃实例(本地/非 BaaS provider 忽略)。
        """
        service = self._get_provider_for_binding(binding_id)
        return service.get_device_connection(
            binding_id=binding_id, operator=operator, port=port, ttl=ttl,
            device_uuid=device_uuid,
        )

    @override
    def update_device_headers(
        self,
        *,
        device: Any,
        agent_pass_token: str = "",
        agent_code: str = "",
    ) -> bool | list[dict]:
        """热更新设备出站 header 规则 - 根据 device_provider 路由.

        Args:
            device: 已分配设备信息（AllocatedDevice）
            agent_pass_token: Agent Passport token
            agent_code: Agent Passport agent_code

        Returns:
            bool | list[dict]: 更新是否成功，或 BaaS 模式下返回更新的设备列表

        Raises:
            DeviceServiceError: 设备 provider 未知或热更新失败时抛出
        """
        provider = getattr(device, "device_provider", "")
        device_id = getattr(device, "device_id", "")
        token_prefix = agent_pass_token[:6] if agent_pass_token else "(empty)"

        if provider in self._providers:
            logger.info(
                f"[update_device_headers] Routing: device_id={device_id}, "
                f"provider={provider}, agent_code={agent_code or '(empty)'}, "
                f"token_prefix={token_prefix}..."
            )
            return self._providers[provider].update_device_headers(
                device=device,
                agent_pass_token=agent_pass_token,
                agent_code=agent_code,
            )
        logger.warning(
            f"[update_device_headers] Unknown provider: device_id={device_id}, "
            f"provider={provider}, using default"
        )
        return self._default_service.update_device_headers(
            device=device,
            agent_pass_token=agent_pass_token,
            agent_code=agent_code,
        )

    def bootstrap_device_auth(
        self,
        *,
        device_id: str,
        bot_id: str,
        owner_id: str,
    ) -> dict:
        """设备启动回调：补充短效凭证并返回 agent_code。

        设备启动后调用，查询 passport token（短效凭证）并热更新到设备出站 header 规则，
        同时返回 agent_code 供设备侧使用。

        Args:
            device_id: 设备 ID (client_id)
            token: 回调 token
            bot_id: Bot ID
            owner_id: Owner ID

        Returns:
            包含 "agent_code" 的字典

        Raises:
            DeviceNotFoundError: 设备未找到
            DeviceServiceError: 其他错误
        """
        logger.info(
            f"[bootstrap_device_auth] Start: device_id={device_id}, "
            f"bot_id={bot_id}, owner_id={owner_id}"
        )

        bot = self._bot_query.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            logger.error(
                f"[bootstrap_device_auth] Bot not found: bot_id={bot_id}, owner_id={owner_id}"
            )
            raise DeviceNotFoundError(f"bot {bot_id} not found")

        # ========== 步骤 1: 查 binding ==========
        record = self._repo.get_by_device_id(device_id)

        # BaaS 设备以 DEVICE- 开头，不查 binding，其余必须命中。
        is_baas_device = device_id.startswith("DEVICE-")

        if record is None and not is_baas_device:
            logger.error(
                f"[bootstrap_device_auth] Binding missing: device_id={device_id}, owner_id={owner_id}"
            )
            raise DeviceNotFoundError(f"device {device_id} not found")

        # ========== 步骤 2: 取 agent_code ==========
        from agentclaw.community.core.bot_management.utils import resolve_agent_code

        agent_code = resolve_agent_code(bot=bot, passport_plugin=self._passport_plugin)
        if agent_code:
            logger.info(
                f"[bootstrap_device_auth] Agent code resolved: device_id={device_id}, "
                f"bot_id={bot_id}, owner_id={owner_id}, agent_code={agent_code}"
            )
        else:
            logger.error(
                f"[bootstrap_device_auth] Agent code empty: device_id={device_id}, "
                f"bot_id={bot_id}, owner_id={owner_id}"
            )
            raise DeviceServiceError(f"Agent code not found for bot_id={bot_id}, owner={owner_id}")

        # ========== 步骤 3: 查 passport token ==========
        agent_pass_token = ""
        try:
            if self._passport_plugin is None:
                raise DeviceServiceError("passport_plugin not injected into DeviceServiceRouter")
            agent_pass_token = (
                self._passport_plugin.query_token(bot_id, owner_id) or ""
            )
            logger.info(
                f"[bootstrap_device_auth] Token queried: device_id={device_id}, "
                f"bot_id={bot_id}, owner_id={owner_id}, has_token={'yes' if agent_pass_token else 'no'}"
            )
        except Exception as e:
            logger.error(
                f"[bootstrap_device_auth] Token query failed: device_id={device_id}, "
                f"bot_id={bot_id}, owner_id={owner_id}, error={e}"
            )
            raise DeviceServiceError(f"Passport token query failed: {e}") from e

        if not agent_pass_token:
            logger.error(
                f"[bootstrap_device_auth] Passport token empty: device_id={device_id}, "
                f"bot_id={bot_id}, owner_id={owner_id}"
            )
            raise DeviceServiceError("Passport token is empty")

        # ========== 步骤 4: 热更新 headers ==========
        if is_baas_device:
            allocated_device = AllocatedDevice(
                device_id=device_id,
                device_provider=BAAS_DEVICE_PROVIDER,
                device_props={
                    "bolt_id": bot.get("bot_id", ""),
                    "entity_id": owner_id,
                    "device_uuid": device_id,
                },
            )
            logger.info(
                f"[bootstrap_device_auth] BaaS device prepared: device_id={device_id}, "
                f"bot_id={bot_id}, owner_id={owner_id}, "
                f"bolt_id={bot.get('bot_id', '')}, entity_id={owner_id}"
            )
        else:
            allocated_device = AllocatedDevice(
                device_id=record.device_id,
                device_provider=record.device_provider,
                device_props=record.device_props,
            )
            logger.info(
                f"[bootstrap_device_auth] Non-BaaS device prepared: device_id={device_id}, "
                f"binding_id={record.id}, bot_id={bot_id}, owner_id={owner_id}, "
                f"provider={record.device_provider}"
            )

        update_ok = self.update_device_headers(
            device=allocated_device,
            agent_pass_token=agent_pass_token,
            agent_code=agent_code,
        )

        logger.info(
            f"[bootstrap_device_auth] Done: device_id={device_id}, "
            f"bot_id={bot_id}, owner_id={owner_id}, "
            f"agent_code={agent_code or '(empty)'}, "
            f"has_token={'yes' if agent_pass_token else 'no'}, "
            f"update_ok={update_ok}"
        )

        return {"agent_code": agent_code}

    # ============== 多实例：实例列表（委托 DeviceInstanceService，§1）==============

    def _instance_service(self) -> DeviceInstanceService:
        """惰性构造多实例读取服务（共享同一 repo / providers）。"""
        if self._instance_svc is None:
            self._instance_svc = DeviceInstanceService(
                repository=self._repo,
                providers=self._providers,
                publish_repo=self._publish_repo,
                bot_repo=self._bot_repo,
            )
        return self._instance_svc

    def get_device_connection_by_bot(
        self,
        *,
        bot_id: str,
        operator: OperatorContext,
        port: int | None = None,
        ttl: int | None = None,
        device_uuid: str | None = None,
    ):
        """通过 bot_id 获取设备连接信息（对话页主入口，§3）。

        内部经 ``ext.binding.online`` 解析运行态 binding_id（复用
        ``DeviceInstanceService._resolve_binding_id_by_bot_id``，与 §1 bot_id
        入口同一解析），再复用 ``get_device_connection``。``device_uuid`` 透传
        锁定多实例中的特定实例；不传则由 provider 自动选活跃实例。

        Raises:
            BotPublishNotFoundError: bot_id 无 success 发布单 / ext.binding.online 缺失
        """
        binding_id = self._instance_service()._resolve_binding_id_by_bot_id(bot_id)
        return self.get_device_connection(
            binding_id=binding_id,
            operator=operator,
            port=port,
            ttl=ttl,
            device_uuid=device_uuid,
        )

    @override
    def get_instances(
        self,
        *,
        binding_id: int,
        health_check: bool = False,
    ) -> dict[str, Any]:
        """实例列表 + 容器信息 + 健康四态（binding_id 入口）。委托 DeviceInstanceService。"""
        return self._instance_service().get_instances(
            binding_id=binding_id, health_check=health_check
        )

    @override
    def get_instances_by_bot(
        self,
        *,
        bot_id: str,
        health_check: bool = False,
    ) -> dict[str, Any]:
        """实例列表 + 容器信息 + 健康四态（bot_id 入口）。委托 DeviceInstanceService。"""
        return self._instance_service().get_instances_by_bot(
            bot_id=bot_id, health_check=health_check
        )

    @override
    def list_devices_by_runtime_binding(
        self,
        *,
        binding_id: int,
        timeout: float | None = None,
    ) -> list[str]:
        """按运行态 binding 获取设备列表。委托 DeviceInstanceService。"""
        if timeout is None:
            return self._instance_service().list_devices_by_runtime_binding(
                binding_id=binding_id
            )
        return self._instance_service().list_devices_by_runtime_binding(
            binding_id=binding_id,
            timeout=timeout,
        )

    @override
    def restart_device(
        self,
        *,
        binding_id: int,
        device_uuid: str,
        operator: OperatorContext,
    ) -> dict[str, Any]:
        """指定设备重启（binding_id 入口，仅 owner）。委托 DeviceInstanceService。"""
        return self._instance_service().restart_device(
            binding_id=binding_id,
            device_uuid=device_uuid,
            operator=operator,
        )
