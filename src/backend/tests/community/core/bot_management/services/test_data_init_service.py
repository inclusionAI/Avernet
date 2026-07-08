"""Tests for core.bot_management.services.data_init_service."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from agentclaw.community.core.bot_management.services.data_init_service import DataInitService
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE


class TestShouldRunInit:
    """幂等检查测试。"""

    def setup_method(self):
        self.service = DataInitService(
            resource_repo=MagicMock(),
            device_service=MagicMock(),
            skill_set_factory=MagicMock(),
            skill_set_activator_factory=MagicMock(),
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
            skill_set_activator_factory=MagicMock(),
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
            skill_set_activator_factory=MagicMock(),
            device_plugin=MagicMock(),
            bot_service_provider=lambda: MagicMock(),
            skill_md_path="/test/SKILL.md",
            resolver=MagicMock(),
        )

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


class TestActivateAndSyncSkillSets:
    """_activate_and_sync_skill_sets 测试。

    覆盖修复:engine_type 必须按 bot 真实 active_engine 透传给
    SkillSetActivatorFactory.create,否则会被兜底成 openclaw,
    导致 claude_code 等 engine 的 bot 在 data_init 全量同步时
    被错按 openclaw 捞技能集 / 算软链路径(曾把 claude_code 已下的
    10 个软链覆盖成 openclaw 默认集的 4 个)。
    """

    def setup_method(self):
        self.bot_service = MagicMock()
        self.service = DataInitService(
            resource_repo=MagicMock(),
            device_service=MagicMock(),
            skill_set_factory=MagicMock(),
            skill_set_activator_factory=MagicMock(),
            device_plugin=MagicMock(),
            bot_service_provider=lambda: self.bot_service,
            skill_md_path="/test/SKILL.md",
            resolver=MagicMock(),
        )
        # activator mock: list_all 返回空,避免 for 循环进入(个别用例再覆写)
        self.activator = MagicMock()
        # activate_skill_set 在源码里是 await 调用,必须用 AsyncMock
        self.activator.activate_skill_set = AsyncMock()
        self.activator.skill_set_service.skill_set_repo.list_all.return_value = []
        self.service._skill_set_activator_factory.create.return_value = self.activator

    async def _run(self):
        await self.service._activate_and_sync_skill_sets(
            bot_id="bot1", owner_id="437240", entity_id="437240",
        )

    @pytest.mark.asyncio
    async def test_openclaw_bot_passes_openclaw_engine(self):
        """openclaw bot 应透传 engine_type=openclaw(零回归验证)。"""
        self.bot_service.get_bot.return_value = {"active_engine": "openclaw"}
        await self._run()
        kwargs = self.service._skill_set_activator_factory.create.call_args.kwargs
        assert kwargs["engine_type"] == "openclaw"
        assert kwargs["entity_id"] == "437240"
        assert kwargs["bot_id"] == "bot1"

    @pytest.mark.asyncio
    async def test_claude_code_bot_passes_claude_code_engine(self):
        """claude_code bot 应透传 engine_type=claude_code(修复验证)。"""
        self.bot_service.get_bot.return_value = {"active_engine": "claude_code"}
        await self._run()
        kwargs = self.service._skill_set_activator_factory.create.call_args.kwargs
        assert kwargs["engine_type"] == "claude_code"

    @pytest.mark.asyncio
    async def test_missing_active_engine_falls_back_to_default(self):
        """active_engine 缺字段时降级 DEFAULT_ENGINE_TYPE。"""
        self.bot_service.get_bot.return_value = {}
        await self._run()
        kwargs = self.service._skill_set_activator_factory.create.call_args.kwargs
        assert kwargs["engine_type"] == DEFAULT_ENGINE_TYPE

    @pytest.mark.asyncio
    async def test_none_active_engine_falls_back_to_default(self):
        """active_engine 显式为 None 时降级 DEFAULT_ENGINE_TYPE。"""
        self.bot_service.get_bot.return_value = {"active_engine": None}
        await self._run()
        kwargs = self.service._skill_set_activator_factory.create.call_args.kwargs
        assert kwargs["engine_type"] == DEFAULT_ENGINE_TYPE

    @pytest.mark.asyncio
    async def test_get_bot_failure_falls_back_and_continues(self):
        """get_bot 抛异常时降级 DEFAULT_ENGINE_TYPE 且流程继续(不外传,
        不整段跳过 skillset 激活与 device sync)。"""
        self.bot_service.get_bot.side_effect = RuntimeError("bot not found")
        await self._run()  # 不应抛出
        kwargs = self.service._skill_set_activator_factory.create.call_args.kwargs
        assert kwargs["engine_type"] == DEFAULT_ENGINE_TYPE
        # 流程继续:仍调了 list_all 与 _do_device_sync
        self.activator.skill_set_service.skill_set_repo.list_all.assert_called_once()
        self.activator._do_device_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_zero_skill_sets_still_runs_device_sync(self):
        """0 个 skill_set 时,for 循环不进入但 _do_device_sync 仍调用一次。"""
        self.bot_service.get_bot.return_value = {"active_engine": "openclaw"}
        self.activator.skill_set_service.skill_set_repo.list_all.return_value = []
        await self._run()
        self.activator.activate_skill_set.assert_not_called()
        self.activator._do_device_sync.assert_called_once_with(
            user_id="437240", caller="DataInitService"
        )

    @pytest.mark.asyncio
    async def test_n_skill_sets_all_activated(self):
        """N 个 skill_set 时,每个都 await activate_skill_set 一次。"""
        self.bot_service.get_bot.return_value = {"active_engine": "claude_code"}
        self.activator.skill_set_service.skill_set_repo.list_all.return_value = [
            {"id": 101}, {"id": 102}, {"id": 103},
        ]
        await self._run()
        assert self.activator.activate_skill_set.await_count == 3
        called_ids = [
            call.args[0] for call in self.activator.activate_skill_set.await_args_list
        ]
        assert called_ids == ["101", "102", "103"]
        self.activator._do_device_sync.assert_called_once()
