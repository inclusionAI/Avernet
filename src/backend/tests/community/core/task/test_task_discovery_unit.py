"""Unit tests for task_discovery core module.

Covers the success paths of DiscoveryService, HttpSessionCreator,
DiscoveredTask.to_notification_message, and TaskDiscoveryLifecycle
that the e2e and endpoint tests do not exercise (session creation,
notification dispatch, engine target resolution).

Uses ``unittest.mock`` to stub httpx.AsyncClient — these are unit
tests, not endpoint tests, so the no-mock rule in ``tests/endpoints/``
does not apply.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_TASK = DiscoveredTask(
    task_id="tsk-001",
    project_name="Test Project",
    description="A test task",
    business_scenario="testing",
    discovery_basis="unit test",
    priority="high",
    status="pending_confirmation",
)

_SESSION = DiscoverySession(
    task_id="tsk-001",
    session_id="sess-123",
    session_url="http://localhost:8000/bcn/chat/session?session=sess-123",
)


# ---------------------------------------------------------------------------
# DiscoveredTask.to_notification_message — cover session_url branch (models.py:78-79)
# ---------------------------------------------------------------------------

class TestNotificationMessage:
    def test_without_session_url(self):
        msg = _TASK.to_notification_message()
        assert "Session" not in msg
        assert "Test Project" in msg

    def test_with_session_url(self):
        """Cover the ``if session_url:`` branch (models.py:78-79)."""
        url = "http://example.com/chat/sess-123"
        msg = _TASK.to_notification_message(session_url=url)
        assert url in msg
        assert "Session" in msg


# ---------------------------------------------------------------------------
# DiscoveryService — cover success + notification paths (discovery_service.py:146-200)
# ---------------------------------------------------------------------------

class TestDiscoveryService:
    def _make_service(
        self,
        tasks: list[DiscoveredTask],
        session: DiscoverySession | None = _SESSION,
        notify_msg_id: str | None = "msg-001",
    ) -> DiscoveryService:
        reader = MagicMock()
        reader.read_pending_tasks.return_value = tasks

        session_creator = MagicMock()
        if session is not None:
            session_creator.create_session = AsyncMock(return_value=session)
        else:
            session_creator.create_session = AsyncMock(
                side_effect=RuntimeError("engine unreachable"),
            )

        notify_sender = MagicMock()
        notify_sender.send.return_value = notify_msg_id

        return DiscoveryService(
            reader=reader,
            session_creator=session_creator,
            notify_sender=notify_sender,
        )

    def test_discover_success_path(self):
        """Cover _discover_single success + _send_notification success
        (discovery_service.py:146,148,182,187,188,189,194)."""
        svc = self._make_service([_TASK], session=_SESSION, notify_msg_id="msg-001")
        results = asyncio.run(svc.discover(
            user_id="u1", agent_id="bot-1", bot_id="bot-1", owner_id="u1",
        ))
        assert len(results) == 1
        r = results[0]
        assert r.success is True
        assert r.session is not None
        assert r.session.session_id == "sess-123"
        assert r.notification_sent is True
        assert r.notification_message  # non-empty

    def test_discover_notification_failure(self):
        """Cover _send_notification failure branch
        (discovery_service.py:196,200)."""
        svc = self._make_service([_TASK], session=_SESSION, notify_msg_id=None)
        results = asyncio.run(svc.discover(
            user_id="u1", agent_id="bot-1", bot_id="bot-1", owner_id="u1",
        ))
        assert results[0].notification_sent is False
        assert results[0].success is True  # session was created

    def test_discover_session_error(self):
        """Cover _discover_single exception path (discovery_service.py:163-169)."""
        svc = self._make_service([_TASK], session=None)
        results = asyncio.run(svc.discover(
            user_id="u1", agent_id="bot-1", bot_id="bot-1", owner_id="u1",
        ))
        assert results[0].success is False
        assert results[0].error is not None
        assert results[0].session is None

    def test_discover_no_tasks(self):
        svc = self._make_service([])
        results = asyncio.run(svc.discover(
            user_id="u1", agent_id="bot-1", bot_id="bot-1", owner_id="u1",
        ))
        assert results == []


# ---------------------------------------------------------------------------
# HttpSessionCreator — cover _resolve_engine_target, create_session, _build_session_url
# (session_creator.py:94-125,153-171,204)
# ---------------------------------------------------------------------------

class TestHttpSessionCreator:
    def _mock_async_client(self, bot_data, conn_data, session_data):
        """Create a mock AsyncClient context manager."""
        mock_bot_resp = MagicMock()
        mock_bot_resp.json.return_value = {"data": bot_data}
        mock_bot_resp.raise_for_status = MagicMock()

        mock_conn_resp = MagicMock()
        mock_conn_resp.json.return_value = {"data": conn_data}
        mock_conn_resp.raise_for_status = MagicMock()

        mock_session_resp = MagicMock()
        mock_session_resp.json.return_value = session_data
        mock_session_resp.raise_for_status = MagicMock()

        mock_cli = AsyncMock()
        mock_cli.get = AsyncMock(side_effect=[mock_bot_resp, mock_conn_resp])
        mock_cli.post = AsyncMock(return_value=mock_session_resp)

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_cli)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        return mock_cm

    def test_resolve_engine_target_success(self):
        """Cover _resolve_engine_target (session_creator.py:94-125)."""
        creator = HttpSessionCreator(
            backend_url="http://test-backend:8888",
            frontend_url="http://test-frontend:8000",
        )
        bot_data = {"binding_id": "bind-001"}
        conn_data = {"target": "localhost:20010"}
        mock_cm = self._mock_async_client(bot_data, conn_data, {})

        with patch("httpx.AsyncClient") as mock_ac:
            mock_ac.return_value = mock_cm
            target = asyncio.run(
                creator._resolve_engine_target("bot-1", "u1", "u1"),
            )
        assert target == "localhost:20010"

    def test_resolve_engine_target_no_binding_id(self):
        """Cover RuntimeError for missing binding_id (session_creator.py:104-105)."""
        creator = HttpSessionCreator(backend_url="http://test:8888")
        bot_data = {}  # no binding_id
        conn_data = {}
        mock_cm = self._mock_async_client(bot_data, conn_data, {})

        with patch("httpx.AsyncClient") as mock_ac:
            mock_ac.return_value = mock_cm
            with pytest.raises(RuntimeError, match="no binding_id"):
                asyncio.run(
                    creator._resolve_engine_target("bot-1", "u1", "u1"),
                )

    def test_resolve_engine_target_no_target(self):
        """Cover RuntimeError for missing target (session_creator.py:117-120)."""
        creator = HttpSessionCreator(backend_url="http://test:8888")
        bot_data = {"binding_id": "bind-001"}
        conn_data = {}  # no target
        mock_cm = self._mock_async_client(bot_data, conn_data, {})

        with patch("httpx.AsyncClient") as mock_ac:
            mock_ac.return_value = mock_cm
            with pytest.raises(RuntimeError, match="no target"):
                asyncio.run(
                    creator._resolve_engine_target("bot-1", "u1", "u1"),
                )

    def test_create_session_success(self):
        """Cover create_session full path (session_creator.py:153-171,204)."""
        creator = HttpSessionCreator(
            backend_url="http://test-backend:8888",
            frontend_url="http://test-frontend:8000",
        )
        bot_data = {"binding_id": "bind-001"}
        conn_data = {"target": "localhost:20010"}
        session_data = {"success": True, "data": {"id": "sess-999"}}
        mock_cm = self._mock_async_client(bot_data, conn_data, session_data)

        with patch("httpx.AsyncClient") as mock_ac:
            mock_ac.return_value = mock_cm
            session = asyncio.run(creator.create_session(
                _TASK,
                user_id="u1", agent_id="bot-1", bot_id="bot-1", owner_id="u1",
            ))

        assert session.session_id == "sess-999"
        assert "sess-999" in session.session_url
        assert "bot-1" in session.session_url

    def test_create_session_engine_error(self):
        """Cover RuntimeError when engine returns success=False (session_creator.py:177-180)."""
        creator = HttpSessionCreator(backend_url="http://test:8888")
        bot_data = {"binding_id": "bind-001"}
        conn_data = {"target": "localhost:20010"}
        session_data = {"success": False, "message": "engine busy"}
        mock_cm = self._mock_async_client(bot_data, conn_data, session_data)

        with patch("httpx.AsyncClient") as mock_ac:
            mock_ac.return_value = mock_cm
            with pytest.raises(RuntimeError, match="engine busy"):
                asyncio.run(creator.create_session(
                    _TASK,
                    user_id="u1", agent_id="bot-1", bot_id="bot-1", owner_id="u1",
                ))

    def test_build_session_url(self):
        """Cover _build_session_url directly (session_creator.py:204)."""
        creator = HttpSessionCreator(frontend_url="http://fe:8000/")
        url = creator._build_session_url("s1", "agent-1")
        assert url == "http://fe:8000/bcn/chat/session?bot_uuid=agent-1&id=agent-1&session=s1"


# ---------------------------------------------------------------------------
# TaskDiscoveryLifecycle — cover _discover_once (lifecycle.py:137,152)
# ---------------------------------------------------------------------------

class TestLifecycleDiscovery:
    def test_discover_once_with_bots(self):
        """Cover _discover_once internals (lifecycle.py:137,152)."""
        from agentclaw.community.core.task.task_discovery.lifecycle import (
            TaskDiscoveryLifecycle,
        )

        bot_service = MagicMock()
        bot_service.list_bots.return_value = {
            "items": [{"bot_id": "bot-1", "owner_id": "u1"}],
        }

        notify_sender = MagicMock()

        lifecycle = TaskDiscoveryLifecycle(
            bot_service=bot_service,
            notify_sender=notify_sender,
        )

        # Patch create_default_service to return a mock service whose
        # discover() returns empty results — we just need lines 137 and 152
        # to execute.
        mock_service = MagicMock()
        mock_service.discover = AsyncMock(return_value=[])

        with patch(
            "agentclaw.community.core.task.task_discovery.lifecycle."
            "create_default_service",
            return_value=mock_service,
        ):
            asyncio.run(lifecycle._discover_once())

        bot_service.list_bots.assert_called_once()

    def test_discover_once_no_bots(self):
        from agentclaw.community.core.task.task_discovery.lifecycle import (
            TaskDiscoveryLifecycle,
        )

        bot_service = MagicMock()
        bot_service.list_bots.return_value = {"items": []}

        lifecycle = TaskDiscoveryLifecycle(
            bot_service=bot_service,
            notify_sender=MagicMock(),
        )

        # Should return early without calling create_default_service
        asyncio.run(lifecycle._discover_once())
