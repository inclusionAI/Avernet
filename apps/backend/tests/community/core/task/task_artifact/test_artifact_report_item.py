"""relay 回调上报产物解析:扁平自描述 item、互斥/一致性校验、上限 64(spec §4)。"""
from __future__ import annotations

import json

import pytest

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import TaskReportEventType
from agentclaw.community.core.task.task_context.task_artifact import (
    CodeContent,
    FileContent,
    StorageType,
    UrlLinkContent,
)
from agentclaw.community.core.task.task_context.task_artifact.report_item import (
    ArtifactReportItem,
    parse_artifact_report_item,
    parse_artifact_report_items,
)
from agentclaw.community.adapters.http.task.schemas_relay_report import (
    TaskReportCommandDTO,
)


def _item(**overrides) -> dict:
    raw = {
        "artifact_name": "数据分析报告",
        "artifact_kind": "file",
        "session_id": "sess-xxx",
        "task_id": "t-a1b2",
        "node_id": "relay-ab12cd34",
        "attempt": 1,
        "text_content": None,
        "file_content": json.dumps(
            {
                "storage_type": "bot_workspace",
                "storage_id": "reports/analysis-report.md",
                "file_type": "text/markdown",
                "file_name": "analysis-report.md",
                "file_id": None,
            }
        ),
        "url_link_content": None,
        "code_content": None,
    }
    raw.update(overrides)
    return raw


class TestValidParses:
    def test_file_bot_workspace(self):
        item = parse_artifact_report_item(
            _item(), report_task_id="t-a1b2", report_node_id="relay-ab12cd34"
        )
        assert isinstance(item, ArtifactReportItem)
        assert item.artifact_name == "数据分析报告"
        assert item.artifact_kind == "file"
        assert item.session_id == "sess-xxx"
        assert item.attempt == 1
        assert item.content == FileContent(
            storage_type=StorageType.BOT_WORKSPACE,
            storage_id="reports/analysis-report.md",
            file_type="text/markdown",
            file_name="analysis-report.md",
            file_id=None,
        )

    def test_file_session_file(self):
        item = parse_artifact_report_item(
            _item(
                file_content=json.dumps(
                    {
                        "storage_type": "session_file",
                        "storage_id": "sess-xxx",
                        "file_type": "application/pdf",
                        "file_name": "analysis-report.pdf",
                        "file_id": "f-987",
                    }
                )
            ),
            report_task_id="t-a1b2",
            report_node_id="relay-ab12cd34",
        )
        assert item.content.file_id == "f-987"
        assert item.content.storage_type is StorageType.SESSION_FILE

    def test_url_link(self):
        item = parse_artifact_report_item(
            _item(
                artifact_kind="url_link",
                file_content=None,
                url_link_content=json.dumps(
                    {"url": "https://example.com/doc", "type": "dima需求"}
                ),
            ),
            report_task_id="t-a1b2",
            report_node_id="relay-ab12cd34",
        )
        assert item.content == UrlLinkContent(url="https://example.com/doc", type="dima需求")

    def test_code(self):
        item = parse_artifact_report_item(
            _item(
                artifact_kind="code",
                file_content=None,
                code_content=json.dumps(
                    {
                        "code_base_name": "avernet",
                        "code_base_address": "https://antcode.example.com/avernet",
                        "pr_link": "https://antcode.example.com/pr/1",
                        "pr_desc": "fix: something",
                    }
                ),
            ),
            report_task_id="t-a1b2",
            report_node_id="relay-ab12cd34",
        )
        assert isinstance(item.content, CodeContent)
        assert item.content.pr_desc == "fix: something"

    def test_text_explicit(self):
        item = parse_artifact_report_item(
            _item(
                artifact_kind="text",
                file_content=None,
                text_content=json.dumps({"text": "全文交付文本"}),
            ),
            report_task_id="t-a1b2",
            report_node_id="relay-ab12cd34",
        )
        assert item.content.text == "全文交付文本"

    def test_content_object_form_parsed_identically(self):
        """对象形态(2026-10-22 线上契约):与字符串形态解析产物逐字段一致。"""
        object_item = parse_artifact_report_item(
            _item(
                file_content={
                    "storage_type": "bot_workspace",
                    "storage_id": "reports/analysis-report.md",
                    "file_type": "text/markdown",
                    "file_name": "analysis-report.md",
                    "file_id": None,
                }
            ),
            report_task_id="t-a1b2",
            report_node_id="relay-ab12cd34",
        )
        string_item = parse_artifact_report_item(
            _item(), report_task_id="t-a1b2", report_node_id="relay-ab12cd34"
        )
        assert object_item.content == string_item.content
        assert object_item.attempt == string_item.attempt

    def test_content_object_form_rejects_kind_tag(self):
        """对象形态同样禁 kind tag(判别由字段名承载)。"""

        with pytest.raises(ValueError):
            parse_artifact_report_item(
                _item(
                    file_content={
                        "kind": "file",
                        "storage_type": "bot_workspace",
                        "storage_id": "reports/analysis-report.md",
                        "file_type": "text/markdown",
                        "file_name": "analysis-report.md",
                        "file_id": None,
                    }
                ),
                report_task_id="t-a1b2",
                report_node_id="relay-ab12cd34",
            )

    def test_content_neither_object_nor_string_rejected(self):
        with pytest.raises(ValueError):
            parse_artifact_report_item(
                _item(file_content=42),
                report_task_id="t-a1b2",
                report_node_id="relay-ab12cd34",
            )


class TestRejections:
    @pytest.mark.parametrize("attempt", [-1, "1", 1.5, None, True])
    def test_invalid_attempt(self, attempt):
        with pytest.raises(ValueError):
            parse_artifact_report_item(
                _item(attempt=attempt),
                report_task_id="t-a1b2",
                report_node_id="relay-ab12cd34",
            )

    def test_task_id_mismatch(self):
        with pytest.raises(ValueError):
            parse_artifact_report_item(
                _item(task_id="t-OTHER"),
                report_task_id="t-a1b2",
                report_node_id="relay-ab12cd34",
            )

    def test_node_id_mismatch(self):
        with pytest.raises(ValueError):
            parse_artifact_report_item(
                _item(node_id="n-OTHER"),
                report_task_id="t-a1b2",
                report_node_id="relay-ab12cd34",
            )

    def test_two_contents_rejected(self):
        with pytest.raises(ValueError):
            parse_artifact_report_item(
                _item(text_content=json.dumps({"text": "x"})),
                report_task_id="t-a1b2",
                report_node_id="relay-ab12cd34",
            )

    def test_kind_field_mismatch(self):
        with pytest.raises(ValueError):
            parse_artifact_report_item(
                _item(artifact_kind="text"),
                report_task_id="t-a1b2",
                report_node_id="relay-ab12cd34",
            )

    def test_unknown_kind(self):
        with pytest.raises(ValueError):
            parse_artifact_report_item(
                _item(artifact_kind="bogus", file_content=None),
                report_task_id="t-a1b2",
                report_node_id="relay-ab12cd34",
            )

    def test_no_content_field(self):
        with pytest.raises(ValueError):
            parse_artifact_report_item(
                _item(file_content=None),
                report_task_id="t-a1b2",
                report_node_id="relay-ab12cd34",
            )

    def test_content_not_json(self):
        with pytest.raises(ValueError):
            parse_artifact_report_item(
                _item(file_content="not json{"),
                report_task_id="t-a1b2",
                report_node_id="relay-ab12cd34",
            )

    def test_branch_validation_applies(self):
        """content 反序列化走 §3.4 分支构造器:路径逃逸同样拒绝。"""

        with pytest.raises(ValueError):
            parse_artifact_report_item(
                _item(
                    file_content=json.dumps(
                        {
                            "storage_type": "bot_workspace",
                            "storage_id": "/etc/passwd",
                            "file_type": "text/plain",
                            "file_name": "passwd",
                            "file_id": None,
                        }
                    )
                ),
                report_task_id="t-a1b2",
                report_node_id="relay-ab12cd34",
            )


class TestListCap:
    def test_over_64_rejected_wholesale(self):
        items = [_item() for _ in range(65)]
        with pytest.raises(ValueError):
            parse_artifact_report_items(
                items, report_task_id="t-a1b2", report_node_id="relay-ab12cd34"
            )

    def test_exactly_64_accepted(self):
        items = [_item(session_id=None, artifact_name=None) for _ in range(64)]
        parsed = parse_artifact_report_items(
            items, report_task_id="t-a1b2", report_node_id="relay-ab12cd34"
        )
        assert len(parsed) == 64

    def test_empty_list_is_explicit_empty(self):
        assert (
            parse_artifact_report_items(
                [], report_task_id="t-a1b2", report_node_id="relay-ab12cd34"
            )
            == []
        )


def _command_dto(event: dict, artifacts=None) -> TaskReportCommandDTO:
    return TaskReportCommandDTO(
        task_id="t-a1b2",
        node_id="relay-ab12cd34",
        event_id="event-1",
        relay_token="token-1",
        event_type=TaskReportEventType.EXECUTION_RESULT,
        event=event,
        artifacts=artifacts,
    )


def _wire_item(**overrides) -> dict:
    """线上 item 形态:content 为 JSON **对象**(2026-10-22 用户定稿,非字符串)。"""
    raw = {
        "artifact_name": "数据分析报告",
        "artifact_kind": "file",
        "session_id": "sess-xxx",
        "task_id": "t-a1b2",
        "node_id": "relay-ab12cd34",
        "attempt": 1,
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
    raw.update(overrides)
    return raw


class TestRelayDtoContract:
    """报文顶层 artifacts 契约(2026-10-22 用户定稿:与 trace 平级,不在 event 内)。"""

    def _accepted_event(self) -> dict:
        return {
            "execution_decision": "ACCEPTED",
            "actual_goal": {"objective": "obj", "acceptances": []},
            "local_acceptance_result": {"verdict": "DONE", "done_items": [], "gap_items": []},
            "output_patch": {"output": "evidence"},
        }

    def test_accepted_report_with_artifacts_lands_in_patch(self):
        command = _command_dto(
            self._accepted_event(), artifacts=[_wire_item()]
        ).to_domain()
        patch = command.event
        assert patch.artifact_patch is not None
        assert len(patch.artifact_patch) == 1
        assert patch.artifact_patch[0].artifact_kind == "file"

    def test_declined_event_with_artifacts_rejected_wholesale(self):
        """DECLINED 夹带产物:整体拒绝,走 to_domain 既有的 TaskStateError 契约信封。"""

        event = self._accepted_event()
        event["execution_decision"] = "DECLINED"
        with pytest.raises(TaskStateError):
            _command_dto(event, artifacts=[_wire_item()]).to_domain()

    def test_report_without_artifacts_patch_is_none(self):
        command = _command_dto(self._accepted_event()).to_domain()
        assert command.event.artifact_patch is None

    def test_empty_artifacts_list_is_explicit_empty(self):
        command = _command_dto(self._accepted_event(), artifacts=[]).to_domain()
        assert command.event.artifact_patch == []

    def test_plan_result_event_with_artifacts_rejected(self):
        """非 EXECUTION_RESULT 事件(PLAN_RESULT)夹带顶层产物 → TaskStateError。"""

        dto = TaskReportCommandDTO(
            task_id="t-a1b2",
            node_id="relay-ab12cd34",
            event_id="event-1",
            relay_token="token-1",
            event_type=TaskReportEventType.PLAN_RESULT,
            artifacts=[_wire_item()],
        )
        with pytest.raises(TaskStateError):
            dto.to_domain()

    def test_old_payload_stays_byte_compatible(self):
        """无 artifacts 字段的旧报文:validation 与 patch 形态不变(Review Focus #4)。"""

        command = _command_dto(self._accepted_event()).to_domain()
        patch = command.event
        assert patch.output_patch == {"output": "evidence"}
        assert patch.execution_decision == "ACCEPTED"

    def test_event_level_artifacts_field_now_forbidden(self):
        """event 内携带 artifacts(extra=forbid)→ to_domain 校验拒绝:顶层是唯一合法位置。"""

        event = self._accepted_event()
        event["artifacts"] = [_wire_item()]
        dto = TaskReportCommandDTO(
            task_id="t-a1b2",
            node_id="relay-ab12cd34",
            event_id="event-1",
            relay_token="token-1",
            event_type=TaskReportEventType.EXECUTION_RESULT,
            event=event,
        )
        # event 在报文上是宽松 dict,ExecutionEventDTO(extra=forbid) 的拒绝
        # 发生在 to_domain 内(与 wire 路由的 4xx 信封一致)。
        with pytest.raises(Exception):  # noqa: B017
            dto.to_domain()
