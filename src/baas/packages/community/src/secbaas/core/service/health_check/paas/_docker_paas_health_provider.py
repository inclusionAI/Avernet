"""Docker 平台健康检查实现。"""

import asyncio
import json
import time
from typing import TYPE_CHECKING

from secbaas.api.health_check.paas import (
    HealthCheckerStrategyResult,
    PaasHealthCheckerResult,
)
from secbaas.logger import get_logger

from ._paas_health_provider import PaaSHealthProvider

if TYPE_CHECKING:
    from secbaas.core.service.paas import PaasServiceFacade

log = get_logger("docker-paas-health-provider")


class EchoHealthChecker:
    """Echo 探活检查器。

    通过 docker exec 执行 echo 命令探活容器。与 ArcaPaaSHealthProvider 的
    EchoHealthChecker 使用相同的 echo payload 格式。
    """

    @property
    def name(self) -> str:
        return "echo"

    async def check(
        self,
        paas_device_id: str,
        paas_facade: "PaasServiceFacade",
    ) -> HealthCheckerStrategyResult:
        start_time = time.time()
        cmd = 'echo \'{"code": 0, "status": "alive"}\''
        try:
            result = await paas_facade.execute_command(
                paas_device_id=paas_device_id,
                cmd=cmd,
                timeout_seconds=10,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            if result.exit_code != 0:
                return HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=(
                        f"Command failed with exit code {result.exit_code}: "
                        f"{result.stderr}"
                    ),
                    timeout=False,
                    duration_ms=duration_ms,
                )

            response = json.loads(result.stdout)
            return HealthCheckerStrategyResult(
                healthy=True,
                response=response,
                error=None,
                timeout=False,
                duration_ms=duration_ms,
            )

        except TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error="Timeout after 10s",
                timeout=True,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            log.error(f"[EchoHealthChecker] check failed: {e}")
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=str(e),
                timeout=False,
                duration_ms=duration_ms,
            )


class ContainerStatusHealthChecker:
    """容器状态检查器。

    通过 paas_facade.get_device_info() 获取容器 attrs 中的 State.Status 字段。
    仅 status=="running" 返回 healthy=True，其他状态均为 unhealthy。
    """

    @property
    def name(self) -> str:
        return "container_status"

    async def check(
        self,
        paas_device_id: str,
        paas_facade: "PaasServiceFacade",
    ) -> HealthCheckerStrategyResult:
        start_time = time.time()
        try:
            device_info = await paas_facade.get_device_info(paas_device_id)
            duration_ms = int((time.time() - start_time) * 1000)

            status = device_info.status
            healthy = status == "running"

            return HealthCheckerStrategyResult(
                healthy=healthy,
                response={
                    "status": status,
                    "platform": device_info.platform,
                },
                error=None
                if healthy
                else f"Container status is '{status}' (expected 'running')",
                timeout=False,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = str(e)
            is_timeout = (
                "PLATFORM_UNAVAILABLE" in error_msg
                or "Timeout" in error_msg
                or "timeout" in error_msg
            )
            log.error(f"[ContainerStatusHealthChecker] check failed: {e}")
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=error_msg,
                timeout=is_timeout,
                duration_ms=duration_ms,
            )


class EngineAdapterHealthChecker:
    """Engine/Adapter 应用层健康检查器。

    通过 docker exec 在容器内执行 curl 命令调用 health endpoint，
    验证应用层是否正常响应。
    """

    def __init__(self, container_port: int = 8080, health_endpoint: str = "/health"):
        self._container_port = container_port
        self._health_endpoint = health_endpoint

    @property
    def name(self) -> str:
        return "engine_adapter"

    async def check(
        self,
        paas_device_id: str,
        paas_facade: "PaasServiceFacade",
    ) -> HealthCheckerStrategyResult:
        start_time = time.time()
        cmd = f"curl -s http://127.0.0.1:{self._container_port}{self._health_endpoint}"
        try:
            result = await paas_facade.execute_command(
                paas_device_id=paas_device_id,
                cmd=cmd,
                timeout_seconds=10,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            if result.exit_code != 0:
                return HealthCheckerStrategyResult(
                    healthy=False,
                    response=None,
                    error=(
                        f"Command failed with exit code {result.exit_code}: "
                        f"{result.stderr}"
                    ),
                    timeout=False,
                    duration_ms=duration_ms,
                )

            return HealthCheckerStrategyResult(
                healthy=True,
                response={"stdout": result.stdout, "exit_code": result.exit_code},
                error=None,
                timeout=False,
                duration_ms=duration_ms,
            )

        except TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error="Timeout after 10s",
                timeout=True,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            log.error(f"[EngineAdapterHealthChecker] check failed: {e}")
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=str(e),
                timeout=False,
                duration_ms=duration_ms,
            )


class DockerPaaSHealthProvider(PaaSHealthProvider):
    """Docker 平台健康检查实现。

    支持 echo, container_status, engine_adapter 三个组件的检查。
    """

    def __init__(
        self,
        paas_facade: "PaasServiceFacade",
        timeout_seconds: int = 10,
        container_port: int = 8080,
        health_endpoint: str = "/health",
    ):
        self._paas_facade = paas_facade
        self._timeout_seconds = timeout_seconds
        self._all_checkers = {
            "echo": EchoHealthChecker(),
            "container_status": ContainerStatusHealthChecker(),
            "engine_adapter": EngineAdapterHealthChecker(
                container_port=container_port,
                health_endpoint=health_endpoint,
            ),
        }
        self._logger = get_logger("docker-paas-health-provider")

    async def check_health(
        self,
        paas_device_id: str,
        checkers: list[str],
    ) -> PaasHealthCheckerResult:
        """执行健康检查。

        Args:
            paas_device_id: PaaS 设备 ID
            checkers: 需要执行的检查器列表

        Returns:
            PaasHealthCheckerResult 包含各检查器的结果
        """
        self._logger.info(
            "[DockerPaaSHealthProvider] check_health for %s, checkers=%s",
            paas_device_id,
            checkers,
        )

        selected_checkers = {
            name: checker
            for name, checker in self._all_checkers.items()
            if name in checkers
        }

        if not selected_checkers:
            self._logger.error(
                "[DockerPaaSHealthProvider] No valid checkers for %s", checkers
            )
            return PaasHealthCheckerResult(
                paas_device_id=paas_device_id,
                overall_healthy=True,
                checkers={},
            )

        async def run_checker(
            name: str,
            checker: EchoHealthChecker
            | ContainerStatusHealthChecker
            | EngineAdapterHealthChecker,
        ) -> tuple[str, HealthCheckerStrategyResult]:
            try:
                result = await asyncio.wait_for(
                    checker.check(paas_device_id, self._paas_facade),
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
                self._logger.error(
                    "[DockerPaaSHealthProvider] checker %s failed: %s", name, e
                )
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
        """检查设备是否活跃（alive）。

        委托给 echo checker 作为活体检测。

        Args:
            paas_device_id: PaaS 设备 ID
            minutes: 检查最近 N 分钟内是否有活跃会话（未使用，保留以匹配接口）
            checkers: 需要执行的检查器列表（未提供时默认使用 echo checker）

        Returns:
            HealthCheckerStrategyResult 包含活跃检查结果
        """
        self._logger.info(
            "[DockerPaaSHealthProvider] check_alive for %s, minutes=%s, checkers=%s",
            paas_device_id,
            minutes,
            checkers,
        )

        # If specific checkers provided, use the first available one
        if checkers:
            selected = {
                name: checker
                for name, checker in self._all_checkers.items()
                if name in checkers
            }
            if selected:
                for name, checker in selected.items():
                    return await checker.check(paas_device_id, self._paas_facade)
            raise ValueError(
                f"No matching alive checker for {checkers}, "
                f"available: {list(self._all_checkers.keys())}"
            )

        # Default: delegate to echo checker
        checker = self._all_checkers.get("echo")
        return await checker.check(paas_device_id, self._paas_facade)
