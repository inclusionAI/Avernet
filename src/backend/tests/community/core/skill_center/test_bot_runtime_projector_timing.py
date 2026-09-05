from __future__ import annotations

import logging

from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    ResolvedCapabilityPlan,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projector import (
    BotRuntimeProjector,
)


class _Bots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        assert (bot_id, owner_id) == ("bot-1", "owner-1")
        return {
            "id": 1,
            "active_engine": "openclaw",
            "entity_id": owner_id,
            "entity_type": "staff",
        }


class _Factory:
    def create(self, **_kwargs) -> "_SkillSetService":
        return _SkillSetService()


class _SkillSetService:
    def collect_bot_active_mcps(self, **_kwargs) -> list[dict]:
        return []


class _Reader:
    def active_skill_assets(self, **_kwargs) -> list:
        return []


class _Projection:
    def validate_plan(self, **_kwargs) -> None:
        return None


class _Registry:
    def for_engine(self, engine: str) -> _Projection:
        assert engine == "openclaw"
        return _Projection()


class _Repository:
    def list_installed_mcps(self, **_kwargs) -> set[str]:
        return set()


class _Passport:
    def query_passport_clis(self, *_args) -> list[dict]:
        return []


class _IdentityRepository:
    def list_draft_call_types(self, *_args) -> dict:
        return {}


def test_runtime_projector_logs_skill_and_mcp_plan_timing(caplog) -> None:
    projector = BotRuntimeProjector(
        factory=_Factory(),
        bot_repo=_Bots(),
        repository=_Repository(),
        reader=_Reader(),
        registry=_Registry(),
        passport=_Passport(),
        caller_identity_repo=_IdentityRepository(),
    )
    caplog.set_level(logging.INFO)

    plan = projector._resolve_plan(
        bot_id="bot-1",
        owner_id="owner-1",
        scope=ProjectionScope(skills=True, mcp=True),
    )

    assert isinstance(plan, ResolvedCapabilityPlan)
    messages = [record.getMessage() for record in caplog.records]
    for stage in (
        "build_skill_plan",
        "build_mcp_plan",
        "resolve_mcp_identity_modes",
        "collect_effective_mcps",
        "query_passport_clis",
        "read_installed_mcps",
        "resolve_effective_capabilities",
    ):
        assert any(
            "[BotRuntimeProjector] timing" in message
            and f"stage={stage}" in message
            and "bot_id=bot-1" in message
            and "duration_ms=" in message
            for message in messages
        )
