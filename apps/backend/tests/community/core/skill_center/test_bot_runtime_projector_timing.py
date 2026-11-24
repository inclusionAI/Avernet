from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agentclaw.community.core.digital_employee.contracts import (
    DigitalEmployeeRepositoryProtocol,
    DigitalEmployeeServiceProtocol,
    DigitalEmployeeSettings,
)
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401
from agentclaw.community.core.skill_center.capability_state_contract import BotCapabilitySnapshot

from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
    ResolvedCapabilityPlan,
)
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeProjectionResolver,
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
        assert isinstance(_kwargs["capability_snapshot"], BotCapabilitySnapshot)
        return []


class _Reader:
    def __init__(self):
        self.snapshot_reads = 0

    def active_capabilities(self, **kwargs):
        self.snapshot_reads += 1
        return BotCapabilitySnapshot(kwargs["bot_id"], kwargs["owner_id"], (), frozenset())

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


def _projector() -> BotRuntimeProjector:
    return BotRuntimeProjector(
        factory=_Factory(),
        bot_repo=_Bots(),
        repository=_Repository(),
        reader=_Reader(),
        registry=_Registry(),
        passport=_Passport(),
        caller_identity_repo=_IdentityRepository(),
    )


def test_runtime_projector_logs_skill_and_mcp_plan_timing(caplog) -> None:
    projector = _projector()
    caplog.set_level(logging.INFO)

    plan = projector._resolve_plan(
        bot_id="bot-1",
        owner_id="owner-1",
        scope=ProjectionScope(skills=True, mcp=True),
    )

    assert isinstance(plan, ResolvedCapabilityPlan)
    assert projector._reader.snapshot_reads == 1
    messages = [record.getMessage() for record in caplog.records]
    for stage in (
        "build_skill_plan",
        "build_mcp_plan",
        "resolve_mcp_identity_modes",
        "collect_effective_mcps",
        "query_passport_clis",
        "resolve_effective_capabilities",
    ):
        assert any(
            "[BotRuntimeProjector] timing" in message
            and f"stage={stage}" in message
            and "bot_id=bot-1" in message
            and "duration_ms=" in message
            and "outcome=success" in message
            for message in messages
        )


@pytest.mark.parametrize(
    "stage",
    (
        "resolve_mcp_identity_modes",
        "build_skill_plan",
        "resolve_effective_capabilities",
    ),
)
def test_runtime_projector_logs_substage_errors(
    caplog, monkeypatch, stage: str
) -> None:
    projector = _projector()
    failure = RuntimeError(stage)

    def fail(*_args, **_kwargs):
        raise failure

    if stage == "resolve_mcp_identity_modes":
        monkeypatch.setattr(projector, "_resolve_mcp_identity_modes", fail)
    elif stage == "build_skill_plan":
        monkeypatch.setattr(projector._reader, "active_capabilities", fail)
    else:
        monkeypatch.setattr(RuntimeProjectionResolver, "resolve", fail)

    caplog.set_level(logging.INFO)
    with pytest.raises(RuntimeError, match=stage):
        projector._resolve_plan(
            bot_id="bot-1",
            owner_id="owner-1",
            scope=ProjectionScope(skills=True, mcp=True),
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "[BotRuntimeProjector] timing" in message
        and f"stage={stage}" in message
        and "outcome=error" in message
        for message in messages
    )


def test_employee_projection_retains_online_mcp_after_draft_removal():
    projector = _projector()
    old = {"mcp_code": "online-tool", "identity_mode": "caller"}
    passport = Mock()
    passport.query_agent_passport.return_value = {"mcps": [old], "clis": []}
    employee = Mock()
    employee.prepare_mcp_change.return_value = [old]
    employee.serialize_passport_scope.return_value = nullcontext()
    projector._passport = passport
    projector._employee_service_provider = lambda: employee
    bot = {"entity_id": "original-entity", "ext": {"digital_employee": {"status": "ACTIVE"}}}
    plan = SimpleNamespace(bot_id="bot-1", owner_id="owner-1", bot=bot, engine="openclaw",
                           projection=SimpleNamespace(mcp_server_codes=[]), identity_modes={}, effective_cli_items=[])
    projector._apply_passport_projection(plan=plan)
    employee.prepare_mcp_change.assert_called_once_with(bot, [], [old], "owner-1")
    assert passport.update_passport.call_args.kwargs["user_id"] == "original-entity"
    scope = passport.update_passport.call_args.kwargs["resource_scope"]
    assert scope["mcp_codes"] == ["online-tool"]
    assert scope["mcp_items"][0]["identity_mode"] == "caller"


def test_concurrent_employee_draft_removal_keeps_new_passport_grant(world, monkeypatch):
    bot = make_bot(world, bot_id="employee-race", owner_id="owner-1", bot_type="service")
    repo = world.get(DigitalEmployeeRepositoryProtocol)
    detail = {"agentId": str(bot["id"]), "platformCode": "test"}
    repo.begin_binding(bot["id"], "AI00000001", "onboarding", detail)
    assert repo.change_binding_phase(bot["id"], "AI00000001", "DETAIL_READY", "ACTIVE")
    bot = repo.get_bot(bot["id"])

    employee = world.get(DigitalEmployeeServiceProtocol)
    monkeypatch.setattr(employee, "_settings", DigitalEmployeeSettings(enabled=True))
    monkeypatch.setattr(employee, "_mcp_center", Mock(
        get_mcp_detail=Mock(return_value={"accessLevel": "PUBLIC"})
    ))
    projector = _projector()
    projector._employee_service_provider = lambda: employee
    passport = Mock()
    projector._passport = passport

    addition_ready = Event()
    allow_addition = Event()
    removal_attempted = Event()
    removal_read = Event()
    scope = {"mcps": [], "clis": []}

    def query(*_args):
        if addition_ready.is_set():
            removal_read.set()
        return scope.copy()

    def update(*, resource_scope, **_kwargs):
        if not addition_ready.is_set():
            addition_ready.set()
            assert allow_addition.wait(5)
        scope["mcps"] = resource_scope["mcp_items"]

    passport.query_agent_passport.side_effect = query
    passport.update_passport.side_effect = update
    lock_scope = employee.serialize_passport_scope

    def guard(current_bot):
        if addition_ready.is_set():
            removal_attempted.set()
        return lock_scope(current_bot)

    monkeypatch.setattr(employee, "serialize_passport_scope", guard)

    def apply(codes):
        plan = SimpleNamespace(
            bot_id=bot["bot_id"], owner_id=bot["owner_id"], bot=bot, engine="openclaw",
            projection=SimpleNamespace(mcp_server_codes=codes),
            identity_modes={}, effective_cli_items=[],
        )
        projector._apply_passport_projection(plan=plan)

    with ThreadPoolExecutor(max_workers=2) as executor:
        addition = executor.submit(apply, ["new-tool"])
        try:
            assert addition_ready.wait(5)
            removal = executor.submit(apply, [])
            assert removal_attempted.wait(5)
            # The removal has reached the guard but cannot see a stale snapshot.
            assert not removal_read.wait(0.2)
        finally:
            allow_addition.set()
        addition.result(timeout=5)
        removal.result(timeout=5)

    assert passport.update_passport.call_count == 2
    assert [item["mcp_code"] for item in scope["mcps"]] == ["new-tool"]
