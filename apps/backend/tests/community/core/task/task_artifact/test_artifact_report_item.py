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

    def test_final_artifact_string_flag(self):
        item = parse_artifact_report_item(
            _item(
                file_content=json.dumps(
                    {
                        "storage_type": "bot_workspace",
                        "storage_id": "reports/analysis-report.md",
                        "file_type": "text/markdown",
                        "file_name": "analysis-report.md",
                        "file_id": None,
                        "is_final_artifact": "true",
                    }
                )
            ),
            report_task_id="t-a1b2",
            report_node_id="relay-ab12cd34",
        )
        assert item.content.is_final_artifact == "true"

    def test_final_artifact_missing_defaults_to_false(self):
        item = parse_artifact_report_item(
            _item(), report_task_id="t-a1b2", report_node_id="relay-ab12cd34"
        )
        assert item.content.is_final_artifact == "false"

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

    def test_final_artifact_non_contract_string_rejected(self):
        with pytest.raises(ValueError):
            parse_artifact_report_item(
                _item(
                    file_content={
                        "storage_type": "bot_workspace",
                        "storage_id": "x",
                        "file_type": "t",
                        "file_name": "x.md",
                        "file_id": None,
                        "is_final_artifact": True,
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


def _terminal_plan_dto(
    *,
    artifacts=None,
    failure_reason=None,
    final_conclusion="业务整体结论：已完成排查并给出建议",
) -> TaskReportCommandDTO:
    return TaskReportCommandDTO(
        task_id="t-a1b2",
        node_id="relay-ab12cd34",
        event_id="plan-event-1",
        relay_token="token-1",
        event_type=TaskReportEventType.PLAN_RESULT,
        event=None,
        task_acceptance_result=(
            None
            if failure_reason is not None
            else {"verdict": "DONE", "done_items": [], "gap_items": []}
        ),
        failure_reason=failure_reason,
        final_conclusion=final_conclusion,
        artifacts=artifacts,
    )


def _successor_plan_event() -> dict:
    return {
        "task_id": "t-a1b2",
        "node_id": None,
        "status": None,
        "task_spec": {
            "context": {"title": "下一棒"},
            "goal": {"objective": "执行剩余部分"},
        },
        "run_info": {"run_mode": "single_bot", "assignee": "bot-2"},
        "node_run_graph": None,
    }


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
        text_item = _wire_item(
            artifact_kind="text",
            file_content=None,
            text_content={"text": "分析完成"},
        )
        command = _command_dto(
            self._accepted_event(), artifacts=[text_item]
        ).to_domain()
        patch = command.event
        assert patch.artifact_patch is not None
        assert len(patch.artifact_patch) == 1
        assert patch.artifact_patch[0].artifact_kind == "text"

    def test_relay_callback_rejects_file_artifact(self):
        """文件产物必须走 object-file 上传登记,不得在回调 artifacts 二次上报。"""

        with pytest.raises(ValueError, match="object-file"):
            _command_dto(
                self._accepted_event(), artifacts=[_wire_item()]
            ).to_domain()

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

    def test_terminal_plan_result_artifacts_land_in_command_patch(self):
        """终态 PLAN_RESULT(event=null) 的最终产物放命令级 artifact_patch。"""

        url_final = _wire_item(
            artifact_kind="url_link",
            file_content=None,
            text_content=None,
            url_link_content={
                "url": "https://example.com/final-report",
                "type": "最终报告",
                "is_final_artifact": "true",
            },
        )
        command = _terminal_plan_dto(
            artifacts=[url_final],
        ).to_domain()

        assert command.final_conclusion == "业务整体结论：已完成排查并给出建议"
        assert command.artifact_patch is not None
        assert len(command.artifact_patch) == 1
        assert command.artifact_patch[0].artifact_kind == "url_link"
        assert command.artifact_patch[0].content.is_final_artifact == "true"

    def test_terminal_plan_result_requires_final_conclusion(self):
        with pytest.raises(
            TaskStateError, match="terminal PLAN_RESULT requires final_conclusion"
        ):
            _terminal_plan_dto(final_conclusion=None).to_domain()

    def test_terminal_plan_result_rejects_text_as_final_conclusion_artifact(self):
        text_final = _wire_item(
            artifact_kind="text",
            file_content=None,
            text_content={
                "text": "系统已完成全部任务",
                "is_final_artifact": "true",
            },
        )

        with pytest.raises(TaskStateError, match="final conclusion must not be reported"):
            _terminal_plan_dto(artifacts=[text_final]).to_domain()

    def test_successor_plan_result_with_final_conclusion_rejected(self):
        with pytest.raises(TaskStateError, match="only allowed on terminal"):
            TaskReportCommandDTO(
                task_id="t-a1b2",
                node_id="relay-ab12cd34",
                event_id="plan-event-2",
                relay_token="token-1",
                event_type=TaskReportEventType.PLAN_RESULT,
                event=_successor_plan_event(),
                final_conclusion="业务整体结论",
            ).to_domain()

    def test_successor_plan_result_with_artifacts_rejected(self):
        """带后继计划的 PLAN_RESULT(event=下一棒)不得夹带产物。"""

        with pytest.raises(TaskStateError):
            TaskReportCommandDTO(
                task_id="t-a1b2",
                node_id="relay-ab12cd34",
                event_id="plan-event-2",
                relay_token="token-1",
                event_type=TaskReportEventType.PLAN_RESULT,
                event=_successor_plan_event(),
                artifacts=[_wire_item()],
            ).to_domain()

    def test_old_payload_stays_byte_compatible(self):
        """无 artifacts 字段的旧报文:validation 与 patch 形态不变(Review Focus #4)。"""

        command = _command_dto(self._accepted_event()).to_domain()
        patch = command.event
        assert patch.output_patch == {"output": "evidence"}
        assert patch.execution_decision == "ACCEPTED"

    def test_event_level_artifacts_are_ignored_not_promoted(self):
        """Unknown event keys are discarded; only top-level artifacts are consumed."""

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
        assert dto.to_domain().event.artifact_patch is None
