"""Unit tests for health check and alive check strategy resolution."""

from secbaas.community.api.health_check.bot import (
    BotDeviceInfo,
    resolve_alive_check_strategy,
    resolve_health_check_strategy,
)
from secbaas.community.api.health_check.bot._models import (
    DEFAULT_ALIVE_CHECKERS,
    DEFAULT_ENGINE_FALLBACK_CHECKERS,
    ENGINE_ALIVE_CHECKERS,
    ENGINE_HEALTH_CHECKERS,
)


class TestResolveHealthCheckStrategy:
    """Tests for resolve_health_check_strategy."""

    def test_openclaw_arca_returns_full_checkers(self) -> None:
        result = resolve_health_check_strategy("ARCA", "openclaw")
        assert result == ["engine", "adapter", "gateway"]

    def test_aicoding_arca_returns_echo_aicoding(self) -> None:
        result = resolve_health_check_strategy("ARCA", "aicoding")
        assert result == ["echo_aicoding"]

    def test_claude_code_arca_returns_echo_claude_code(self) -> None:
        result = resolve_health_check_strategy("ARCA", "claude_code")
        assert result == ["echo_claude_code"]

    def test_unknown_engine_arca_returns_fallback_echo(self) -> None:
        result = resolve_health_check_strategy("ARCA", "unknown_engine")
        assert result == ["echo"]

    def test_none_engine_arca_returns_fallback_echo(self) -> None:
        result = resolve_health_check_strategy("ARCA", None)
        assert result == ["echo"]

    def test_sigma_returns_default_empty(self) -> None:
        result = resolve_health_check_strategy("SIGMA", None)
        assert result == []

    def test_local_returns_default_empty(self) -> None:
        result = resolve_health_check_strategy("LOCAL", None)
        assert result == []

    def test_none_provider_type_returns_empty(self) -> None:
        result = resolve_health_check_strategy(None, "openclaw")
        assert result == []

    def test_case_insensitive_provider_type(self) -> None:
        result = resolve_health_check_strategy("arca", "openclaw")
        assert result == ["engine", "adapter", "gateway"]

    def test_engine_config_takes_priority_over_fallback(self) -> None:
        """Engine-specific config should override fallback."""
        result = resolve_health_check_strategy("ARCA", "openclaw")
        # Should be engine config, not fallback
        assert result == ["engine", "adapter", "gateway"]
        assert result != DEFAULT_ENGINE_FALLBACK_CHECKERS.get("ARCA")

    def test_fallback_used_when_engine_not_configured(self) -> None:
        """Fallback should be used when engine is not in ENGINE_HEALTH_CHECKERS."""
        result = resolve_health_check_strategy("ARCA", "nonexistent_engine")
        assert result == DEFAULT_ENGINE_FALLBACK_CHECKERS.get("ARCA")

    def test_k8s_openclaw_returns_readiness(self) -> None:
        """K8S with openclaw engine returns ['readiness']."""
        assert resolve_health_check_strategy("K8S", "openclaw") == ["readiness"]

    def test_k8s_aicoding_returns_readiness(self) -> None:
        """K8S with aicoding engine returns ['readiness']."""
        assert resolve_health_check_strategy("K8S", "aicoding") == ["readiness"]

    def test_k8s_claude_code_returns_readiness(self) -> None:
        """K8S with claude_code engine returns ['readiness']."""
        assert resolve_health_check_strategy("K8S", "claude_code") == ["readiness"]

    def test_k8s_none_engine_returns_fallback_readiness(self) -> None:
        """K8S with None engine falls back to DEFAULT_ENGINE_FALLBACK_CHECKERS."""
        assert resolve_health_check_strategy("K8S", None) == ["readiness"]

    def test_k8s_unknown_engine_returns_fallback_readiness(self) -> None:
        """K8S with unknown engine falls back to DEFAULT_ENGINE_FALLBACK_CHECKERS."""
        assert resolve_health_check_strategy("K8S", "unknown_engine") == ["readiness"]

    def test_k8s_case_insensitive(self) -> None:
        """K8S is case-insensitive: 'k8s' returns same as 'K8S'."""
        assert resolve_health_check_strategy("k8s", "openclaw") == ["readiness"]


class TestResolveAliveCheckStrategy:
    """Tests for resolve_alive_check_strategy."""

    def test_openclaw_arca_returns_active(self) -> None:
        result = resolve_alive_check_strategy("ARCA", "openclaw")
        assert result == ["active"]

    def test_aicoding_arca_returns_active_claude_code(self) -> None:
        result = resolve_alive_check_strategy("ARCA", "aicoding")
        assert result == ["active_claude_code"]

    def test_claude_code_arca_returns_active_claude_code(self) -> None:
        result = resolve_alive_check_strategy("ARCA", "claude_code")
        assert result == ["active_claude_code"]

    def test_sigma_returns_empty(self) -> None:
        result = resolve_alive_check_strategy("SIGMA", None)
        assert result == []

    def test_local_returns_empty(self) -> None:
        result = resolve_alive_check_strategy("LOCAL", None)
        assert result == []

    def test_none_provider_type_returns_empty(self) -> None:
        result = resolve_alive_check_strategy(None, "openclaw")
        assert result == []

    def test_unknown_provider_type_returns_empty(self) -> None:
        result = resolve_alive_check_strategy("UNKNOWN", None)
        assert result == []

    def test_unknown_engine_arca_returns_empty(self) -> None:
        """No fallback for alive - unknown engine returns empty."""
        result = resolve_alive_check_strategy("ARCA", "unknown_engine")
        assert result == []

    def test_none_engine_arca_returns_empty(self) -> None:
        """No fallback for alive - None engine returns empty (DEFAULT_ALIVE_CHECKERS is empty)."""
        result = resolve_alive_check_strategy("ARCA", None)
        assert result == []

    def test_case_insensitive_provider_type(self) -> None:
        result = resolve_alive_check_strategy("arca", "openclaw")
        assert result == ["active"]

    def test_engine_config_provider_not_found(self) -> None:
        """Engine configured but provider_type not in engine config."""
        result = resolve_alive_check_strategy("SIGMA", "openclaw")
        # openclaw only has ARCA, SIGMA falls through to DEFAULT_ALIVE_CHECKERS
        assert result == []

    def test_k8s_openclaw_returns_liveness(self) -> None:
        """K8S with openclaw engine returns ['liveness']."""
        assert resolve_alive_check_strategy("K8S", "openclaw") == ["liveness"]

    def test_k8s_aicoding_returns_liveness(self) -> None:
        """K8S with aicoding engine returns ['liveness']."""
        assert resolve_alive_check_strategy("K8S", "aicoding") == ["liveness"]

    def test_k8s_claude_code_returns_liveness(self) -> None:
        """K8S with claude_code engine returns ['liveness']."""
        assert resolve_alive_check_strategy("K8S", "claude_code") == ["liveness"]

    def test_k8s_none_engine_returns_liveness(self) -> None:
        """K8S with None engine falls back to DEFAULT_ALIVE_CHECKERS."""
        assert resolve_alive_check_strategy("K8S", None) == ["liveness"]

    def test_k8s_unknown_engine_returns_default_liveness(self) -> None:
        """K8S with unknown engine falls back to DEFAULT_ALIVE_CHECKERS."""
        assert resolve_alive_check_strategy("K8S", "unknown_engine") == ["liveness"]


class TestStrategyConstants:
    """Tests for strategy constant values."""

    def test_engine_health_checkers_has_openclaw(self) -> None:
        assert "openclaw" in ENGINE_HEALTH_CHECKERS
        assert ENGINE_HEALTH_CHECKERS["openclaw"]["ARCA"] == [
            "engine",
            "adapter",
            "gateway",
        ]

    def test_engine_health_checkers_has_aicoding(self) -> None:
        assert "aicoding" in ENGINE_HEALTH_CHECKERS
        assert ENGINE_HEALTH_CHECKERS["aicoding"]["ARCA"] == ["echo_aicoding"]

    def test_engine_health_checkers_has_claude_code(self) -> None:
        assert "claude_code" in ENGINE_HEALTH_CHECKERS
        assert ENGINE_HEALTH_CHECKERS["claude_code"]["ARCA"] == ["echo_claude_code"]

    def test_default_engine_fallback_checkers(self) -> None:
        assert DEFAULT_ENGINE_FALLBACK_CHECKERS == {
            "ARCA": ["echo"],
            "POOLAB": ["api"],
            "K8S": ["readiness"],
        }

    def test_engine_alive_checkers_has_openclaw(self) -> None:
        assert "openclaw" in ENGINE_ALIVE_CHECKERS
        assert ENGINE_ALIVE_CHECKERS["openclaw"]["ARCA"] == ["active"]
        assert ENGINE_ALIVE_CHECKERS["openclaw"]["K8S"] == ["liveness"]

    def test_default_alive_checkers_has_k8s(self) -> None:
        assert DEFAULT_ALIVE_CHECKERS == {"K8S": ["liveness"]}

    def test_default_health_checkers_has_k8s(self) -> None:
        """DEFAULT_HEALTH_CHECKERS includes K8S entry."""
        from secbaas.community.api.health_check.bot._models import (
            DEFAULT_HEALTH_CHECKERS,
        )

        assert DEFAULT_HEALTH_CHECKERS["K8S"] == ["readiness"]

    def test_engine_health_checkers_k8s_readiness(self) -> None:
        """All engine health checker dicts have K8S readiness entry."""
        assert ENGINE_HEALTH_CHECKERS["openclaw"]["K8S"] == ["readiness"]
        assert ENGINE_HEALTH_CHECKERS["aicoding"]["K8S"] == ["readiness"]
        assert ENGINE_HEALTH_CHECKERS["claude_code"]["K8S"] == ["readiness"]

    def test_engine_alive_checkers_k8s_liveness(self) -> None:
        """All engine alive checker dicts have K8S liveness entry."""
        assert ENGINE_ALIVE_CHECKERS["openclaw"]["K8S"] == ["liveness"]
        assert ENGINE_ALIVE_CHECKERS["aicoding"]["K8S"] == ["liveness"]
        assert ENGINE_ALIVE_CHECKERS["claude_code"]["K8S"] == ["liveness"]


class TestBotDeviceInfoFromBindingDict:
    """Tests for BotDeviceInfo.from_binding_dict."""

    def test_from_binding_dict_with_binding_id(self) -> None:
        result = BotDeviceInfo.from_binding_dict(
            {
                "bot_id": "bot1",
                "entity_id": "entity1",
                "binding_id": "10",
                "bot_type": "service",
                "status": "active",
                "active_engine": "openclaw",
            }
        )
        assert result.bot_id == "bot1"
        assert result.entity_id == "entity1"
        assert result.binding_id == 10
        assert result.bot_type == "service"
        assert result.status == "active"
        assert result.active_engine == "openclaw"

    def test_from_binding_dict_without_binding_id(self) -> None:
        result = BotDeviceInfo.from_binding_dict(
            {
                "bot_id": "bot2",
                "entity_id": "entity2",
                "binding_id": None,
                "bot_type": "personal",
                "status": "active",
            }
        )
        assert result.bot_id == "bot2"
        assert result.binding_id is None
        assert result.active_engine is None
