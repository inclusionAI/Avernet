"""Pydantic Response Model Schema Conformance Tests.

Validates that current Pydantic response models match their saved JSON Schema
snapshots. When a model changes (fields added/removed/retyped), this test
catches the divergence.

Snapshot files live in ``schema_snapshots/`` next to this file.
To update snapshots after an intentional model change::

    uv run pytest tests/contracts/gateway/test_schema_conformance.py --snapshot-update

**Run alongside other gateway contract tests:**

    uv run pytest tests/contracts/gateway/ -v
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.community.contracts.gateway.schema_utils import (
    SNAPSHOT_DIR,
    diff_schemas,
    load_snapshot,
    model_to_json_schema,
    save_snapshot,
    validate_mock_against_schema,
)

# ── Pydantic Response Models to Snapshot ────────────────────────────────

# Each entry: (model_class, snapshot_name_override_or_None)
# Modules that have well-typed response models are included here.
# Modules using `ApiResponse(data: Any)` are excluded — their contracts
# are enforced by the field-level assertions in the rule test files.

_MODEL_SNAPSHOT_TARGETS: list[tuple[type, str | None]] = []


def _collect_models():
    """Lazy-import and collect Pydantic response models for snapshot testing."""
    models = []

    # ── devices ──
    from agentclaw.community.adapters.http.devices.schemas import (
        ApiResponse as DeviceApiResponse,
        DeviceBindingResponse,
        DeviceConnectionResponse,
        DeviceBindingWithConnectionResponse,
        BatchSetDeviceEnvResult,
        ExecShellResult,
        DeviceProps,
    )
    models.extend([
        (DeviceBindingResponse, None),
        (DeviceConnectionResponse, None),
        (DeviceBindingWithConnectionResponse, None),
        (BatchSetDeviceEnvResult, None),
        (ExecShellResult, None),
        (DeviceProps, None),
    ])

    # ── expert_chat ──
    from agentclaw.community.adapters.http.expert_chat.schemas import (
        AddChatBotResponse,
        ExpertBotInfo,
        ConnectionInfo,
        ChatSessionResponse,
        ExpertBotListResponse,
    )
    models.extend([
        (AddChatBotResponse, None),
        (ExpertBotInfo, None),
        (ConnectionInfo, None),
        (ChatSessionResponse, None),
        (ExpertBotListResponse, None),
    ])

    # ── bot_public ──
    from agentclaw.community.adapters.http.bot_public.schemas import (
        BotFriendResponse,
        BotFriendApprovalResponse,
    )
    models.extend([
        (BotFriendResponse, None),
        (BotFriendApprovalResponse, None),
    ])

    # ── mcp ──
    from agentclaw.community.adapters.http.mcp.schemas import (
        MCPValidationResponse,
        MCPListResponse,
        MCPDetailResponse,
        MCPPermissionResponse,
        TenantCategory,
        TenantItem,
        TenantListResponse,
        MCPApplyPermissionResponse,
    )
    models.extend([
        (MCPValidationResponse, None),
        (MCPPermissionResponse, None),
        (TenantCategory, None),
        (TenantItem, None),
        (TenantListResponse, None),
        (MCPApplyPermissionResponse, None),
    ])
    # MCPListResponse & MCPDetailResponse use Dict[str, Any] for data — skip

    # ── skill_center ──
    from agentclaw.community.adapters.http.skill_center.schemas import (
        SkillSetResponse,
        SkillSetListResponse,
        SkillInSetSummary,
        SkillInSetResponse,
        SkillSetSkillsResponse,
        CLIInSetResponse,
        SkillSetResourceItem,
        SkillSetResourcesResponse,
        SkillSetWithMCPsItem,
        SkillSetsWithMCPsResponse,
        MCPServerInSetResponse,
        AddMCPResponse,
        SetSkillSetActiveResponse,
        SkillMetadataResponse,
        SkillListResponse,
    )
    models.extend([
        (SkillSetResponse, None),
        (SkillSetListResponse, None),
        (SkillInSetSummary, None),
        (SkillInSetResponse, None),
        (SkillSetSkillsResponse, None),
        (CLIInSetResponse, None),
        (SkillSetResourceItem, None),
        (SkillSetResourcesResponse, None),
        (SkillSetWithMCPsItem, None),
        (SkillSetsWithMCPsResponse, None),
        (MCPServerInSetResponse, None),
        (AddMCPResponse, None),
        (SetSkillSetActiveResponse, None),
        (SkillMetadataResponse, None),
        (SkillListResponse, None),
    ])

    # ── cron ──
    from agentclaw.community.adapters.http.cron.schemas import (
        CronTaskData,
        CronRunData,
        CronStatusData,
    )
    models.extend([
        (CronTaskData, None),
        (CronRunData, None),
        (CronStatusData, None),
    ])

    # ── bot_management ──
    from agentclaw.community.adapters.http.bot_management.schemas import BotListData
    models.append((BotListData, None))

    return models


@pytest.fixture(scope="session")
def snapshot_update(request):
    return request.config.getoption("--snapshot-update", default=False)


@pytest.fixture(scope="session")
def model_targets():
    return _collect_models()


class TestSchemaConformance:
    """Verify each Pydantic response model's JSON Schema matches its snapshot."""

    def test_all_models_have_snapshots(self, model_targets, snapshot_update):
        """Every registered model should have a snapshot file (or create one)."""
        missing = []
        for model_cls, name_override in model_targets:
            name = name_override or model_cls.__name__
            path = SNAPSHOT_DIR / f"{name}.json"
            if not path.exists():
                if snapshot_update:
                    save_snapshot(model_cls, name)
                else:
                    missing.append(name)

        if missing and not snapshot_update:
            pytest.fail(
                f"Missing snapshots for: {missing}. "
                f"Run with --snapshot-update to create them."
            )

    def test_schema_snapshots_match_current_models(self, model_targets, snapshot_update):
        """Current model schemas should match saved snapshots."""
        failures = []
        for model_cls, name_override in model_targets:
            name = name_override or model_cls.__name__

            if snapshot_update:
                save_snapshot(model_cls, name)
                continue

            snapshot = load_snapshot(name)
            if snapshot is None:
                continue  # Handled by test_all_models_have_snapshots

            current = model_to_json_schema(model_cls)
            diffs = diff_schemas(current, snapshot)

            if diffs:
                failures.append(
                    f"{name} schema drift detected:\n" + "\n".join(diffs)
                )

        if failures:
            pytest.fail(
                "Schema snapshot mismatches detected. "
                "If intentional, run with --snapshot-update to refresh.\n\n"
                + "\n\n".join(failures)
            )


class TestBCSContractSchemas:
    """Validate that BCS/Engine mock data in contract tests conforms to
    the authoritative JSON Schema contracts in schema_snapshots/bcs/.

    This breaks the tautology where mock data and field assertions share
    the same source. The schema is the contract; mock data must conform.
    """

    def test_group_mock_matches_schema(self):
        """BCS group mock data should satisfy the group_response schema."""
        from tests.community.contracts.gateway.test_rule07_bcs_groups import MOCK_GROUP
        validate_mock_against_schema(
            MOCK_GROUP,
            load_contract_schema("group_response"),
            label="MOCK_GROUP",
        )

    def test_group_session_mock_matches_schema(self):
        """BCS session mock data should satisfy the session_response schema."""
        from tests.community.contracts.gateway.test_rule07_bcs_groups import MOCK_SESSION_MEMBER_RESPONSE
        validate_mock_against_schema(
            MOCK_SESSION_MEMBER_RESPONSE,
            load_contract_schema("session_member_response"),
            label="MOCK_SESSION_MEMBER_RESPONSE",
        )

    def test_bot_detail_mock_matches_schema(self):
        """BCS bot detail mock data should satisfy the bot_detail_response schema."""
        from tests.community.contracts.gateway.test_rule07_bcs_groups import MOCK_BOT_DETAIL
        validate_mock_against_schema(
            MOCK_BOT_DETAIL,
            load_contract_schema("bot_detail_response"),
            label="MOCK_BOT_DETAIL",
        )

    def test_friend_request_mock_matches_schema(self):
        """BCS friend request mock data should satisfy the friend_request_response schema."""
        from tests.community.contracts.gateway.test_rule07_bcs_groups import MOCK_FRIEND_REQUEST
        validate_mock_against_schema(
            MOCK_FRIEND_REQUEST,
            load_contract_schema("friend_request_response"),
            label="MOCK_FRIEND_REQUEST",
        )

    def test_onboard_mock_matches_schema(self):
        """BCS onboard mock data should satisfy the onboard_response schema."""
        validate_mock_against_schema(
            _get_onboard_mock(),
            load_contract_schema("onboard_response"),
            label="onboard_response",
        )

    def test_engine_session_mock_matches_schema(self):
        """Engine proxy session mock data should satisfy the session_data schema."""
        from tests.community.contracts.gateway.test_rule08_engine_proxy import MOCK_SESSION
        validate_mock_against_schema(
            MOCK_SESSION,
            load_contract_schema("session_data"),
            label="MOCK_SESSION",
        )

    def test_engine_model_mock_matches_schema(self):
        """Engine proxy model mock data should satisfy the model_capabilities schema."""
        from tests.community.contracts.gateway.test_rule08_engine_proxy import MOCK_MODEL
        validate_mock_against_schema(
            MOCK_MODEL,
            load_contract_schema("model_capabilities"),
            label="MOCK_MODEL",
        )

    def test_engine_status_mock_matches_schema(self):
        """Engine proxy status mock data should satisfy the engine_status_response schema."""
        from tests.community.contracts.gateway.test_rule08_engine_proxy import MOCK_ENGINE_STATUS
        validate_mock_against_schema(
            MOCK_ENGINE_STATUS,
            load_contract_schema("engine_status_response"),
            label="MOCK_ENGINE_STATUS",
        )


def load_contract_schema(name: str) -> dict[str, Any]:
    """Load a BCS/Engine contract schema."""
    schema_path = SNAPSHOT_DIR / "bcs" / f"{name}.json"
    if not schema_path.exists():
        pytest.fail(f"Contract schema not found: {schema_path}")
    return json.loads(schema_path.read_text())


def _get_onboard_mock() -> dict:
    """Extract onboard mock data from test_rule07_bcs_groups."""
    return {
        "bot_uuid": "bot_test_001:448524",
        "onboarded": True,
        "name": "TestBot",
        "capabilities": {
            "name": "TestBot",
            "summary": "A test bot",
            "hidden": False,
            "visibility": "public",
            "skills": [],
            "domains": [],
        },
        "created_by": "448524",
    }
