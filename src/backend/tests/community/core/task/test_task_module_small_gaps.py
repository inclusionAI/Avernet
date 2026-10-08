from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from agentclaw.community.core.task.domain.errors import (
    TaskArtifactContentError,
    TaskStateError,
)
from agentclaw.community.core.task.domain.json_extract import (
    _balanced_substring,
    extract_json,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceVerdict,
    RelationType,
)
from agentclaw.community.core.task.repository.models import (
    TaskArtifactModel,
    TaskCallbackCorrelationModel,
    TaskCallbackModel,
    TaskNodeRelationModel,
)
from agentclaw.community.core.task.repository.types import TaskArtifactRecord
from agentclaw.community.core.task.task_center.relay_sampling import (
    build_relay_sample_patch,
)
from agentclaw.community.core.task.task_discovery.lock_models import TaskDiscoveryLockModel
from agentclaw.community.core.task.task_dispatch.rationale import _identity_of


def test_json_extract_retries_after_invalid_balanced_fence_and_scans_nested_object():
    content = '```json\nprose {"bad": }\n```\n```json\n{"ok": true}\n```'
    assert extract_json(content) == {"ok": True}
    assert _balanced_substring('prefix {"nested": {"value": 1}} suffix') == (
        '{"nested": {"value": 1}}'
    )


def test_acceptance_verdict_legacy_fail_and_invalid_value():
    assert AcceptanceVerdict("PASS") is AcceptanceVerdict.DONE
    assert AcceptanceVerdict("FAIL") is AcceptanceVerdict.FAILED
    with pytest.raises(ValueError):
        AcceptanceVerdict("UNKNOWN")


def test_repository_models_project_to_records():
    now = datetime(2026, 10, 8)
    relation = TaskNodeRelationModel(
        id=1,
        task_id="t1",
        src_node_id="n1",
        dst_node_id="n2",
        relation_type="DEPENDENCY",
        extend_props='{"reason": "next"}',
        gmt_create=now,
        gmt_modified=now,
    ).to_record()
    assert relation.relation_type is RelationType.DEPENDENCY
    assert relation.extend_props == {"reason": "next"}

    callback = TaskCallbackModel(
        id=2,
        invoker="bot",
        run_id="run",
        node_id="node",
        main_session_id="session",
        status="DONE",
        orig_callback_data="raw",
        execution_graph='{"run_id": 1}',
        result='{"ok": true}',
        result_success=True,
        exec_error=None,
        extend_props='{"trace": 1}',
        event_id="event",
        process_status="processed",
        processed_at=now,
        gmt_create=now,
        gmt_modified=now,
    ).to_record()
    assert callback.execution_graph == {"run_id": 1}
    assert callback.result == {"ok": True}
    assert callback.extend_props == {"trace": 1}

    correlation = TaskCallbackCorrelationModel(
        id=3,
        event_id="event",
        main_session_id="session",
        task_id="task",
        node_id="node",
        retry=2,
        gmt_create=now,
    ).to_record()
    assert correlation.retry == 2

    artifact = TaskArtifactModel(
        id=4,
        artifact_id="artifact",
        task_id="task",
        node_id="node",
        attempt=1,
        artifact_kind="document",
        content_kind="text",
        content='{"text": "hello"}',
        content_hash="sha256:" + "a" * 64,
        supersedes=None,
        derived_from='["source"]',
        created_by="bot",
        created_at=1,
        gmt_create=now,
        gmt_modified=now,
    ).to_record()
    assert artifact.content == {"text": "hello"}
    assert artifact.derived_from == ["source"]


def test_artifact_record_rejects_unknown_artifact_kind():
    record = TaskArtifactRecord(
        id=1,
        artifact_id="a1",
        task_id="t1",
        node_id="n1",
        attempt=0,
        artifact_kind="unknown",
        content_kind="text",
        content={"kind": "text", "text": "x"},
        content_hash="sha256:" + "0" * 64,
    )
    with pytest.raises(TaskArtifactContentError, match="unknown artifact kind"):
        record.to_artifact()


def test_discovery_lock_model_projects_to_record():
    now = datetime(2026, 10, 8)
    record = TaskDiscoveryLockModel(
        id=1,
        env="test",
        bot_id="bot",
        discovery_date="2026-10-08",
        holder="worker",
        lock_token="token",
        gmt_create=now,
        gmt_modified=now,
    ).to_record()
    assert record.bot_id == "bot"
    assert record.lock_token == "token"


def test_rationale_identity_rejects_non_mapping_candidate():
    assert _identity_of("not-a-dict") == ""


def test_relay_sample_patch_rejects_missing_or_too_short_sample_lists():
    common = {
        "task_id": "t1",
        "node_id": "n1",
        "sample_count": 3,
        "progress_reason": "sample",
        "failure_reason": None,
    }
    with pytest.raises(TaskStateError, match="requires sample_bot_ids"):
        build_relay_sample_patch(payload={}, **common)
    with pytest.raises(TaskStateError, match="at least two"):
        build_relay_sample_patch(payload={"sample_bot_ids": ["bot-a"]}, **common)


def test_json_extract_skips_unbalanced_fence_before_valid_fence():
    assert extract_json('```json\n{"open": 1\n```\n```json\n{"ok": 2}\n```') == {
        "ok": 2
    }


def test_artifact_service_error_and_reference_helper_branches():

    from agentclaw.community.core.task.task_context.task_artifact.artifact_service import (
        TaskArtifactService,
    )
    from agentclaw.community.core.task.task_context.task_artifact.models import ArtifactKind

    class _Repo:
        def list_by_task(self, task_id):
            return [
                SimpleNamespace(node_id="n1"),
                SimpleNamespace(node_id="n1"),
            ]

        def latest_for_node(self, task_id, node_id):
            return None

        def list_by_node(self, task_id, node_id, *, attempt=None):
            raise RuntimeError("artifact repository unavailable")

    class _Resources:
        def get_by_resource_id(self, resource_id):
            raise RuntimeError("resource repository unavailable")

    service = TaskArtifactService(repo=_Repo(), session_resource_repo=_Resources())
    assert service.primary_artifact_ids_by_node("t1") == {}
    assert service._collect_file_refs(
        {
            "resource_id": "sr_direct",
            "first": {"resource_id": "sr_same"},
            "second": {"resource_id": "sr_same"},
            "ignored": "sr_not_top_level_resource_id_key",
        }
    ) == [
        {"resource_id": "sr_direct"},
        {"resource_id": "sr_same"},
    ]
    assert service._resolve_file_meta(
        {"resource_id": "sr_error"}, "t1", "n1"
    ) is None
    assert service._previous_for(
        "t1", "n1", 0, ArtifactKind.NODE_RESULT
    ) is None

    class _NoMatchRepo(_Repo):
        def list_by_node(self, task_id, node_id, *, attempt=None):
            return [SimpleNamespace(artifact_kind="graph_rollup")]

    assert TaskArtifactService(repo=_NoMatchRepo())._previous_for(
        "t1", "n1", 0, ArtifactKind.NODE_RESULT
    ) is None


def test_remaining_domain_dispatch_and_scheduler_branches():
    from unittest.mock import MagicMock

    from agentclaw.community.core.task.task_discovery.scheduler import (
        TaskDiscoveryScheduler,
    )
    from agentclaw.community.core.task.task_dispatch.search import TaskSearch
    from agentclaw.community.core.task.task_dispatch.strategies import (
        _multi_sample_result,
    )

    with pytest.raises(ValueError):
        AcceptanceVerdict(1)

    scheduler = TaskDiscoveryScheduler(discovery_service=MagicMock())
    import asyncio

    asyncio.run(scheduler.shutdown())

    service = TaskSearch.__new__(TaskSearch)
    assert service._project_candidate({"bot_id": "b1", "recommend": "bad"}) == {
        "bot_uuid": "b1"
    }
    assert _multi_sample_result([], 2).miss_reason == "no_candidates"
    assert _multi_sample_result([{"not_an_identity": True}], 2).miss_reason == (
        "no_candidates"
    )


def test_artifact_service_without_resource_repository_rejects_file():
    from agentclaw.community.core.task.task_context.task_artifact.artifact_service import (
        TaskArtifactService,
    )

    service = TaskArtifactService(repo=object())
    assert service._resolve_file_meta({"resource_id": "sr_missing"}, "t1", "n1") is None


def test_graph_support_remaining_guard_and_error_branches():

    from agentclaw.community.core.task.task_context.task_graph_support import (
        _attach_done_output_artifacts,
        _relay_output_value,
        list_bbs_tasks_overview,
    )

    assert _relay_output_value({"other": "value"}) == {"other": "value"}
    assert list_bbs_tasks_overview(SimpleNamespace(_graph_repo=None), 1, 10) == ([], 0)

    class _BadArtifact:
        scope = SimpleNamespace(node_id="n1")

        def to_dict(self):
            raise RuntimeError("broken manifest")

    output = SimpleNamespace(node_id="n1", artifacts=[])
    owner = SimpleNamespace(
        _artifact_service=SimpleNamespace(
            list_artifacts_for_task=lambda _task_id: [_BadArtifact()]
        )
    )
    _attach_done_output_artifacts(owner, "t1", [output])
    assert output.artifacts == []

    owner._artifact_service = SimpleNamespace(
        list_artifacts_for_task=lambda _task_id: (_ for _ in ()).throw(
            RuntimeError("artifact read failed")
        )
    )
    _attach_done_output_artifacts(owner, "t1", [output])
    assert output.artifacts == []
