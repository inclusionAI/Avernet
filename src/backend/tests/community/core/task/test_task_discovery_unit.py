"""Task discovery unit tests — adapted for backend-scheduled-initiation redesign.

Covers:
  - DiscoveredTask new fields and methods (to_discovery_prompt, to_notification_body, to_card_data)
  - SqliteTaskReader.read_pending_tasks_for_bot
  - DiscoveryService with SessionInitiator (mocked)
  - CronRelaySessionInitiator._build_discovery_prompt / _build_session_url
  - TaskDiscoveryScheduler startup/shutdown
"""
from __future__ import annotations

import asyncio
import os

import pytest
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
from agentclaw.community.core.task.task_discovery.session_creator import (
    HttpSessionCreator,
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
    project_name="TestSkill",
    description="A test task",
    business_scenario="testing",
    discovery_basis="unit test",
    work_item_url="http://example.com/123",
    priority="high",
    status="pending_confirmation",
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
                "dt": _DT, "project_name": "Task1", "description": "d1",
                "business_scenario": "s1", "discovery_basis": "b1",
                "status": "pending_confirmation",
            },
            {
                "task_id": "t2", "bot_id": "bot-002", "owner_id": "user-001",
                "dt": _DT, "project_name": "Task2", "description": "d2",
                "business_scenario": "s2", "discovery_basis": "b2",
                "status": "pending_confirmation",
            },
            {
                "task_id": "t3", "bot_id": "bot-001", "owner_id": "user-001",
                "dt": _DT, "project_name": "Task3", "description": "d3",
                "business_scenario": "s3", "discovery_basis": "b3",
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
        assert tasks[0].project_name == "Task1"

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
        assert "http://example.com/123" in prompt
        assert "是否确认执行" in prompt

    def test_build_session_url(self):
        inst = self._make_initiator()
        url = inst._build_session_url("sess-456", "bot-001")
        assert "sess-456" in url
        assert "bot-001" in url


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

# ---------------------------------------------------------------------------
# TaskDiscoveryLifecycle — backend lifecycle scheduling and discovery
# ---------------------------------------------------------------------------

class TestTaskDiscoveryLifecycle:
    def _make_lifecycle(self, bots=None):
        from agentclaw.community.core.task.task_discovery.lifecycle import (
            TaskDiscoveryLifecycle,
        )

        bot_service = MagicMock()
        bot_service.list_bots.return_value = {"items": bots or []}
        return TaskDiscoveryLifecycle(bot_service, MagicMock()), bot_service

    def test_startup_disabled_does_not_schedule(self):
        lifecycle, _ = self._make_lifecycle()
        with patch.dict(os.environ, {"TASK_DISCOVERY_AUTO_START": "false"}):
            asyncio.run(lifecycle.startup())
        assert lifecycle._task is None

    def test_startup_and_shutdown_schedule_and_cancel(self):
        lifecycle, _ = self._make_lifecycle()
        with patch.dict(
            os.environ,
            {
                "TASK_DISCOVERY_AUTO_START": "true",
                "TASK_DISCOVERY_SCHEDULE_HOUR": "23",
                "TASK_DISCOVERY_SCHEDULE_MINUTE": "59",
            },
        ):
            async def run():
                await lifecycle.startup()
                assert lifecycle._task is not None
                await lifecycle.shutdown()
                assert lifecycle._task.cancelled()

            asyncio.run(run())

    def test_list_all_bots_returns_empty_on_service_failure(self):
        lifecycle, bot_service = self._make_lifecycle()
        bot_service.list_bots.side_effect = RuntimeError("database unavailable")
        assert lifecycle._list_all_bots() == []

    def test_resolve_data_file_honors_environment_override(self):
        lifecycle, _ = self._make_lifecycle()
        with patch.dict(os.environ, {"TASK_DISCOVERY_DATA_FILE": "/tmp/tasks.db"}):
            assert lifecycle._resolve_data_file() == "/tmp/tasks.db"

    def test_discover_once_skips_when_no_bots(self):
        lifecycle, _ = self._make_lifecycle()
        asyncio.run(lifecycle._discover_once())

    def test_discover_once_skips_incomplete_bot_and_handles_results(self):
        lifecycle, _ = self._make_lifecycle(
            [
                {"bot_id": "", "owner_id": "owner"},
                {"bot_id": "bot-1", "owner_id": "owner-1"},
            ]
        )
        task = MagicMock(task_id="task-1")
        task.project_name = "Project"
        result = MagicMock(
            success=True,
            task=task,
            session=MagicMock(session_id="session-1"),
            notification_sent=True,
            error=None,
        )
        service = MagicMock()
        service.discover = AsyncMock(return_value=[result])
        with patch(
            "agentclaw.community.core.task.task_discovery.lifecycle.create_default_service",
            return_value=service,
        ):
            asyncio.run(lifecycle._discover_once())
        service.discover.assert_awaited_once_with(
            agent_id="bot-1",
            bot_id="bot-1",
            owner_id="owner-1",
        )


class TestHttpSessionCreator:
    def test_create_session_resolves_target_and_builds_url(self, monkeypatch):
        import httpx

        task = replace(_TASK, task_id="task-http", project_name="HTTP task")
        requests = []

        class _Response:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, url, **kwargs):
                requests.append(("GET", url, kwargs))
                if "/api/bots/" in url:
                    return _Response({"data": {"binding_id": 42}})
                return _Response({"data": {"target": "engine:20010"}})

            async def post(self, url, **kwargs):
                requests.append(("POST", url, kwargs))
                return _Response({"success": True, "data": {"id": "session-1"}})

        monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: _Client())
        creator = HttpSessionCreator(
            backend_url="http://backend/", frontend_url="http://frontend/"
        )

        result = asyncio.run(
            creator.create_session(
                task,
                user_id="owner",
                agent_id="bot-1",
                bot_id="bot-1",
                owner_id="owner",
                model="model-a",
            )
        )

        assert result.session_id == "session-1"
        assert result.session_url == (
            "http://frontend/bcn/chat/session?bot_uuid=bot-1"
            "&id=bot-1&session=session-1"
        )
        assert requests[0][2]["params"] == {"owner_id": "owner"}
        assert requests[1][2]["headers"] == {"x-user-id": "owner"}
        assert requests[2][2]["json"]["model"] == "model-a"
        assert requests[2][2]["json"]["extInfo"]["source"] == "task_discovery"

    def test_missing_binding_id_is_rejected(self, monkeypatch):
        import httpx

        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"data": {}}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def get(self, *_args, **_kwargs):
                return _Response()

        monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: _Client())
        creator = HttpSessionCreator(backend_url="http://backend")
        with pytest.raises(RuntimeError, match="no binding_id"):
            asyncio.run(creator._resolve_engine_target("bot", "owner", "user"))

    def test_session_creator_adapter_rejects_empty_tasks(self):
        from agentclaw.community.core.task.task_discovery.discovery_service import (
            _SessionCreatorAdapter,
        )

        adapter = _SessionCreatorAdapter(MagicMock())
        with pytest.raises(ValueError, match="at least one task"):
            asyncio.run(
                adapter.initiate_session(
                    [], bot_id="bot", owner_id="owner", agent_id="bot"
                )
            )
