"""Bootstrap reconciliation of AgentPass CLI scope."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agentclaw.community.core.mcp.services.cli_passport_scope import (
    CliPassportScopeReconciler,
    build_passport_resource_scope,
)
from agentclaw.community.core.mcp.services._defaults import get_default_cli_items


class _Passport:
    def __init__(self) -> None:
        self.updated: dict | None = None

    def query_agent_passport(self, bot_id: str, owner_id: str) -> dict:
        assert (bot_id, owner_id) == ("bot-1", "owner-1")
        return {
            "clis": [{
                "cli_code": "dataphin",
                "cli_name": "historical dataphin",
                "cli_desc": "historical description",
                "identity_mode": "owner",
            }],
            "mcps": [{"mcp_code": "mcp.a", "identity_mode": "caller"}],
        }

    def update_passport(self, **kwargs: object) -> None:
        self.updated = kwargs


class _IdentityRepository:
    def list_draft_call_types(self, bot_pk: int, engine_type: str) -> dict[str, str]:
        assert (bot_pk, engine_type) == (7, "openclaw")
        return {}

    def list_draft_cli_call_types(self, bot_pk: int, engine_type: str) -> dict[str, str]:
        assert (bot_pk, engine_type) == (7, "openclaw")
        return {"dataphin": "caller"}


def test_bootstrap_merges_history_default_and_sparse_override_into_one_scope() -> None:
    """Removing the MCP item or caller overlay must make this contract fail."""
    passport = _Passport()
    reconciler = CliPassportScopeReconciler(
        passport_plugin=passport,  # type: ignore[arg-type]
        identity_repository=_IdentityRepository(),  # type: ignore[arg-type]
    )

    result = reconciler.reconcile(
        bot={
            "id": 7,
            "bot_id": "bot-1",
            "owner_id": "owner-1",
            "active_engine": "openclaw",
            "template_type": None,
        }
    )

    assert result.cli_codes == ("dataphin", "deepinsight-cli")
    assert result.updated is True
    assert passport.updated is not None
    assert passport.updated["resource_scope"] == {
        "mcp_codes": ["mcp.a"],
        "mcp_items": [{"mcp_code": "mcp.a", "identity_mode": "caller"}],
        "cli_items": [
            {
                "cli_code": "dataphin",
                "cli_name": "historical dataphin",
                "cli_desc": "historical description",
                "identity_mode": "caller",
            },
            {
                "cli_code": "deepinsight-cli",
                "cli_name": "deepinsight-cli",
                "cli_desc": "DeepInsight 命令行工具",
                "identity_mode": "owner",
            },
        ],
    }


def test_generalcc_bootstrap_retains_legacy_creation_clis_and_adds_yaml_clis() -> None:
    """YAML defaults supplement the original generalCC creation scope."""

    class _GeneralCCIdentityRepository:
        def list_draft_call_types(self, bot_pk: int, engine_type: str) -> dict[str, str]:
            assert (bot_pk, engine_type) == (7, "claude_code")
            return {}

        def list_draft_cli_call_types(self, bot_pk: int, engine_type: str) -> dict[str, str]:
            assert (bot_pk, engine_type) == (7, "claude_code")
            return {}

    legacy_items = get_default_cli_items("aicoding")
    passport = _Passport()
    passport.query_agent_passport = lambda _bot_id, _owner_id: {
        "mcps": [],
        "clis": legacy_items,
    }
    reconciler = CliPassportScopeReconciler(
        passport_plugin=passport,  # type: ignore[arg-type]
        identity_repository=_GeneralCCIdentityRepository(),  # type: ignore[arg-type]
    )

    result = reconciler.reconcile(
        bot={
            "id": 7,
            "bot_id": "bot-1",
            "owner_id": "owner-1",
            "active_engine": "claude_code",
            "template_type": "generalCC",
        }
    )

    legacy_codes = [item["cli_code"] for item in legacy_items]
    assert result.cli_codes == (*legacy_codes, "dataphin", "deepinsight-cli")
    assert passport.updated is not None
    assert [
        item["cli_code"] for item in passport.updated["resource_scope"]["cli_items"]
    ] == [*legacy_codes, "dataphin", "deepinsight-cli"]


def test_bootstrap_applies_mcp_sparse_identity_without_dropping_cli_snapshot() -> None:
    """An MCP caller row still requires a complete, non-partial scope write."""

    class _McpOverrideRepository(_IdentityRepository):
        def list_draft_call_types(self, bot_pk: int, engine_type: str) -> dict[str, str]:
            assert (bot_pk, engine_type) == (7, "claude_code")
            return {"mcp.a": "caller"}

        def list_draft_cli_call_types(self, bot_pk: int, engine_type: str) -> dict[str, str]:
            assert (bot_pk, engine_type) == (7, "claude_code")
            return {}

    passport = _Passport()
    passport.query_agent_passport = lambda _bot_id, _owner_id: {
        "clis": [],
        "mcps": [{"mcp_code": "mcp.a", "identity_mode": "owner"}],
    }
    reconciler = CliPassportScopeReconciler(
        passport_plugin=passport,  # type: ignore[arg-type]
        identity_repository=_McpOverrideRepository(),  # type: ignore[arg-type]
    )

    result = reconciler.reconcile(
        bot={
            "id": 7,
            "bot_id": "bot-1",
            "owner_id": "owner-1",
            "active_engine": "claude_code",
            "template_type": "normalCC",
        }
    )

    assert result.cli_codes == ()
    assert passport.updated is not None
    assert passport.updated["resource_scope"] == {
        "mcp_codes": ["mcp.a"],
        "mcp_items": [{"mcp_code": "mcp.a", "identity_mode": "caller"}],
        "cli_items": [],
    }


def test_reconcile_query_failure_logs_error_type_without_passport_details() -> None:
    class _FailingPassport(_Passport):
        def query_agent_passport(self, bot_id: str, owner_id: str) -> dict:
            raise RuntimeError("passport-token-secret")

    reconciler = CliPassportScopeReconciler(
        passport_plugin=_FailingPassport(),  # type: ignore[arg-type]
        identity_repository=_IdentityRepository(),  # type: ignore[arg-type]
    )
    with patch(
        "agentclaw.community.core.mcp.services.cli_passport_scope.logger"
    ) as logger:
        with pytest.raises(RuntimeError):
            reconciler.reconcile(
                bot={
                    "id": 7,
                    "bot_id": "bot-1",
                    "owner_id": "owner-1",
                    "active_engine": "openclaw",
                }
            )

    logged = " ".join(str(call) for call in logger.method_calls)
    assert "cli_passport_reconcile_failed" in logged
    assert "RuntimeError" in logged
    assert "passport-token-secret" not in logged


def test_reconcile_update_failure_logs_scope_event_without_passport_details() -> None:
    """The overwrite failure is visible without leaking the external response."""

    class _UpdateFailingPassport(_Passport):
        def update_passport(self, **kwargs: object) -> None:
            raise RuntimeError("passport-token-secret")

    reconciler = CliPassportScopeReconciler(
        passport_plugin=_UpdateFailingPassport(),  # type: ignore[arg-type]
        identity_repository=_IdentityRepository(),  # type: ignore[arg-type]
    )
    with patch(
        "agentclaw.community.core.mcp.services.cli_passport_scope.logger"
    ) as logger:
        with pytest.raises(RuntimeError):
            reconciler.reconcile(
                bot={
                    "id": 7,
                    "bot_id": "bot-1",
                    "owner_id": "owner-1",
                    "active_engine": "openclaw",
                }
            )

    logged = " ".join(str(call) for call in logger.method_calls)
    assert "agentpass_cli_scope_update_requested" in logged
    assert "agentpass_cli_scope_update_failed" in logged
    assert "status=failed" in logged
    assert "cli_passport_reconcile_failed" in logged
    assert "duration_ms" in logged
    assert "passport-token-secret" not in logged


def test_reconcile_update_success_logs_status_and_duration_without_scope_contents() -> None:
    """The external AgentPass success event remains low-sensitive and auditable."""
    reconciler = CliPassportScopeReconciler(
        passport_plugin=_Passport(),  # type: ignore[arg-type]
        identity_repository=_IdentityRepository(),  # type: ignore[arg-type]
    )

    with patch(
        "agentclaw.community.core.mcp.services.cli_passport_scope.logger"
    ) as logger:
        reconciler.reconcile(
            bot={
                "id": 7,
                "bot_id": "bot-1",
                "owner_id": "owner-1",
                "active_engine": "openclaw",
            }
        )

    logged = " ".join(str(call) for call in logger.method_calls)
    assert "agentpass_cli_scope_update_succeeded" in logged
    assert "status=succeeded" in logged
    assert "duration_ms" in logged
    assert "passport-token-secret" not in logged


@pytest.mark.parametrize(
    "passport",
    [
        None,
        {"mcps": [None], "clis": []},
        {"mcps": [{"mcp_code": "mcp.a"}, {"mcp_code": "mcp.a"}], "clis": []},
        {"mcps": [{"mcp_code": "mcp.a", "identity_mode": "invalid"}], "clis": []},
    ],
)
def test_shared_scope_builder_rejects_malformed_agentpass_mcp_snapshot(passport: object) -> None:
    """Malformed history never reaches an overwrite writer as guessed owner scope."""
    with pytest.raises(ValueError):
        build_passport_resource_scope(
            passport,  # type: ignore[arg-type]
            desired_mcp_items=[],
            mcp_identity_modes={},
        )


@pytest.mark.parametrize(
    "desired_items",
    [
        [
            {"mcp_code": "mcp.a", "identity_mode": "owner"},
            {"mcp_code": "mcp.a", "identity_mode": "owner"},
        ],
        [{"mcp_code": "mcp.a", "identity_mode": "invalid"}],
    ],
)
def test_shared_scope_builder_rejects_invalid_desired_identity(
    desired_items: list[dict[str, str]],
) -> None:
    """A writer cannot submit duplicated membership or an invalid identity mode."""
    with pytest.raises(ValueError):
        build_passport_resource_scope(
            {"mcps": [], "clis": []},
            desired_mcp_items=desired_items,  # type: ignore[arg-type]
            mcp_identity_modes={},
        )


def test_current_scope_read_validates_template_and_bot_fields() -> None:
    """Read-only Bootstrap scope access uses the same fail-closed inputs."""
    reconciler = CliPassportScopeReconciler(
        passport_plugin=_Passport(),  # type: ignore[arg-type]
        identity_repository=_IdentityRepository(),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="template_type"):
        reconciler.current_passport_cli_items(
            bot={
                "id": 7,
                "bot_id": "bot-1",
                "owner_id": "owner-1",
                "active_engine": "openclaw",
                "template_type": 1,
            }
        )
    with pytest.raises(ValueError, match="bot_id"):
        reconciler.reconcile(
            bot={"id": 7, "owner_id": "owner-1", "active_engine": "openclaw"}
        )


@pytest.mark.parametrize(
    ("engine_type", "template_type", "expected"),
    [
        ("openclaw", None, True),
        ("claude_code", "generalCC", True),
        ("claude_code", "normalCC", False),
        ("aicoding", None, False),
    ],
)
def test_reconciler_exposes_phase_one_cli_profile_gate(
    engine_type: str, template_type: str | None, expected: bool
) -> None:
    reconciler = CliPassportScopeReconciler(
        passport_plugin=_Passport(),  # type: ignore[arg-type]
        identity_repository=_IdentityRepository(),  # type: ignore[arg-type]
    )

    assert reconciler.supports_profile(
        bot={"active_engine": engine_type, "template_type": template_type}
    ) is expected
