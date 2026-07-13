"""Poolab 平台健康检查实现。"""

import asyncio
import time
from typing import TYPE_CHECKING

from secbaas.community.api.health_check.paas import (
    HealthCheckerStrategyResult,
    PaasHealthCheckerResult,
)
from secbaas.community.logger import get_logger

from ._paas_health_provider import PaaSHealthProvider

if TYPE_CHECKING:
    from secbaas.community.core.service.paas import PaasServiceFacade

logger = get_logger("core-service")


class ApiHealthChecker:
    """Poolab API 健康检查器。

    端点: GET /openapi/antclaw/getMachine/{id}
    健康判断: 调用成功返回 healthy=True
    """

    @property
    def name(self) -> str:
        return "api"

    async def check(
        self,
        paas_device_id: str,
        paas_facade: "PaasServiceFacade",
    ) -> HealthCheckerStrategyResult:
        start_time = time.time()
        try:
            device_info = await paas_facade.get_device_info(paas_device_id)
            duration_ms = int((time.time() - start_time) * 1000)

            return HealthCheckerStrategyResult(
                healthy=True,
                response={
                    "platform": device_info.platform,
                    "poolab_id": device_info.poolab_id,
                    "poolab_status": device_info.poolab_status,
                },
                error=None,
                timeout=False,
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            # Detect timeout semantics from underlying PaasError/DeviceFacadeException
            # (aiohttp timeouts are caught internally and re-raised, so TimeoutError
            # never propagates to this handler — see cr-8896 for rationale).
            error_msg = str(e)
            is_timeout = (
                "PLATFORM_UNAVAILABLE" in error_msg
                or "Timeout" in error_msg
                or "timeout" in error_msg
            )
            logger.error(f"[ApiHealthChecker] check failed: {e}")
            return HealthCheckerStrategyResult(
                healthy=False,
                response=None,
                error=error_msg,
                timeout=is_timeout,
                duration_ms=duration_ms,
            )


class PoolabPaaSHealthProvider(PaaSHealthProvider):
    """Poolab 平台健康检查实现。

    支持 api 组件的检查，通过调用 Poolab REST API 的 get_device_info
    端点验证 API 可达性和鉴权有效性。
    """

    def __init__(self, paas_facade: "PaasServiceFacade", timeout_seconds: int = 10):
        self._paas_facade = paas_facade
        self._timeout_seconds = timeout_seconds
        self._all_checkers = {
            "api": ApiHealthChecker(),
        }
        self._logger = get_logger("core-service")

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
            f"[PoolabPaaSHealthProvider] check_health for {paas_device_id}, checkers={checkers}"
        )

        selected_checkers = {
            name: checker
            for name, checker in self._all_checkers.items()
            if name in checkers
        }

        if not selected_checkers:
            self._logger.warning(
                f"[PoolabPaaSHealthProvider] No valid checkers for {checkers}"
            )
            return PaasHealthCheckerResult(
                paas_device_id=paas_device_id,
                overall_healthy=True,
                checkers={},
            )

        async def run_checker(
            name: str,
            checker: ApiHealthChecker,
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
                    f"[PoolabPaaSHealthProvider] checker {name} failed: {e}"
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
