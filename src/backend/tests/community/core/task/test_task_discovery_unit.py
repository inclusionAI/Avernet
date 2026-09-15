"""Task discovery unit tests — adapted for backend-scheduled-initiation redesign.

Covers:
  - DiscoveredTask new fields and methods (to_discovery_prompt, to_notification_body, to_card_data)
  - OrmTaskReader.read_pending_tasks_for_bot
  - DiscoveryService with SessionInitiator (mocked)
  - TaskDiscoveryScheduler startup/shutdown

(2026-09-15 统一化: 原 ``CronRelaySessionInitiator`` 辅助方法/标题更新单测已随
Relay 链删除;统一实现 ``OpenApiBotSessionInitiator`` 的等价单测见
``test_openapi_bot_session_initiator.py``。)
"""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, call, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.task.task_discovery.discovered_task_models import (  # noqa: F401
    DiscoveredTaskModel,
)
from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryResult,
    DiscoveryService,
)
from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    OrmTaskReader,
    seed_discovered_tasks,
)
from agentclaw.community.core.task.task_discovery.notify_messages_provider import (
    NotifyMessagesProvider,
)
from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
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
# OrmTaskReader.read_pending_tasks_for_bot
# ---------------------------------------------------------------------------

class _InMemoryDB:
    """In-memory SQLite database for unit testing (mimics DatabasePlugin)."""

    def __init__(self):
        self._engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        from agentclaw.community.core.base import Base
        Base.metadata.create_all(self._engine)
        self._session_factory = sessionmaker(bind=self._engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def transactional_orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class TestTaskReader:
    _MOCK_TASKS = [
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
    ]

    def _make_reader(self):
        db = _InMemoryDB()
        seed_discovered_tasks(db, self._MOCK_TASKS)
        return OrmTaskReader(db)

    def test_read_pending_for_bot(self):
        reader = self._make_reader()
        tasks = reader.read_pending_tasks_for_bot("bot-001", "user-001", _DT)
        assert len(tasks) == 1
        assert tasks[0].bot_id == "bot-001"
        assert tasks[0].title == "Task1"
        assert tasks[0].objective == "Objective-1"
        assert tasks[0].acceptances == [{"id": "ac1", "description": "acc-1"}]

    def test_read_pending_for_wrong_bot(self):
        reader = self._make_reader()
        tasks = reader.read_pending_tasks_for_bot("wrong-bot", "user-001", _DT)
        assert tasks == []

    def test_read_pending_excludes_non_pending(self):
        reader = self._make_reader()
        tasks = reader.read_pending_tasks_for_bot("bot-001", "user-001", _DT)
        # Task3 is "confirmed", not included
        assert all(t.needs_confirmation for t in tasks)

    def test_read_all_backward_compat(self):
        reader = self._make_reader()
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

        notify_sender = MagicMock(spec=NotifyMessagesProvider)
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
            notify_sender=MagicMock(spec=NotifyMessagesProvider),
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
        notify_sender = MagicMock(spec=NotifyMessagesProvider)

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