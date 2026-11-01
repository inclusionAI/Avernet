"""Hard execution budgets must match the observed real-model serving profile."""

from __future__ import annotations

from pathlib import Path


class EmptyConfig:
    """Return the composed default for every execution-budget setting."""

    def get_float(self, key: str, default: float) -> float:
        return default


def test_deployment_yaml_exports_real_model_budget_defaults() -> None:
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    root = Path(__file__).resolve().parents[2]
    config = YamlEnvConfigProvider(config_path=str(root / "configs/application.yaml"))

    assert config.get_float("search.execution.embedding_timeout_seconds", 0.0) == 1.5
    assert config.get_float("search.execution.retrieval_timeout_seconds", 0.0) == 3.0
    assert (
        config.get_float("search.execution.local_snapshot_timeout_seconds", 0.0)
        == 0.5
    )
    assert config.get_float("search.execution.total_timeout_seconds", 0.0) == 5.0


def test_empty_deployment_composition_matches_real_model_budget_defaults() -> None:
    from app.application.services.agent_search_execution_budget import (
        AgentSearchExecutionBudget,
    )

    budget = AgentSearchExecutionBudget.from_context(EmptyConfig())

    assert budget.embedding_timeout_seconds == 1.5
    assert budget.retrieval_timeout_seconds == 3.0
    assert budget.local_snapshot_timeout_seconds == 0.5
    assert budget.total_timeout_seconds == 5.0
    assert budget.total_timeout_seconds == (
        budget.embedding_timeout_seconds
        + budget.retrieval_timeout_seconds
        + budget.local_snapshot_timeout_seconds
    )
