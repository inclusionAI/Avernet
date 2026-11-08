"""Endpoint-framework coverage for task artifact registration and listing."""
from __future__ import annotations

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.repository.protocols.task import (
    TaskInfoRepositoryProtocol,
)
from agentclaw.community.core.task.domain.models import (
    Context,
    Goal,
    Status,
    TaskInfo,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.repository.types import TaskInfoRecord
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_BASE = "/api/v1/collaboration/tasks/artifacts"
_TASK_ID = "task-artifact-endpoint"
_STAFF_COOKIE = {"cookie": "staff_id=user-endpoint-1"}
_TASK_SPEC = TaskSpec(
    context=Context(title="Artifact endpoint", background="coverage"),
    goal=Goal(objective="register an artifact", acceptances=[]),
)
_ARTIFACT_BODY = {
    "artifact_name": None,
    "artifact_kind": "file",
    "session_id": None,
    "task_id": _TASK_ID,
    "node_id": _TASK_ID,
    "attempt": 0,
    "text_content": None,
    "file_content": {
        "storage_type": "bot_workspace",
        "storage_id": "reports/analysis-report.md",
        "file_type": "text/markdown",
        "file_name": "analysis-report.md",
        "file_id": None,
    },
    "url_link_content": None,
    "code_content": None,
}


def _seed_artifact_graph(world) -> None:
    world.get(TaskInfoRepositoryProtocol).insert(
        TaskInfoRecord(
            id=0,
            task_id=_TASK_ID,
            source_type="bot",
            owner_user_id="user-endpoint-1",
            owner_bot_id="bot-endpoint-1",
            execution_config={"MAX_DEPTH": 3, "BBS_MAX_DEPTH": 3},
            task_spec=_TASK_SPEC.to_dict(),
            status=Status.PENDING,
        )
    )
    # Resolving the facade attaches the persistent graph repository to the
    # TaskGraphService singleton used to build the artifact validation state.
    world.get(TaskServiceProtocol)
    world.get(TaskGraphService).initialize_graph(
        TaskInfo(
            task_id=_TASK_ID,
            task_spec=_TASK_SPEC,
            source_type="bot",
            owner_bot_id="bot-endpoint-1",
            owner_user_id="user-endpoint-1",
            execution_config={"MAX_DEPTH": 3, "BBS_MAX_DEPTH": 3},
        )
    )


@endpoint_test(
    method="GET",
    path=_BASE,
    scenario="happy_empty",
    input=CaseInput(
        headers=_STAFF_COOKIE,
        query_params={"task_id": _TASK_ID},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": []},
    ),
)
def list_artifacts_happy():
    pass


@endpoint_test(
    method="GET",
    path=_BASE,
    scenario="err_unauthenticated",
    input=CaseInput(query_params={"task_id": _TASK_ID}),
    expect=ExpectError(status=401),
)
def list_artifacts_unauthenticated():
    pass


@endpoint_test(
    method="POST",
    path=_BASE,
    scenario="happy_ok",
    seed=_seed_artifact_graph,
    input=CaseInput(headers=_STAFF_COOKIE, json_body=_ARTIFACT_BODY),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "artifact_name": "analysis-report.md",
                "kind": "file",
                "attempt": 0,
                "content": {
                    "kind": "file",
                    "storage_id": "reports/analysis-report.md",
                },
            },
        },
    ),
)
def add_artifact_happy():
    pass


@endpoint_test(
    method="POST",
    path=_BASE,
    scenario="err_unauthenticated",
    input=CaseInput(json_body=_ARTIFACT_BODY),
    expect=ExpectError(status=401),
)
def add_artifact_unauthenticated():
    pass


# ------------------------------------------------------------------ relay 分支(spec §14)


# relay 夹具用短字面量而非 12+ 位变量名:pre-push 凭证扫描会把高熵裸标识符当疑似凭证。


def _seed_relay_claim(world) -> None:
    _seed_artifact_graph(world)
    world.get(TaskGraphService).update_task_node_info(
        TaskNodePatch(
            task_id=_TASK_ID,
            node_id=_TASK_ID,
            assignee="bot-relay",
            extend_props_patch={"relay_token": "gen-1"},
        )
    )


@endpoint_test(
    method="POST",
    path=_BASE,
    scenario="relay_register_happy",
    seed=_seed_relay_claim,
    input=CaseInput(
        headers={"X-Relay-Token": "gen-1"},
        json_body=_ARTIFACT_BODY,
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"kind": "file", "attempt": 0},
        },
    ),
)
def add_artifact_relay_token_happy():
    pass


@endpoint_test(
    method="POST",
    path=_BASE,
    scenario="relay_register_stale",
    seed=_seed_relay_claim,
    input=CaseInput(
        headers={"X-Relay-Token": "gen-wrong"},
        json_body=_ARTIFACT_BODY,
    ),
    expect=ExpectError(status=409),
)
def add_artifact_relay_token_stale():
    pass
