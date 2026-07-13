"""K8s 平台健康检查实现。

通过直接读取 K8s Pod 探针状态（readiness/liveness）报告平台健康，
不使用 exec-based 检查。遵循 ArcaPaaSHealthProvider 的多 checker + asyncio.gather 模式。

读操作:
- ReadinessChecker: 读取 container_statuses[].ready 判断容器就绪状态
- LivenessChecker: 读取 Pod Phase 和 container state 判断存活状态
- K8sPaaSHealthProvider: 编排 checker 并发执行，统一处理 K8s API 错误
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from secbaas.community.api.health_check.paas import (
    HealthCheckerStrategyResult,
    PaasHealthCheckerResult,
)
from secbaas.community.logger import get_logger

if TYPE_CHECKING:
    from secbaas.community.spi.sandbox.k8s import K8sClientManager

from ._paas_health_provider import PaaSHealthProvider

logger = get_logger("core-service")


class ReadinessChecker:
    """K8s readiness probe checker.

    读取 container_statuses[].ready 判断容器是否就绪。
    当无探针配置时默认 healthy=True（K8s 默认语义）。
    """

    @property
    def name(self) -> str:
        return "readiness"

    def check(self, paas_device_id: str, pod: object) -> HealthCheckerStrategyResult:
        """Check readiness from V1Pod container_statuses.

        Args:
            paas_device_id: PaaS 设备 ID
            pod: V1Pod instance from K8s API

        Returns:
            HealthCheckerStrategyResult with healthy status and container details.
        """
        start_time = time.time()
        try:
            container_statuses = getattr(pod.status, "container_statuses", None) or []
            if not container_statuses:
                duration_ms = int((time.time() - start_time) * 1000)
                return HealthCheckerStrategyResult(
                    healthy=True,
                    response=None,
                    error=None,
                    timeout=False,
                    duration_ms=duration_ms,
                )

            all_ready = all(cs.ready for cs in container_statuses)
            duration_ms = int((time.time() - start_time) * 1000)

            if all_ready:
                return HealthCheckerStrategyResult(
                    healthy=True,
                    response={"container_count": len(container_statuses)},
                    error=None,
                    timeout=False,
                    duration_ms=duration_ms,
                )

            not_ready_names = [cs.name for cs in container_statuses if not cs.ready]
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=f"TRANSIENT: readiness probe failing for: {', '.join(not_ready_names)}",
                timeout=False,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=str(e),
                timeout=False,
                duration_ms=duration_ms,
            )


class LivenessChecker:
    """K8s liveness probe checker.

    通过 Pod Phase 和 container state 判断容器是否存活。
    不支持 exec-based 检查，仅通过 K8s API 数据判断。
    """

    @property
    def name(self) -> str:
        return "liveness"

    def check(self, paas_device_id: str, pod: object) -> HealthCheckerStrategyResult:
        """Check liveness from V1Pod status.

        Args:
            paas_device_id: PaaS 设备 ID
            pod: V1Pod instance from K8s API

        Returns:
            HealthCheckerStrategyResult with healthy status and phase details.
        """
        start_time = time.time()
        try:
            phase = getattr(pod.status, "phase", "Unknown")
            if phase in ("Failed", "Succeeded"):
                duration_ms = int((time.time() - start_time) * 1000)
                return HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=f"PERMANENT: Pod phase={phase}",
                    timeout=False,
                    duration_ms=duration_ms,
                )

            if phase == "Pending":
                duration_ms = int((time.time() - start_time) * 1000)
                return HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error="TRANSIENT: Pod pending",
                    timeout=False,
                    duration_ms=duration_ms,
                )

            # 检查 container state: 如果任何容器处于 terminated 状态，视为不健康
            container_statuses = getattr(pod.status, "container_statuses", None) or []
            for cs in container_statuses:
                state = getattr(cs, "state", None)
                if state is not None:
                    terminated = getattr(state, "terminated", None)
                    if terminated is not None:
                        duration_ms = int((time.time() - start_time) * 1000)
                        return HealthCheckerStrategyResult(
                            healthy=False,
                            response=None,
                            error=f"TRANSIENT: container '{cs.name}' is terminated",
                            timeout=False,
                            duration_ms=duration_ms,
                        )

            # Phase is Running + no terminated containers = healthy
            duration_ms = int((time.time() - start_time) * 1000)
            return HealthCheckerStrategyResult(
                healthy=True,
                response={"phase": phase, "container_count": len(container_statuses)},
                error=None,
                timeout=False,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=str(e),
                timeout=False,
                duration_ms=duration_ms,
            )


class K8sPaaSHealthProvider(PaaSHealthProvider):
    """K8s 平台健康检查实现。

    支持 readiness 和 liveness 两个组件的检查。
    通过 K8sClientManager.get_or_create_default_client() 获取 API client，
    不需要 K8sCredentials DI 注入（DI 简化）。

    遵循 ArcaPaaSHealthProvider 的模式：
    - checker 类（ReadinessChecker, LivenessChecker）
    - asyncio.gather 并发执行
    - run_checker wrapper 统一超时和异常处理
    """

    def __init__(
        self,
        k8s_client_manager: K8sClientManager,
        namespace: str = "default",
        timeout_seconds: int = 10,
    ):
        """Initialize the K8sPaaSHealthProvider.

        Args:
            k8s_client_manager: K8sClientManager instance for K8s API access.
            namespace: K8s namespace where pods reside. Defaults to "default".
            timeout_seconds: Timeout in seconds for each checker execution.
        """
        self._k8s_client_manager = k8s_client_manager
        self._namespace = namespace
        self._timeout_seconds = timeout_seconds
        self._all_checkers: dict[str, ReadinessChecker | LivenessChecker] = {
            "readiness": ReadinessChecker(),
            "liveness": LivenessChecker(),
        }

    def _parse_pod_name(self, paas_device_id: str) -> str:
        """Parse paas_device_id into a Pod name.

        Format: "{statefulset_name}--{ordinal}" per D-01.
        Pod name is derived as "{statefulset_name}-{ordinal}".

        Identical logic to RealK8sSandbox._parse_pod_name.

        Args:
            paas_device_id: PaaS device ID in StatefulSet format.

        Returns:
            Pod name string.

        Raises:
            RuntimeError(422): If the paas_device_id format is invalid.
        """
        parts = paas_device_id.split("--", maxsplit=1)
        if len(parts) != 2:
            raise RuntimeError(f"Invalid paas_device_id format: {paas_device_id} (422)")
        statefulset_name, ordinal_str = parts
        try:
            ordinal = int(ordinal_str)
        except ValueError:
            raise RuntimeError(
                f"Invalid paas_device_id ordinal: {paas_device_id} (422)"
            ) from None
        if ordinal < 0:
            raise RuntimeError(
                f"Invalid paas_device_id ordinal (must be >= 0): {paas_device_id} (422)"
            ) from None
        return f"{statefulset_name}-{ordinal}"

    async def _read_pod(self, pod_name: str) -> object:
        """Read pod from K8s API using the async bridge.

        Uses K8sClientManager.get_or_create_default_client() to get an API client,
        then calls CoreV1Api.read_namespaced_pod() via _run_sync().

        Args:
            pod_name: The name of the K8s Pod to read.

        Returns:
            V1Pod instance from the K8s API.

        Raises:
            RuntimeError: If the K8s API call fails. Error message includes
                the HTTP status code for error classification.
        """
        try:
            from kubernetes.client import CoreV1Api
            from kubernetes.client.rest import ApiException
        except (ImportError, ModuleNotFoundError) as e:
            raise RuntimeError(f"K8s SDK not available: {e}") from e

        client = self._k8s_client_manager.get_or_create_default_client()
        core_api = CoreV1Api(client)

        def _do_read_pod() -> object:
            return core_api.read_namespaced_pod(
                name=pod_name,
                namespace=self._namespace,
            )

        try:
            return await self._k8s_client_manager._run_sync(_do_read_pod)
        except ApiException as e:
            # Match _map_error pattern: include HTTP status code for classification
            status_code = getattr(e, "status", None)
            if status_code is not None:
                reason = getattr(e, "reason", "Unknown")
                raise RuntimeError(f"K8s API error ({status_code}): {reason}") from e
            raise RuntimeError(f"K8s API error: {e}") from e

    async def check_health(
        self,
        paas_device_id: str,
        checkers: list[str],
    ) -> PaasHealthCheckerResult:
        """执行健康检查。

        单个 _read_pod() 调用获取 V1Pod，所有 checker 共享该对象。
        使用 asyncio.gather 并发执行 checker，与 ArcaPaaSHealthProvider 模式对齐。

        Args:
            paas_device_id: PaaS 设备 ID (格式: "{statefulset_name}--{ordinal}")
            checkers: 需要执行的检查器列表 (如 ["readiness"])

        Returns:
            PaasHealthCheckerResult 包含各检查器的结果。
        """
        logger.info(
            "[K8sPaaSHealthProvider] check_health for %s, checkers=%s",
            paas_device_id,
            checkers,
        )

        # 解析 Pod 名称
        try:
            pod_name = self._parse_pod_name(paas_device_id)
        except RuntimeError as e:
            logger.error("[K8sPaaSHealthProvider] failed to parse device_id: %s", e)
            return PaasHealthCheckerResult(
                paas_device_id=paas_device_id,
                overall_healthy=False,
                checkers={
                    "_pod_read": HealthCheckerStrategyResult(
                        healthy=False,
                        response=None,
                        error=f"PLATFORM_UNAVAILABLE: {e}",
                        timeout=False,
                        duration_ms=0,
                    ),
                },
            )

        # 选择需要执行的检查器
        selected_checkers: dict[str, ReadinessChecker | LivenessChecker] = {
            name: checker
            for name, checker in self._all_checkers.items()
            if name in checkers
        }

        if not selected_checkers:
            logger.warning("[K8sPaaSHealthProvider] No valid checkers for %s", checkers)
            return PaasHealthCheckerResult(
                paas_device_id=paas_device_id,
                overall_healthy=True,
                checkers={},
            )

        # 单个 K8s API 调用来获取 Pod（D-04: 缓存共享）
        try:
            pod = await self._read_pod(pod_name)
        except RuntimeError as e:
            error_msg = str(e)
            # D-09: Pod 404 分类为 NOT_FOUND
            if "(404)" in error_msg:
                error_prefix = f"NOT_FOUND: Pod {pod_name} not found"
            else:
                error_prefix = f"PLATFORM_UNAVAILABLE: {error_msg}"
            logger.warning("[K8sPaaSHealthProvider] Pod read failed: %s", error_prefix)
            return PaasHealthCheckerResult(
                paas_device_id=paas_device_id,
                overall_healthy=False,
                checkers={
                    "_pod_read": HealthCheckerStrategyResult(
                        healthy=False,
                        response=None,
                        error=error_prefix,
                        timeout=False,
                        duration_ms=0,
                    ),
                },
            )

        # 并发执行检查器
        async def run_checker(
            name: str,
            checker: ReadinessChecker | LivenessChecker,
        ) -> tuple[str, HealthCheckerStrategyResult]:
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(checker.check, paas_device_id, pod),
                    timeout=self._timeout_seconds,
                )
                return name, result
            except TimeoutError:
                return name, HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=f"Timeout after {self._timeout_seconds}s",
                    timeout=True,
                    duration_ms=int(self._timeout_seconds * 1000),
                )
            except Exception as e:
                logger.error("[K8sPaaSHealthProvider] checker %s failed: %s", name, e)
                return name, HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=str(e),
                    timeout=False,
                    duration_ms=0,
                )

        tasks = [
            run_checker(name, checker) for name, checker in selected_checkers.items()
        ]
        results_list = await asyncio.gather(*tasks)
        results = dict(results_list)

        overall_healthy = all(r.healthy for r in results.values())

        logger.info(
            "[K8sPaaSHealthProvider] check_health complete: "
            "overall_healthy=%s, checkers=%s",
            overall_healthy,
            list(results.keys()),
        )

        return PaasHealthCheckerResult(
            paas_device_id=paas_device_id,
            overall_healthy=overall_healthy,
            checkers=results,
        )

    async def check_alive(
        self,
        paas_device_id: str,
        minutes: int = 1440,
        checkers: list[str] | None = None,
    ) -> HealthCheckerStrategyResult:
        """检查设备是否存活（alive）。

        使用策略指定的检查器，取第一个匹配的 checker 执行。
        与 ArcaPaaSHealthProvider.check_alive 模式对齐。

        Args:
            paas_device_id: PaaS 设备 ID
            minutes: 检查最近 N 分钟内是否有活跃会话（K8s health check 不使用）
            checkers: 需要执行的检查器列表，必须由策略解析提供

        Returns:
            HealthCheckerStrategyResult 包含存活检查结果。

        Raises:
            ValueError: checkers 为空或未提供时。
        """
        if not checkers:
            raise ValueError(
                f"check_alive requires checkers to be specified, "
                f"got checkers={checkers} for device {paas_device_id}. "
                f"Use resolve_alive_check_strategy to determine checkers."
            )

        # 使用策略指定的检查器
        selected: dict[str, ReadinessChecker | LivenessChecker] = {
            name: checker
            for name, checker in self._all_checkers.items()
            if name in checkers
        }
        if not selected:
            raise ValueError(
                f"No valid alive checkers found for {checkers}, "
                f"available: {list(self._all_checkers.keys())}"
            )

        # 解析 Pod 名称并读取 Pod
        try:
            pod_name = self._parse_pod_name(paas_device_id)
        except RuntimeError as e:
            logger.error(
                "[K8sPaaSHealthProvider] check_alive: failed to parse device_id: %s",
                e,
            )
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=f"PLATFORM_UNAVAILABLE: {e}",
                timeout=False,
                duration_ms=0,
            )

        try:
            pod = await self._read_pod(pod_name)
        except RuntimeError as e:
            error_msg = str(e)
            if "(404)" in error_msg:
                error_prefix = f"NOT_FOUND: Pod {pod_name} not found"
            else:
                error_prefix = f"PLATFORM_UNAVAILABLE: {error_msg}"
            logger.warning(
                "[K8sPaaSHealthProvider] check_alive: Pod read failed: %s",
                error_prefix,
            )
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=error_prefix,
                timeout=False,
                duration_ms=0,
            )

        # 对 alive 检查，取第一个匹配的 checker 执行
        for name, checker in selected.items():
            return await asyncio.to_thread(checker.check, paas_device_id, pod)
