"""Bot Health Checker Service 实现。

提供 Bot 级别的沙箱实例批量查询、TTL 管理、健康检查能力。
"""

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from secbaas.community.api.health_check.bot import (
    AliveDeviceInfo,
    BotAliveCheckResult,
    BotDeviceInfo,
    BotHealthCheckerConfig,
    BotHealthCheckerError,
    BotHealthCheckResult,
    DeviceAliveStatus,
    DeviceProviderType,
    FailedDeviceInfo,
    PaasDeviceInfo,
    PaasDeviceListResponse,
    SandboxNotFoundError,
    TTLExtendResult,
    UnsupportedDeviceProviderError,
    resolve_alive_check_strategy,
    resolve_health_check_strategy,
)
from secbaas.community.api.health_check.bot import (
    BotHealthCheckerService as BotHealthCheckerServiceProtocol,
)
from secbaas.community.api.health_check.paas import (
    HealthCheckerStrategyResult,
    PaasHealthCheckerResult,
)
from secbaas.community.core.service.health_check.paas import PaaSHealthProviderFactory
from secbaas.community.core.service.paas import PaasServiceFacade
from secbaas.community.logger import get_logger

from ._device_source_provider import DeviceSourceProvider
from ._personal_device_provider import PersonalDeviceProvider
from ._service_device_provider import ServiceDeviceProvider

if TYPE_CHECKING:
    from secbaas.community.core.repository.device import DeviceRepository
    from secbaas.community.core.repository.device_binding import (
        DeviceBindingRepository,
    )
    from secbaas.community.core.service.device_binding_query import (
        DeviceBindingQueryService,
    )

logger = get_logger("core-service")


def _determine_alive_status(
    provider_type: str | None,
    checker_result: HealthCheckerStrategyResult,
    minutes: int,
    now: datetime | None = None,
) -> tuple[DeviceAliveStatus, str | None]:
    """根据 provider_type 和 checker 结果判定设备活跃状态。

    Arca 分支（provider_type.upper() == "ARCA"）：
      基于 healthy + lastSessionTime + hasEnabledCron + minutes 判定。
    非 Arca 分支：
      healthy=True → live，healthy=False → error。

    Args:
        provider_type: 设备 provider 类型
        checker_result: checker 返回结果
        minutes: 活跃时间窗口（分钟）
        now: 当前时间（可选，用于测试注入）

    Returns:
        (DeviceAliveStatus, error_msg) 元组
    """
    # 非 Arca 分支：K8S/Docker checker 语义为"容器是否存活"
    if not provider_type or provider_type.upper() != "ARCA":
        if checker_result.healthy:
            return DeviceAliveStatus.LIVE, None
        return DeviceAliveStatus.ERROR, checker_result.error

    # Arca 分支
    if not checker_result.healthy:
        return DeviceAliveStatus.ERROR, checker_result.error

    response = checker_result.response or {}
    last_session_time = response.get("lastSessionTime", "")
    has_enabled_cron = response.get("hasEnabledCron", False)

    # 解析 lastSessionTime
    session_parsed = False
    session_within_window = False
    if last_session_time:
        try:
            session_dt = datetime.strptime(last_session_time, "%Y-%m-%d %H:%M:%S")
            reference_time = now or datetime.now()
            diff_minutes = (reference_time - session_dt).total_seconds() / 60
            session_parsed = True
            if diff_minutes <= minutes:
                session_within_window = True
        except (ValueError, TypeError):
            # 解析失败，视为无效时间
            pass

    # 优先级 1：lastSessionTime 在时间窗口内 → live
    if session_within_window:
        return DeviceAliveStatus.LIVE, None

    # 优先级 2：有启用的 cron → live
    if has_enabled_cron:
        return DeviceAliveStatus.LIVE, None

    # 优先级 3：lastSessionTime 解析成功但超出窗口 → idle
    if session_parsed:
        return DeviceAliveStatus.IDLE, None

    # 优先级 4：无有效时间且无 cron → unknown
    return DeviceAliveStatus.UNKNOWN, None


class BotHealthCheckerService(BotHealthCheckerServiceProtocol):
    """Bot Health Checker Service。

    提供四个核心能力：
    1. list_all_active_bot_device - 获取所有活跃 Bot 设备（分页）
    2. list_paas_device_by_bot - 获取指定 Bot 的 PaaS 设备
    3. extend_ttl_by_bot - 延长 Bot 设备 TTL
    4. check_health_by_bot - 检查 Bot 设备健康状态

    采用两层 Provider 架构：
    - Layer 1: DeviceSourceProvider (Personal/Service) - 路由数据查询
    - Layer 2: PaaSHealthProvider (Arca/Sigma/Local) - 执行健康检查
    """

    def __init__(
        self,
        device_binding_repo: "DeviceBindingRepository",
        device_repo: "DeviceRepository",
        paas_facade: "PaasServiceFacade",
        config: BotHealthCheckerConfig | None = None,
        health_provider_factory: PaaSHealthProviderFactory | None = None,
        query_service: "DeviceBindingQueryService | None" = None,
    ):
        """初始化 BotHealthCheckerService。

        Args:
            device_binding_repo: 设备绑定仓库
            device_repo: 设备仓库（用于 baas_device 表查询）
            paas_facade: PaaS 服务门面
            config: 配置对象，为 None 时使用默认配置
            health_provider_factory: PaaS Health Provider 工厂，为 None 时创建默认实例
            query_service: 跨表查询编排服务，为 None 时回退到 repository 直接查询
        """
        self._device_binding_repo = device_binding_repo
        self._device_repo = device_repo
        self._paas_facade = paas_facade
        self._config = config or BotHealthCheckerConfig()
        self._query_service = query_service

        # 初始化 Provider（传入 config 和 query_service）
        self._device_providers: dict[DeviceProviderType, DeviceSourceProvider] = {
            DeviceProviderType.ARCA: PersonalDeviceProvider(
                device_binding_repo=device_binding_repo,
                paas_facade=paas_facade,
                config=self._config,
                query_service=query_service,
            ),
            DeviceProviderType.BAAS: ServiceDeviceProvider(
                device_binding_repo=device_binding_repo,
                paas_facade=paas_facade,
                config=self._config,
                query_service=query_service,
            ),
        }

        # 使用依赖注入的工厂或创建默认实例
        self._health_provider_factory = (
            health_provider_factory
            or PaaSHealthProviderFactory(
                paas_facade=paas_facade,
                timeout_seconds=self._config.health_check_timeout,
            )
        )

    async def list_all_active_bot_device(
        self,
        page: int = 1,
        page_size: int = 20,
        bot_type: str | None = None,
        env: str = "prod",
    ) -> tuple[int, list[BotDeviceInfo]]:
        """获取所有活跃的 Bot 设备信息（分页）。

        Args:
            page: 页码（从 1 开始）
            page_size: 每页大小
            bot_type: Bot 类型过滤 (personal/service)，None 表示不过滤
            env: 环境参数，默认 prod

        Returns:
            (总数, BotDeviceInfo 列表)

        Raises:
            BotHealthCheckerError: 查询失败
        """
        logger.info(
            f"[BotHealthCheckerService] list_all_active_bot_device: "
            f"page={page}, page_size={page_size}, bot_type={bot_type}, env={env}"
        )

        try:
            # 优先使用 query_service（Python 编排），回退到 repository
            if self._query_service is not None:
                total, bindings = self._query_service.list_all_active_bot_device(
                    page=page,
                    page_size=page_size,
                    env=env,
                    bot_type=bot_type,
                )
            else:
                total, bindings = self._device_binding_repo.list_all_active_bot_device(
                    page=page,
                    page_size=page_size,
                    env=env,
                    bot_type=bot_type,
                )
            items = [BotDeviceInfo.from_binding_dict(b) for b in bindings]
            return total, items

        except Exception as e:
            logger.error(
                f"[BotHealthCheckerService] list_all_active_bot_device failed: {e}"
            )
            raise BotHealthCheckerError(
                f"Failed to list active bot devices: {e}"
            ) from e

    async def _get_bot_binding(
        self,
        bot_id: str,
        entity_id: str,
        env: str = "prod",
    ) -> dict:
        """获取 Bot 绑定信息。

        Args:
            bot_id: Bot ID
            entity_id: 实体 ID
            env: 环境参数，默认 prod

        Returns:
            绑定信息字典，包含 binding_id, bot_type, active_engine 等

        Raises:
            SandboxNotFoundError: Bot 不存在
        """
        if self._query_service is not None:
            binding = self._query_service.get_bot_binding(
                bot_id=bot_id,
                entity_id=entity_id,
                env=env,
            )
        else:
            binding = self._device_binding_repo.get_bot_binding(
                bot_id=bot_id,
                entity_id=entity_id,
                env=env,
            )
        if binding is None:
            raise SandboxNotFoundError(
                f"Bot not found: bot_id={bot_id}, entity_id={entity_id}"
            )
        return binding

    async def list_paas_device_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        statuses: list[str],  # 仅对 service 类型有效，personal 类型忽略此参数
        env: str = "prod",
    ) -> PaasDeviceListResponse:
        """获取指定 Bot 的所有 PaaS 设备信息。

        Args:
            bot_id: Bot ID
            entity_id: 实体 ID
            statuses: 要查询的状态列表（仅对 service 类型有效，personal 类型忽略）
            env: 环境参数，默认 prod

        Returns:
            PaasDeviceListResponse 包含 Bot 级别信息和设备列表

        Raises:
            BotHealthCheckerError: 查询失败
        """
        logger.info(
            f"[BotHealthCheckerService] list_paas_device_by_bot: "
            f"bot_id={bot_id}, entity_id={entity_id}, statuses={statuses}, env={env}"
        )

        try:
            # 1. 获取 Bot 绑定信息
            binding = await self._get_bot_binding(bot_id, entity_id, env)
            bot_type = binding.get("bot_type")
            binding_id = binding.get("binding_id")
            active_engine = binding.get("active_engine")

            if not bot_type:
                raise BotHealthCheckerError(
                    f"Bot bot_type is missing: bot_id={bot_id}, entity_id={entity_id}"
                )

            # 2. 根据 bot_type 选择 Provider 并查询
            # personal 始终用 PersonalDeviceProvider，其内部已根据 device_provider
            # 分支处理 arca（device_props.sandbox_id）和 baas（baas 链路）两种数据源
            if bot_type == "personal":
                provider = self._device_providers[DeviceProviderType.ARCA]
                devices = await provider.list_paas_device_by_bot(
                    bot_id=bot_id,
                    entity_id=entity_id,
                    binding_id=binding_id,
                )
            else:  # service
                provider = self._device_providers[DeviceProviderType.BAAS]
                devices = await provider.list_paas_device_by_bot(
                    bot_id=bot_id,
                    entity_id=entity_id,
                    statuses=statuses,
                    env=env,
                )

            # 3. 对缺少 TTL 的设备进行刷新
            for device in devices:
                if device.ttl_expiration_timestamp is None and device.paas_device_id:
                    try:
                        ttl_ts = await provider.refresh_device_ttl(device)
                        if ttl_ts:
                            logger.info(
                                f"[BotHealthCheckerService] Refreshed TTL for device {device.paas_device_id}: "
                                f"ttl={device.ttl_expiration_time}"
                            )
                    except Exception as e:
                        logger.warning(
                            f"[BotHealthCheckerService] Failed to refresh TTL for device "
                            f"{device.paas_device_id}: {e}"
                        )

            logger.info(
                f"[BotHealthCheckerService] Found {len(devices)} devices for bot_id={bot_id}"
            )
            return PaasDeviceListResponse(
                bot_id=bot_id,
                entity_id=entity_id,
                bot_type=bot_type,
                active_engine=active_engine,
                paas_devices=devices,
            )

        except SandboxNotFoundError:
            raise
        except Exception as e:
            logger.error(
                f"[BotHealthCheckerService] list_paas_device_by_bot failed: {e}"
            )
            raise BotHealthCheckerError(f"Failed to list paas devices: {e}") from e

    async def extend_ttl_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        env: str = "prod",
    ) -> TTLExtendResult:
        """为指定 Bot 的所有设备延长 TTL。

        策略：当剩余 TTL ≤ extend_when_remaining_hours（默认16小时）时延长，
        目标为 now() + target_ttl_hours（默认24小时）。
        内部自动查询所有状态的设备（draft/validating/online）。

        Args:
            bot_id: Bot ID
            entity_id: 实体 ID
            env: 环境参数，默认 prod

        Returns:
            TTLExtendResult 包含延长结果

        Raises:
            BotHealthCheckerError: 操作失败
        """
        logger.info(
            f"[BotHealthCheckerService] extend_ttl_by_bot: "
            f"bot_id={bot_id}, entity_id={entity_id}, env={env}"
        )

        try:
            # 1. 获取 Bot 绑定信息
            binding = await self._get_bot_binding(bot_id, entity_id, env)
            bot_type = binding.get("bot_type")
            binding_id = binding.get("binding_id")

            if not bot_type:
                raise BotHealthCheckerError(
                    f"Bot bot_type is missing: bot_id={bot_id}, entity_id={entity_id}"
                )

            # 2. 根据 bot_type 选择 Provider 并执行 TTL 续期
            # personal 始终用 PersonalDeviceProvider，其内部已根据 device_provider 分支处理
            if bot_type == "personal":
                provider = self._device_providers[DeviceProviderType.ARCA]
                result = await provider.extend_ttl_by_bot(
                    bot_id=bot_id,
                    entity_id=entity_id,
                    binding_id=binding_id,
                )
            else:  # service
                provider = self._device_providers[DeviceProviderType.BAAS]
                result = await provider.extend_ttl_by_bot(
                    bot_id=bot_id,
                    entity_id=entity_id,
                    binding_id=binding_id,
                )

            return result

        except SandboxNotFoundError:
            raise
        except Exception as e:
            logger.error(f"[BotHealthCheckerService] extend_ttl_by_bot failed: {e}")
            raise BotHealthCheckerError(f"Failed to extend TTL: {e}") from e

    async def check_health_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        statuses: list[str],  # 仅对 service 类型有效
        env: str = "prod",
    ) -> BotHealthCheckResult:
        """检查指定 Bot 的所有设备健康状态。

        Args:
            bot_id: Bot ID
            entity_id: 实体 ID
            statuses: 要查询的状态列表（仅对 service 类型有效）
            env: 环境参数，默认 prod

        Returns:
            BotHealthCheckResult 包含各设备的健康检查结果

        Raises:
            SandboxNotFoundError: 未找到设备
            BotHealthCheckerError: 检查失败
        """
        logger.info(
            f"[BotHealthCheckerService] check_health_by_bot: bot_id={bot_id}, "
            f"entity_id={entity_id}, statuses={statuses}, env={env}"
        )

        try:
            # 1. 获取 Bot 绑定信息
            binding = await self._get_bot_binding(bot_id, entity_id, env)
            bot_type = binding.get("bot_type")
            binding_id = binding.get("binding_id")
            active_engine = binding.get("active_engine")

            if not bot_type:
                raise BotHealthCheckerError(
                    f"Bot bot_type is missing: bot_id={bot_id}, entity_id={entity_id}"
                )

            # 2. 获取设备列表
            # personal 始终用 PersonalDeviceProvider，其内部已根据 device_provider 分支处理
            if bot_type == "personal":
                provider = self._device_providers[DeviceProviderType.ARCA]
                device_list = await provider.list_paas_device_by_bot(
                    bot_id=bot_id,
                    entity_id=entity_id,
                    binding_id=binding_id,
                )
            else:  # service
                provider = self._device_providers[DeviceProviderType.BAAS]
                device_list = await provider.list_paas_device_by_bot(
                    bot_id=bot_id,
                    entity_id=entity_id,
                    statuses=statuses,
                    env=env,
                )
            devices = device_list

            if not devices:
                raise SandboxNotFoundError(
                    f"No devices found for bot_id={bot_id}, entity_id={entity_id}"
                )

            # 3. 使用信号量控制并发
            semaphore = asyncio.Semaphore(self._config.health_check_max_concurrent)

            async def _check_single_with_semaphore(
                device: PaasDeviceInfo,
            ) -> tuple[str, PaasHealthCheckerResult] | None:
                async with semaphore:
                    return await self.check_single_device(device, active_engine)

            # 4. 并发执行所有设备的健康检查
            tasks = [_check_single_with_semaphore(device) for device in devices]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)

            # 5. 处理结果
            device_results: list[PaasHealthCheckerResult] = []
            overall_healthy = True
            healthy_count = 0
            unhealthy_count = 0
            failed_devices: list[FailedDeviceInfo] = []

            for device, result in zip(devices, results_list):
                current_healthy = False
                if isinstance(result, Exception):
                    # 单个设备检查异常
                    logger.error(
                        f"[BotHealthCheckerService] Health check exception for {device.paas_device_id}: {result}"
                    )
                    device_results.append(
                        PaasHealthCheckerResult(
                            paas_device_id=device.paas_device_id,
                            overall_healthy=False,
                            checkers={},
                            query_status=device.query_status,
                            source_table=device.source_table,
                            source_table_id=device.source_table_id,
                        )
                    )
                    overall_healthy = False
                    unhealthy_count += 1
                    failed_devices.append(
                        FailedDeviceInfo(
                            paas_device_id=device.paas_device_id,
                            error_message=str(result),
                            failed_checkers=None,
                        )
                    )
                elif result is None:
                    # paas_device_id 为空或 provider_type 为 None，跳过检查
                    skip_reason = (
                        "paas_device_id is empty, skipped"
                        if not device.paas_device_id
                        else "provider_type is None, skipped"
                    )
                    device_results.append(
                        PaasHealthCheckerResult(
                            paas_device_id=device.paas_device_id,
                            overall_healthy=False,
                            checkers={},
                            query_status=device.query_status,
                            source_table=device.source_table,
                            source_table_id=device.source_table_id,
                        )
                    )
                    overall_healthy = False
                    unhealthy_count += 1
                    failed_devices.append(
                        FailedDeviceInfo(
                            paas_device_id=device.paas_device_id,
                            error_message=skip_reason,
                            failed_checkers=None,
                        )
                    )
                else:
                    paas_device_id, health_result = result
                    # 补填 PaasDeviceInfo 上的元数据字段
                    health_result.query_status = device.query_status
                    health_result.source_table = device.source_table
                    health_result.source_table_id = device.source_table_id
                    device_results.append(health_result)
                    current_healthy = health_result.overall_healthy
                    if current_healthy:
                        healthy_count += 1
                    else:
                        unhealthy_count += 1
                        overall_healthy = False
                        # 收集失败的检查器
                        failed_checkers = [
                            name
                            for name, checker in health_result.checkers.items()
                            if not checker.healthy
                        ]
                        failed_devices.append(
                            FailedDeviceInfo(
                                paas_device_id=paas_device_id,
                                error_message=None,
                                failed_checkers=failed_checkers
                                if failed_checkers
                                else None,
                            )
                        )

                logger.info(
                    f"[BotHealthCheckerService] Health check for {device.paas_device_id}: "
                    f"healthy={current_healthy}"
                )

            return BotHealthCheckResult(
                bot_id=bot_id,
                entity_id=entity_id,
                bot_type=bot_type,
                active_engine=active_engine,
                overall_healthy=overall_healthy,
                healthy_count=healthy_count,
                unhealthy_count=unhealthy_count,
                devices=device_results,
                failed_devices=failed_devices,
            )

        except (SandboxNotFoundError, UnsupportedDeviceProviderError):
            raise
        except Exception as e:
            logger.error(f"[BotHealthCheckerService] check_health_by_bot failed: {e}")
            raise BotHealthCheckerError(f"Failed to check health: {e}") from e

    async def check_alive_by_bot(
        self,
        bot_id: str,
        entity_id: str,
        minutes: int = 1440,
        statuses: list[str] | None = None,
        env: str = "prod",
    ) -> BotAliveCheckResult:
        """检查指定 Bot 的所有设备是否活跃（alive）。

        Args:
            bot_id: Bot ID
            entity_id: 实体 ID
            minutes: 检查最近 N 分钟内是否有活跃会话，默认 1440（24小时）
            statuses: 要查询的状态列表（仅 service 类型有效）
            env: 环境参数，默认 prod

        Returns:
            BotAliveCheckResult 包含各设备的活跃检查结果

        Raises:
            SandboxNotFoundError: 未找到设备
            BotHealthCheckerError: 检查失败
        """
        logger.info(
            f"[BotHealthCheckerService] check_alive_by_bot: bot_id={bot_id}, "
            f"entity_id={entity_id}, minutes={minutes}, env={env}"
        )

        try:
            binding = await self._get_bot_binding(bot_id, entity_id, env)
            bot_type = binding.get("bot_type")
            binding_id = binding.get("binding_id")
            active_engine = binding.get("active_engine")

            if not bot_type:
                raise BotHealthCheckerError(
                    f"Bot bot_type is missing: bot_id={bot_id}, entity_id={entity_id}"
                )

            # desktop 类型无需做 alive 检查，直接返回 overall_alive=None
            if bot_type == "desktop":
                return BotAliveCheckResult(
                    bot_id=bot_id,
                    entity_id=entity_id,
                    bot_type=bot_type,
                    active_engine=active_engine,
                    minutes=minutes,
                    overall_alive=None,
                    live_count=0,
                    idle_count=0,
                    unknown_count=0,
                    error_count=0,
                    devices=[],
                )

            # 获取设备列表
            # personal 始终用 PersonalDeviceProvider，其内部已根据 device_provider 分支处理
            if bot_type == "personal":
                provider = self._device_providers[DeviceProviderType.ARCA]
                device_list = await provider.list_paas_device_by_bot(
                    bot_id=bot_id,
                    entity_id=entity_id,
                    binding_id=binding_id,
                )
            else:
                provider = self._device_providers[DeviceProviderType.BAAS]
                device_list = await provider.list_paas_device_by_bot(
                    bot_id=bot_id,
                    entity_id=entity_id,
                    statuses=statuses or ["online"],
                    env=env,
                )

            if not device_list:
                raise SandboxNotFoundError(
                    f"No devices found for bot_id={bot_id}, entity_id={entity_id}"
                )

            # 并发执行 alive 检查
            semaphore = asyncio.Semaphore(self._config.health_check_max_concurrent)

            async def check_single_alive(
                device: PaasDeviceInfo,
            ) -> tuple[str, HealthCheckerStrategyResult, list[str]] | None:
                async with semaphore:
                    if not device.paas_device_id or device.provider_type is None:
                        return None

                    # 使用策略解析获取 alive 检查器列表
                    alive_checkers = resolve_alive_check_strategy(
                        device.provider_type, active_engine
                    )
                    if not alive_checkers:
                        # 不支持的引擎/provider 组合
                        return None  # 标记为 unsupported，由外部处理

                    health_provider = self._health_provider_factory.get(
                        device.provider_type
                    )
                    return (
                        device.paas_device_id,
                        await health_provider.check_alive(
                            paas_device_id=device.paas_device_id,
                            minutes=minutes,
                            checkers=alive_checkers,
                        ),
                        alive_checkers,
                    )

            tasks = [check_single_alive(device) for device in device_list]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理结果
            device_results: list[AliveDeviceInfo] = []
            live_count = 0
            idle_count = 0
            unknown_count = 0
            error_count = 0

            for device, result in zip(device_list, results_list):
                if isinstance(result, Exception):
                    logger.error(
                        f"[BotHealthCheckerService] Alive check exception for {device.paas_device_id}: {result}"
                    )
                    device_results.append(
                        AliveDeviceInfo(
                            paas_device_id=device.paas_device_id,
                            status=DeviceAliveStatus.ERROR,
                            error=str(result),
                        )
                    )
                    error_count += 1
                elif result is None:
                    # 区分 missing identity 和 unsupported
                    if not device.paas_device_id or device.provider_type is None:
                        # missing identity → error
                        skip_reason = (
                            "paas_device_id is empty"
                            if not device.paas_device_id
                            else "provider_type is None"
                        )
                        device_results.append(
                            AliveDeviceInfo(
                                paas_device_id=device.paas_device_id,
                                status=DeviceAliveStatus.ERROR,
                                error=skip_reason,
                            )
                        )
                        error_count += 1
                    else:
                        # 策略返回空列表 = 不支持 → unknown
                        device_results.append(
                            AliveDeviceInfo(
                                paas_device_id=device.paas_device_id,
                                status=DeviceAliveStatus.UNKNOWN,
                                error=f"alive check not supported for engine: {active_engine}, provider: {device.provider_type}",
                            )
                        )
                        unknown_count += 1
                else:
                    paas_device_id, checker_result, _ = result
                    last_session_time = (
                        checker_result.response.get("lastSessionTime")
                        if checker_result.response
                        else None
                    )
                    alive_status, alive_error = _determine_alive_status(
                        device.provider_type, checker_result, minutes
                    )
                    device_results.append(
                        AliveDeviceInfo(
                            paas_device_id=paas_device_id,
                            status=alive_status,
                            last_session_time=last_session_time,
                            error=alive_error,
                        )
                    )
                    if alive_status == DeviceAliveStatus.LIVE:
                        live_count += 1
                    elif alive_status == DeviceAliveStatus.IDLE:
                        idle_count += 1
                    elif alive_status == DeviceAliveStatus.UNKNOWN:
                        unknown_count += 1
                    elif alive_status == DeviceAliveStatus.ERROR:
                        error_count += 1

            # 计算 overall_alive：全 live → True，全 idle → False，其余 → None
            statuses = [d.status for d in device_results]
            if all(s == DeviceAliveStatus.LIVE for s in statuses):
                overall_alive = True
            elif all(s == DeviceAliveStatus.IDLE for s in statuses):
                overall_alive = False
            else:
                overall_alive = None

            return BotAliveCheckResult(
                bot_id=bot_id,
                entity_id=entity_id,
                bot_type=bot_type,
                active_engine=active_engine,
                minutes=minutes,
                overall_alive=overall_alive,
                live_count=live_count,
                idle_count=idle_count,
                unknown_count=unknown_count,
                error_count=error_count,
                devices=device_results,
            )

        except (SandboxNotFoundError, UnsupportedDeviceProviderError):
            raise
        except Exception as e:
            logger.error(f"[BotHealthCheckerService] check_alive_by_bot failed: {e}")
            raise BotHealthCheckerError(f"Failed to check alive: {e}") from e

    async def check_single_device(
        self,
        device: PaasDeviceInfo,
        active_engine: str | None = None,
    ) -> tuple[str, PaasHealthCheckerResult] | None:
        """对单个设备执行健康检查。

        Args:
            device: PaaS 设备信息
            active_engine: 引擎类型（可选），None 时使用 fallback 检查器

        Returns:
            (paas_device_id, PaasHealthCheckerResult) 或 None（跳过检查时）
        """
        logger.info(
            f"[BotHealthCheckerService.check_single_device] device="
            f"paas_device_id={device.paas_device_id!r}, "
            f"provider_type={device.provider_type!r}, "
            f"device_uuid={device.device_uuid!r}, "
            f"status={device.status!r}, "
            f"source_table={device.source_table!r}, "
            f"source_table_id={device.source_table_id!r}, "
            f"active_engine={active_engine!r}"
        )

        if not device.paas_device_id:
            logger.warning(
                "[BotHealthCheckerService] Skipping health check: "
                "paas_device_id is empty"
            )
            return None

        if device.provider_type is None:
            logger.warning(
                f"[BotHealthCheckerService] Skipping health check for "
                f"{device.paas_device_id}: provider_type is None"
            )
            return None

        health_provider = self._health_provider_factory.get(device.provider_type)
        logger.info(
            f"[BotHealthCheckerService.check_single_device] Resolved "
            f"health_provider={type(health_provider).__name__} "
            f"for provider_type={device.provider_type}"
        )
        device_checkers = resolve_health_check_strategy(
            device.provider_type, active_engine
        )
        logger.info(
            f"[BotHealthCheckerService.check_single_device] Resolved "
            f"checkers={device_checkers} "
            f"for provider_type={device.provider_type}, active_engine={active_engine}"
        )
        try:
            result = await health_provider.check_health(
                paas_device_id=device.paas_device_id,
                checkers=device_checkers,
            )
            logger.info(
                f"[BotHealthCheckerService.check_single_device] Result for "
                f"{device.paas_device_id}: "
                f"overall_healthy={result.overall_healthy}, "
                f"checker_count={len(result.checkers)}"
            )
            return device.paas_device_id, result
        except Exception as e:
            logger.error(
                f"[BotHealthCheckerService.check_single_device] check_health "
                f"failed for {device.paas_device_id}: {e}",
                exc_info=True,
            )
            raise

    async def get_sandbox_info(
        self,
        sandbox_id: str,
    ) -> dict[str, Any] | None:
        """通过 sandbox_id 反查沙箱信息。

        优先查询 ac_entity_device_binding 表，若无结果则查询 baas_device 表。

        Args:
            sandbox_id: 沙箱 ID（可能带 @0 后缀）

        Returns:
            包含沙箱信息的字典，或 None（如果不存在）

        Raises:
            BotHealthCheckerError: 查询失败
        """
        # 去掉 @0 后缀进行模糊查询
        sandbox_id_prefix = sandbox_id.split("@")[0]

        logger.info(
            f"[BotHealthCheckerService] get_sandbox_info: sandbox_id={sandbox_id}, "
            f"sandbox_id_prefix={sandbox_id_prefix}"
        )

        try:
            # 1. 优先查询 ac_entity_device_binding 表
            binding = self._device_binding_repo.get_binding_by_sandbox_id_like(
                sandbox_id_prefix=sandbox_id_prefix
            )

            if binding is not None:
                device_props = binding.device_props or {}
                paas_device_id = device_props.get("sandbox_id")

                result = {
                    "sandbox_id": (paas_device_id or sandbox_id).split("@")[0],
                    "paas_device_id": paas_device_id,
                    "status": binding.status,
                    "source_table": "ac_binding",
                    "source_table_id": str(binding.id),
                    "device_provider": binding.device_provider,
                    "env": binding.env,
                }

                logger.info(
                    f"[BotHealthCheckerService] Found in ac_binding: sandbox_id={sandbox_id}, "
                    f"device_provider={result.get('device_provider')}, env={result.get('env')}"
                )
                return result

            # 2. 查询 baas_device 表
            device = self._device_repo.get_by_provider_device_id_like(
                provider_device_id_prefix=sandbox_id_prefix,
            )

            if device is None:
                logger.info(
                    f"[BotHealthCheckerService] Sandbox not found: sandbox_id={sandbox_id}"
                )
                return None

            result = {
                "sandbox_id": device.provider_device_id.split("@")[0],
                "paas_device_id": device.provider_device_id,
                "status": device.status,
                "source_table": "baas_device",
                "source_table_id": str(device.id),
                "device_provider": "baas",
                "env": device.env,
            }

            logger.info(
                f"[BotHealthCheckerService] Found in baas_device: sandbox_id={sandbox_id}, "
                f"device_provider={result.get('device_provider')}, env={result.get('env')}"
            )
            return result

        except Exception as e:
            logger.error(
                f"[BotHealthCheckerService] get_sandbox_info failed: {e}",
                exc_info=True,
            )
            raise BotHealthCheckerError(f"Failed to get sandbox info: {e}") from e
