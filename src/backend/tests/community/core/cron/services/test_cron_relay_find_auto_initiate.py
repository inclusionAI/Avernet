"""Tests for CronRelayService.find_auto_initiate_and_run.

覆盖场景:
1. 成功找到 autoInitiate job 并触发
2. Bot 无设备绑定 → ValueError
3. 设备不 ACTIVE → ValueError
4. 列出 cron jobs 失败 → ValueError
5. 无 autoInitiate 类型 job → ValueError
6. 多 job 时只取第一个 autoInitiate
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.cron.protocols import DeviceBindingStatus
from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.core.devices.services.device_context import DeviceContext


@pytest.fixture(autouse=True)
def _assistant_url_env(monkeypatch):
    monkeypatch.setenv(
        "TEAMCLAW_ASSISTANT_URL_BASE",
        "https://teamclaw.alipay.com/assistant",
    )
    monkeypatch.setenv(
        "TEAMCLAW_ASSISTANT_URL_BASE_PRE",
        "https://teamclaw-pre.alipay.com/assistant",
    )


def _make_service(
    *,
    bot_provider=None,
    device_provider=None,
    transport=None,
    resolver=None,
    publish_repo=None,
):
    return CronRelayService(
        bot_provider=bot_provider or MagicMock(),
        device_provider=device_provider or MagicMock(),
        transport=transport or MagicMock(),
        resolver=resolver or MagicMock(),
        template_repo=MagicMock(),
        publish_repo=publish_repo or MagicMock(),
    )


def _make_ctx(provider="baas", binding_id=42, bot_id="bot-1", user_id="user-1"):
    return DeviceContext(
        provider=provider,
        conn_info={
            "url": "http://test",
            "headers": {},
            "use_proxy": True,
            "sandbox_id": None,
            "target": "x",
            "binding_id": binding_id,
        },
        binding_id=binding_id,
        bot_id=bot_id,
        user_id=user_id,
    )


def _active_device_provider():
    """构造返回 ACTIVE 设备的 device_provider。"""
    device_provider = MagicMock()
    device_provider.get_device.return_value = MagicMock(
        status=DeviceBindingStatus.ACTIVE
    )
    return device_provider


def _bot_with_binding():
    """构造有 binding_id 的 bot dict。"""
    return {"bot_id": "bot-1", "binding_id": 42, "bot_name": "TestBot"}


class TestFindAutoInitiateAndRun:
    """CronRelayService.find_auto_initiate_and_run"""

    @pytest.mark.asyncio
    async def test_success(self):
        """成功找到 autoInitiate job 并通过 forward_request 触发。"""
        bot = _bot_with_binding()
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = bot

        device_provider = _active_device_provider()

        resolver = MagicMock()
        ctx = _make_ctx()
        resolver.resolve_for_bot.return_value = ctx

        # 列出 jobs 时返回含 autoInitiate 的列表
        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=[
            # 第一次 invoke: GET /api/cron 列出 jobs
            {"success": True, "data": [
                {"id": "task-agent", "payload": {"kind": "agentTurn", "message": "hello"}},
                {"id": "task-auto", "payload": {"kind": "autoInitiate", "message": "|kind:autoInitiate| 查询dima空间..."}},
            ]},
            # 第二次 invoke: forward_request 内部的 POST
            {"success": True, "data": {
                "job_id": "task-auto",
                "message": "本次发起 1 个新任务",
                "sessions": [
                    {"session_id": "user:382716:session:s-1:agent:bot-1"},
                ],
            }},
        ])

        # get_device_connection_v2 也要 mock (forward_request 内部调用)
        device_provider.get_device_connection_v2.return_value = {"url": "http://test", "headers": {}}

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        result = await svc.find_auto_initiate_and_run(
            bot_id="bot-1", user_id="user-1", nick_name="nick-1", force=True,
        )

        # 验证 get_bot 被调（find_auto_initiate_and_run + forward_request 各一次）
        bot_provider.get_bot.assert_called_with("bot-1", "user-1")
        # 验证 resolver 路径调用（list cron + forward_request 内各一次，均走 resolver）
        resolver.resolve_for_bot.assert_called_with("bot-1", "user-1")
        # 验证第一次 transport.invoke 是列出 cron
        assert transport.invoke.await_count >= 1
        first_call = transport.invoke.call_args_list[0]
        assert first_call[0][1] == "GET"
        assert first_call[0][2] == "/api/cron"
        # 验证最终结果，并为发起的会话补充 TeamClaw assistant 链接
        assert result["success"] is True
        expected_url = (
            "https://teamclaw.alipay.com/assistant?botId=bot-1"
            "&sessionId=user:382716:session:s-1:agent:bot-1"
        )
        assert "sessions" not in result["data"]
        assert result["data"]["message"] == (
            "本次发起 1 个新任务\n\n会话链接：\n"
            f"- {expected_url}"
        )

    @pytest.mark.asyncio
    async def test_no_binding_raises(self):
        """Bot 无 binding_id → ValueError。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {"bot_id": "bot-1", "binding_id": None}

        svc = _make_service(bot_provider=bot_provider)

        with pytest.raises(ValueError, match="has no device binding"):
            await svc.find_auto_initiate_and_run(
                bot_id="bot-1", user_id="user-1", nick_name="nick-1",
            )

    @pytest.mark.asyncio
    async def test_device_not_active_raises(self):
        """设备不 ACTIVE → ValueError。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(
            status=DeviceBindingStatus.RELEASED
        )

        svc = _make_service(bot_provider=bot_provider, device_provider=device_provider)

        with pytest.raises(ValueError, match="device not ACTIVE"):
            await svc.find_auto_initiate_and_run(
                bot_id="bot-1", user_id="user-1", nick_name="nick-1",
            )

    @pytest.mark.asyncio
    async def test_list_crons_failure_raises(self):
        """列出 cron jobs 失败 → ValueError。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = _active_device_provider()

        resolver = MagicMock()
        resolver.resolve_for_bot.side_effect = Exception("resolver boom")

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            resolver=resolver,
        )

        with pytest.raises(ValueError, match="Failed to list crons"):
            await svc.find_auto_initiate_and_run(
                bot_id="bot-1", user_id="user-1", nick_name="nick-1",
            )

    @pytest.mark.asyncio
    async def test_no_auto_initiate_job_raises(self):
        """没有 autoInitiate 类型的 job → ValueError。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = _active_device_provider()

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx()

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={
            "success": True,
            "data": [
                {"id": "task-1", "payload": {"kind": "agentTurn", "message": "hello"}},
                {"id": "task-2", "payload": {"kind": "agentTurn", "message": "world"}},
            ],
        })

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        with pytest.raises(ValueError, match="No autoInitiate cron job found"):
            await svc.find_auto_initiate_and_run(
                bot_id="bot-1", user_id="user-1", nick_name="nick-1",
            )

    @pytest.mark.asyncio
    async def test_empty_cron_list_raises(self):
        """cron 列表为空 → ValueError。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = _active_device_provider()

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx()

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": []})

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        with pytest.raises(ValueError, match="No autoInitiate cron job found"):
            await svc.find_auto_initiate_and_run(
                bot_id="bot-1", user_id="user-1", nick_name="nick-1",
            )

    @pytest.mark.asyncio
    async def test_picks_first_auto_initiate(self):
        """多个 autoInitiate job 时取第一个。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = _active_device_provider()
        device_provider.get_device_connection_v2.return_value = {"url": "http://test", "headers": {}}

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx()

        transport = MagicMock()
        transport.invoke = AsyncMock(side_effect=[
            {"success": True, "data": [
                {"id": "auto-first", "payload": {"kind": "autoInitiate", "message": "|kind:autoInitiate| first"}},
                {"id": "auto-second", "payload": {"kind": "autoInitiate", "message": "|kind:autoInitiate| second"}},
            ]},
            {"success": True, "data": {"job_id": "auto-first"}},
        ])

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        await svc.find_auto_initiate_and_run(
            bot_id="bot-1", user_id="user-1", nick_name="nick-1",
        )

        # 验证 forward_request 内部调用了第一个 autoInitiate job
        run_call = transport.invoke.call_args_list[-1]
        assert "/api/cron/auto-first/run" in run_call[0][2]

    @pytest.mark.asyncio
    async def test_data_not_list_treated_as_empty(self):
        """data 为非 list 类型时视为空列表 → ValueError。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = _active_device_provider()

        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = _make_ctx()

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": "not-a-list"})

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
            resolver=resolver,
        )

        with pytest.raises(ValueError, match="No autoInitiate cron job found"):
            await svc.find_auto_initiate_and_run(
                bot_id="bot-1", user_id="user-1", nick_name="nick-1",
            )


def test_assistant_session_url_uses_pre_domain(monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.cron.services.cron_relay.get_current_env",
        lambda: "pre",
    )

    assert CronRelayService._assistant_session_url("bot-x", "session-x") == (
        "https://teamclaw-pre.alipay.com/assistant?botId=bot-x"
        "&sessionId=session-x"
    )


def test_append_auto_initiate_session_links_to_message():
    payload = {
        "message": "本次发起 2 个新任务",
        "sessions": [
            {"session_id": "user:u:session:s1:agent:bot-x"},
            {"session_id": "user:u:session:s2:agent:bot-x"},
        ],
    }

    CronRelayService._append_auto_initiate_session_links_to_message(payload, "bot-x")

    assert payload["message"] == (
        "本次发起 2 个新任务\n\n会话链接：\n"
        "- https://teamclaw.alipay.com/assistant?botId=bot-x&sessionId=user:u:session:s1:agent:bot-x\n"
        "- https://teamclaw.alipay.com/assistant?botId=bot-x&sessionId=user:u:session:s2:agent:bot-x"
    )
    assert "sessions" not in payload


def test_collect_auto_initiate_session_links_handles_nested_shapes():
    payload = {
        "sessions": [
            {"session_id": "user:u:session:s1:agent:bot-x"},
            {"sessionId": "user:u:session:s2:agent:bot-x"},
        ],
        "ignored": {"session_id": ""},
    }

    urls = CronRelayService._collect_auto_initiate_session_links(payload, "bot-x")

    assert urls == [
        "https://teamclaw.alipay.com/assistant?botId=bot-x&sessionId=user:u:session:s1:agent:bot-x",
        "https://teamclaw.alipay.com/assistant?botId=bot-x&sessionId=user:u:session:s2:agent:bot-x",
    ]


class TestRunSingleAutoInitiate:
    """CronRelayService.run_single_auto_initiate"""

    @pytest.mark.asyncio
    async def test_success_with_workflow_from_template(self):
        """成功发起，workflow 从 template_config 读取。"""
        bot = _bot_with_binding()
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = bot

        device_provider = _active_device_provider()
        device_provider.get_device_connection_v2.return_value = {"url": "http://test", "headers": {}}

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={
            "success": True,
            "data": {
                "total": 1,
                "created": 1,
                "message": "本次发起 1 个新任务",
                "sessions": [{"session_id": "user:u1:session:s-2:agent:bot-1"}],
                "errors": [],
            },
        })

        template_repo = MagicMock()
        template_repo.get_by_bot_id.return_value = {
            "ext": {"devflow_workflow": {"name": "my-flow"}},
        }

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
        )
        svc._template_repo = template_repo

        result = await svc.run_single_auto_initiate(
            bot_id="bot-1", user_id="user-1", nick_name="nick-1",
            dima_url="https://project.teamclaw.com/space/W1/requirement?openWorkItemId=123",
        )
        assert result["success"] is True
        expected_url = (
            "https://teamclaw.alipay.com/assistant?botId=bot-1"
            "&sessionId=user:u1:session:s-2:agent:bot-1"
        )
        assert "sessions" not in result["data"]
        assert result["data"]["message"] == (
            "本次发起 1 个新任务\n\n会话链接：\n"
            f"- {expected_url}"
        )
        body = transport.invoke.call_args[0][3]
        assert body["workflow"] == "my-flow"

    @pytest.mark.asyncio
    async def test_workflow_from_template_string(self):
        """devflow_workflow 为字符串时直接使用。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = _active_device_provider()
        device_provider.get_device_connection_v2.return_value = {"url": "http://test", "headers": {}}

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": {}})

        template_repo = MagicMock()
        template_repo.get_by_bot_id.return_value = {
            "ext": {"devflow_workflow": "str-flow"},
        }

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
        )
        svc._template_repo = template_repo

        await svc.run_single_auto_initiate(
            bot_id="bot-1", user_id="user-1", nick_name="nick-1",
            dima_url="https://project.teamclaw.com/space/W1/requirement?openWorkItemId=456",
        )
        body = transport.invoke.call_args[0][3]
        assert body["workflow"] == "str-flow"

    @pytest.mark.asyncio
    async def test_no_binding_raises(self):
        """Bot 无 binding_id → ValueError。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = {"bot_id": "bot-1", "binding_id": None}

        svc = _make_service(bot_provider=bot_provider)

        with pytest.raises(ValueError, match="has no device binding"):
            await svc.run_single_auto_initiate(
                bot_id="bot-1", user_id="user-1", nick_name="nick-1",
                dima_url="https://example.com?openWorkItemId=1",
            )

    @pytest.mark.asyncio
    async def test_device_not_active_raises(self):
        """设备不 ACTIVE → ValueError。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = MagicMock()
        device_provider.get_device.return_value = MagicMock(status="INACTIVE")

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
        )

        with pytest.raises(ValueError, match="not ACTIVE"):
            await svc.run_single_auto_initiate(
                bot_id="bot-1", user_id="user-1", nick_name="nick-1",
                dima_url="https://example.com?openWorkItemId=1",
            )

    @pytest.mark.asyncio
    async def test_device_exception_raises(self):
        """get_device 抛非 ValueError → 包装为 ValueError。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = MagicMock()
        device_provider.get_device.side_effect = ConnectionError("timeout")

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
        )

        with pytest.raises(ValueError, match="Device not available"):
            await svc.run_single_auto_initiate(
                bot_id="bot-1", user_id="user-1", nick_name="nick-1",
                dima_url="https://example.com?openWorkItemId=1",
            )

    @pytest.mark.asyncio
    async def test_template_repo_failure_does_not_block(self):
        """template_repo 查询失败 → workflow 为空，不阻塞流程。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = _active_device_provider()
        device_provider.get_device_connection_v2.return_value = {"url": "http://test", "headers": {}}

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": {}})

        template_repo = MagicMock()
        template_repo.get_by_bot_id.side_effect = Exception("DB error")

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
        )
        svc._template_repo = template_repo

        result = await svc.run_single_auto_initiate(
            bot_id="bot-1", user_id="user-1", nick_name="nick-1",
            dima_url="https://project.teamclaw.com/space/W1/requirement?openWorkItemId=789",
        )
        assert result["success"] is True
        body = transport.invoke.call_args[0][3]
        assert body["workflow"] == ""

    @pytest.mark.asyncio
    async def test_template_not_found_workflow_empty(self):
        """template 不存在 → workflow 为空。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = _active_device_provider()
        device_provider.get_device_connection_v2.return_value = {"url": "http://test", "headers": {}}

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": {}})

        template_repo = MagicMock()
        template_repo.get_by_bot_id.return_value = None

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
        )
        svc._template_repo = template_repo

        await svc.run_single_auto_initiate(
            bot_id="bot-1", user_id="user-1", nick_name="nick-1",
            dima_url="https://project.teamclaw.com/space/W1/requirement?openWorkItemId=000",
        )
        body = transport.invoke.call_args[0][3]
        assert body["workflow"] == ""

    @pytest.mark.asyncio
    async def test_model_param_forwarded(self):
        """model 参数传入时写入 body。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = _active_device_provider()
        device_provider.get_device_connection_v2.return_value = {"url": "http://test", "headers": {}}

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": {}})

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
        )

        await svc.run_single_auto_initiate(
            bot_id="bot-1", user_id="user-1", nick_name="nick-1",
            dima_url="https://project.teamclaw.com/space/W1/requirement?openWorkItemId=M1",
            model="claude-sonnet",
        )
        body = transport.invoke.call_args[0][3]
        assert body["model"] == "claude-sonnet"

    @pytest.mark.asyncio
    async def test_model_none_not_in_body(self):
        """model 不传时 body 中无 model 字段。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = _active_device_provider()
        device_provider.get_device_connection_v2.return_value = {"url": "http://test", "headers": {}}

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": {}})

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
        )

        await svc.run_single_auto_initiate(
            bot_id="bot-1", user_id="user-1", nick_name="nick-1",
            dima_url="https://project.teamclaw.com/space/W1/requirement?openWorkItemId=M2",
        )
        body = transport.invoke.call_args[0][3]
        assert "model" not in body

    @pytest.mark.asyncio
    async def test_append_message_forwarded(self):
        """append_message 透传到 body。"""
        bot_provider = MagicMock()
        bot_provider.get_bot.return_value = _bot_with_binding()

        device_provider = _active_device_provider()
        device_provider.get_device_connection_v2.return_value = {"url": "http://test", "headers": {}}

        transport = MagicMock()
        transport.invoke = AsyncMock(return_value={"success": True, "data": {}})

        svc = _make_service(
            bot_provider=bot_provider,
            device_provider=device_provider,
            transport=transport,
        )

        await svc.run_single_auto_initiate(
            bot_id="bot-1", user_id="user-1", nick_name="nick-1",
            dima_url="https://project.teamclaw.com/space/W1/requirement?openWorkItemId=A1",
            append_message="请注意性能",
        )
        body = transport.invoke.call_args[0][3]
        assert body["append_message"] == "请注意性能"
