"""Tests for core.bot_management.services.data_init_service."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from agentclaw.community.core.bot_management.services.data_init_service import DataInitService


class TestShouldRunInit:
    """幂等检查测试。"""

    def setup_method(self):
        self.service = DataInitService(
            resource_repo=MagicMock(),
            device_service=MagicMock(),
            skill_set_factory=MagicMock(),
            device_plugin=MagicMock(),
            bot_service_provider=lambda: MagicMock(),
            skill_md_path="/test/SKILL.md",
            resolver=MagicMock(),
        )
        self.mock_bot_service = MagicMock()

    def test_in_progress_without_timestamp_skips(self):
        """in_progress 但没有 started_at 时间戳，保守跳过（避免并发）。"""
        self.mock_bot_service.get_bot.return_value = {
            "ext": json.dumps({"data_init_status": "in_progress"})
        }
        assert self.service._should_run_init("bot1", "437240", self.mock_bot_service) is False

    def test_in_progress_recent_skips(self):
        """in_progress 且 started_at 在超时阈值内，跳过。"""
        from datetime import datetime, timezone
        recent_time = datetime.now(timezone.utc).isoformat()
        self.mock_bot_service.get_bot.return_value = {
            "ext": json.dumps({
                "data_init_status": "in_progress",
                "data_init_started_at": recent_time,
            })
        }
        assert self.service._should_run_init("bot1", "437240", self.mock_bot_service) is False

    def test_in_progress_timeout_allows_rerun(self):
        """in_progress 且 started_at 超过超时阈值，允许重新执行。"""
        from datetime import datetime, timezone, timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        self.mock_bot_service.get_bot.return_value = {
            "ext": json.dumps({
                "data_init_status": "in_progress",
                "data_init_started_at": old_time,
            })
        }
        assert self.service._should_run_init("bot1", "437240", self.mock_bot_service) is True

    def test_completed_without_force_skips(self):
        self.mock_bot_service.get_bot.return_value = {
            "ext": json.dumps({"data_init_status": "completed"})
        }
        assert self.service._should_run_init("bot1", "437240", self.mock_bot_service, force=False) is False

    def test_completed_with_force_runs(self):
        self.mock_bot_service.get_bot.return_value = {
            "ext": json.dumps({"data_init_status": "completed"})
        }
        assert self.service._should_run_init("bot1", "437240", self.mock_bot_service, force=True) is True

    def test_pending_init_runs(self):
        self.mock_bot_service.get_bot.return_value = {
            "ext": json.dumps({"data_init_status": "pending_init"})
        }
        assert self.service._should_run_init("bot1", "437240", self.mock_bot_service) is True

    def test_null_status_runs(self):
        self.mock_bot_service.get_bot.return_value = {"ext": {}}
        assert self.service._should_run_init("bot1", "437240", self.mock_bot_service) is True

    def test_ext_as_string_parsed(self):
        """ext 为 JSON 字符串时能正确解析。"""
        from datetime import datetime, timezone
        recent_time = datetime.now(timezone.utc).isoformat()
        self.mock_bot_service.get_bot.return_value = {
            "ext": json.dumps({
                "data_init_status": "in_progress",
                "data_init_started_at": recent_time,
            })
        }
        assert self.service._should_run_init("bot1", "437240", self.mock_bot_service) is False

    def test_ext_invalid_json_treated_as_empty(self):
        self.mock_bot_service.get_bot.return_value = {"ext": "not-json"}
        assert self.service._should_run_init("bot1", "437240", self.mock_bot_service) is True

    def test_bot_not_found_returns_false(self):
        self.mock_bot_service.get_bot.side_effect = Exception("Bot not found")
        assert self.service._should_run_init("bot1", "437240", self.mock_bot_service) is False


class TestGetStatus:
    def setup_method(self):
        self.bot_service = MagicMock()
        self.service = DataInitService(
            resource_repo=MagicMock(),
            device_service=MagicMock(),
            skill_set_factory=MagicMock(),
            device_plugin=MagicMock(),
            bot_service_provider=lambda: self.bot_service,
            skill_md_path="/test/SKILL.md",
            resolver=MagicMock(),
        )

    def test_absent_status_is_not_started(self):
        self.bot_service.get_bot.return_value = {"ext": {"iam_token": "secret"}}

        assert self.service.get_status("bot1", "u1") == {
            "bot_id": "bot1",
            "status": "not_started",
            "started_at": None,
        }

    def test_reads_only_public_status_fields_from_json_ext(self):
        self.bot_service.get_bot.return_value = {
            "ext": json.dumps({
                "data_init_status": "in_progress",
                "data_init_started_at": "2026-08-18T08:00:00+00:00",
                "iam_token": "must-not-leak",
                "downstream_sync": {"ecb": {"success": False}},
            })
        }

        assert self.service.get_status("bot1", "u1") == {
            "bot_id": "bot1",
            "status": "in_progress",
            "started_at": "2026-08-18T08:00:00+00:00",
        }

    def test_unknown_or_malformed_status_fails_closed_to_not_started(self):
        self.bot_service.get_bot.return_value = {
            "ext": json.dumps({"data_init_status": "internal-new-state"})
        }

        assert self.service.get_status("bot1", "u1")["status"] == "not_started"


class TestParseLlmResult:
    """LLM 结果解析测试。"""

    def test_valid_json_between_markers(self):
        llm_text = (
            "Some preamble\n"
            "DATA_INIT_RESULT_BEGIN\n"
            '{"summary": {"capability": "test cap", "role": "test role"}, '
            '"raw_data": {"yuque": {"success": true}}, '
            '"fetch_errors": []}\n'
            "DATA_INIT_RESULT_END\n"
            "Some epilogue"
        )
        result = DataInitService._parse_llm_result(llm_text)
        assert result["summary"]["capability"] == "test cap"
        assert result["summary"]["role"] == "test role"
        assert result["raw_data"]["yuque"]["success"] is True
        assert result["fetch_errors"] == []

    def test_missing_markers_returns_empty(self):
        result = DataInitService._parse_llm_result("No markers here")
        assert result["summary"] == {}
        assert result["raw_data"] == {}
        assert len(result["fetch_errors"]) == 1

    def test_invalid_json_between_markers(self):
        llm_text = "DATA_INIT_RESULT_BEGIN\n{invalid json}\nDATA_INIT_RESULT_END"
        result = DataInitService._parse_llm_result(llm_text)
        assert result["summary"] == {}
        assert "Invalid JSON" in result["fetch_errors"][0]

    def test_missing_summary_key_defaults(self):
        llm_text = 'DATA_INIT_RESULT_BEGIN\n{"raw_data": {}}\nDATA_INIT_RESULT_END'
        result = DataInitService._parse_llm_result(llm_text)
        assert result["summary"] == {}
        assert result["raw_data"] == {}
        assert result["fetch_errors"] == []

    def test_empty_text_returns_empty(self):
        result = DataInitService._parse_llm_result("")
        assert result["summary"] == {}


class TestExtractAssistantResponse:
    """assistant 回复提取测试。"""

    def test_extracts_completed_response(self):
        messages = [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "result DATA_INIT_RESULT_BEGIN {} DATA_INIT_RESULT_END"}
                ],
            },
        ]
        result = DataInitService._extract_assistant_response(messages)
        assert "DATA_INIT_RESULT_BEGIN" in result

    def test_ignores_incomplete_response(self):
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": "still working..."}]},
        ]
        result = DataInitService._extract_assistant_response(messages)
        assert result == ""

    def test_empty_messages_returns_empty(self):
        assert DataInitService._extract_assistant_response([]) == ""

    def test_string_content_supported(self):
        messages = [
            {"role": "assistant", "content": "DATA_INIT_RESULT_BEGIN {} DATA_INIT_RESULT_END"},
        ]
        result = DataInitService._extract_assistant_response(messages)
        assert "DATA_INIT_RESULT_BEGIN" in result


class TestCollectBotInfo:
    """Bot 信息收集测试。"""

    def setup_method(self):
        self.service = DataInitService(
            resource_repo=MagicMock(),
            device_service=MagicMock(),
            skill_set_factory=MagicMock(),
            device_plugin=MagicMock(),
            bot_service_provider=lambda: MagicMock(),
            skill_md_path="/test/SKILL.md",
            resolver=MagicMock(),
        )
        self.service._collect_link_resources = MagicMock(return_value=[])
        self.mock_bot_service = MagicMock()

    def test_clears_stored_iam_token_after_read(self):
        self.mock_bot_service.get_bot.return_value = {
            "binding_id": 123,
            "bot_name": "test bot",
            "bot_desc": "test desc",
            "ext": json.dumps({"iam_token": "secret-token"}),
        }

        bot_info = self.service._collect_bot_info(
            "bot1", "437240", self.mock_bot_service
        )

        assert bot_info["iam_token"] == "secret-token"
        self.mock_bot_service.update_bot_ext.assert_called_once_with(
            "bot1", "437240", {"iam_token": None}
        )

    def test_skips_clear_when_iam_token_absent(self):
        self.mock_bot_service.get_bot.return_value = {
            "binding_id": 123,
            "bot_name": "test bot",
            "bot_desc": "test desc",
            "ext": json.dumps({"data_init_status": "in_progress"}),
        }

        bot_info = self.service._collect_bot_info(
            "bot1", "437240", self.mock_bot_service
        )

        assert bot_info["iam_token"] == ""
        self.mock_bot_service.update_bot_ext.assert_not_called()


class TestTriggerInit:
    """trigger_init 集成测试（mock 外部依赖）。"""

    def setup_method(self):
        self.service = DataInitService(
            resource_repo=MagicMock(),
            device_service=MagicMock(),
            skill_set_factory=MagicMock(),
            device_plugin=MagicMock(),
            bot_service_provider=lambda: MagicMock(),
            skill_md_path="/test/SKILL.md",
            resolver=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_active_init_persists_iam_token_before_execution(self):
        bot_service = MagicMock()
        bot_service.get_bot.return_value = {"status": "ACTIVE", "ext": {}}
        self.service._bot_service_provider = lambda: bot_service
        self.service._should_run_init = MagicMock(return_value=True)
        self.service._async_execute_with_retry = AsyncMock(return_value=True)

        result = await self.service.trigger_init(
            bot_id="bot1", owner_id="u1", entity_id="u1", entity_type="staff",
            iam_token="iam-secret",
        )

        assert result["status"] == "completed"
        first_update = bot_service.update_bot_ext.call_args_list[0].args
        assert first_update[0:2] == ("bot1", "u1")
        assert first_update[2]["data_init_status"] == "in_progress"
        assert first_update[2]["iam_token"] == "iam-secret"

    @pytest.mark.asyncio
    async def test_second_active_idempotency_check_does_not_leave_iam_token(self):
        bot_service = MagicMock()
        bot_service.get_bot.return_value = {
            "status": "ACTIVE",
            "ext": {"data_init_status": "in_progress"},
        }
        self.service._bot_service_provider = lambda: bot_service
        self.service._should_run_init = MagicMock(return_value=True)

        result = await self.service.trigger_init(
            bot_id="bot1", owner_id="u1", entity_id="u1", entity_type="staff",
            iam_token="iam-secret",
        )

        assert result["status"] == "skipped"
        bot_service.update_bot_ext.assert_not_called()

    @pytest.mark.asyncio
    async def test_pending_init_persists_status_and_iam_token_together(self):
        bot_service = MagicMock()
        bot_service.get_bot.return_value = {"status": "PENDING", "ext": {}}
        self.service._bot_service_provider = lambda: bot_service
        self.service._should_run_init = MagicMock(return_value=True)

        result = await self.service.trigger_init(
            bot_id="bot1", owner_id="u1", entity_id="u1", entity_type="staff",
            iam_token="iam-secret",
        )

        assert result["status"] == "pending_init"
        bot_service.update_bot_ext.assert_called_once_with(
            "bot1",
            "u1",
            {"data_init_status": "pending_init", "iam_token": "iam-secret"},
        )

    @pytest.mark.asyncio
    async def test_skipped_init_does_not_persist_iam_token(self):
        bot_service = MagicMock()
        self.service._bot_service_provider = lambda: bot_service
        self.service._should_run_init = MagicMock(return_value=False)

        result = await self.service.trigger_init(
            bot_id="bot1", owner_id="u1", entity_id="u1", entity_type="staff",
            iam_token="iam-secret",
        )

        assert result["status"] == "skipped"
        bot_service.update_bot_ext.assert_not_called()

    @pytest.mark.asyncio
    async def test_already_completed_skips(self):
        """已完成的 Bot 不会重复初始化（通过 _should_run_init mock 验证）。

        _should_run_init 返回 False 时，trigger_init 应直接返回 skipped，
        不会走到 bot_service.get_bot（避免 arca 依赖问题）。
        """
        self.service._should_run_init = MagicMock(return_value=False)
        result = await self.service.trigger_init(
            bot_id="bot1", owner_id="437240",
            entity_id="437240", entity_type="staff",
        )
        assert result["status"] == "skipped"
        self.service._should_run_init.assert_called_once()


