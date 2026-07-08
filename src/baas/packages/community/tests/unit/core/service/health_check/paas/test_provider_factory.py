"""Unit tests for PaaSHealthProviderFactory."""

from unittest.mock import MagicMock

import pytest

from secbaas.core.service.health_check.paas._arca_paas_health_provider import (
    ArcaPaaSHealthProvider,
)
from secbaas.core.service.health_check.paas._k8s_paas_health_provider import (
    K8sPaaSHealthProvider,
)
from secbaas.core.service.health_check.paas._local_paas_health_provider import (
    LocalPaaSHealthProvider,
)
from secbaas.core.service.health_check.paas._paas_health_provider_factory import (
    PaaSHealthProviderFactory,
)
from secbaas.core.service.health_check.paas._sigma_paas_health_provider import (
    SigmaPaaSHealthProvider,
)


class TestPaaSHealthProviderFactory:
    """Tests for PaaSHealthProviderFactory."""

    @pytest.fixture
    def mock_facade(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def mock_k8s_client_manager(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def factory(self, mock_facade: MagicMock) -> PaaSHealthProviderFactory:
        return PaaSHealthProviderFactory(paas_facade=mock_facade, timeout_seconds=10)

    def test_get_arca_returns_arca_provider(
        self, factory: PaaSHealthProviderFactory
    ) -> None:
        provider = factory.get("ARCA")
        assert isinstance(provider, ArcaPaaSHealthProvider)

    def test_get_sigma_returns_sigma_provider(
        self, factory: PaaSHealthProviderFactory
    ) -> None:
        provider = factory.get("SIGMA")
        assert isinstance(provider, SigmaPaaSHealthProvider)

    def test_get_local_returns_local_provider(
        self, factory: PaaSHealthProviderFactory
    ) -> None:
        provider = factory.get("LOCAL")
        assert isinstance(provider, LocalPaaSHealthProvider)

    def test_get_lowercase_arca_returns_arca(
        self, factory: PaaSHealthProviderFactory
    ) -> None:
        provider = factory.get("arca")
        assert isinstance(provider, ArcaPaaSHealthProvider)

    def test_get_none_returns_local(self, factory: PaaSHealthProviderFactory) -> None:
        provider = factory.get(None)
        assert isinstance(provider, LocalPaaSHealthProvider)

    def test_get_unknown_returns_local(
        self, factory: PaaSHealthProviderFactory
    ) -> None:
        provider = factory.get("UNKNOWN")
        assert isinstance(provider, LocalPaaSHealthProvider)

    def test_caching_returns_same_instance(
        self, factory: PaaSHealthProviderFactory
    ) -> None:
        p1 = factory.get("ARCA")
        p2 = factory.get("ARCA")
        assert p1 is p2

    def test_different_types_not_shared(
        self, factory: PaaSHealthProviderFactory
    ) -> None:
        arca = factory.get("ARCA")
        local = factory.get("LOCAL")
        assert arca is not local

    def test_case_insensitive_caching(self, factory: PaaSHealthProviderFactory) -> None:
        """'arca' and 'ARCA' should map to the same cached instance (both upper-cased)."""
        # The factory uppercases the type, so both keys become "ARCA"
        lower = factory.get("arca")
        upper = factory.get("ARCA")
        p3 = factory.get("Arca")
        assert lower is upper
        assert upper is p3

    def test_default_timeout(self) -> None:
        factory = PaaSHealthProviderFactory(paas_facade=MagicMock())
        provider = factory.get("ARCA")
        assert provider._timeout_seconds == 10

    def test_custom_timeout(self) -> None:
        factory = PaaSHealthProviderFactory(paas_facade=MagicMock(), timeout_seconds=30)
        provider = factory.get("ARCA")
        assert provider._timeout_seconds == 30

    def test_get_k8s_returns_k8s_provider(
        self, mock_facade: MagicMock, mock_k8s_client_manager: MagicMock
    ) -> None:
        """Factory with k8s_client_manager returns K8sPaaSHealthProvider for 'K8S'."""
        factory = PaaSHealthProviderFactory(
            paas_facade=mock_facade,
            timeout_seconds=10,
            k8s_client_manager=mock_k8s_client_manager,
        )
        provider = factory.get("K8S")
        assert isinstance(provider, K8sPaaSHealthProvider)

    def test_get_k8s_lowercase_returns_k8s_provider(
        self, mock_facade: MagicMock, mock_k8s_client_manager: MagicMock
    ) -> None:
        """Case-insensitive: 'k8s' also returns K8sPaaSHealthProvider."""
        factory = PaaSHealthProviderFactory(
            paas_facade=mock_facade,
            timeout_seconds=10,
            k8s_client_manager=mock_k8s_client_manager,
        )
        provider = factory.get("k8s")
        assert isinstance(provider, K8sPaaSHealthProvider)

    def test_get_k8s_missing_client_manager_raises(
        self, factory: PaaSHealthProviderFactory
    ) -> None:
        """Factory without k8s_client_manager raises RuntimeError for 'K8S'."""
        with pytest.raises(RuntimeError, match="k8s_client_manager"):
            factory.get("K8S")

    def test_k8s_caching_returns_same_instance(
        self,
        mock_facade: MagicMock,
        mock_k8s_client_manager: MagicMock,
    ) -> None:
        """K8S provider is cached — same instance returned on repeated calls."""
        factory = PaaSHealthProviderFactory(
            paas_facade=mock_facade,
            timeout_seconds=10,
            k8s_client_manager=mock_k8s_client_manager,
        )
        p1 = factory.get("K8S")
        p2 = factory.get("K8S")
        assert p1 is p2

    def test_k8s_caching_case_insensitive(
        self,
        mock_facade: MagicMock,
        mock_k8s_client_manager: MagicMock,
    ) -> None:
        """'K8S' and 'k8s' cache to the same instance."""
        factory = PaaSHealthProviderFactory(
            paas_facade=mock_facade,
            timeout_seconds=10,
            k8s_client_manager=mock_k8s_client_manager,
        )
        upper = factory.get("K8S")
        lower = factory.get("k8s")
        assert upper is lower
