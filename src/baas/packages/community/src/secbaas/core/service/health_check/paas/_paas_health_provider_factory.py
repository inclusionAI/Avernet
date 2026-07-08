"""PaaS Health Provider 工厂类。

根据 provider_type 返回对应的健康检查实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from secbaas.api.health_check.paas import PaaSProviderType
from secbaas.core.service.paas import PaasServiceFacade

from ._arca_paas_health_provider import ArcaPaaSHealthProvider
from ._docker_paas_health_provider import DockerPaaSHealthProvider
from ._k8s_paas_health_provider import K8sPaaSHealthProvider
from ._local_paas_health_provider import LocalPaaSHealthProvider
from ._paas_health_provider import PaaSHealthProvider
from ._poolab_paas_health_provider import PoolabPaaSHealthProvider
from ._sigma_paas_health_provider import SigmaPaaSHealthProvider

if TYPE_CHECKING:
    from secbaas.spi.sandbox.k8s import K8sClientManager


class PaaSHealthProviderFactory:
    """健康检查 Provider 工厂。

    根据 provider_type (ARCA/SIGMA/LOCAL/DOCKER/POOLAB/TECLAW/K8S) 返回对应的健康检查实现。
    """

    def __init__(
        self,
        paas_facade: PaasServiceFacade,
        timeout_seconds: int = 10,
        k8s_client_manager: K8sClientManager | None = None,
    ):
        self._paas_facade = paas_facade
        self._timeout_seconds = timeout_seconds
        self._k8s_client_manager = k8s_client_manager
        self._providers: dict[str, PaaSHealthProvider] = {}

    def get(self, provider_type: str | None) -> PaaSHealthProvider:
        """根据 provider_type 获取健康检查 Provider。

        Args:
            provider_type: 平台类型 (ARCA/SIGMA/LOCAL/DOCKER/POOLAB/TECLAW/K8S).
                           None 返回 LocalPaaSHealthProvider。

        Returns:
            对应的 PaaSHealthProvider 实例
        """
        if provider_type is None:
            return LocalPaaSHealthProvider()

        provider_type_upper = provider_type.upper()

        if provider_type_upper not in self._providers:
            self._providers[provider_type_upper] = self._create_provider(
                provider_type_upper
            )

        return self._providers[provider_type_upper]

    def _create_provider(self, provider_type: str) -> PaaSHealthProvider:
        """创建健康检查 Provider 实例。

        Supported types: ARCA, SIGMA, POOLAB, TECLAW, K8S, LOCAL.
        K8S returns K8sPaaSHealthProvider (Phase 8).
        Unknown types return LocalPaaSHealthProvider as fallback.
        """
        if provider_type == PaaSProviderType.ARCA.value.upper():
            return ArcaPaaSHealthProvider(
                paas_facade=self._paas_facade,
                timeout_seconds=self._timeout_seconds,
            )
        elif provider_type == PaaSProviderType.SIGMA.value.upper():
            return SigmaPaaSHealthProvider()
        elif provider_type == PaaSProviderType.POOLAB.value.upper():
            return PoolabPaaSHealthProvider(
                paas_facade=self._paas_facade,
                timeout_seconds=self._timeout_seconds,
            )
        elif provider_type == PaaSProviderType.TECLAW.value.upper():
            return LocalPaaSHealthProvider()
        elif provider_type == PaaSProviderType.K8S.value.upper():
            if self._k8s_client_manager is None:
                raise RuntimeError(
                    "k8s_client_manager is required for K8sPaaSHealthProvider "
                    "(container wiring missing k8s_client_manager dependency)"
                )
            return K8sPaaSHealthProvider(
                k8s_client_manager=self._k8s_client_manager,
                namespace="default",
                timeout_seconds=self._timeout_seconds,
            )
        elif provider_type == PaaSProviderType.DOCKER.value.upper():
            return DockerPaaSHealthProvider(
                paas_facade=self._paas_facade,
                timeout_seconds=self._timeout_seconds,
            )
        elif provider_type == PaaSProviderType.LOCAL.value.upper():
            return LocalPaaSHealthProvider()
        else:
            # 未知类型，返回默认健康状态
            return LocalPaaSHealthProvider()
