"""Internal /api/v1 context collab delivery tests (spec §6.1-§6.4).

Mirrors the ``knowledge_management/test_http.py`` recipe: a fresh
``FastAPI()`` with only the ``context_collab`` router, ``get_current_user``
overridden to a fixed ``AuthenticatedUser``, and an injector binding
``ContextCollabServiceProtocol`` to the REAL service over the real repository
and in-memory SQLite (no shared ``endpoints/conftest.py`` change — the
SQLite harness is a local copy of the core-side ``context_collab/conftest``).

Route prefix is asserted to be the spec §6.1 exact form — singular ``task``.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from injector import Injector
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.base import Base

# Side-effect import: registers the 2 context collab models on Base.metadata.
import agentclaw.community.core.task.context_collab.persistence.models  # noqa: F401
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.services.context_collab_service import (
    ContextCollabService,
)
from agentclaw.community.plugins.local.context_collab_runtime import (
    LocalContextCollabRuntimePlugin,
)
from agentclaw.community.api.task.context_collab_service import (
    ContextCollabServiceProtocol,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ContextCollabCreatedResult,
    ContextCollabRunResultDTO,
    ContextCollabStatusProjection,
    EventAcceptedResult,
    TaskProgressProjectionDTO,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

# Match the real app's DI-first bootstrap (auth-first isolated imports are not
# a supported baseline; see knowledge_management/conftest.py for the precedent).
import agentclaw.community.di  # noqa: E402,F401

PREFIX = "/api/v1/collaboration/task"  # spec §6.1: singular "task"

AUTHENTICATED_STAFF_ID = "u_1001"


class InMemorySqliteDB:
    """Minimal DatabasePlugin stand-in offering orm_session()."""

    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class RecordingContextCollabService:
    """Protocol-shaped passthrough that records which use case was called."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = []
        self.create_commands = []

    def create_context(self, command, context):
        self.calls.append("create_context")
        self.create_commands.append(command)
        return self._inner.create_context(command, context)

    def append_event(self, collab_context_id, command, context):
        self.calls.append("append_event")
        return self._inner.append_event(collab_context_id, command, context)

    async def run_context(self, collab_context_id, context):
        self.calls.append("run_context")
        return await self._inner.run_context(collab_context_id, context)

    def get_status(self, collab_context_id, *, trunk_events, context):
        self.calls.append("get_status")
        return self._inner.get_status(
            collab_context_id, trunk_events=trunk_events, context=context
        )


@pytest.fixture
def client_world():
    from agentclaw.community.adapters.http.task.context_collab import router
    from fastapi import FastAPI
    from fastapi_injector import attach_injector

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    service = RecordingContextCollabService(
        ContextCollabService(
            repository=ContextCollabRepository(InMemorySqliteDB(engine)),
            runtime=LocalContextCollabRuntimePlugin(),
            authorizer=StructuralContextCollabAuthorizer(),
        )
    )

    app = FastAPI()
    app.include_router(router)

    def authenticated_user():
        return AuthenticatedUser(
            id="SSO", staffId=AUTHENTICATED_STAFF_ID, operatorName="login"
        )

    app.dependency_overrides[get_current_user] = authenticated_user
    injector = Injector(auto_bind=False)
    injector.binder.bind(ContextCollabServiceProtocol, to=service)
    attach_injector(app, injector)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, service=service)
    engine.dispose()


def create_body():
    """spec §6.2 指定执行请求示例 (verbatim JSON)."""
    return {
        "space": {"space_id": "space_workspace_001", "environment": "prod"},
        "principal_ref": {"principal_type": "human", "principal_id": "u_1001"},
        "product_kind": "team_todo",
        "workflow_kind": "team_todo_execution",
        "method_kind": "Single",
        "executor_ref": {"agent_type": "bot", "agent_id": "bot_coding_001"},
        "objective": "整理 Context 协作协议评审结论并提交可评审 PR",
        "resource_refs": [
            {
                "resource_type": "forum_topic",
                "resource_id": "topic_001",
                "revision": "3",
            }
        ],
    }


def append_body(parent_event):
    """spec §6.3 用户新增互动消息示例 (verbatim JSON, parent linked)."""
    return {
        "expected_version": 1,
        "event_kind": "user_interaction.posted",
        "primitive_type": "Message",
        "actor_ref": {"principal_type": "human", "principal_id": "u_1001"},
        "parent_event": parent_event,
        "visibility": "workspace",
        "payload": {
            "message_kind": "user_interaction",
            "content": "上下文做一下补充xxx",
        },
    }


def create_context(client_world, changes=None):
    body = create_body() | (changes or {})
    response = client_world.client.post(f"{PREFIX}/context_collab", json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def append_event(client_world, collab_context_id, parent_event, changes=None):
    body = append_body(parent_event) | (changes or {})
    response = client_world.client.post(
        f"{PREFIX}/context_collab/{collab_context_id}/events", json=body
    )
    return response


# ── §6.2 创建 ──────────────────────────────────────────────────────────────


def test_create_happy_path_envelope_and_snapshot(client_world):
    response = client_world.client.post(f"{PREFIX}/context_collab", json=create_body())
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200000
    assert body["message"] == "OK"
    assert "request_id" in body
    assert body["data"]["collab_context_id"].startswith("collet_")
    assert body["data"]["context_version"] == 1

    snapshot = body["data"]["current_snapshot"]
    assert snapshot["objective"] == "整理 Context 协作协议评审结论并提交可评审 PR"
    assert snapshot["status"] == "created"
    assert snapshot["principal_ref"] == "u_1001"
    assert snapshot["executor_ref"] == "bot_coding_001"
    assert snapshot["method_kind"] == "Single"
    assert snapshot["progress"]["percent"] == 0
    assert (
        snapshot["progress"]["current_event_id"]
        == body["data"]["current_event"]["event_id"]
    )

    current_event = body["data"]["current_event"]
    assert current_event["event_id"].startswith("evt_")
    assert current_event["parent_event"] is None
    assert current_event["event_kind"] == "task.created"
    assert current_event["primitive_type"] == "Task"


def test_create_carries_executor_owner_identity_into_context_spec(client_world):
    response = client_world.client.post(
        f"{PREFIX}/context_collab",
        json={**create_body(), "executor_owner_id": "bot_owner_1"},
    )

    assert response.status_code == 200, response.text
    assert client_world.service.create_commands[-1].executor_owner_id == "bot_owner_1"


def test_create_route_path_is_singular_task(client_world):
    # spec §6.1 spells the prefix .../collaboration/task (singular); the plural
    # /api/v1/collaboration/tasks surface must not gain a context_collab route.
    assert (
        client_world.client.post(
            "/api/v1/collaboration/tasks/context_collab", json=create_body()
        ).status_code
        == 404
    )


# ── §6.3 追加事件 ───────────────────────────────────────────────────────────


def test_append_event_happy_path_version_chain(client_world):
    created = create_context(client_world)
    response = append_event(
        client_world, created["collab_context_id"], created["current_event"]["event_id"]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 200000
    data = body["data"]
    assert data["accepted"] is True
    assert data["collab_context_id"] == created["collab_context_id"]
    assert data["event_id"].startswith("evt_")
    assert data["event_id"] != created["current_event"]["event_id"]
    assert data["parent_event"] == created["current_event"]["event_id"]
    assert data["event_kind"] == "user_interaction.posted"
    assert data["primitive_type"] == "Message"
    assert data["context_version"] == 2
    assert data["current_status"] == "created"


def test_append_event_actor_ref_defaults_to_authenticated_actor(client_world):
    created = create_context(client_world)
    body = append_body(created["current_event"]["event_id"])
    body.pop("actor_ref")
    response = client_world.client.post(
        f"{PREFIX}/context_collab/{created['collab_context_id']}/events", json=body
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["accepted"] is True


# ── §6.4 查询 ───────────────────────────────────────────────────────────────


def test_get_status_happy_path_with_trunk_events(client_world):
    created = create_context(client_world)
    appended = append_event(
        client_world, created["collab_context_id"], created["current_event"]["event_id"]
    )
    assert appended.status_code == 200, appended.text

    response = client_world.client.get(
        f"{PREFIX}/context_collab/{created['collab_context_id']}?trunk_events=true"
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["collab_context_id"] == created["collab_context_id"]
    assert data["context_version"] == 2
    assert data["current_snapshot"]["status"] == "created"
    assert data["current_event"]["event_kind"] == "user_interaction.posted"
    assert data["current_event"]["created_at"] is not None
    assert data["task_progress"]["total_events"] == 2
    statuses = [event["status"] for event in data["task_progress"]["events"]]
    assert statuses == ["completed", "active"]
    kinds = [event["event_kind"] for event in data["task_progress"]["events"]]
    assert kinds == ["task.created", "user_interaction.posted"]


def test_get_status_without_query_param_defaults_to_all_events(client_world):
    created = create_context(client_world)
    append_event(
        client_world, created["collab_context_id"], created["current_event"]["event_id"]
    )
    response = client_world.client.get(
        f"{PREFIX}/context_collab/{created['collab_context_id']}"
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["task_progress"]["total_events"] == 2


def test_get_status_wire_shape_has_no_steps_field(client_world):
    """spec §12.2 #5(wire 侧锚点):GET 响应 data 恰为 §6.4 三段式五键,
    task_progress.events[] 条目恰为 7 字段投影 —— delivery 未来多加一个
    字段(如 steps[])即红,不依赖 core 投影的 dataclass 键集。"""
    created = create_context(client_world)
    append_event(
        client_world, created["collab_context_id"], created["current_event"]["event_id"]
    )
    response = client_world.client.get(
        f"{PREFIX}/context_collab/{created['collab_context_id']}"
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == {
        "collab_context_id",
        "context_version",
        "current_snapshot",
        "current_event",
        "task_progress",
    }
    assert set(data["task_progress"]) == {"total_events", "events"}
    assert "steps" not in data["current_snapshot"]
    for entry in data["task_progress"]["events"]:
        assert "steps" not in entry
        assert set(entry) == {
            "event_id",
            "event_seq",
            "parent_event",
            "event_kind",
            "primitive_type",
            "actor_id",
            "actor_type",
            "execution_id",
            "context_version",
            "status",
            "summary",
            "content",
            "artifacts",
            "created_at",
        }


# ── 领域错误映射(ErrorEnvelope)──────────────────────────────────────────────


def test_version_conflict_maps_to_409_error_envelope(client_world):
    created = create_context(client_world)
    append_event(
        client_world, created["collab_context_id"], created["current_event"]["event_id"]
    )
    # Aggregate is now at version 2; an append expecting version 1 conflicts.
    response = append_event(
        client_world,
        created["collab_context_id"],
        created["current_event"]["event_id"],
        changes={"expected_version": 1},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == 409000
    assert body["message"] == "Context version conflict"
    assert body["data"] is None


def test_missing_content_maps_to_422_error_envelope(client_world):
    created = create_context(client_world)
    response = append_event(
        client_world,
        created["collab_context_id"],
        created["current_event"]["event_id"],
        changes={"payload": {"message_kind": "user_interaction"}},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422000
    assert body["message"] == "Invalid context collab request"
    assert body["data"] is None


def test_unknown_parent_event_maps_to_422_error_envelope(client_world):
    created = create_context(client_world)
    response = append_event(
        client_world,
        created["collab_context_id"],
        "evt_does_not_exist",
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422000
    assert body["message"] == "Parent event not found"


def test_actor_ref_mismatch_with_authenticated_actor_is_rejected(client_world):
    created = create_context(client_world)
    response = append_event(
        client_world,
        created["collab_context_id"],
        created["current_event"]["event_id"],
        changes={"actor_ref": {"principal_type": "human", "principal_id": "u_9999"}},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == 422000
    assert body["message"] == "Invalid context collab request"


def test_unknown_context_id_maps_to_404_error_envelope(client_world):
    response = client_world.client.get(f"{PREFIX}/context_collab/collet_missing")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == 404000
    assert body["message"] == "Not found"
    assert body["data"] is None


# ── G1:认证在 delivery,未登录不触达服务 ──────────────────────────────────


def test_unauthenticated_request_is_401_and_service_never_called(client_world):
    def unauthenticated():
        raise HTTPException(401)

    client_world.app.dependency_overrides[get_current_user] = unauthenticated
    assert client_world.service.calls == []
    assert (
        client_world.client.post(
            f"{PREFIX}/context_collab", json=create_body()
        ).status_code
        == 401
    )
    assert client_world.service.calls == []


# ── Endpoint coverage gate cases (framework @endpoint_test) ────────────────
# The framework's frozen-baseline gate (framework/coverage_gate.py) fails on
# any new route without a registered happy + error case, so the three live
# routes also register here. Mirror of test_task_config_router.py: seeds
# ``bind_overrides`` protocol stand-ins onto the framework ``world`` (the
# real service over the per-test in-memory DB has wire-level coverage above),
# cases pin auth (x-user-id -> staffId -> ActorContext), envelope shape and
# the error mapping. These registrations fire because endpoints/conftest.py
# glob-imports every test_*.py at collection time.
_HEADERS = {"x-user-id": AUTHENTICATED_STAFF_ID}
_CREATE_PATH = f"{PREFIX}/context_collab"
_APPEND_PATH = f"{PREFIX}/context_collab/{{collab_context_id}}/events"
_RUN_PATH = f"{PREFIX}/context_collab/{{collab_context_id}}/run"
_GET_PATH = f"{PREFIX}/context_collab/{{collab_context_id}}"

_CASE_SNAPSHOT = {
    "objective": "整理 Context 协作协议评审结论并提交可评审 PR",
    "status": "created",
    "principal_ref": "u_1001",
    "executor_ref": "bot_coding_001",
    "method_kind": "Single",
    "progress": {
        "percent": 0,
        "completed_count": 0,
        "active_count": 0,
        "blocked_count": 0,
        "current_event_id": "evt_case_001",
    },
}


def _event_projection(
    *,
    event_id: str,
    event_seq: int,
    parent_event: str | None,
    event_kind: str,
    primitive_type: str,
    status: str,
    content: str | None = None,
) -> dict:
    return {
        "event_id": event_id,
        "event_seq": event_seq,
        "parent_event": parent_event,
        "event_kind": event_kind,
        "primitive_type": primitive_type,
        "actor_id": "u_1001",
        "actor_type": "human",
        "execution_id": "",
        "context_version": event_seq,
        "status": status,
        "summary": None,
        "content": content,
        "artifacts": [],
        "created_at": "2026-11-10T08:00:00Z",
    }


def _seed_create_happy(world) -> None:
    def create_context(self, _command, _context):
        return ContextCollabCreatedResult(
            collab_context_id="collet_case_001",
            context_version=1,
            current_snapshot=dict(_CASE_SNAPSHOT),
            current_event={
                "event_id": "evt_case_001",
                "parent_event": None,
                "event_kind": "task.created",
                "primitive_type": "Task",
            },
        )

    bind_overrides(
        world,
        ContextCollabServiceProtocol,
        {"create_context": create_context},
    )


def _seed_create_error(world) -> None:
    def create_context(self, _command, _context):
        raise ContextCollabValidationError("case: invalid create body")

    bind_overrides(
        world,
        ContextCollabServiceProtocol,
        {"create_context": create_context},
    )


def _seed_append_happy(world) -> None:
    def append_event(self, _collab_context_id, _command, _context):
        return EventAcceptedResult(
            accepted=True,
            collab_context_id="collet_case_001",
            event_id="evt_case_002",
            parent_event="evt_case_001",
            event_kind="user_interaction.posted",
            primitive_type="Message",
            context_version=2,
            current_status="created",
        )

    bind_overrides(
        world,
        ContextCollabServiceProtocol,
        {"append_event": append_event},
    )


def _seed_append_error(world) -> None:
    def append_event(self, _collab_context_id, _command, _context):
        raise ContextCollabVersionConflict("case: stale expected_version")

    bind_overrides(
        world,
        ContextCollabServiceProtocol,
        {"append_event": append_event},
    )


def _seed_run_happy(world) -> None:
    async def run_context(self, collab_context_id, context):
        return ContextCollabRunResultDTO(
            collab_context_id=collab_context_id,
            run_id="run_case_001",
            session_id="session_case_001",
            context_version=2,
            current_status="in_progress",
        )

    bind_overrides(
        world,
        ContextCollabServiceProtocol,
        {"run_context": run_context},
    )


def _seed_run_error(world) -> None:
    async def run_context(self, collab_context_id, context):
        raise ContextCollabNotFoundError("case: unknown collab context")

    bind_overrides(
        world,
        ContextCollabServiceProtocol,
        {"run_context": run_context},
    )


def _seed_get_happy(world) -> None:
    def get_status(self, _collab_context_id, *, trunk_events, context):
        assert trunk_events is True  # query param must reach the service
        return ContextCollabStatusProjection(
            collab_context_id="collet_case_001",
            context_version=2,
            current_snapshot=dict(_CASE_SNAPSHOT),
            current_event=_event_projection(
                event_id="evt_case_002",
                event_seq=2,
                parent_event="evt_case_001",
                event_kind="user_interaction.posted",
                primitive_type="Message",
                status="active",
                content="上下文做一下补充xxx",
            ),
            task_progress=TaskProgressProjectionDTO(
                total_events=2,
                events=[
                    _event_projection(
                        event_id="evt_case_001",
                        event_seq=1,
                        parent_event=None,
                        event_kind="task.created",
                        primitive_type="Task",
                        status="completed",
                    ),
                    _event_projection(
                        event_id="evt_case_002",
                        event_seq=2,
                        parent_event="evt_case_001",
                        event_kind="user_interaction.posted",
                        primitive_type="Message",
                        status="active",
                        content="上下文做一下补充xxx",
                    ),
                ],
            ),
        )

    bind_overrides(
        world,
        ContextCollabServiceProtocol,
        {"get_status": get_status},
    )


def _seed_get_error(world) -> None:
    def get_status(self, _collab_context_id, *, trunk_events, context):
        raise ContextCollabNotFoundError("case: unknown collab context")

    bind_overrides(
        world,
        ContextCollabServiceProtocol,
        {"get_status": get_status},
    )


@endpoint_test(
    method="POST",
    path=_CREATE_PATH,
    scenario="create_happy",
    input=CaseInput(headers=_HEADERS, json_body=create_body()),
    seed=_seed_create_happy,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"collab_context_id": "collet_case_001", "context_version": 1},
        },
    ),
)
def context_collab_create_happy():
    """Create returns the collet id and the v1 snapshot envelope."""


@endpoint_test(
    method="POST",
    path=_CREATE_PATH,
    scenario="create_error",
    input=CaseInput(
        headers=_HEADERS,
        json_body={**create_body()},
    ),
    seed=_seed_create_error,
    expect=ExpectError(
        status=422,
        json_contains={"code": 422000, "message": "Invalid context collab request"},
    ),
)
def context_collab_create_error():
    """Domain validation maps to the 422 error envelope."""


@endpoint_test(
    method="POST",
    path=_APPEND_PATH,
    scenario="append_happy",
    input=CaseInput(
        path_params={"collab_context_id": "collet_case_001"},
        headers=_HEADERS,
        json_body=append_body("evt_case_001"),
    ),
    seed=_seed_append_happy,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"accepted": True, "context_version": 2},
        },
    ),
)
def context_collab_append_happy():
    """Append returns the accepted event projection at the new version."""


@endpoint_test(
    method="POST",
    path=_APPEND_PATH,
    scenario="append_error",
    input=CaseInput(
        path_params={"collab_context_id": "collet_case_001"},
        headers=_HEADERS,
        json_body=append_body("evt_case_001"),
    ),
    seed=_seed_append_error,
    expect=ExpectError(
        status=409,
        json_contains={"code": 409000, "message": "Context version conflict"},
    ),
)
def context_collab_append_error():
    """Stale expected_version maps to the 409 conflict envelope."""


@endpoint_test(
    method="POST",
    path=_RUN_PATH,
    scenario="run_happy",
    input=CaseInput(
        path_params={"collab_context_id": "collet_case_001"},
        headers=_HEADERS,
    ),
    seed=_seed_run_happy,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "collab_context_id": "collet_case_001",
                "run_id": "run_case_001",
                "context_version": 2,
                "current_status": "in_progress",
            },
        },
    ),
)
def context_collab_run_happy():
    """Run returns the runtime handles and updated Context projection."""


@endpoint_test(
    method="POST",
    path=_RUN_PATH,
    scenario="run_error",
    input=CaseInput(
        path_params={"collab_context_id": "collet_case_missing"},
        headers=_HEADERS,
    ),
    seed=_seed_run_error,
    expect=ExpectError(
        status=404,
        json_contains={"code": 404000, "message": "Not found"},
    ),
)
def context_collab_run_error():
    """Running an unknown Context maps to the 404 error envelope."""


@endpoint_test(
    method="GET",
    path=_GET_PATH,
    scenario="get_happy",
    input=CaseInput(
        path_params={"collab_context_id": "collet_case_001"},
        query_params={"trunk_events": True},
        headers=_HEADERS,
    ),
    seed=_seed_get_happy,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "collab_context_id": "collet_case_001",
                "current_snapshot": {"status": "created", "principal_ref": "u_1001"},
                "task_progress": {"total_events": 2},
            },
        },
    ),
)
def context_collab_get_happy():
    """Get returns snapshot, current event and the trunk-filtered progress."""


@endpoint_test(
    method="GET",
    path=_GET_PATH,
    scenario="get_error",
    input=CaseInput(
        path_params={"collab_context_id": "collet_case_missing"},
        headers=_HEADERS,
    ),
    seed=_seed_get_error,
    expect=ExpectError(
        status=404,
        json_contains={"code": 404000, "message": "Not found"},
    ),
)
def context_collab_get_error():
    """Unknown collab context maps to the 404 error envelope."""
