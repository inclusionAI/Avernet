"""Tests for LocalInstanceRouter.

Per Architecture Rule 25: Every Protocol must have contract tests.
These tests validate the LocalInstanceRouter implementation.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from secbaas.core.service.paas.desktop.instance_router._config import (
    InstanceRouterConfig,
)
from secbaas.core.service.paas.desktop.instance_router._exceptions import (
    ForwardHTTPError,
    ForwardTimeoutError,
)
from secbaas.core.service.paas.desktop.instance_router._local_instance_router import (
    LocalInstanceRouter,
)
from tests.utils import load_web_port


class TestInstanceRouterConfig:
    """Tests for InstanceRouterConfig."""

    def test_default_values(self) -> None:
        """Test that default config values are reasonable."""
        port = load_web_port()
        config = InstanceRouterConfig(internal_port=port)

        assert config.connect_timeout == 5.0
        assert config.read_timeout == 30.0
        assert config.pool_timeout == 5.0
        assert config.max_connections == 100
        assert config.max_keepalive == 20
        assert config.internal_port == port

    def test_custom_values(self) -> None:
        """Test that custom values are accepted."""
        config = InstanceRouterConfig(
            connect_timeout=10.0,
            read_timeout=60.0,
            pool_timeout=10.0,
            max_connections=200,
            max_keepalive=50,
            internal_port=8080,
        )

        assert config.connect_timeout == 10.0
        assert config.read_timeout == 60.0
        assert config.pool_timeout == 10.0
        assert config.max_connections == 200
        assert config.max_keepalive == 50
        assert config.internal_port == 8080

    def test_timeout_dict(self) -> None:
        """Test get_timeout_dict returns correct values."""
        config = InstanceRouterConfig(
            internal_port=load_web_port(),
            connect_timeout=10.0,
            read_timeout=60.0,
            pool_timeout=15.0,
        )
        timeout_dict = config.get_timeout_dict()

        assert timeout_dict == {
            "connect": 10.0,
            "read": 60.0,
            "pool": 15.0,
        }

    def test_invalid_values(self) -> None:
        """Test that invalid values raise ValueError."""
        with pytest.raises(ValueError, match="connect_timeout must be positive"):
            InstanceRouterConfig(internal_port=8889, connect_timeout=0)

        with pytest.raises(ValueError, match="read_timeout must be positive"):
            InstanceRouterConfig(internal_port=8889, read_timeout=-1)

        with pytest.raises(ValueError, match="pool_timeout must be positive"):
            InstanceRouterConfig(internal_port=8889, pool_timeout=0)

        with pytest.raises(ValueError, match="max_connections must be positive"):
            InstanceRouterConfig(internal_port=8889, max_connections=0)

        with pytest.raises(ValueError, match="max_keepalive must be positive"):
            InstanceRouterConfig(internal_port=8889, max_keepalive=0)

        with pytest.raises(
            ValueError, match="max_keepalive cannot exceed max_connections"
        ):
            InstanceRouterConfig(
                internal_port=8889, max_connections=10, max_keepalive=20
            )

        with pytest.raises(
            ValueError, match="internal_port must be a valid port number"
        ):
            InstanceRouterConfig(internal_port=99999)


class TestLocalInstanceRouter:
    """Tests for LocalInstanceRouter."""

    @pytest.fixture
    def mock_repository(self) -> MagicMock:
        """Create a mock repository."""
        return MagicMock()

    @pytest.fixture
    def mock_http_client(self) -> AsyncMock:
        """Create a mock HTTP client."""
        return AsyncMock()

    @pytest.fixture
    def router(
        self, mock_repository: MagicMock, mock_http_client: AsyncMock
    ) -> LocalInstanceRouter:
        """Create a LocalInstanceRouter with mocked dependencies."""
        config = InstanceRouterConfig(internal_port=load_web_port())
        return LocalInstanceRouter(
            repository=mock_repository,
            config=config,
            http_client=mock_http_client,
        )

    def test_init_with_config(self, mock_repository: MagicMock) -> None:
        """Test initialization with a config."""
        config = InstanceRouterConfig(internal_port=load_web_port())
        router = LocalInstanceRouter(repository=mock_repository, config=config)

        assert router._repository is mock_repository
        assert isinstance(router._config, InstanceRouterConfig)
        assert router._client is not None
        assert router._client._trust_env is False

    @pytest.mark.asyncio
    async def test_init_ignores_environment_proxy(
        self, mock_repository: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Local routing should not require optional SOCKS proxy dependencies."""
        monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:13659")

        config = InstanceRouterConfig(internal_port=load_web_port())
        router = LocalInstanceRouter(repository=mock_repository, config=config)

        assert router._client._trust_env is False
        await router._client.aclose()

    def test_init_with_custom_config(
        self, mock_repository: MagicMock, mock_http_client: AsyncMock
    ) -> None:
        """Test initialization with custom config."""
        config = InstanceRouterConfig(internal_port=load_web_port(), max_connections=50)
        router = LocalInstanceRouter(
            repository=mock_repository,
            config=config,
            http_client=mock_http_client,
        )

        assert router._config.max_connections == 50
        assert router._client is mock_http_client

    def test_get_instance_for_found(
        self, router: LocalInstanceRouter, mock_repository: MagicMock
    ) -> None:
        """Test get_instance_for returns instance when machine found."""
        # Create mock record
        mock_record = MagicMock()
        mock_record.connected_server_instance = "instance-b"
        mock_repository.get_by_machine_id.return_value = mock_record

        result = router.get_instance_for("machine-1", "dev")

        assert result == "instance-b"
        mock_repository.get_by_machine_id.assert_called_once_with("machine-1", "dev")

    def test_get_instance_for_not_found(
        self, router: LocalInstanceRouter, mock_repository: MagicMock
    ) -> None:
        """Test get_instance_for returns None when machine not found."""
        mock_repository.get_by_machine_id.return_value = None

        result = router.get_instance_for("machine-1", "dev")

        assert result is None

    def test_get_instance_for_no_instance(
        self, router: LocalInstanceRouter, mock_repository: MagicMock
    ) -> None:
        """Test get_instance_for returns None when machine has no instance."""
        mock_record = MagicMock()
        mock_record.connected_server_instance = ""
        mock_repository.get_by_machine_id.return_value = mock_record

        result = router.get_instance_for("machine-1", "dev")

        assert result is None

    @pytest.mark.asyncio
    async def test_route_to_instance_success(
        self, router: LocalInstanceRouter, mock_http_client: AsyncMock
    ) -> None:
        """Test route_to_instance sends correct HTTP request."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "success",
            "data": {"output": "hello"},
        }
        mock_http_client.post.return_value = mock_response

        result = await router.route_to_instance(
            target_instance="instance-b",
            action="execute_command",
            machine_id="machine-1",
            params={"cmd": "ls"},
            request_id="req-123",
        )

        # Verify result
        assert result == {"status": "success", "data": {"output": "hello"}}

        # Verify HTTP call
        mock_http_client.post.assert_called_once()
        call_args = mock_http_client.post.call_args
        assert (
            call_args[0][0]
            == f"http://instance-b:{router._config.internal_port}/internal/v1/forward"
        )
        assert call_args[1]["json"] == {
            "action": "execute_command",
            "machine_id": "machine-1",
            "params": {"cmd": "ls"},
            "request_id": "req-123",
        }
        assert call_args[1]["headers"] == {"X-Request-ID": "req-123"}

    @pytest.mark.asyncio
    async def test_route_to_instance_timeout(
        self, router: LocalInstanceRouter, mock_http_client: AsyncMock
    ) -> None:
        """Test route_to_instance raises ForwardTimeoutError on timeout."""
        mock_http_client.post.side_effect = httpx.TimeoutException("Connection timeout")

        with pytest.raises(ForwardTimeoutError) as exc_info:
            await router.route_to_instance(
                target_instance="instance-b",
                action="execute_command",
                machine_id="machine-1",
                params={"cmd": "ls"},
                request_id="req-123",
            )

        assert exc_info.value.target_instance == "instance-b"
        assert exc_info.value.action == "execute_command"
        assert exc_info.value.timeout == 30.0

    @pytest.mark.asyncio
    async def test_route_to_instance_http_error(
        self, router: LocalInstanceRouter, mock_http_client: AsyncMock
    ) -> None:
        """Test route_to_instance raises ForwardHTTPError on HTTP error."""
        # Setup mock response with error
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"

        # Create HTTPStatusError
        error = httpx.HTTPStatusError(
            "Service Unavailable",
            request=MagicMock(),
            response=mock_response,
        )
        mock_http_client.post.side_effect = error

        with pytest.raises(ForwardHTTPError) as exc_info:
            await router.route_to_instance(
                target_instance="instance-b",
                action="execute_command",
                machine_id="machine-1",
                params={"cmd": "ls"},
                request_id="req-123",
            )

        assert exc_info.value.target_instance == "instance-b"
        assert exc_info.value.status_code == 503
        assert exc_info.value.response_body == "Service Unavailable"

    @pytest.mark.asyncio
    async def test_route_to_instance_connection_error(
        self, router: LocalInstanceRouter, mock_http_client: AsyncMock
    ) -> None:
        """Test route_to_instance handles connection errors."""
        mock_http_client.post.side_effect = httpx.ConnectError("Connection refused")

        with pytest.raises(ForwardHTTPError) as exc_info:
            await router.route_to_instance(
                target_instance="instance-b",
                action="execute_command",
                machine_id="machine-1",
                params={"cmd": "ls"},
                request_id="req-123",
            )

        assert exc_info.value.status_code == 0  # Indicates connection error

    @pytest.mark.asyncio
    async def test_close(
        self, router: LocalInstanceRouter, mock_http_client: AsyncMock
    ) -> None:
        """Test close method closes HTTP client."""
        await router.close()

        mock_http_client.aclose.assert_called_once()
