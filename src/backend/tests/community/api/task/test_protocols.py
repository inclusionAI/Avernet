"""TDD for task module API Protocols (Phase 0.5).

api/task/ is Protocol-only (no impl). 6 Protocols align plan §2.1/§2.4:
- TaskService (unified: query get/list/progress + intake create/amend/
  finalize_plan + on_event fold / claim_node guard). One Protocol, per spec/plan
  定稿 — NOT split into Query/Intake.
- TaskDriverPort (Scheduler dispatch/redispatch/escalate_to_bbs)
- BotDiscoverPort (recommend)
- DecomposerPort (decompose)
- ExecutionPort (dispatch_single_bot/coop_group/redispatch_node/bbs)

Tests assert Protocol shape + runtime_checkable structural conformance via
Noop impls. Signatures carry DTOs (BotCandidate / RouteRecommendation /
DispatchResult) defined alongside.
"""
from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.task.protocols import (
    BcsCollaborationProtocol,
    BotCandidate,
    BotDiscoverPort,
    DecomposerPort,
    DispatchResult,
    ExecutionPort,
    RouteRecommendation,
    TaskDriverPort,
    TaskService,
)
from agentclaw.community.core.task.domain.models import (
    Plan,
    RouteClass,
    RunMode,
)


# --- Noop impls (structurally conform) --------------------------------------

class _NoopTaskService:
    # query face
    def get(self, task_id: str) -> Any:
        return None

    def list_by_user(self, user_id: str, limit: int = 50) -> list[Any]:
        return []

    def progress(self, task_id: str) -> dict:
        return {}

    # intake face
    def create(self, title: str, source: str = "api", background: str = "") -> Any:
        return None

    def amend(self, task_id: str, patch: dict) -> Any:
        return None

    def finalize_plan(self, task_id: str, plan: Any) -> Any:
        return None

    # event fold / guard face (plan §2.1, §5.3)
    def on_event(self, event: Any) -> Any:
        return None

    def claim_node(self, task_id: str, node_id: str, executor_id: str) -> Any:
        return None


class _NoopDiscover:
    def recommend(self, task_id: str, node_id: str) -> Any:
        return None


class _NoopDecomposer:
    def decompose(self, task_id: str) -> Any:
        return None


class _NoopDriver:
    def dispatch_node(self, task_id: str, node_id: str) -> Any:
        return None

    def redispatch(self, task_id: str, node_id: str, route_class: Any) -> Any:
        return None

    def escalate_to_bbs(self, task_id: str, reason: str = "") -> Any:
        return None


class _NoopExecution:
    def dispatch_single_bot(self, task_id: str, node_id: str, bot_id: str) -> Any:
        return None

    def coop_group(self, task_id: str, node_id: str, bot_ids: list[str]) -> Any:
        return None

    def redispatch_node(self, task_id: str, node_id: str, bot_id: str) -> Any:
        return None

    def probe(self, task_id: str, node_id: str, bot_id: str) -> Any:
        # 6.5: watchdog PROBE asks the executor to report its current status.
        return None

    def bbs(self, task_id: str, node_id: str, reason: str = "") -> Any:
        return None


class _NoopBcsCollab:
    def fetch_state_machine_run_graph(self, bcs_run_id: str) -> Any:
        return None

    def fetch_node_detail(self, bcs_run_id: str, node_id: str) -> Any:
        return None


# --- Protocol existence + runtime_checkable ---------------------------------

@pytest.mark.parametrize(
    "proto,noop",
    [
        (TaskService, _NoopTaskService()),
        (BotDiscoverPort, _NoopDiscover()),
        (DecomposerPort, _NoopDecomposer()),
        (TaskDriverPort, _NoopDriver()),
        (ExecutionPort, _NoopExecution()),
        (BcsCollaborationProtocol, _NoopBcsCollab()),
    ],
)
def test_protocol_is_runtime_checkable(proto, noop):
    assert isinstance(noop, proto)


def test_six_protocols_distinct():
    protos = {
        TaskService,
        BotDiscoverPort,
        DecomposerPort,
        TaskDriverPort,
        ExecutionPort,
        BcsCollaborationProtocol,
    }
    assert len(protos) == 6


def test_task_service_unified_has_query_and_intake_and_event_faces():
    # one Protocol carries query + intake + on_event/claim_node (NOT split)
    noop = _NoopTaskService()
    assert isinstance(noop, TaskService)
    for m in ("get", "list_by_user", "progress", "create", "amend",
              "finalize_plan", "on_event", "claim_node"):
        assert callable(getattr(noop, m)), f"TaskService missing {m}"


def test_bcs_collaboration_protocol_is_readonly_query_face():
    # plan §2.4/§1.4b: BcsCollaborationProtocol is a read-only query Port for
    # sub-dag drill-down (fetch SM run graph / node detail). No state writes.
    noop = _NoopBcsCollab()
    assert isinstance(noop, BcsCollaborationProtocol)
    for m in ("fetch_state_machine_run_graph", "fetch_node_detail"):
        assert callable(getattr(noop, m)), f"BcsCollaborationProtocol missing {m}"


# --- DTO shapes -------------------------------------------------------------

def test_bot_candidate_fields():
    c = BotCandidate(bot_id="b1", fit_score=0.8, reason="fast")
    assert c.bot_id == "b1"
    assert c.fit_score == 0.8
    assert c.reason == "fast"


def test_route_recommendation_defaults():
    r = RouteRecommendation(route_class=RouteClass.C3, run_mode=RunMode.COOP_GROUP)
    assert r.route_class is RouteClass.C3
    assert r.run_mode is RunMode.COOP_GROUP
    assert r.candidates == []
    assert r.confidence == 0.0


def test_dispatch_result_fields():
    d = DispatchResult(node_id="n1", executor_id="b1", run_mode=RunMode.SINGLE_BOT)
    assert d.node_id == "n1"
    assert d.executor_id == "b1"
    assert d.run_mode is RunMode.SINGLE_BOT
    assert d.accept_token == ""


def test_decomposer_returns_plan_signable():
    plan = Plan()
    assert plan.sub_tasks == []