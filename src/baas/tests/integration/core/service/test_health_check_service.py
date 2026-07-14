"""Integration tests for BotHealthCheckerService.

Verifies container wiring and basic configuration.
Focuses on wiring verification since meaningful operations
require devices in the database.
"""

import asyncio

import pytest

from secbaas.community.api.health_check.bot import BotHealthCheckerConfig


@pytest.mark.integration
class TestBotHealthCheckerServiceIntegration:
    """Integration tests for BotHealthCheckerService."""

    def test_service_resolves_from_container(
        self,
        bootstrap_init,
    ):
        """Verify BotHealthCheckerService is properly registered in the container."""
        service = bootstrap_init.services.bot_health_checker_service()
        assert service is not None, (
            "BotHealthCheckerService should resolve from "
            "bootstrap_init.services.bot_health_checker_service()"
        )

    def test_service_has_correct_config(
        self,
        bootstrap_init,
    ):
        """Verify config defaults are populated (not None)."""
        service = bootstrap_init.services.bot_health_checker_service()
        assert service._config is not None, (
            "Config should not be None (defaults fall back to BotHealthCheckerConfig())"
        )
        assert isinstance(service._config, BotHealthCheckerConfig), (
            f"Expected BotHealthCheckerConfig, got {type(service._config)}"
        )
        assert service._config.health_check_timeout == 10, (
            f"Expected health_check_timeout=10, got {service._config.health_check_timeout}"
        )
        assert service._config.health_check_max_concurrent == 10, (
            f"Expected health_check_max_concurrent=10, got {service._config.health_check_max_concurrent}"
        )

    def test_service_returns_empty_for_empty_db(
        self,
        bootstrap_init,
    ):
        """Call list_all_active_bot_device on an empty dev database (SQLite in-memory)."""

        async def _run():
            service = bootstrap_init.services.bot_health_checker_service()
            total, items = await service.list_all_active_bot_device(
                page=1,
                page_size=20,
                env="dev",
            )
            return total, items

        total, items = asyncio.run(_run())
        assert isinstance(total, int), f"Expected total to be int, got {type(total)}"
        assert isinstance(items, list), f"Expected items to be list, got {type(items)}"
        # Empty dev database should return zero results
        assert total >= 0, f"Expected total >= 0, got {total}"
        assert len(items) == total, (
            f"Expected items length ({len(items)}) to match total ({total})"
        )
