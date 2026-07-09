"""Unit tests for bot health check API layer (models, enums, exceptions, protocols)."""

from typing import Any

from secbaas.api.health_check.bot import (
    BotDeviceInfo,
    BotHealthCheckerConfig,
    BotHealthCheckerError,
    BotHealthCheckerService,
    DeviceProviderType,
    DeviceSourceProvider,
    HealthCheckError,
    HealthCheckTimeoutError,
    PaasDeviceInfo,
    PartialSuccessError,
    SandboxNotFoundError,
    TTLExtendFailedError,
    UnsupportedDeviceProviderError,
    resolve_alive_check_strategy,
    resolve_health_check_strategy,
)


class TestDeviceProviderType:
    """DeviceProviderType enum tests."""

    def test_members(self) -> None:
        assert DeviceProviderType.ARCA == "arca"
        assert DeviceProviderType.BAAS == "baas"

    def test_unique_values(self) -> None:
        values = {m.value for m in DeviceProviderType}
        assert values == {"arca", "baas"}


class TestExceptions:
    """Exception hierarchy tests."""

    def test_hierarchy(self) -> None:
        assert issubclass(SandboxNotFoundError, BotHealthCheckerError)
        assert issubclass(UnsupportedDeviceProviderError, BotHealthCheckerError)
        assert issubclass(HealthCheckError, BotHealthCheckerError)
        assert issubclass(HealthCheckTimeoutError, BotHealthCheckerError)
        assert issubclass(TTLExtendFailedError, BotHealthCheckerError)
        assert issubclass(PartialSuccessError, BotHealthCheckerError)

    def test_bot_health_checker_error(self) -> None:
        err = BotHealthCheckerError("something went wrong")
        assert str(err) == "something went wrong"

    def test_sandbox_not_found(self) -> None:
        err = SandboxNotFoundError("sandbox-001")
        assert "sandbox-001" in str(err)

    def test_partial_success_error(self) -> None:
        err = PartialSuccessError(
            success_count=2,
            failed_count=1,
            errors=["dev-3 failed"],
        )
        assert err.success_count == 2
        assert err.failed_count == 1
        assert err.errors == ["dev-3 failed"]
        assert "Partial success" in str(err)


class TestBotModels:
    """Bot health check model tests."""

    def test_bot_device_info(self) -> None:
        info = BotDeviceInfo(
            bot_id="bot-1",
            entity_id="entity-1",
            bot_type="personal",
            status="online",
        )
        assert info.bot_id == "bot-1"
        assert info.bot_type == "personal"
        assert info.binding_id is None

    def test_paas_device_info(self) -> None:
        info = PaasDeviceInfo(
            paas_device_id="paas-dev-1",
            provider_type=DeviceProviderType.ARCA,
        )
        assert info.paas_device_id == "paas-dev-1"
        assert info.provider_type == DeviceProviderType.ARCA

    def test_bot_health_checker_config(self) -> None:
        config = BotHealthCheckerConfig(
            health_check_timeout=30,
            extend_when_remaining_hours=8,
        )
        assert config.health_check_timeout == 30
        assert config.extend_when_remaining_hours == 8
        assert config.target_ttl_hours == 24


class TestStrategyResolvers:
    """Strategy resolver function tests."""

    def test_resolve_health_check_strategy_known_provider(self) -> None:
        strategy = resolve_health_check_strategy(provider_type="ARCA")
        assert isinstance(strategy, list)
        assert len(strategy) > 0

    def test_resolve_health_check_strategy_none(self) -> None:
        strategy = resolve_health_check_strategy(provider_type=None)
        assert strategy == []

    def test_resolve_alive_check_strategy_known(self) -> None:
        strategy = resolve_alive_check_strategy(
            provider_type="ARCA", active_engine="openclaw"
        )
        assert isinstance(strategy, list)

    def test_resolve_alive_check_strategy_none(self) -> None:
        strategy = resolve_alive_check_strategy(provider_type=None)
        assert strategy == []


class TestBotProtocols:
    """Bot health check Protocol compliance tests."""

    def test_device_source_provider_compliance(self) -> None:
        class MinimalProvider:
            @property
            def provider_type(self) -> DeviceProviderType:
                return DeviceProviderType.ARCA

            async def list_all_active_bot_device(
                self,
                bot_type: str | None = None,
                page: int = 1,
                page_size: int = 20,
                env: str = "prod",
            ) -> tuple[int, list[Any]]:
                return 0, []

            async def list_paas_device_by_bot(
                self,
                bot_id: str,
                entity_id: str,
                **kwargs: Any,
            ) -> list[Any]:
                return []

            async def extend_ttl_by_bot(
                self,
                bot_id: str,
                entity_id: str,
                binding_id: int | None = None,
            ) -> Any:
                return None

        provider = MinimalProvider()
        assert isinstance(provider, DeviceSourceProvider)

    def test_bot_health_checker_service_compliance(self) -> None:
        class MinimalService:
            async def list_all_active_bot_device(
                self,
                page: int = 1,
                page_size: int = 20,
                bot_type: str | None = None,
                env: str = "prod",
            ) -> tuple[int, list[Any]]:
                return 0, []

            async def list_paas_device_by_bot(
                self,
                bot_id: str,
                entity_id: str,
                statuses: list[str] | None = None,
                env: str = "prod",
            ) -> Any:
                return None

            async def check_single_device(
                self,
                device: Any,
                active_engine: str | None = None,
            ) -> Any:
                return None

            async def check_health_by_bot(
                self,
                bot_id: str,
                entity_id: str,
                statuses: list[str] | None = None,
                env: str = "prod",
            ) -> Any:
                return None

            async def check_alive_by_bot(
                self,
                bot_id: str,
                entity_id: str,
                minutes: int = 1440,
                env: str = "prod",
            ) -> Any:
                return None

            async def extend_ttl_by_bot(
                self,
                bot_id: str,
                entity_id: str,
                env: str = "prod",
            ) -> Any:
                return None

            async def get_sandbox_info(
                self,
                sandbox_id: str,
            ) -> Any:
                return None

        service = MinimalService()
        assert isinstance(service, BotHealthCheckerService)
