from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.health_check.paas import (
    HealthCheckerStrategyResult,
    PaasHealthCheckerResult,
)
from secbaas.community.core.service.health_check.paas._docker_paas_health_provider import (
    ContainerStatusHealthChecker,
    DockerPaaSHealthProvider,
    EchoHealthChecker,
    EngineAdapterHealthChecker,
)


class TestEchoHealthChecker:
    @pytest.fixture
    def facade(self):
        f = MagicMock()
        f.execute_command = AsyncMock()
        return f

    @pytest.mark.asyncio
    async def test_name(self):
        assert EchoHealthChecker().name == "echo"

    @pytest.mark.asyncio
    async def test_check_healthy(self, facade):
        result = MagicMock()
        result.exit_code = 0
        result.stdout = '{"code": 0, "status": "alive"}'
        result.stderr = ""
        facade.execute_command.return_value = result
        r = await EchoHealthChecker().check("dev-1", facade)
        assert r.healthy is True
        assert r.response == {"code": 0, "status": "alive"}
        assert r.timeout is False
        assert r.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_check_exit_code_nonzero(self, facade):
        result = MagicMock()
        result.exit_code = 1
        result.stderr = "error msg"
        facade.execute_command.return_value = result
        r = await EchoHealthChecker().check("dev-1", facade)
        assert r.healthy is False
        assert "exit code 1" in r.error
        assert r.timeout is False

    @pytest.mark.asyncio
    async def test_check_timeout(self, facade):
        facade.execute_command.side_effect = TimeoutError()
        r = await EchoHealthChecker().check("dev-1", facade)
        assert r.healthy is False
        assert "Timeout" in r.error
        assert r.timeout is True

    @pytest.mark.asyncio
    async def test_check_json_decode_error(self, facade):
        result = MagicMock()
        result.exit_code = 0
        result.stdout = "not json"
        facade.execute_command.return_value = result
        r = await EchoHealthChecker().check("dev-1", facade)
        assert r.healthy is False
        assert r.timeout is False

    @pytest.mark.asyncio
    async def test_check_generic_exception(self, facade):
        facade.execute_command.side_effect = RuntimeError("boom")
        r = await EchoHealthChecker().check("dev-1", facade)
        assert r.healthy is False
        assert "boom" in r.error
        assert r.timeout is False


class TestContainerStatusHealthChecker:
    @pytest.fixture
    def facade(self):
        f = MagicMock()
        f.get_device_info = AsyncMock()
        return f

    @pytest.mark.asyncio
    async def test_name(self):
        assert ContainerStatusHealthChecker().name == "container_status"

    @pytest.mark.asyncio
    async def test_check_running_healthy(self, facade):
        info = MagicMock()
        info.status = "running"
        info.platform = "docker"
        facade.get_device_info.return_value = info
        r = await ContainerStatusHealthChecker().check("dev-1", facade)
        assert r.healthy is True
        assert r.response["status"] == "running"
        assert r.error is None

    @pytest.mark.asyncio
    async def test_check_not_running_unhealthy(self, facade):
        info = MagicMock()
        info.status = "stopped"
        info.platform = "docker"
        facade.get_device_info.return_value = info
        r = await ContainerStatusHealthChecker().check("dev-1", facade)
        assert r.healthy is False
        assert "stopped" in r.error

    @pytest.mark.asyncio
    async def test_check_exception_with_timeout(self, facade):
        facade.get_device_info.side_effect = Exception("PLATFORM_UNAVAILABLE error")
        r = await ContainerStatusHealthChecker().check("dev-1", facade)
        assert r.healthy is False
        assert r.timeout is True

    @pytest.mark.asyncio
    async def test_check_exception_with_lowercase_timeout(self, facade):
        facade.get_device_info.side_effect = Exception("timeout occurred")
        r = await ContainerStatusHealthChecker().check("dev-1", facade)
        assert r.healthy is False
        assert r.timeout is True

    @pytest.mark.asyncio
    async def test_check_exception_not_timeout(self, facade):
        facade.get_device_info.side_effect = RuntimeError("connection refused")
        r = await ContainerStatusHealthChecker().check("dev-1", facade)
        assert r.healthy is False
        assert r.timeout is False


class TestEngineAdapterHealthChecker:
    @pytest.fixture
    def facade(self):
        f = MagicMock()
        f.execute_command = AsyncMock()
        return f

    @pytest.mark.asyncio
    async def test_name(self):
        c = EngineAdapterHealthChecker()
        assert c.name == "engine_adapter"

    @pytest.mark.asyncio
    async def test_custom_port_and_endpoint(self):
        c = EngineAdapterHealthChecker(container_port=9090, health_endpoint="/ready")
        assert c._container_port == 9090
        assert c._health_endpoint == "/ready"

    @pytest.mark.asyncio
    async def test_check_healthy(self, facade):
        result = MagicMock()
        result.exit_code = 0
        result.stdout = '{"ok": true}'
        result.stderr = ""
        facade.execute_command.return_value = result
        c = EngineAdapterHealthChecker(container_port=8080, health_endpoint="/health")
        r = await c.check("dev-1", facade)
        assert r.healthy is True
        assert r.response["stdout"] == '{"ok": true}'

    @pytest.mark.asyncio
    async def test_check_exit_code_nonzero(self, facade):
        result = MagicMock()
        result.exit_code = 1
        result.stderr = "fail"
        facade.execute_command.return_value = result
        r = await EngineAdapterHealthChecker().check("dev-1", facade)
        assert r.healthy is False
        assert "exit code 1" in r.error

    @pytest.mark.asyncio
    async def test_check_timeout(self, facade):
        facade.execute_command.side_effect = TimeoutError()
        r = await EngineAdapterHealthChecker().check("dev-1", facade)
        assert r.healthy is False
        assert r.timeout is True
        assert "Timeout" in r.error

    @pytest.mark.asyncio
    async def test_check_generic_exception(self, facade):
        facade.execute_command.side_effect = RuntimeError("exec failed")
        r = await EngineAdapterHealthChecker().check("dev-1", facade)
        assert r.healthy is False
        assert "exec failed" in r.error


class TestDockerPaaSHealthProviderInit:
    def test_init(self):
        facade = MagicMock()
        p = DockerPaaSHealthProvider(
            facade, timeout_seconds=5, container_port=9090, health_endpoint="/ready"
        )
        assert p._timeout_seconds == 5
        assert "echo" in p._all_checkers
        assert "container_status" in p._all_checkers
        assert "engine_adapter" in p._all_checkers
        assert p._all_checkers["engine_adapter"]._container_port == 9090


class TestDockerPaaSHealthProviderCheckHealth:
    @pytest.fixture
    def facade(self):
        f = MagicMock()
        f.execute_command = AsyncMock()
        f.get_device_info = AsyncMock()
        return f

    @pytest.fixture
    def provider(self, facade):
        return DockerPaaSHealthProvider(facade, timeout_seconds=10)

    @pytest.mark.asyncio
    async def test_no_valid_checkers(self, provider):
        result = await provider.check_health("dev-1", checkers=["nonexistent"])
        assert result.overall_healthy is True
        assert result.checkers == {}

    @pytest.mark.asyncio
    async def test_all_healthy(self, provider, facade):
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.stdout = '{"code": 0, "status": "alive"}'
        exec_result.stderr = ""
        facade.execute_command.return_value = exec_result

        info = MagicMock()
        info.status = "running"
        info.platform = "docker"
        facade.get_device_info.return_value = info

        result = await provider.check_health(
            "dev-1", checkers=["echo", "container_status"]
        )
        assert result.overall_healthy is True
        assert len(result.checkers) == 2

    @pytest.mark.asyncio
    async def test_one_unhealthy(self, provider, facade):
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.stdout = '{"code": 0, "status": "alive"}'
        exec_result.stderr = ""
        facade.execute_command.return_value = exec_result

        info = MagicMock()
        info.status = "stopped"
        info.platform = "docker"
        facade.get_device_info.return_value = info

        result = await provider.check_health(
            "dev-1", checkers=["echo", "container_status"]
        )
        assert result.overall_healthy is False

    @pytest.mark.asyncio
    async def test_checker_timeout_in_run_checker(self, facade):
        async def slow_check(*args, **kwargs):
            import asyncio

            await asyncio.sleep(100)

        facade.execute_command.side_effect = slow_check
        provider = DockerPaaSHealthProvider(facade, timeout_seconds=1)

        result = await provider.check_health("dev-1", checkers=["echo"])
        assert result.overall_healthy is False
        assert "echo" in result.checkers
        assert result.checkers["echo"].timeout is True

    @pytest.mark.asyncio
    async def test_checker_exception_in_run_checker(self, provider, facade):
        facade.execute_command.side_effect = ValueError("bad value")

        result = await provider.check_health("dev-1", checkers=["echo"])
        assert result.overall_healthy is False
        assert "echo" in result.checkers
        assert "bad value" in result.checkers["echo"].error


class TestDockerPaaSHealthProviderCheckAlive:
    @pytest.fixture
    def facade(self):
        f = MagicMock()
        f.execute_command = AsyncMock()
        f.get_device_info = AsyncMock()
        return f

    @pytest.fixture
    def provider(self, facade):
        return DockerPaaSHealthProvider(facade, timeout_seconds=10)

    @pytest.mark.asyncio
    async def test_check_alive_default_echo(self, provider, facade):
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.stdout = '{"code": 0, "status": "alive"}'
        exec_result.stderr = ""
        facade.execute_command.return_value = exec_result

        r = await provider.check_alive("dev-1")
        assert r.healthy is True

    @pytest.mark.asyncio
    async def test_check_alive_with_checkers_echo(self, provider, facade):
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.stdout = '{"code": 0, "status": "alive"}'
        exec_result.stderr = ""
        facade.execute_command.return_value = exec_result

        r = await provider.check_alive("dev-1", checkers=["echo"])
        assert r.healthy is True

    @pytest.mark.asyncio
    async def test_check_alive_with_checkers_container_status(self, provider, facade):
        info = MagicMock()
        info.status = "running"
        info.platform = "docker"
        facade.get_device_info.return_value = info

        r = await provider.check_alive("dev-1", checkers=["container_status"])
        assert r.healthy is True

    @pytest.mark.asyncio
    async def test_check_alive_no_matching_checker_raises(self, provider):
        with pytest.raises(ValueError, match="No matching alive checker"):
            await provider.check_alive("dev-1", checkers=["nonexistent"])
