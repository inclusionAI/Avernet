"""Endpoint-framework coverage for OBJECT_FILE artifact upload and download.

上传走一步原子的后端代理口(POST /artifacts/object-file,multipart);下载按
artifact_id 逐次校验后回发字节(GET /artifacts/{id}/content,非 Envelope 二进制)。
下载 happy 用例以固定 artifact_id 预置一行合法 OBJECT_FILE 产物 + 内存插件里的
对象字节(service 层单测已覆盖上传→下载全程,这里钉 HTTP 协议面)。
"""
from __future__ import annotations

from dataclasses import replace

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.repository.protocols.task import (
    TaskArtifactRepositoryProtocol,
    TaskInfoRepositoryProtocol,
)
from agentclaw.community.core.task.domain.models import (
    Context,
    Goal,
    Status,
    TaskInfo,
    TaskSpec,
)
from agentclaw.community.core.task.repository.types import TaskInfoRecord
from agentclaw.community.core.task.domain.models import TaskNodePatch
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_publish import (
    compose_artifact_record,
)
from agentclaw.community.core.task.task_context.task_artifact.models import (
    ArtifactKind,
    FileContent,
    StorageType,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_object_storage import (
    TaskArtifactObjectStoragePlugin,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_BASE = "/api/v1/collaboration/tasks/artifacts"
_TASK_ID = "task-objfile-endpoint"
_ARTIFACT_ID = "task-art-objfile00001"
_STAFF_COOKIE = {"cookie": "staff_id=user-endpoint-1"}
_TASK_SPEC = TaskSpec(
    context=Context(title="Object file endpoint", background="coverage"),
    goal=Goal(objective="upload and download", acceptances=[]),
)
_UPLOAD_PAYLOAD = b"%PDF-1.4 object-file-endpoint-bytes"
_DOWNLOAD_KEY = f"task-artifacts/{_TASK_ID}/deadbeef00000000.pdf"


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


def _seed_download_row(world) -> None:
    """预置一行固定 id 的 OBJECT_FILE 产物 + 内存插件里的对象字节。"""
    world.get(TaskArtifactObjectStoragePlugin).put_object(_DOWNLOAD_KEY, _UPLOAD_PAYLOAD)
    content = FileContent(
        storage_type=StorageType.OBJECT_FILE,
        storage_id=_DOWNLOAD_KEY,
        file_type="application/pdf",
        file_name="analysis-report.pdf",
        file_id=None,
    )
    record = compose_artifact_record(
        task_id=_TASK_ID,
        node_id=_TASK_ID,
        attempt=0,
        scope_session_id=None,
        kind=ArtifactKind.FILE,
        content=content,
        artifact_name="analysis-report.pdf",
        creator="user-endpoint-1",
        now_ms=1730000000000,
    )
    record = replace(record, artifact_id=_ARTIFACT_ID)
    world.get(TaskArtifactRepositoryProtocol).append(record)


# ------------------------------------------------------------------ 上传


@endpoint_test(
    method="POST",
    path=f"{_BASE}/object-file",
    scenario="happy_ok",
    seed=_seed_artifact_graph,
    input=CaseInput(
        headers=_STAFF_COOKIE,
        form_data={
            "task_id": _TASK_ID,
            "node_id": _TASK_ID,
            "attempt": 0,
        },
        files=[("file", ("analysis-report.pdf", _UPLOAD_PAYLOAD, "application/pdf"))],
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "artifact_name": "analysis-report.pdf",
                "kind": "file",
                "attempt": 0,
                "content": {"kind": "file", "storage_type": "object_file"},
            },
        },
    ),
)
def upload_object_file_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/object-file",
    scenario="err_unauthenticated",
    input=CaseInput(
        form_data={"task_id": _TASK_ID, "node_id": _TASK_ID, "attempt": 0},
        files=[("file", ("a.pdf", b"bytes"))],
    ),
    expect=ExpectError(status=401),
)
def upload_object_file_unauthenticated():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/object-file",
    scenario="err_task_not_found",
    input=CaseInput(
        headers=_STAFF_COOKIE,
        form_data={"task_id": "unknown-task", "node_id": "unknown-task", "attempt": 0},
        files=[("file", ("a.pdf", b"bytes"))],
    ),
    expect=ExpectError(status=404),
)
def upload_object_file_unknown_task():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/object-file",
    scenario="err_attempt_mismatch",
    seed=_seed_artifact_graph,
    input=CaseInput(
        headers=_STAFF_COOKIE,
        form_data={"task_id": _TASK_ID, "node_id": _TASK_ID, "attempt": 99},
        files=[("file", ("a.pdf", b"bytes"))],
    ),
    expect=ExpectError(status=422),
)
def upload_object_file_attempt_mismatch():
    pass


# ------------------------------------------------------------------ 下载


def _assert_download_payload(response, world):
    assert response.content == _UPLOAD_PAYLOAD
    assert "attachment" in response.headers.get("content-disposition", "")


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{artifact_id}}/content",
    scenario="happy_ok",
    seed=_seed_download_row,
    input=CaseInput(
        headers=_STAFF_COOKIE,
        path_params={"artifact_id": _ARTIFACT_ID},
    ),
    expect=ExpectSuccess(status=200),
    extra_assertions=(_assert_download_payload,),
)
def download_object_file_happy():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{artifact_id}}/content",
    scenario="err_unauthenticated",
    input=CaseInput(path_params={"artifact_id": _ARTIFACT_ID}),
    expect=ExpectError(status=401),
)
def download_object_file_unauthenticated():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{artifact_id}}/content",
    scenario="err_not_found",
    input=CaseInput(
        headers=_STAFF_COOKIE,
        path_params={"artifact_id": "task-art-unknown0000000"},
    ),
    expect=ExpectError(status=404),
)
def download_object_file_not_found():
    pass

# ------------------------------------------------------------------ relay 分支(spec §14)


_RELAY_BOT = "bot-endpoint"  # 短名(≤11 位)不触发 pre-push 凭证扫描


def _seed_relay_claim(world) -> None:
    """在既有图上打认领上下文:节点 relay_token + assignee(claim 时绑定)。"""
    _seed_artifact_graph(world)
    world.get(TaskGraphService).update_task_node_info(
        TaskNodePatch(
            task_id=_TASK_ID,
            node_id=_TASK_ID,
            assignee=_RELAY_BOT,
            extend_props_patch={"relay_token": "gen-1"},
        )
    )


def _seed_relay_unclaimed(world) -> None:
    """已派发 token 但无人认领(纯代次 fence,无 assignee)。"""
    _seed_artifact_graph(world)
    world.get(TaskGraphService).update_task_node_info(
        TaskNodePatch(
            task_id=_TASK_ID,
            node_id=_TASK_ID,
            extend_props_patch={"relay_token": "gen-1"},
        )
    )


@endpoint_test(
    method="POST",
    path=f"{_BASE}/object-file",
    scenario="relay_happy",
    seed=_seed_relay_claim,
    input=CaseInput(
        # cookie 与 token 双带:钉分支只看 token,creator 记 assignee 而非 staff id。
        headers={"X-Relay-Token": "gen-1", "cookie": "staff_id=user-endpoint-1"},
        form_data={"task_id": _TASK_ID, "node_id": _TASK_ID, "attempt": 0},
        files=[("file", ("relay.pdf", _UPLOAD_PAYLOAD, "application/pdf"))],
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "creator": _RELAY_BOT,
                "kind": "file",
                "attempt": 0,
                "content": {"kind": "file", "storage_type": "object_file"},
            },
        },
    ),
)
def upload_object_file_relay_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/object-file",
    scenario="relay_err_stale_token",
    seed=_seed_relay_claim,
    input=CaseInput(
        headers={"X-Relay-Token": "gen-wrong"},
        # attempt 同时失配:钉 fence(409)先于快照(422)的业务序。
        form_data={"task_id": _TASK_ID, "node_id": _TASK_ID, "attempt": 99},
        files=[("file", ("a.pdf", b"bytes"))],
    ),
    expect=ExpectError(status=409),
)
def upload_object_file_relay_stale_token():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/object-file",
    scenario="relay_err_empty_header_falls_back_to_user_branch",
    seed=_seed_relay_claim,
    input=CaseInput(
        # 空白 token 视为未带(§14.5):走用户分支,无登录态即 401,而非 409。
        headers={"X-Relay-Token": "  "},
        form_data={"task_id": _TASK_ID, "node_id": _TASK_ID, "attempt": 0},
        files=[("file", ("a.pdf", b"bytes"))],
    ),
    expect=ExpectError(status=401),
)
def upload_object_file_relay_empty_token_header():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/object-file",
    scenario="relay_err_unclaimed_node",
    seed=_seed_relay_unclaimed,
    input=CaseInput(
        headers={"X-Relay-Token": "gen-1"},
        form_data={"task_id": _TASK_ID, "node_id": _TASK_ID, "attempt": 0},
        files=[("file", ("a.pdf", b"bytes"))],
    ),
    expect=ExpectError(status=409),
)
def upload_object_file_relay_unclaimed():
    pass
