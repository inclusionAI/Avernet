"""发布 capture 服务:文本双轨派生、patch 逐条、attempt 交叉校验、contextvar 暂存(spec §5.1)。"""
from __future__ import annotations

import json

import pytest

from agentclaw.community.core.task.domain.errors import TaskArtifactContentError
from agentclaw.community.core.task.domain.models import (
    Context,
    Goal,
    Relation,
    RelationType,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.repository.types import TaskArtifactRecord
from agentclaw.community.core.task.task_context.task_artifact.artifact_publish import (
    TaskArtifactPublishService,
    drain_published,
    stage_published,
)


def _node(node_id="n-1", *, task_id="t-1", assignee="bot-9", output=None, harness_retries=None):
    extend_props = {}
    if harness_retries is not None:
        extend_props["harness_retries"] = harness_retries
    return TaskNode(
        node_id=node_id,
        task_id=task_id,
        status=Status.RUNNING,
        task_spec=TaskSpec(
            context=Context(title="title", background="background"),
            goal=Goal(objective="objective", acceptances=[]),
        ),
        run_info=RuntimeInfo(assignee=assignee, output=output or {}, extend_props=extend_props),
        node_run_graph=None,
    )


def _graph(root_session_id="sess-1"):
    root = _node("t-1", assignee="bot-root")
    if root_session_id is not None:
        root.run_info.extend_props["session_id"] = root_session_id
    child = _node("n-1")
    graph = TaskExecutionGraph(
        run_id=7,
        loop_round=1,
        status=Status.RUNNING,
        output={},
        extend_props={},
        tasks=[root, child],
        task_id="t-1",
    )
    graph.relations.append(Relation(src_id="t-1", dst_id="n-1", type=RelationType.DEPENDENCY))
    root.node_run_graph = graph
    child.node_run_graph = graph
    return graph


def _file_item(
    storage_type="bot_workspace",
    *,
    file_id=None,
    file_name="analysis-report.md",
    attempt=0,
    artifact_name=None,
    session_id="sess-9",
):
    from agentclaw.community.core.task.task_context.task_artifact import FileContent, StorageType
    from agentclaw.community.core.task.task_context.task_artifact.report_item import (
        ArtifactReportItem,
    )

    return ArtifactReportItem(
        artifact_name=artifact_name,
        artifact_kind="file",
        session_id=session_id,
        task_id="t-1",
        node_id="n-1",
        attempt=attempt,
        content=FileContent(
            storage_type=StorageType(storage_type),
            storage_id="reports/analysis-report.md" if storage_type == "bot_workspace" else "sess-9",
            file_type="text/markdown",
            file_name=file_name,
            file_id=file_id,
        ),
    )


def _item(kind, content, *, attempt=0, artifact_name=None, session_id="sess-9"):
    from agentclaw.community.core.task.task_context.task_artifact.report_item import (
        ArtifactReportItem,
    )

    return ArtifactReportItem(
        artifact_name=artifact_name,
        artifact_kind=kind,
        session_id=session_id,
        task_id="t-1",
        node_id="n-1",
        attempt=attempt,
        content=content,
    )


def _service():
    return TaskArtifactPublishService()


@pytest.fixture(autouse=True)
def _clean_contextvar():
    drain_published()
    yield
    drain_published()


class TestTextDualTrack:
    def test_single_key_output_makes_one_text_record(self):
        graph = _graph()
        node = graph.tasks[1]
        node.run_info.output = {"output": "done"}
        records = _service().capture_node(graph, node, artifact_patch=None, now_ms=1000)
        assert len(records) == 1
        assert records[0].kind == "text"
        assert records[0].content.text == "done"
        assert records[0].storage == "inline"
        assert records[0].scope_session_id == "sess-1"
        assert records[0].attempt == 0
        assert records[0].creator == "bot-9"
        assert records[0].created_at == 1000

    def test_control_signal_dict_makes_one_text_record(self):
        graph = _graph()
        node = graph.tasks[1]
        node.run_info.output = {"skipped": True, "reason": "no-capability"}
        records = _service().capture_node(graph, node, artifact_patch=None, now_ms=1000)
        assert len(records) == 1
        assert records[0].kind == "text"
        assert json.loads(records[0].content.text) == {"skipped": True, "reason": "no-capability"}
        # canonical:键序确定
        assert records[0].content.text == json.dumps(
            {"reason": "no-capability", "skipped": True},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def test_business_dict_output_records_nothing(self):
        graph = _graph()
        node = graph.tasks[1]
        node.run_info.output = {"report": {"rows": 3}}
        assert _service().capture_node(graph, node, artifact_patch=None, now_ms=1000) == []

    def test_patch_text_plus_derived_signal_same_attempt_two_records(self):
        """同一 attempt:显式上报 text 产物 + output 控制信号派生 → 2 条。"""

        graph = _graph()
        node = graph.tasks[1]
        node.run_info.output = {"notify_result": {"message": "milestone"}}
        from agentclaw.community.core.task.task_context.task_artifact import TextContent

        patch = [
            _item("text", TextContent(text="显式上报的文本产物")),
        ]
        records = _service().capture_node(graph, node, artifact_patch=patch, now_ms=1000)
        assert len(records) == 2
        kinds = sorted(r.kind for r in records)
        assert kinds == ["text", "text"]
        texts = {r.content.text for r in records}
        assert "显式上报的文本产物" in texts
        assert any(json.loads(t).get("notify_result") for t in texts if t.startswith("{"))


class TestPatchItems:
    def test_four_patch_items_produce_four_records(self):
        graph = _graph()
        node = graph.tasks[1]
        node.run_info.extend_props["harness_retries"] = 2
        from agentclaw.community.core.task.task_context.task_artifact import (
            CodeContent,
            UrlLinkContent,
        )

        patch = [
            _file_item("bot_workspace", attempt=2),
            _file_item("session_file", file_id="f-987", file_name="报告.pdf", attempt=2),
            _item(
                "url_link",
                UrlLinkContent(url="https://example.com/doc", type="dima需求"),
                attempt=2,
                artifact_name="需求条目",
            ),
            _item(
                "code",
                CodeContent(
                    code_base_name="avernet",
                    code_base_address="https://antcode.example.com/avernet",
                    pr_link="https://antcode.example.com/pr/1",
                    pr_desc="",
                ),
                attempt=2,
            ),
        ]
        records = _service().capture_node(graph, node, artifact_patch=patch, now_ms=2000)
        assert len(records) == 4
        by_kind = {}
        for record in records:
            assert isinstance(record, TaskArtifactRecord)
            assert record.attempt == 2
            assert record.task_id == "t-1"
            assert record.node_id == "n-1"
            assert record.content_hash
            by_kind.setdefault(record.kind, []).append(record)
        file_records = by_kind["file"]
        # storage 与 storage_type 强一致
        assert {r.storage for r in file_records} == {"bot_workspace", "session_file"}
        # artifact_name 回落:file → file_name(file 未上报业务名)
        assert {r.artifact_name for r in file_records} == {"analysis-report.md", "报告.pdf"}
        # url_link 保留上报业务名;code 无回落来源
        assert by_kind["url_link"][0].artifact_name == "需求条目"
        assert by_kind["code"][0].artifact_name is None
        # session_id 图绑定优先
        assert all(r.scope_session_id == "sess-1" for r in records)

    def test_attempt_mismatch_raises(self):
        """报文 attempt 与图上 harness_retries 快照失配 → 拒绝(Review Focus #3)。"""

        graph = _graph()
        node = graph.tasks[1]
        node.run_info.extend_props["harness_retries"] = 1
        with pytest.raises(TaskArtifactContentError):
            _service().capture_node(
                graph, node, artifact_patch=[_file_item(attempt=0)], now_ms=1000
            )

    def test_missing_assignee_creator_is_system(self):
        graph = _graph()
        node = graph.tasks[1]
        node.run_info.assignee = None
        node.run_info.output = {"output": "done"}
        records = _service().capture_node(graph, node, artifact_patch=None, now_ms=1000)
        assert records[0].creator == "system"


class TestBedlessSession:
    def test_unbound_graph_adopts_reported_session(self):
        graph = _graph(root_session_id=None)
        node = graph.tasks[1]
        node.run_info.output = {"output": "done"}
        patch = [
            _file_item("session_file", file_id="f-987", session_id="sess-reported"),
        ]
        records = _service().capture_node(graph, node, artifact_patch=patch, now_ms=1000)
        assert records[1].scope_session_id == "sess-reported"

    def test_unbound_graph_without_reported_session_is_none(self):
        graph = _graph(root_session_id=None)
        node = graph.tasks[1]
        node.run_info.output = {"output": "done"}
        records = _service().capture_node(graph, node, artifact_patch=None, now_ms=1000)
        assert records[0].scope_session_id is None


class TestContextvarStaging:
    def test_capture_stages_records(self):
        graph = _graph()
        node = graph.tasks[1]
        node.run_info.output = {"output": "done"}
        records = _service().capture_node(graph, node, artifact_patch=None, now_ms=1000)
        drained = drain_published()
        assert drained == records

    def test_stage_and_drain_is_idempotent(self):
        from agentclaw.community.core.task.task_context.task_artifact import TextContent

        record = TaskArtifactRecord(
            artifact_id="task-art-0000000000000001",
            artifact_name=None,
            task_id="t-1",
            node_id="n-1",
            attempt=0,
            scope_session_id=None,
            kind="text",
            storage="inline",
            content=TextContent(text="x"),
            content_hash="x" * 64,
            creator="system",
            modifier=None,
            created_at=1,
            gmt_create=None,
        )
        stage_published([record])
        first = drain_published()
        assert first == [record]
        # drain 取走并清空:再 drain 为空
        assert drain_published() == []
        # 重新 stage 仍可取到
        stage_published([record])
        assert drain_published() == [record]

    def test_record_identity_per_capture(self):
        graph = _graph()
        node = graph.tasks[1]
        node.run_info.output = {"output": "done"}
        first = _service().capture_node(graph, node, artifact_patch=None, now_ms=1000)
        second = _service().capture_node(graph, node, artifact_patch=None, now_ms=1000)
        assert first[0].artifact_id != second[0].artifact_id