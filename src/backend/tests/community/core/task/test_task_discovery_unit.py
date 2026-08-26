"""Task discovery unit tests — adapted for backend-scheduled-initiation redesign.

Covers:
  - DiscoveredTask new fields and methods (to_discovery_prompt, to_notification_body, to_card_data)
  - SqliteTaskReader.read_pending_tasks_for_bot
  - DiscoveryService with SessionInitiator (mocked)
  - CronRelaySessionInitiator._build_discovery_prompt / _build_session_url
  - CronRelaySessionInitiator.initiate_session Step 2.5 title update (success/fail/exception)
  - TaskDiscoveryScheduler startup/shutdown
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, call, patch


from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryResult,
    DiscoveryService,
)
from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_discovery.session_initiator import (
    CronRelaySessionInitiator,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    SqliteTaskReader,
    init_discovered_tasks_db,
)
from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
    NotifySenderPlugin,
)

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_DT = "2026-08-19"

_TASK = DiscoveredTask(
    task_id="discover_task_test-bot_test-owner_2026-08-19",
    bot_id="test-bot",
    owner_id="test-owner",
    dt=_DT,
    title="TestSkill",
    instruction="A test task",
    background="testing",
    discovery_basis="unit test",
    priority="high",
    status="pending_confirmation",
    objective="Unit-test discovery objective",
    acceptances=[{"id": "a1", "description": "acceptance one"}],
)

_SESSION = DiscoverySession(
    task_id="discover_task_test-bot_test-owner_2026-08-19",
    session_id="sess-123",
    session_url="http://localhost:8000/bcn/chat/session?session=sess-123",
)


# ---------------------------------------------------------------------------
# DiscoveredTask — new fields + methods
# ---------------------------------------------------------------------------

class TestDiscoveredTask:
    def test_new_fields(self):
        assert _TASK.bot_id == "test-bot"
        assert _TASK.owner_id == "test-owner"
        assert _TASK.dt == _DT

    def test_to_session_ext_info_includes_new_fields(self):
        info = _TASK.to_session_ext_info()
        assert info["bot_id"] == "test-bot"
        assert info["owner_id"] == "test-owner"
        assert info["dt"] == _DT
        assert info["source"] == "task_discovery"

    def test_to_discovery_prompt(self):
        prompt = _TASK.to_discovery_prompt()
        assert "TestSkill" in prompt
        assert "A test task" in prompt
        assert "testing" in prompt
        assert "是否确认执行" in prompt

    def test_to_notification_body(self):
        body = _TASK.to_notification_body(3)
        assert "发现了 3 件" in body
        assert "TestSkill" in body
        assert "请点击进入会话" in body

    def test_to_card_data(self):
        card = _TASK.to_card_data()
        assert card["workitem_name"] == "TestSkill"
        assert card["workitem_bg"] == "A test task"
        # No vendor brand names
        assert "card_name" in card


# ---------------------------------------------------------------------------
# SqliteTaskReader.read_pending_tasks_for_bot
# ---------------------------------------------------------------------------

class TestTaskReader:
    def _setup_db(self, tmpdir):
        db = os.path.join(str(tmpdir), "test.db")
        init_discovered_tasks_db(db, [
            {
                "task_id": "t1", "bot_id": "bot-001", "owner_id": "user-001",
                "dt": _DT, "title": "Task1", "instruction": "d1",
                "background": "s1", "discovery_basis": "b1",
                "status": "pending_confirmation",
                "objective": "Objective-1",
                "acceptances": [{"id": "ac1", "description": "acc-1"}],
            },
            {
                "task_id": "t2", "bot_id": "bot-002", "owner_id": "user-001",
                "dt": _DT, "title": "Task2", "instruction": "d2",
                "background": "s2", "discovery_basis": "b2",
                "status": "pending_confirmation",
                "objective": "",
                "acceptances": [],
            },
            {
                "task_id": "t3", "bot_id": "bot-001", "owner_id": "user-001",
                "dt": _DT, "title": "Task3", "instruction": "d3",
                "background": "s3", "discovery_basis": "b3",
                "status": "confirmed",  # not pending
            },
        ])
        return db

    def test_read_pending_for_bot(self, tmp_path):
        db = self._setup_db(tmp_path)
        reader = SqliteTaskReader(db)
        tasks = reader.read_pending_tasks_for_bot("bot-001", "user-001", _DT)
        assert len(tasks) == 1
        assert tasks[0].bot_id == "bot-001"
        assert tasks[0].title == "Task1"
        assert tasks[0].objective == "Objective-1"
        assert tasks[0].acceptances == [{"id": "ac1", "description": "acc-1"}]

    def test_read_pending_for_wrong_bot(self, tmp_path):
        db = self._setup_db(tmp_path)
        reader = SqliteTaskReader(db)
        tasks = reader.read_pending_tasks_for_bot("wrong-bot", "user-001", _DT)
        assert tasks == []

    def test_read_pending_excludes_non_pending(self, tmp_path):
        db = self._setup_db(tmp_path)
        reader = SqliteTaskReader(db)
        tasks = reader.read_pending_tasks_for_bot("bot-001", "user-001", _DT)
        # Task3 is "confirmed", not included
        assert all(t.needs_confirmation for t in tasks)

    def test_read_all_backward_compat(self, tmp_path):
        db = self._setup_db(tmp_path)
        reader = SqliteTaskReader(db)
        all_tasks = reader.read_discovered_tasks()
        assert len(all_tasks) == 3


# ---------------------------------------------------------------------------
# DiscoveryService — with SessionInitiator mock
# ---------------------------------------------------------------------------

class TestDiscoveryService:
    def _make_service(self, tasks, session=None, notify_ok=True):
        reader = MagicMock()
        reader.read_pending_tasks_for_bot = MagicMock(return_value=tasks)

        initiator = AsyncMock()
        initiator.initiate_session = AsyncMock(return_value=session or _SESSION)

        notify_sender = MagicMock(spec=NotifySenderPlugin)
        notify_sender.send = MagicMock(
            return_value="msg-id-123" if notify_ok else None
        )

        return DiscoveryService(
            reader=reader,
            session_initiator=initiator,
            notify_sender=notify_sender,
        )

    def test_discover_success(self):
        svc = self._make_service([_TASK])
        results = asyncio.run(svc.discover(
            bot_id="test-bot", owner_id="test-owner",
            agent_id="test-bot",
        ))
        assert len(results) == 1
        assert results[0].success
        assert results[0].session.session_id == "sess-123"
        assert results[0].notification_sent is True
        assert svc.get_discovery_result(_TASK.task_id) is results[0]

    def test_discover_all_bots_intersects_live_bots_and_deduplicates_owners(self):
        pending = [
            replace(_TASK, task_id="task-a1", bot_id="bot-a", owner_id="owner-1"),
            replace(_TASK, task_id="task-a2", bot_id="bot-a", owner_id="owner-1"),
            replace(_TASK, task_id="task-b", bot_id="bot-b", owner_id="owner-1"),
            replace(_TASK, task_id="task-c", bot_id="bot-c", owner_id="owner-2"),
            replace(_TASK, task_id="task-dead", bot_id="bot-dead", owner_id="owner-3"),
        ]
        reader = MagicMock()
        reader.read_pending_tasks.return_value = pending
        bot_service = MagicMock()
        bot_service.list_bots.return_value = {
            "items": [
                {"bot_id": "bot-a"},
                {"bot_id": "bot-b"},
                {"bot_id": "bot-c"},
            ]
        }
        svc = DiscoveryService(
            reader=reader,
            session_initiator=AsyncMock(),
            notify_sender=MagicMock(spec=NotifySenderPlugin),
            bot_service=bot_service,
        )
        result_a = DiscoveryResult(task=pending[0], session=_SESSION)
        result_c = DiscoveryResult(task=pending[3], session=_SESSION)
        svc.discover = AsyncMock(side_effect=[[result_a], [result_c]])

        results = asyncio.run(svc.discover_all_bots())

        assert results == [result_a, result_c]
        assert svc.discover.await_args_list == [
            call(bot_id="bot-a", owner_id="owner-1", agent_id="bot-a"),
            call(bot_id="bot-c", owner_id="owner-2", agent_id="bot-c"),
        ]

    def test_discover_notification_failure(self):
        svc = self._make_service([_TASK], notify_ok=False)
        results = asyncio.run(svc.discover(
            bot_id="test-bot", owner_id="test-owner",
            agent_id="test-bot",
        ))
        assert results[0].success  # session created = success
        assert results[0].notification_sent is False

    def test_discover_session_error(self):
        reader = MagicMock()
        reader.read_pending_tasks_for_bot = MagicMock(return_value=[_TASK])

        initiator = AsyncMock()
        initiator.initiate_session = AsyncMock(
            side_effect=RuntimeError("engine down")
        )
        notify_sender = MagicMock(spec=NotifySenderPlugin)

        svc = DiscoveryService(
            reader=reader,
            session_initiator=initiator,
            notify_sender=notify_sender,
        )
        results = asyncio.run(svc.discover(
            bot_id="test-bot", owner_id="test-owner",
            agent_id="test-bot",
        ))
        assert not results[0].success
        assert "engine down" in results[0].error
        assert results[0].notification_sent is False

    def test_discover_no_tasks(self):
        svc = self._make_service([])
        results = asyncio.run(svc.discover(
            bot_id="test-bot", owner_id="test-owner",
            agent_id="test-bot",
        ))
        assert results == []

    def test_notify_message_extra_has_card_params(self):
        svc = self._make_service([_TASK])
        asyncio.run(svc.discover(
            bot_id="test-bot", owner_id="test-owner",
            agent_id="test-bot",
        ))
        # Inspect the NotifyMessage passed to send()
        call_args = svc._notify_sender.send.call_args
        msg: NotifyMessage = call_args[0][0]
        assert "card_template_id" in msg.extra
        assert "card_biz_id" in msg.extra
        assert "card_data" in msg.extra
        assert msg.deep_link == _SESSION.session_url


# ---------------------------------------------------------------------------
# CronRelaySessionInitiator — helper methods
# ---------------------------------------------------------------------------

class TestCronRelaySessionInitiatorHelpers:
    def _make_initiator(self):
        inst = CronRelaySessionInitiator.__new__(CronRelaySessionInitiator)
        inst._frontend_url = "http://localhost:8000"
        inst._backend_url = "http://localhost:8888"
        inst._wait_for_reply = False
        return inst

    def test_build_discovery_prompt(self):
        inst = self._make_initiator()
        prompt = inst._build_discovery_prompt([_TASK])
        assert "TestSkill" in prompt
        assert "是否确认执行" in prompt

    def test_build_session_url(self):
        inst = self._make_initiator()
        url = inst._build_session_url("sess-456", "bot-001")
        assert "sess-456" in url
        assert "bot-001" in url

    def test_build_session_url_encodes_colons(self):
        inst = self._make_initiator()
        url = inst._build_session_url("agent:main:cron_001", "bot-001")
        assert "agent%3Amain%3Acron_001" in url
        assert "/assistant?botId=bot-001" in url


# ---------------------------------------------------------------------------
# CronRelaySessionInitiator — initiate_session Step 2.5 title update
# ---------------------------------------------------------------------------

class TestCronRelaySessionInitiatorTitleUpdate:
    """Cover the _initiate_session Step 2.5 title-update paths (success/fail/exception)."""

    def _make_initiator(self):
        cron_relay = MagicMock()
        cron_relay.forward_request = AsyncMock(return_value={
            "success": True,
            "data": {"id": "cron_001"},
        })
        inst = CronRelaySessionInitiator(
            cron_relay=cron_relay,
            frontend_url="http://localhost:8000",
        )
        inst._backend_url = "http://localhost:8888"
        inst._wait_for_reply = False
        return inst

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_title_update_success_http200(self):
        """Step 2.5: engine returns HTTP 200 — title update succeeds."""
        inst = self._make_initiator()
        inst._extract_engine_target = AsyncMock(return_value="localhost:20010")
        inst._ws_send_message = AsyncMock()

        success_resp = MagicMock()
        success_resp.status_code = 200
        with patch("agentclaw.community.core.task.task_discovery.session_initiator.httpx.AsyncClient") as mock_client_cls:
            mock_cli = AsyncMock()
            mock_cli.__aenter__ = AsyncMock(return_value=mock_cli)
            mock_cli.__aexit__ = AsyncMock(return_value=None)
            mock_cli.post = AsyncMock(return_value=success_resp)
            mock_client_cls.return_value = mock_cli

            session = asyncio.run(inst.initiate_session(
                [_TASK], bot_id="test-bot", owner_id="test-owner", agent_id="test-bot",
            ))

        assert session.session_id == "cron_001"
        # title update was called (Step 2.5)
        mock_cli.post.assert_awaited_once()
        call_kwargs = mock_cli.post.call_args
        assert "/api/sessions/cron_001/update" in call_kwargs.args[0]
        assert call_kwargs.kwargs["params"]["title"] == "[DreamMode-任务发现] TestSkill"
        # WebSocket injection also ran (Step 3)
        inst._ws_send_message.assert_awaited_once()

    def test_title_update_failed_non200(self):
        """Step 2.5: engine returns HTTP 500 — title update fails (non-fatal)."""
        inst = self._make_initiator()
        inst._extract_engine_target = AsyncMock(return_value="localhost:20010")
        inst._ws_send_message = AsyncMock()

        fail_resp = MagicMock()
        fail_resp.status_code = 500
        fail_resp.text = "internal server error"
        with patch("agentclaw.community.core.task.task_discovery.session_initiator.httpx.AsyncClient") as mock_client_cls:
            mock_cli = AsyncMock()
            mock_cli.__aenter__ = AsyncMock(return_value=mock_cli)
            mock_cli.__aexit__ = AsyncMock(return_value=None)
            mock_cli.post = AsyncMock(return_value=fail_resp)
            mock_client_cls.return_value = mock_cli

            session = asyncio.run(inst.initiate_session(
                [_TASK], bot_id="test-bot", owner_id="test-owner", agent_id="test-bot",
            ))

        # session still created despite title update failure (non-fatal)
        assert session.session_id == "cron_001"
        inst._ws_send_message.assert_awaited_once()

    def test_title_update_exception_non_fatal(self):
        """Step 2.5: httpx raises — exception caught, session still created."""
        inst = self._make_initiator()
        inst._extract_engine_target = AsyncMock(return_value="localhost:20010")
        inst._ws_send_message = AsyncMock()

        with patch("agentclaw.community.core.task.task_discovery.session_initiator.httpx.AsyncClient") as mock_client_cls:
            mock_cli = AsyncMock()
            mock_cli.__aenter__ = AsyncMock(return_value=mock_cli)
            mock_cli.__aexit__ = AsyncMock(return_value=None)
            mock_cli.post = AsyncMock(side_effect=ConnectionError("refused"))
            mock_client_cls.return_value = mock_cli

            session = asyncio.run(inst.initiate_session(
                [_TASK], bot_id="test-bot", owner_id="test-owner", agent_id="test-bot",
            ))

        # session still created despite title update exception (non-fatal)
        assert session.session_id == "cron_001"
        inst._ws_send_message.assert_awaited_once()

    def test_title_update_skipped_when_no_engine_target(self):
        """Step 2.5 skipped (along with Step 3) when engine target is None."""
        inst = self._make_initiator()
        inst._extract_engine_target = AsyncMock(return_value=None)
        inst._ws_send_message = AsyncMock()

        with patch("agentclaw.community.core.task.task_discovery.session_initiator.httpx.AsyncClient") as mock_client_cls:
            session = asyncio.run(inst.initiate_session(
                [_TASK], bot_id="test-bot", owner_id="test-owner", agent_id="test-bot",
            ))
            mock_client_cls.assert_not_called()

        assert session.session_id == "cron_001"
        inst._ws_send_message.assert_not_called()

    def test_title_format_single_task(self):
        """Single task title: [DreamMode-任务发现] {title}."""
        inst = self._make_initiator()
        inst._extract_engine_target = AsyncMock(return_value=None)

        async def _capture_title():
            inst._ws_send_message = AsyncMock()
            with patch("agentclaw.community.core.task.task_discovery.session_initiator.httpx.AsyncClient"):
                await inst.initiate_session(
                    [_TASK], bot_id="test-bot", owner_id="test-owner", agent_id="test-bot",
                )

        asyncio.run(_capture_title())
        forward_call = inst._cron_relay.forward_request.call_args
        body = forward_call.kwargs["body"]
        assert body["title"] == "[DreamMode-任务发现] TestSkill"

    def test_title_format_multi_task(self):
        """Multiple tasks title: [DreamMode-任务发现] 发现 N 件可能有意义的事情."""
        inst = self._make_initiator()
        inst._extract_engine_target = AsyncMock(return_value=None)

        task2 = replace(_TASK, task_id="discover_task_2")
        with patch("agentclaw.community.core.task.task_discovery.session_initiator.httpx.AsyncClient"):
            asyncio.run(inst.initiate_session(
                [_TASK, task2], bot_id="test-bot", owner_id="test-owner", agent_id="test-bot",
            ))

        forward_call = inst._cron_relay.forward_request.call_args
        body = forward_call.kwargs["body"]
        assert body["title"] == "[DreamMode-任务发现] 发现 2 件可能有意义的事情"


# ---------------------------------------------------------------------------
# TaskDiscoveryScheduler — startup/shutdown
# ---------------------------------------------------------------------------

class TestTaskDiscoveryScheduler:
    def test_startup_auto_start_false(self):
        from agentclaw.community.core.task.task_discovery.scheduler import (
            TaskDiscoveryScheduler,
        )
        svc = MagicMock()
        sched = TaskDiscoveryScheduler(discovery_service=svc)
        with patch.dict(os.environ, {"TASK_DISCOVERY_AUTO_START": "false"}):
            asyncio.run(sched.startup())
        assert sched._scheduler is None

    def test_startup_auto_start_true(self):
        from agentclaw.community.core.task.task_discovery.scheduler import (
            TaskDiscoveryScheduler,
        )
        svc = MagicMock()
        sched = TaskDiscoveryScheduler(discovery_service=svc)
        with patch.dict(os.environ, {
            "TASK_DISCOVERY_AUTO_START": "true",
            "TASK_DISCOVERY_CRON": "0 11 * * *",
            "TASK_DISCOVERY_TIMEZONE": "Asia/Shanghai",
        }):
            asyncio.run(sched.startup())
        assert sched._scheduler is not None
        asyncio.run(sched.shutdown())
        assert sched._scheduler is None