"""Comprehensive unit tests for K8sPaaSHealthProvider.

Tests cover:
- ReadinessChecker: healthy/unhealthy/no-probe/exception scenarios
- LivenessChecker: Pod phase checks, terminated containers, exceptions
- K8sPaaSHealthProvider: check_health, check_alive, pod name parsing,
  error handling, concurrent execution, single pod read optimization
- Stub integration: synthetic probe data from StubK8sSandboxPlugin
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.device_manage._device_info import PodInfo
from secbaas.community.api.health_check.paas import (
    HealthCheckerStrategyResult,
    PaasHealthCheckerResult,
)
from secbaas.community.core.service.health_check.paas._k8s_paas_health_provider import (
    K8sPaaSHealthProvider,
    LivenessChecker,
    ReadinessChecker,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def create_mock_pod(phase: str = "Running", container_statuses=None):
    """Create a mock V1Pod with the given phase and container_statuses.

    Args:
        phase: Pod status phase string (e.g., "Running", "Failed").
        container_statuses: List of dicts with name, ready, restart_count, state,
            and image keys. Each dict is converted to a MagicMock with the
            corresponding attributes.

    Returns:
        MagicMock simulating a V1Pod.
    """
    pod = MagicMock()
    pod.status.phase = phase

    def _make_container_status(
        name="bot", ready=True, restart_count=0, state="running", terminated=False
    ):
        """Create a single container status MagicMock.

        By default, state has no ``terminated`` sub-attribute (i.e. ``getattr``
        returns None), matching a running K8s container. When ``terminated=True``
        the mock deliberately has ``state.terminated`` set so the LivenessChecker
        treats the container as terminated.
        """
        cs = MagicMock()
        cs.name = name
        cs.ready = ready
        cs.restart_count = restart_count
        state_mock = MagicMock()
        state_mock.type = state
        if terminated:
            state_mock.terminated = MagicMock()
        else:
            # Explicitly set to None so ``getattr(state, "terminated", None)``
            # returns None — a bare MagicMock would return a truthy auto-created
            # child mock.
            state_mock.terminated = None
        cs.state = state_mock
        return cs

    if container_statuses is None:
        pod.status.container_statuses = [_make_container_status()]
    elif isinstance(container_statuses, list):
        mocks = []
        for item in container_statuses:
            if isinstance(item, dict):
                mocks.append(
                    _make_container_status(
                        name=item.get("name", "bot"),
                        ready=item.get("ready", True),
                        restart_count=item.get("restart_count", 0),
                        state=item.get("state", "running"),
                        terminated=item.get("terminated", False),
                    )
                )
            else:
                mocks.append(item)
        pod.status.container_statuses = mocks
    else:
        pod.status.container_statuses = container_statuses

    return pod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_k8s_client_manager() -> MagicMock:
    """Mock K8sClientManager with get_or_create_default_client and _run_sync."""
    cm = MagicMock()
    cm.get_or_create_default_client.return_value = MagicMock()
    cm._run_sync = AsyncMock()
    return cm


@pytest.fixture
def provider(mock_k8s_client_manager: MagicMock) -> K8sPaaSHealthProvider:
    """Create a K8sPaaSHealthProvider with mocked K8sClientManager."""
    return K8sPaaSHealthProvider(
        k8s_client_manager=mock_k8s_client_manager,
        namespace="test-ns",
        timeout_seconds=10,
    )


# ---------------------------------------------------------------------------
# TestReadinessChecker
# ---------------------------------------------------------------------------


class TestReadinessChecker:
    """Tests for the ReadinessChecker."""

    def test_all_containers_ready(self) -> None:
        """All containers report ready=True -> healthy=True."""
        checker = ReadinessChecker()
        pod = create_mock_pod(
            container_statuses=[
                {"name": "bot", "ready": True},
                {"name": "sidecar", "ready": True},
            ]
        )
        result = checker.check("test-device--0", pod)
        assert result.healthy is True
        assert result.error is None
        assert result.response == {"container_count": 2}

    def test_one_container_not_ready(self) -> None:
        """One container not ready -> healthy=False, TRANSIENT error with container name."""
        checker = ReadinessChecker()
        pod = create_mock_pod(
            container_statuses=[
                {"name": "bot", "ready": False},
                {"name": "sidecar", "ready": True},
            ]
        )
        result = checker.check("test-device--0", pod)
        assert result.healthy is False
        assert "TRANSIENT" in result.error
        assert "bot" in result.error

    def test_no_container_statuses(self) -> None:
        """No container_statuses (None) -> healthy=True (D-03 default)."""
        checker = ReadinessChecker()
        pod = MagicMock()
        pod.status.container_statuses = None
        result = checker.check("test-device--0", pod)
        assert result.healthy is True
        assert result.error is None

    def test_empty_container_statuses(self) -> None:
        """Empty container_statuses list -> healthy=True."""
        checker = ReadinessChecker()
        pod = create_mock_pod(container_statuses=[])
        result = checker.check("test-device--0", pod)
        assert result.healthy is True
        assert result.error is None

    def test_exception_in_check(self) -> None:
        """Exception during check -> healthy=False, error string captured."""
        checker = ReadinessChecker()
        pod = MagicMock()
        # AttributeError when accessing pod.status.container_statuses
        del pod.status  # type: ignore[attr-defined]
        result = checker.check("test-device--0", pod)
        assert result.healthy is False
        assert result.error is not None


# ---------------------------------------------------------------------------
# TestLivenessChecker
# ---------------------------------------------------------------------------


class TestLivenessChecker:
    """Tests for the LivenessChecker."""

    def test_pod_phase_running(self) -> None:
        """Pod phase Running with no terminated containers -> healthy=True."""
        checker = LivenessChecker()
        pod = create_mock_pod(phase="Running")
        result = checker.check("test-device--0", pod)
        assert result.healthy is True
        assert result.response is not None

    def test_pod_phase_failed(self) -> None:
        """Pod phase Failed -> healthy=False, PERMANENT error."""
        checker = LivenessChecker()
        pod = create_mock_pod(phase="Failed")
        result = checker.check("test-device--0", pod)
        assert result.healthy is False
        assert "PERMANENT" in result.error
        assert "Failed" in result.error

    def test_pod_phase_succeeded(self) -> None:
        """Pod phase Succeeded -> healthy=False, PERMANENT error."""
        checker = LivenessChecker()
        pod = create_mock_pod(phase="Succeeded")
        result = checker.check("test-device--0", pod)
        assert result.healthy is False
        assert "PERMANENT" in result.error
        assert "Succeeded" in result.error

    def test_pod_phase_pending(self) -> None:
        """Pod phase Pending -> healthy=False, TRANSIENT error."""
        checker = LivenessChecker()
        pod = create_mock_pod(phase="Pending")
        result = checker.check("test-device--0", pod)
        assert result.healthy is False
        assert "TRANSIENT" in result.error
        assert "pending" in result.error.lower()

    def test_container_terminated(self) -> None:
        """Pod phase Running but container state terminated -> healthy=False."""
        checker = LivenessChecker()
        pod = MagicMock()
        pod.status.phase = "Running"
        cs = MagicMock()
        cs.name = "bot"
        cs.state = MagicMock()
        cs.state.terminated = MagicMock()  # terminated object present
        pod.status.container_statuses = [cs]
        result = checker.check("test-device--0", pod)
        assert result.healthy is False
        assert "terminated" in result.error.lower()

    def test_exception_in_check(self) -> None:
        """Exception during liveness check -> healthy=False, error string captured."""
        checker = LivenessChecker()
        pod = MagicMock()
        # Make pod.status non-existent to trigger AttributeError
        del pod.status  # type: ignore[attr-defined]
        result = checker.check("test-device--0", pod)
        assert result.healthy is False
        assert result.error is not None


# ---------------------------------------------------------------------------
# TestK8sPaaSHealthProvider
# ---------------------------------------------------------------------------


class TestK8sPaaSHealthProvider:
    """Tests for K8sPaaSHealthProvider check_health and check_alive."""

    # -- check_health -----------------------------------------------------

    @pytest.mark.asyncio
    async def test_check_health_all_ready(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """check_health returns overall_healthy=True when all containers ready."""
        mock_pod = create_mock_pod(
            container_statuses=[
                {"name": "bot", "ready": True},
            ]
        )
        provider._k8s_client_manager._run_sync.return_value = mock_pod  # type: ignore[union-attr]

        result = await provider.check_health("stss--0", ["readiness"])
        assert isinstance(result, PaasHealthCheckerResult)
        assert result.overall_healthy is True
        assert result.paas_device_id == "stss--0"
        assert len(result.checkers) == 1
        assert result.checkers["readiness"].healthy is True

    @pytest.mark.asyncio
    async def test_check_health_not_ready(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """check_health returns overall_healthy=False when a container is not ready."""
        mock_pod = create_mock_pod(
            container_statuses=[
                {"name": "bot", "ready": False},
            ]
        )
        provider._k8s_client_manager._run_sync.return_value = mock_pod  # type: ignore[union-attr]

        result = await provider.check_health("stss--0", ["readiness"])
        assert result.overall_healthy is False
        assert "TRANSIENT" in result.checkers["readiness"].error

    @pytest.mark.asyncio
    async def test_check_health_empty_checkers(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """check_health with empty checkers list -> overall_healthy=True, checkers={}."""
        result = await provider.check_health("stss--0", [])
        assert result.overall_healthy is True
        assert result.checkers == {}

    @pytest.mark.asyncio
    async def test_check_health_pod_not_found(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """check_health when pod not found (404) -> NOT_FOUND error with _pod_read key."""
        provider._k8s_client_manager._run_sync.side_effect = RuntimeError(  # type: ignore[union-attr]
            "K8s API error (404): Not Found"
        )

        result = await provider.check_health("stss--0", ["readiness"])
        assert result.overall_healthy is False
        assert "_pod_read" in result.checkers
        assert "NOT_FOUND" in result.checkers["_pod_read"].error

    @pytest.mark.asyncio
    async def test_check_health_platform_unavailable(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """check_health when K8s API connection refused -> PLATFORM_UNAVAILABLE."""
        provider._k8s_client_manager._run_sync.side_effect = RuntimeError(  # type: ignore[union-attr]
            "Connection refused"
        )

        result = await provider.check_health("stss--0", ["readiness"])
        assert result.overall_healthy is False
        assert "_pod_read" in result.checkers
        assert "PLATFORM_UNAVAILABLE" in result.checkers["_pod_read"].error

    @pytest.mark.asyncio
    async def test_check_health_invalid_device_id(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """check_health with invalid device_id -> PLATFORM_UNAVAILABLE for parse error."""
        result = await provider.check_health("invalid", ["readiness"])
        assert result.overall_healthy is False
        assert "_pod_read" in result.checkers
        assert "PLATFORM_UNAVAILABLE" in result.checkers["_pod_read"].error

    # -- check_alive ------------------------------------------------------

    @pytest.mark.asyncio
    async def test_check_alive_liveness_ok(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """check_alive with liveness checker, Running pod -> healthy=True."""
        mock_pod = create_mock_pod(phase="Running")
        provider._k8s_client_manager._run_sync.return_value = mock_pod  # type: ignore[union-attr]

        result = await provider.check_alive("stss--0", checkers=["liveness"])
        assert isinstance(result, HealthCheckerStrategyResult)
        assert result.healthy is True

    @pytest.mark.asyncio
    async def test_check_alive_terminated(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """check_alive with Failed phase pod -> healthy=False, PERMANENT."""
        mock_pod = create_mock_pod(phase="Failed")
        provider._k8s_client_manager._run_sync.return_value = mock_pod  # type: ignore[union-attr]

        result = await provider.check_alive("stss--0", checkers=["liveness"])
        assert result.healthy is False
        assert "PERMANENT" in result.error

    @pytest.mark.asyncio
    async def test_check_alive_pending(self, provider: K8sPaaSHealthProvider) -> None:
        """check_alive with Pending pod -> healthy=False, TRANSIENT."""
        mock_pod = create_mock_pod(phase="Pending")
        provider._k8s_client_manager._run_sync.return_value = mock_pod  # type: ignore[union-attr]

        result = await provider.check_alive("stss--0", checkers=["liveness"])
        assert result.healthy is False
        assert "TRANSIENT" in result.error

    @pytest.mark.asyncio
    async def test_check_alive_without_checkers_raises(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """check_alive with checkers=None -> raises ValueError."""
        with pytest.raises(ValueError, match="check_alive requires checkers"):
            await provider.check_alive("stss--0", checkers=None)

    @pytest.mark.asyncio
    async def test_check_alive_empty_checkers_raises(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """check_alive with checkers=[] -> raises ValueError."""
        with pytest.raises(ValueError, match="check_alive requires checkers"):
            await provider.check_alive("stss--0", checkers=[])

    @pytest.mark.asyncio
    async def test_check_alive_pod_not_found(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """check_alive when pod not found (404) -> healthy=False, NOT_FOUND error."""
        provider._k8s_client_manager._run_sync.side_effect = RuntimeError(  # type: ignore[union-attr]
            "K8s API error (404): Not Found"
        )

        result = await provider.check_alive("stss--0", checkers=["liveness"])
        assert result.healthy is False
        assert "NOT_FOUND" in result.error

    # -- _parse_pod_name --------------------------------------------------

    def test_parse_pod_name_valid(self, provider: K8sPaaSHealthProvider) -> None:
        """Valid paas_device_id 'stss--0' -> 'stss-0'."""
        assert provider._parse_pod_name("stss--0") == "stss-0"

    def test_parse_pod_name_invalid_no_separator(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """Invalid paas_device_id without '--' separator -> RuntimeError(422)."""
        with pytest.raises(RuntimeError, match=r"\(422\)"):
            provider._parse_pod_name("invalid")

    def test_parse_pod_name_invalid_ordinal(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """Invalid paas_device_id with non-numeric ordinal -> RuntimeError(422)."""
        with pytest.raises(RuntimeError, match=r"\(422\)"):
            provider._parse_pod_name("stss--abc")

    def test_parse_pod_name_negative_ordinal(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """Invalid paas_device_id with negative ordinal -> RuntimeError(422)."""
        with pytest.raises(RuntimeError, match=r"\(422\)"):
            provider._parse_pod_name("stss---1")

    # -- Concurrent execution ---------------------------------------------

    @pytest.mark.asyncio
    async def test_concurrent_checker_execution(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """Both readiness and liveness checkers execute and produce results."""
        mock_pod = create_mock_pod(
            phase="Running",
            container_statuses=[{"name": "bot", "ready": True}],
        )
        provider._k8s_client_manager._run_sync.return_value = mock_pod  # type: ignore[union-attr]

        result = await provider.check_health("stss--0", ["readiness", "liveness"])
        assert len(result.checkers) == 2
        assert "readiness" in result.checkers
        assert "liveness" in result.checkers
        assert result.checkers["readiness"].healthy is True
        assert result.checkers["liveness"].healthy is True
        assert result.overall_healthy is True

    @pytest.mark.asyncio
    async def test_single_pod_read_per_check_health(
        self, provider: K8sPaaSHealthProvider
    ) -> None:
        """check_health with multiple checkers makes only one _read_pod call (D-04)."""
        mock_pod = create_mock_pod(
            phase="Running",
            container_statuses=[{"name": "bot", "ready": True}],
        )
        provider._k8s_client_manager._run_sync.return_value = mock_pod  # type: ignore[union-attr]

        with patch.object(provider, "_read_pod", wraps=provider._read_pod) as mock_read:
            await provider.check_health("stss--0", ["readiness", "liveness"])
            assert mock_read.call_count == 1


# ---------------------------------------------------------------------------
# TestStubIntegration
# ---------------------------------------------------------------------------


class TestStubIntegration:
    """Integration-style tests with StubK8sSandboxPlugin synthetic probe data."""

    def test_health_with_stub_default_pods(self) -> None:
        """StubK8sSandboxPlugin with default pods -> ReadinessChecker reports healthy."""
        from secbaas.community.plugins.sandbox.k8s.stub._stub_k8s_sandbox import (
            StubK8sSandboxPlugin,
        )

        plugin = StubK8sSandboxPlugin()
        device = plugin.create_device(
            template_id=1,
            template_uuid="uuid-1",
            tenant_name="test",
            namespace="default",
            image="test:latest",
            cpu_request="100m",
            cpu_limit="200m",
            memory_request="128Mi",
            memory_limit="256Mi",
        )
        info = device.get_info()
        container_statuses = info.get("container_statuses", [])
        # Convert stub dicts to mock pod
        mock_pod = create_mock_pod(container_statuses=container_statuses)

        checker = ReadinessChecker()
        result = checker.check("stss--0", mock_pod)
        assert result.healthy is True

    def test_health_with_stub_unhealthy_pods(self) -> None:
        """StubK8sSandboxPlugin with unhealthy pods -> ReadinessChecker reports unhealthy."""
        from secbaas.community.plugins.sandbox.k8s.stub._stub_k8s_sandbox import (
            StubK8sSandboxPlugin,
        )

        plugin = StubK8sSandboxPlugin(
            default_pods=[
                PodInfo(name="bot", ready=False, restart_count=0, state="waiting")
            ]
        )
        device = plugin.create_device(
            template_id=1,
            template_uuid="uuid-1",
            tenant_name="test",
            namespace="default",
            image="test:latest",
            cpu_request="100m",
            cpu_limit="200m",
            memory_request="128Mi",
            memory_limit="256Mi",
        )
        info = device.get_info()
        container_statuses = info.get("container_statuses", [])
        mock_pod = create_mock_pod(container_statuses=container_statuses)

        checker = ReadinessChecker()
        result = checker.check("stss--0", mock_pod)
        assert result.healthy is False
