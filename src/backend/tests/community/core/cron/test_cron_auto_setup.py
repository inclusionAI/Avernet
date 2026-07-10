"""Unit tests for cron_auto_setup service."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from agentclaw.community.core.cron.services.aicoding.cron_auto_setup import (
    CronAutoSetupService,
    _is_hosted_24x7,
    _get_trigger_frequency,
    _frequency_to_cron,
    _build_cron_name,
    _build_cron_command,
    _parse_cron_command,
    _replace_workflow_in_command,
    CronAutoSetupError,
    DEFAULT_CRON_SCHEDULE,
    DEFAULT_CRON_TIMEZONE,
    DEFAULT_CRON_TIMEOUT_SECS,
)
from agentclaw.community.core.bot_management.utils import extract_workflow_name


# ── Helper function tests ──────────────────────────────────────────────


class TestIsHosted24x7:
    """Tests for _is_hosted_24x7 helper."""

    def test_true_when_one(self):
        assert _is_hosted_24x7({"is_hosted_24x7": 1}) is True

    def test_false_when_zero(self):
        assert _is_hosted_24x7({"is_hosted_24x7": 0}) is False

    def test_false_when_missing(self):
        assert _is_hosted_24x7({}) is False

    def test_false_when_string(self):
        assert _is_hosted_24x7({"is_hosted_24x7": "1"}) is False

    def test_false_when_true_bool(self):
        # Only integer 1 is treated as true; bool True is not 1
        assert _is_hosted_24x7({"is_hosted_24x7": True}) is False

    def test_false_when_two(self):
        assert _is_hosted_24x7({"is_hosted_24x7": 2}) is False


class TestGetTriggerFrequency:
    """Tests for _get_trigger_frequency helper."""

    def test_returns_configured_frequency(self):
        assert _get_trigger_frequency({"trigger_frequency": "hourly"}) == "hourly"

    def test_defaults_to_daily(self):
        assert _get_trigger_frequency({}) == "daily"

    def test_defaults_when_missing(self):
        assert _get_trigger_frequency({"is_hosted_24x7": 1}) == "daily"


class TestFrequencyToCron:
    """Tests for _frequency_to_cron helper."""

    def test_daily(self):
        assert _frequency_to_cron("daily") == "0 10,18 * * *"

    def test_hourly(self):
        assert _frequency_to_cron("hourly") == "0 * * * *"

    def test_weekly(self):
        assert _frequency_to_cron("weekly") == "0 10,18 * * 1"

    def test_unknown_defaults_to_daily(self):
        assert _frequency_to_cron("custom") == "0 10,18 * * *"

    def test_empty_defaults_to_daily(self):
        assert _frequency_to_cron("") == "0 10,18 * * *"


class TestBuildCronName:
    """Tests for _build_cron_name helper."""

    def test_default_bot(self):
        assert _build_cron_name("MyBot", "bot_123") == "7*24小时自动生码_MyBot_bot_123"

    def test_numeric_bot_id(self):
        assert _build_cron_name("TestBot", "2026031601234567") == "7*24小时自动生码_TestBot_2026031601234567"

    def test_special_chars(self):
        assert _build_cron_name("My_Bot", "my_bot_456") == "7*24小时自动生码_My_Bot_my_bot_456"

    def test_empty_bot_name(self):
        assert _build_cron_name("", "bot_789") == "7*24小时自动生码__bot_789"


class TestBuildCronCommand:
    """Tests for _build_cron_command helper."""

    def test_with_dima_space_id_only(self):
        """仅 dima_space_id 时，构建基本命令。"""
        result = _build_cron_command("W26001118999")
        assert result.startswith("查询dima空间W26001118999的待开发需求，开启7*24小时自动研发|")
        assert "space:W26001118999" in result

    def test_with_all_params(self):
        """所有参数都提供时，构建完整命令。"""
        result = _build_cron_command(
            dima_space_id="W26001118999",
            user_id="user_dima_bot_1",
            agent_id="bot_personal_dima_1",
            message="开始编码",
        )
        assert result.startswith("查询dima空间W26001118999的待开发需求，开启7*24小时自动研发|")
        assert "space:W26001118999" in result
        assert "user:user_dima_bot_1" in result
        assert "agent:bot_personal_dima_1" in result
        assert "message:开始编码" in result

    def test_without_message(self):
        """不提供 message 时，命令中不包含 message 字段。"""
        result = _build_cron_command(
            dima_space_id="W26001118999",
            user_id="user_1",
            agent_id="agent_1",
        )
        assert "space:W26001118999" in result
        assert "user:user_1" in result
        assert "agent:agent_1" in result
        assert "message:" not in result

    def test_with_message(self):
        """提供 message 时，命令中包含 message 字段。"""
        result = _build_cron_command(
            dima_space_id="W123",
            user_id="user_1",
            agent_id="agent_1",
            message="你好，请开始编码",
        )
        assert "message:你好，请开始编码" in result

    def test_command_format_matches_design(self):
        """命令格式符合设计文档规范。"""
        result = _build_cron_command(
            dima_space_id="W26001118999",
            user_id="user_dima_bot_1",
            agent_id="bot_personal_dima_1",
            message="开始编码",
        )
        assert result == (
            "查询dima空间W26001118999的待开发需求，开启7*24小时自动研发"
            "|space:W26001118999"
            "|user:user_dima_bot_1|agent:bot_personal_dima_1"
            "|kind:autoInitiate"
            "|message:开始编码"
        )

    def test_empty_message_not_included(self):
        """空字符串的 message 不应出现在命令中。"""
        result = _build_cron_command(
            dima_space_id="W123",
            user_id="user_1",
            agent_id="agent_1",
            message="",
        )
        assert "message:" not in result

    def test_with_append_message(self):
        """提供 append_message 时，命令中包含 append_message 字段。"""
        result = _build_cron_command(
            dima_space_id="W123",
            user_id="user_1",
            agent_id="agent_1",
            append_message="请优先处理核心逻辑",
        )
        assert "append_message:请优先处理核心逻辑" in result

    def test_empty_append_message_not_included(self):
        """空字符串的 append_message 不应出现在命令中。"""
        result = _build_cron_command(
            dima_space_id="W123",
            user_id="user_1",
            agent_id="agent_1",
            append_message="",
        )
        assert "append_message:" not in result

    def test_command_format_with_append_message(self):
        """所有参数（含 append_message）时，命令格式正确。"""
        result = _build_cron_command(
            dima_space_id="W26001118999",
            user_id="user_dima_bot_1",
            agent_id="bot_personal_dima_1",
            message="开始编码",
            workflow="devflow",
            append_message="注意代码质量",
        )
        assert result == (
            "查询dima空间W26001118999的待开发需求，开启7*24小时自动研发"
            "|space:W26001118999"
            "|user:user_dima_bot_1|agent:bot_personal_dima_1"
            "|kind:autoInitiate"
            "|workflow:devflow"
            "|message:开始编码"
            "|append_message:注意代码质量"
        )


# ── CronAutoSetupService tests ─────────────────────────────────────────


class TestExtractWorkflowName:
    """Tests for extract_workflow_name utility."""

    def test_dict_with_name(self):
        assert extract_workflow_name({"devflow_workflow": {"name": "spec-to-pr"}}) == "spec-to-pr"

    def test_dict_without_name(self):
        assert extract_workflow_name({"devflow_workflow": {"path": "/some/path"}}) == ""

    def test_string_value(self):
        assert extract_workflow_name({"devflow_workflow": "my-flow"}) == "my-flow"

    def test_empty_string(self):
        assert extract_workflow_name({"devflow_workflow": ""}) == ""

    def test_missing_key(self):
        assert extract_workflow_name({"other_key": "val"}) == ""

    def test_empty_dict(self):
        assert extract_workflow_name({}) == ""

    def test_non_dict_input(self):
        assert extract_workflow_name("not-a-dict") == ""

    def test_none_value(self):
        assert extract_workflow_name({"devflow_workflow": None}) == ""


class TestCronAutoSetupService:
    """Tests for CronAutoSetupService class with constructor injection."""

    def _create_service(self, template_data=None, relay_methods=None):
        """Create a service with mocked dependencies."""
        mock_template_repo = MagicMock()
        mock_template_repo.get_by_bot_id.return_value = template_data

        mock_relay = AsyncMock()
        if relay_methods:
            for method, return_val in relay_methods.items():
                getattr(mock_relay, method).return_value = return_val
        else:
            mock_relay.list_all_crons.return_value = {"success": True, "data": []}
            mock_relay.forward_request.return_value = {"success": True, "data": {"id": "job_123"}}

        service = CronAutoSetupService(
            template_repository=mock_template_repo,
            cron_relay_service=mock_relay,
        )
        return service, mock_template_repo, mock_relay

    @pytest.mark.asyncio
    async def test_skip_when_no_template(self):
        """No template found → skip."""
        service, mock_repo, mock_relay = self._create_service(template_data=None)
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")
        assert result is None
        mock_relay.forward_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_ext_not_dict(self):
        """Template ext is not a dict → skip."""
        service, mock_repo, mock_relay = self._create_service(
            template_data={"ext": "not-a-dict"}
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")
        assert result is None
        mock_relay.forward_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_not_hosted_24x7(self):
        """is_hosted_24x7 != 1 → skip."""
        service, mock_repo, mock_relay = self._create_service(
            template_data={"ext": {"is_hosted_24x7": 0}}
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")
        assert result is None
        mock_relay.forward_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_hosted_24x7_missing(self):
        """is_hosted_24x7 key missing → skip."""
        service, mock_repo, mock_relay = self._create_service(
            template_data={"ext": {"trigger_frequency": "daily"}}
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")
        assert result is None
        mock_relay.forward_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_no_dima_space_id(self):
        """is_hosted_24x7 == 1 但无 dima_space_id → 跳过创建定时任务。"""
        service, mock_repo, mock_relay = self._create_service(
            template_data={"ext": {"is_hosted_24x7": 1, "trigger_frequency": "daily"}}
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")
        assert result is None
        mock_relay.forward_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_dima_space_id_empty(self):
        """is_hosted_24x7 == 1 但 dima_space_id 为空字符串 → 跳过创建定时任务。"""
        service, mock_repo, mock_relay = self._create_service(
            template_data={"ext": {"is_hosted_24x7": 1, "dima_space_id": ""}}
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")
        assert result is None
        mock_relay.forward_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_cron_when_hosted_24x7_with_dima_space_id(self):
        """is_hosted_24x7 == 1 且有 dima_space_id → 创建 cron 任务，command 使用新格式。"""
        dima_space_id = "W26001118999"
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {"is_hosted_24x7": 1, "trigger_frequency": "daily", "dima_space_id": dima_space_id}
            }
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

        assert result is not None
        mock_relay.forward_request.assert_called_once()
        call_kwargs = mock_relay.forward_request.call_args.kwargs
        assert call_kwargs["bot_id"] == "bot1"
        assert call_kwargs["user_id"] == "user1"
        assert call_kwargs["nick_name"] == "nick1"
        assert call_kwargs["method"] == "POST"
        assert call_kwargs["path"] == "/api/cron"
        body = call_kwargs["body"]
        assert body["name"] == "7*24小时自动生码_TestBot_bot1"
        assert body["schedule"] == "0 10,18 * * *"
        # 新格式命令：查询dima空间{space_id}的待开发需求，开启7*24小时自动研发|space:{space_id}|user:{user_id}|agent:{agent_id}
        assert body["command"].startswith("查询dima空间")
        assert f"space:{dima_space_id}" in body["command"]
        assert "user:user1" in body["command"]
        assert "agent:bot1" in body["command"]
        assert body["kind"] == "autoInitiate"  # kind 字段应显式设置为 autoInitiate
        assert body["timezone"] == DEFAULT_CRON_TIMEZONE
        assert body["enabled"] is True
        assert body["timeout_secs"] == DEFAULT_CRON_TIMEOUT_SECS

    @pytest.mark.asyncio
    async def test_create_cron_with_hourly_frequency(self):
        """trigger_frequency=hourly → cron expression '0 * * * *'。"""
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {"is_hosted_24x7": 1, "trigger_frequency": "hourly", "dima_space_id": "W123"}
            }
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

        assert result is not None
        body = mock_relay.forward_request.call_args.kwargs["body"]
        assert body["schedule"] == "0 * * * *"

    @pytest.mark.asyncio
    async def test_create_cron_with_runtime(self):
        """template ext 带 runtime → adapter_body 透传 runtime。"""
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {
                    "is_hosted_24x7": 1,
                    "dima_space_id": "W123",
                    "runtime": "antd",
                },
            }
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

        assert result is not None
        body = mock_relay.forward_request.call_args.kwargs["body"]
        assert body["runtime"] == "antd"

    @pytest.mark.asyncio
    async def test_create_cron_without_runtime(self):
        """template ext 不带 runtime → adapter_body 不包含 runtime 字段。"""
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {"is_hosted_24x7": 1, "dima_space_id": "W123"},
            }
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

        assert result is not None
        body = mock_relay.forward_request.call_args.kwargs["body"]
        assert "runtime" not in body

    @pytest.mark.asyncio
    async def test_create_cron_with_empty_runtime_ignored(self):
        """runtime 为空字符串 → 忽略，不写入 adapter_body。"""
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {"is_hosted_24x7": 1, "dima_space_id": "W123", "runtime": ""},
            }
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

        assert result is not None
        body = mock_relay.forward_request.call_args.kwargs["body"]
        assert "runtime" not in body

    @pytest.mark.asyncio
    async def test_create_cron_with_model_from_config(self):
        """template ext 带 model → adapter_body 透传外部配置的 model。"""
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {"is_hosted_24x7": 1, "dima_space_id": "W123", "model": "gpt-4o"},
            }
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

        assert result is not None
        body = mock_relay.forward_request.call_args.kwargs["body"]
        assert body["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_create_cron_model_fallback_to_default(self, monkeypatch):
        """ext 未配置 model 且 runtime 已配置 → model 走 DEFAULT_CRON_MODEL 兜底。"""
        import agentclaw.community.core.cron.services.aicoding.cron_auto_setup as mod
        monkeypatch.setattr(mod, "DEFAULT_CRON_MODEL", "claude-sonnet")
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {"is_hosted_24x7": 1, "dima_space_id": "W123"},
            }
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

        assert result is not None
        body = mock_relay.forward_request.call_args.kwargs["body"]
        assert body["model"] == "claude-sonnet"

    @pytest.mark.asyncio
    async def test_create_cron_no_model_when_no_config_no_default(self):
        """ext 无 model 且 DEFAULT_CRON_MODEL 为 None → adapter_body 不含 model。"""
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {"is_hosted_24x7": 1, "dima_space_id": "W123"},
            }
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

        assert result is not None
        body = mock_relay.forward_request.call_args.kwargs["body"]
        assert "model" not in body

    @pytest.mark.asyncio
    async def test_idempotent_when_cron_exists(self):
        """Already existing cron with same name → skip."""
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {"is_hosted_24x7": 1, "dima_space_id": "W123"}
            },
            relay_methods={"list_all_crons": {"success": True, "data": [{"name": "7*24小时自动生码_TestBot_bot1", "task_id": "existing_job"}]}}
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

        assert result is None
        mock_relay.forward_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_proceed_when_list_crons_fails(self):
        """list_all_crons fails → still attempt to create (best-effort)."""
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {"is_hosted_24x7": 1, "dima_space_id": "W123"}
            }
        )
        mock_relay.list_all_crons.side_effect = Exception("Connection refused")

        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

        assert result is not None
        mock_relay.forward_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_on_forward_request_failure(self):
        """forward_request raises → CronAutoSetupError."""
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {"is_hosted_24x7": 1, "dima_space_id": "W123"}
            }
        )
        mock_relay.forward_request.side_effect = ValueError("Connection refused")

        with pytest.raises(CronAutoSetupError, match="Auto cron setup failed"):
            await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

    @pytest.mark.asyncio
    async def test_skip_when_template_repo_raises(self):
        """Template repository raises → skip gracefully."""
        service, mock_repo, mock_relay = self._create_service(template_data=None)
        mock_repo.get_by_bot_id.side_effect = Exception("DB error")

        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")
        assert result is None
        mock_relay.forward_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_cron_command_uses_dima_space_id_from_template(self):
        """template_config 中有 dima_space_id 时，command 使用新格式包含 space_id。"""
        custom_space_id = "W26001118999"
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {"is_hosted_24x7": 1, "dima_space_id": custom_space_id}
            }
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

        assert result is not None
        body = mock_relay.forward_request.call_args.kwargs["body"]
        # 新格式命令包含 space_id、user_id 和 agent_id
        assert body["command"].startswith("查询dima空间")
        assert f"space:{custom_space_id}" in body["command"]

    @pytest.mark.asyncio
    async def test_append_message_from_template(self):
        """template_config 中有 append_message 时，command 中包含 append_message 字段。"""
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {"is_hosted_24x7": 1, "dima_space_id": "W123", "append_message": "请优先处理核心逻辑"}
            }
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

        assert result is not None
        body = mock_relay.forward_request.call_args.kwargs["body"]
        assert "append_message:请优先处理核心逻辑" in body["command"]

    @pytest.mark.asyncio
    async def test_no_append_message_in_command(self):
        """template_config 中无 append_message 时，command 中不包含 append_message 字段。"""
        service, mock_repo, mock_relay = self._create_service(
            template_data={
                "name": "TestBot",
                "ext": {"is_hosted_24x7": 1, "dima_space_id": "W123"}
            }
        )
        result = await service.auto_setup_cron_for_bot("bot1", "user1", "nick1")

        assert result is not None
        body = mock_relay.forward_request.call_args.kwargs["body"]
        assert "append_message:" not in body["command"]


# ── _parse_cron_command tests ─────────────────────────────────────────


class TestParseCronCommand:
    """Tests for _parse_cron_command helper."""

    def test_full_command(self):
        cmd = (
            "查询dima空间W123的待开发需求，开启7*24小时自动研发"
            "|space:W123|user:u1|agent:a1|kind:autoInitiate|workflow:dev"
        )
        parsed = _parse_cron_command(cmd)
        assert parsed["space"] == "W123"
        assert parsed["user"] == "u1"
        assert parsed["agent"] == "a1"
        assert parsed["kind"] == "autoInitiate"
        assert parsed["workflow"] == "dev"

    def test_without_workflow(self):
        cmd = "前缀|space:W123|user:u1|agent:a1|kind:autoInitiate"
        parsed = _parse_cron_command(cmd)
        assert "workflow" not in parsed

    def test_with_message(self):
        cmd = "前缀|space:W123|user:u1|agent:a1|kind:autoInitiate|message:hello"
        parsed = _parse_cron_command(cmd)
        assert parsed["message"] == "hello"

    def test_with_append_message(self):
        cmd = "前缀|space:W123|user:u1|agent:a1|kind:autoInitiate|append_message:注意性能"
        parsed = _parse_cron_command(cmd)
        assert parsed["append_message"] == "注意性能"

    def test_workflow_with_slash(self):
        """workflow 值包含 / 时，完整解析。"""
        cmd = "前缀|space:W123|user:u1|agent:a1|kind:autoInitiate|workflow:home/devflow"
        parsed = _parse_cron_command(cmd)
        assert parsed["workflow"] == "home/devflow"

    def test_empty_command(self):
        assert _parse_cron_command("") == {}

    def test_prefix_only(self):
        assert _parse_cron_command("前缀") == {}


# ── _replace_workflow_in_command tests ─────────────────────────────────


class TestReplaceWorkflowInCommand:
    """Tests for _replace_workflow_in_command helper."""

    def test_replace_existing_workflow(self):
        """替换已有的 workflow 值。"""
        cmd = "前缀|space:W123|user:u1|agent:a1|kind:autoInitiate|workflow:old-flow"
        result = _replace_workflow_in_command(cmd, "new-flow")
        assert result == "前缀|space:W123|user:u1|agent:a1|kind:autoInitiate|workflow:new-flow"

    def test_replace_workflow_with_slash_in_value(self):
        """workflow:home/devflow → workflow:rfc-driven-dev，整段替换。"""
        cmd = "查询dima空间W26001120145的待开发需求，开启7*24小时自动研发|space:W26001120145|user:100000|agent: 20260624_6qhoyg0o |kind:autoInitiate|workflow:home/devflow"
        result = _replace_workflow_in_command(cmd, "rfc-driven-dev")
        assert "workflow:rfc-driven-dev" in result
        assert "workflow:home/devflow" not in result
        assert "workflow:home/rfc-driven-dev" not in result
        # 其他字段不变
        assert "space:W26001120145" in result
        assert "user:100000" in result
        assert "kind:autoInitiate" in result

    def test_replace_workflow_preserves_other_fields(self):
        """替换 workflow 时保留其他所有字段，包括 message 和 append_message。"""
        cmd = "前缀|space:W999|user:user42|agent:bot77|kind:autoInitiate|workflow:old-wf|message:test-msg|append_message:注意性能"
        result = _replace_workflow_in_command(cmd, "new-wf")
        assert "space:W999" in result
        assert "user:user42" in result
        assert "agent:bot77" in result
        assert "message:test-msg" in result
        assert "append_message:注意性能" in result
        assert "workflow:new-wf" in result
        assert "workflow:old-wf" not in result

    def test_add_workflow_when_missing(self):
        """原命令无 workflow 时，追加在末尾。"""
        cmd = "前缀|space:W123|user:u1|agent:a1|kind:autoInitiate"
        result = _replace_workflow_in_command(cmd, "new-flow")
        assert result == "前缀|space:W123|user:u1|agent:a1|kind:autoInitiate|workflow:new-flow"

    def test_add_workflow_preserves_message(self):
        """追加 workflow 时保留 message 字段。"""
        cmd = "前缀|space:W123|user:u1|agent:a1|kind:autoInitiate|message:hello"
        result = _replace_workflow_in_command(cmd, "my-flow")
        assert "message:hello" in result
        assert "workflow:my-flow" in result

    def test_replace_workflow_with_colon_in_value(self):
        """workflow 值包含冒号时，只替换到下一个 | 或末尾。"""
        cmd = "前缀|space:W123|workflow:old:with:colons|message:hi"
        result = _replace_workflow_in_command(cmd, "new:flow")
        assert "workflow:new:flow" in result
        assert "message:hi" in result
        assert "workflow:old" not in result

    def test_preserves_unknown_future_fields(self):
        """未来新增的未知字段不会被替换逻辑丢失。"""
        cmd = "前缀|space:W123|user:u1|kind:autoInitiate|append_message:注意性能|workflow:old|extra_field:abc"
        result = _replace_workflow_in_command(cmd, "new")
        assert "append_message:注意性能" in result
        assert "extra_field:abc" in result
        assert "workflow:new" in result


# ── update_auto_initiate_workflow tests ───────────────────────────────


class TestUpdateAutoInitiateWorkflow:
    """Tests for CronAutoSetupService.update_auto_initiate_workflow."""

    def _create_service(self, relay_methods=None):
        mock_template_repo = MagicMock()
        mock_relay = AsyncMock()
        if relay_methods:
            for method, return_val in relay_methods.items():
                getattr(mock_relay, method).return_value = return_val
        else:
            mock_relay.list_all_crons.return_value = {"success": True, "data": []}
            mock_relay.forward_request.return_value = {"success": True}

        service = CronAutoSetupService(
            template_repository=mock_template_repo,
            cron_relay_service=mock_relay,
        )
        return service, mock_relay

    @pytest.mark.asyncio
    async def test_updates_workflow_when_changed(self):
        """workflow 变化时，应更新 cron task 的 command。"""
        old_command = _build_cron_command(
            dima_space_id="W123", user_id="u1", agent_id="a1", workflow="old-flow",
        )
        service, mock_relay = self._create_service(relay_methods={
            "list_all_crons": {
                "success": True,
                "data": [{
                    "id": "job_1",
                    "payload": {"message": old_command},
                }],
            },
            "forward_request": {"success": True},
        })

        result = await service.update_auto_initiate_workflow(
            bot_id="a1", owner_id="u1", nick_name="nick", new_workflow_name="new-flow",
        )

        assert result is True
        call_kwargs = mock_relay.forward_request.call_args.kwargs
        assert call_kwargs["method"] == "PUT"
        assert call_kwargs["path"] == "/api/cron/job_1"
        new_cmd = call_kwargs["body"]["command"]
        assert "workflow:new-flow" in new_cmd
        assert "workflow:old-flow" not in new_cmd

    @pytest.mark.asyncio
    async def test_updates_workflow_with_slash_path(self):
        """workflow:home/devflow → rfc-driven-dev，整段替换不会变成 home/rfc-driven-dev。"""
        old_command = _build_cron_command(
            dima_space_id="W26001120145", user_id="100000", agent_id="20260624_6qhoyg0o",
            workflow="home/devflow",
        )
        service, mock_relay = self._create_service(relay_methods={
            "list_all_crons": {
                "success": True,
                "data": [{
                    "id": "job_1",
                    "payload": {"message": old_command},
                }],
            },
            "forward_request": {"success": True},
        })

        result = await service.update_auto_initiate_workflow(
            bot_id="20260624_6qhoyg0o", owner_id="100000", nick_name="nick",
            new_workflow_name="rfc-driven-dev",
        )

        assert result is True
        new_cmd = mock_relay.forward_request.call_args.kwargs["body"]["command"]
        assert "workflow:rfc-driven-dev" in new_cmd
        assert "workflow:home/devflow" not in new_cmd
        assert "workflow:home/rfc-driven-dev" not in new_cmd

    @pytest.mark.asyncio
    async def test_skips_when_workflow_unchanged(self):
        """workflow 未变化时，不应调用 forward_request。"""
        old_command = _build_cron_command(
            dima_space_id="W123", user_id="u1", agent_id="a1", workflow="same-flow",
        )
        service, mock_relay = self._create_service(relay_methods={
            "list_all_crons": {
                "success": True,
                "data": [{
                    "id": "job_1",
                    "payload": {"message": old_command},
                }],
            },
        })

        result = await service.update_auto_initiate_workflow(
            bot_id="a1", owner_id="u1", nick_name="nick", new_workflow_name="same-flow",
        )

        assert result is False
        mock_relay.forward_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_false_when_no_auto_initiate_cron(self):
        """无 autoInitiate 类型任务时，返回 False。"""
        service, mock_relay = self._create_service(relay_methods={
            "list_all_crons": {
                "success": True,
                "data": [{
                    "id": "job_1",
                    "payload": {"message": "some other task"},
                }],
            },
        })

        result = await service.update_auto_initiate_workflow(
            bot_id="a1", owner_id="u1", nick_name="nick", new_workflow_name="new-flow",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_crons(self):
        """没有任何 cron 任务时，返回 False。"""
        service, mock_relay = self._create_service()

        result = await service.update_auto_initiate_workflow(
            bot_id="a1", owner_id="u1", nick_name="nick", new_workflow_name="new-flow",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_list_fails(self):
        """list_all_crons 失败时，返回 False。"""
        service, mock_relay = self._create_service()
        mock_relay.list_all_crons.side_effect = Exception("Connection refused")

        result = await service.update_auto_initiate_workflow(
            bot_id="a1", owner_id="u1", nick_name="nick", new_workflow_name="new-flow",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_forward_fails(self):
        """forward_request 失败时，返回 False（不抛异常）。"""
        old_command = _build_cron_command(
            dima_space_id="W123", user_id="u1", agent_id="a1", workflow="old-flow",
        )
        service, mock_relay = self._create_service(relay_methods={
            "list_all_crons": {
                "success": True,
                "data": [{
                    "id": "job_1",
                    "payload": {"message": old_command},
                }],
            },
        })
        mock_relay.forward_request.side_effect = ValueError("Device offline")

        result = await service.update_auto_initiate_workflow(
            bot_id="a1", owner_id="u1", nick_name="nick", new_workflow_name="new-flow",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_preserves_other_command_fields(self):
        """更新 workflow 时，应保留 command 中的其他字段（space、user、agent、message、append_message 等）。"""
        old_command = _build_cron_command(
            dima_space_id="W999", user_id="user42", agent_id="bot77",
            message="test-msg", workflow="old-wf", append_message="注意性能",
        )
        service, mock_relay = self._create_service(relay_methods={
            "list_all_crons": {
                "success": True,
                "data": [{
                    "task_id": "t1",
                    "payload": {"message": old_command},
                }],
            },
            "forward_request": {"success": True},
        })

        await service.update_auto_initiate_workflow(
            bot_id="bot77", owner_id="user42", nick_name="nick", new_workflow_name="new-wf",
        )

        new_cmd = mock_relay.forward_request.call_args.kwargs["body"]["command"]
        assert "space:W999" in new_cmd
        assert "user:user42" in new_cmd
        assert "agent:bot77" in new_cmd
        assert "message:test-msg" in new_cmd
        assert "append_message:注意性能" in new_cmd
        assert "workflow:new-wf" in new_cmd

    @pytest.mark.asyncio
    async def test_add_workflow_when_previously_empty(self):
        """旧 command 无 workflow 时，新增 workflow 字段。"""
        old_command = _build_cron_command(
            dima_space_id="W123", user_id="u1", agent_id="a1",
        )
        assert "workflow:" not in old_command

        service, mock_relay = self._create_service(relay_methods={
            "list_all_crons": {
                "success": True,
                "data": [{
                    "id": "job_1",
                    "payload": {"message": old_command},
                }],
            },
            "forward_request": {"success": True},
        })

        result = await service.update_auto_initiate_workflow(
            bot_id="a1", owner_id="u1", nick_name="nick", new_workflow_name="new-flow",
        )

        assert result is True
        new_cmd = mock_relay.forward_request.call_args.kwargs["body"]["command"]
        assert "workflow:new-flow" in new_cmd
