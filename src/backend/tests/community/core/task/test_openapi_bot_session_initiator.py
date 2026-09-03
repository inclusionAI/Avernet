"""Unit tests for OpenApiBotSessionInitiator — corp/pre/prod session creation via BaaS Open API.

Covers initiate_session (happy/error/fallback), _build_discovery_prompt, _build_session_url.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_discovery.openapi_bot_session_initiator import (
    OpenApiBotSessionInitiator,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    BotSendResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DT = "2026-08-27"


def _make_task(
    *,
    task_id: str = "td-unit-1",
    title: str = "Unit task",
    objective: str = "Do the thing",
    instruction: str = "Step-by-step instructions",
    background: str = "Some context",
    acceptances: list[dict] | None = None,
) -> DiscoveredTask:
    return DiscoveredTask(
        task_id=task_id,
        bot_id="bot-1",
        owner_id="owner-1",
        dt=_DT,
        title=title,
        instruction=instruction,
        background=background,
        discovery_basis="unit test",
        priority="medium",
        status="pending_confirmation",
        objective=objective,
        acceptances=acceptances if acceptances is not None else [],
    )


def _make_openapi_bot(
    *,
    send_result: BotSendResult | None = None,
    grant_raises: Exception | None = None,
) -> AsyncMock:
    bot = AsyncMock()
    if grant_raises is not None:
        bot.ensure_grant.side_effect = grant_raises
    else:
        bot.ensure_grant.return_value = None
    if send_result is not None:
        bot.send_message.return_value = send_result
    else:
        bot.send_message.return_value = BotSendResult(
            run_id="run-123", session_id="sess-456"
        )
    return bot


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestInit:
    def test_stores_attributes(self):
        bot = _make_openapi_bot()
        initiator = OpenApiBotSessionInitiator(
            openapi_bot=bot,
            frontend_url="http://example.com:8000",
        )
        assert initiator._openapi_bot is bot
        assert initiator._frontend_url == "http://example.com:8000"
        assert initiator._ensure_grant is False

    def test_default_frontend_url(self):
        bot = _make_openapi_bot()
        initiator = OpenApiBotSessionInitiator(openapi_bot=bot)
        assert initiator._frontend_url == "http://localhost:8000"

    def test_ensure_grant_flag(self):
        bot = _make_openapi_bot()
        initiator = OpenApiBotSessionInitiator(
            openapi_bot=bot, ensure_grant=True
        )
        assert initiator._ensure_grant is True


# ---------------------------------------------------------------------------
# initiate_session
# ---------------------------------------------------------------------------

class TestInitiateSession:
    def test_happy_single_task_with_session_id(self):
        """Single task: title uses first_task.title; session_id from BaaS."""
        bot = _make_openapi_bot(
            send_result=BotSendResult(run_id="run-1", session_id="sess-1")
        )
        initiator = OpenApiBotSessionInitiator(openapi_bot=bot, frontend_url="http://fe:8000")
        task = _make_task(title="My Task", acceptances=[{"id": "a1", "description": "d1"}])
        result = _run(initiator.initiate_session(
            [task], bot_id="bot-1", owner_id="owner-1", agent_id="bot-1",
        ))
        assert isinstance(result, DiscoverySession)
        assert result.task_id == task.task_id
        assert result.session_id == "sess-1"
        assert "workspace" in result.session_url
        bot.ensure_grant.assert_awaited_once_with("bot-1")
        bot.send_message.assert_awaited_once()
        # metadata title for single task
        _, kwargs = bot.send_message.call_args
        assert kwargs["metadata"]["title"] == "[DreamMode-任务发现] My Task"
        assert kwargs["metadata"]["task_count"] == 1

    def test_happy_multi_task_title(self):
        """Multiple tasks: title uses count format."""
        bot = _make_openapi_bot(
            send_result=BotSendResult(run_id="run-2", session_id="sess-2")
        )
        initiator = OpenApiBotSessionInitiator(openapi_bot=bot)
        tasks = [
            _make_task(task_id="t1", title="Task A"),
            _make_task(task_id="t2", title="Task B"),
        ]
        result = _run(initiator.initiate_session(
            tasks, bot_id="bot-1", owner_id="owner-1", agent_id="bot-1",
        ))
        assert result.session_id == "sess-2"
        _, kwargs = bot.send_message.call_args
        assert "发现 2 件" in kwargs["metadata"]["title"]
        assert kwargs["metadata"]["task_count"] == 2

    def test_ensure_grant_exception_is_non_fatal(self):
        """ensure_grant raising should not abort the flow."""
        bot = _make_openapi_bot(
            grant_raises=RuntimeError("grant failed"),
            send_result=BotSendResult(run_id="run-3", session_id="sess-3"),
        )
        initiator = OpenApiBotSessionInitiator(openapi_bot=bot)
        result = _run(initiator.initiate_session(
            [_make_task()], bot_id="bot-1", owner_id="owner-1", agent_id="bot-1",
        ))
        assert result.session_id == "sess-3"
        bot.send_message.assert_awaited_once()

    def test_session_id_none_falls_back_to_run_id(self):
        """BaaS returning no session_id → use run_id as fallback."""
        bot = _make_openapi_bot(
            send_result=BotSendResult(run_id="run-only", session_id=None),
        )
        initiator = OpenApiBotSessionInitiator(openapi_bot=bot)
        result = _run(initiator.initiate_session(
            [_make_task()], bot_id="bot-1", owner_id="owner-1", agent_id="bot-1",
        ))
        assert result.session_id == "run-only"

    def test_send_message_called_with_correct_args(self):
        """Verify send_message receives bot_id, message, and metadata."""
        bot = _make_openapi_bot()
        initiator = OpenApiBotSessionInitiator(openapi_bot=bot)
        task = _make_task(acceptances=[{"id": "x", "description": "y"}])
        _run(initiator.initiate_session(
            [task], bot_id="bot-1", owner_id="owner-1", agent_id="bot-1",
        ))
        bot.send_message.assert_awaited_once()
        call_kwargs = bot.send_message.call_args.kwargs
        assert call_kwargs["bot_id"] == "bot-1"
        assert isinstance(call_kwargs["message"], str)
        assert "task" in call_kwargs["message"].lower()
        assert call_kwargs["metadata"]["source"] == "task_discovery"
        assert call_kwargs["metadata"]["discovery_date"] == _DT
        assert len(call_kwargs["metadata"]["ext_info"]["tasks"]) == 1

    def test_session_url_encoding(self):
        """session_url properly URL-encodes bot and session params."""
        bot = _make_openapi_bot(
            send_result=BotSendResult(run_id="r", session_id="s with spaces"),
        )
        initiator = OpenApiBotSessionInitiator(
            openapi_bot=bot, frontend_url="http://localhost:8000",
        )
        result = _run(initiator.initiate_session(
            [_make_task()], bot_id="bot 1", owner_id="owner 1", agent_id="bot 1",
        ))
        # bot and session should be URL-encoded
        assert "bot=bot%201%3Aowner%201" in result.session_url
        assert "session=agent%3Amain%3As%20with%20spaces" in result.session_url


# ---------------------------------------------------------------------------
# _build_discovery_prompt
# ---------------------------------------------------------------------------

class TestBuildDiscoveryPrompt:
    def test_with_acceptances(self):
        initiator = OpenApiBotSessionInitiator(openapi_bot=_make_openapi_bot())
        prompt = initiator._build_discovery_prompt([
            _make_task(title="T1", objective="O1", acceptances=[{"id": "a1", "description": "desc1"}]),
        ])
        assert "T1" in prompt
        assert "O1" in prompt
        assert "验收标准" in prompt
        assert "a1" in prompt
        assert "desc1" in prompt
        assert "请向用户展示" in prompt

    def test_without_acceptances(self):
        initiator = OpenApiBotSessionInitiator(openapi_bot=_make_openapi_bot())
        prompt = initiator._build_discovery_prompt([
            _make_task(title="T2", acceptances=[]),
        ])
        assert "确认时可由你补充" in prompt

    def test_objective_fallback_to_title(self):
        initiator = OpenApiBotSessionInitiator(openapi_bot=_make_openapi_bot())
        prompt = initiator._build_discovery_prompt([
            _make_task(title="FallbackTitle", objective=""),
        ])
        assert "FallbackTitle" in prompt

    def test_multiple_tasks_numbered(self):
        initiator = OpenApiBotSessionInitiator(openapi_bot=_make_openapi_bot())
        tasks = [_make_task(title=f"Task{i}") for i in range(3)]
        prompt = initiator._build_discovery_prompt(tasks)
        assert "1. 【Task0】" in prompt
        assert "2. 【Task1】" in prompt
        assert "3. 【Task2】" in prompt


# ---------------------------------------------------------------------------
# _build_session_url
# ---------------------------------------------------------------------------

class TestBuildSessionUrl:
    def test_uses_constructor_url(self):
        initiator = OpenApiBotSessionInitiator(
            openapi_bot=_make_openapi_bot(),
            frontend_url="http://my-host:9000",
        )
        url = initiator._build_session_url("s1", "b1", "o1")
        assert url.startswith("http://my-host:9000/workspace")
        assert "tab=chat" in url
        assert "bot=b1%3Ao1" in url
        assert "session=agent%3Amain%3As1" in url

    def test_frontend_url_provider_overrides(self):
        """Provider 返回非空 → 覆盖构造 frontend_url(holder-override 语义
        现由 CorpFrontendUrlProvider.get() 承载)。"""

        class _StubProvider:
            def get(self) -> str:
                return "http://override:7777"

        initiator = OpenApiBotSessionInitiator(
            openapi_bot=_make_openapi_bot(),
            frontend_url="http://ignored:8000",
            frontend_url_provider=_StubProvider(),
        )
        url = initiator._build_session_url("s2", "b2", "o2")
        assert url.startswith("http://override:7777/workspace")

    def test_frontend_url_provider_empty_falls_back_to_ctor(self):
        """Provider 返回空串(NullFrontendUrlProvider 语义) → 回落构造值。"""

        class _EmptyProvider:
            def get(self) -> str:
                return ""

        initiator = OpenApiBotSessionInitiator(
            openapi_bot=_make_openapi_bot(),
            frontend_url="http://ctor:8000",
            frontend_url_provider=_EmptyProvider(),
        )
        url = initiator._build_session_url("s2", "b2", "o2")
        assert url.startswith("http://ctor:8000/workspace")

    def test_strips_trailing_slash(self):
        initiator = OpenApiBotSessionInitiator(
            openapi_bot=_make_openapi_bot(),
            frontend_url="http://host:8000/",
        )
        url = initiator._build_session_url("s3", "b3", "o3")
        assert "host:8000/workspace" in url
        assert "host:8000//workspace" not in url


# ---------------------------------------------------------------------------
# _update_session_title — double agent:main: prefix guard
# ---------------------------------------------------------------------------

class TestUpdateSessionTitle:
    """Verify _update_session_title guards against double agent:main: prefix
    and calls engine directly (not the OpenAPI v1 public surface).

    BaaS may return session_id already containing the ``agent:main:`` prefix.
    Also, the title update now resolves engine target and calls the engine's
    ``POST /api/sessions/{session_id}/update`` directly, bypassing the
    OpenAPI v1 admission layer (which requires Bearer/Cookie auth → 401 for
    internal service calls).
    """

    @staticmethod
    def _capture_update_url(session_id: str, *, bot_id: str = "bot-1",
                            owner_id: str = "owner-1") -> str | None:
        """Run _update_session_title with mocked engine target + httpx,
        return the captured POST URL."""
        from unittest.mock import MagicMock, patch

        initiator = OpenApiBotSessionInitiator(
            openapi_bot=_make_openapi_bot(),
            backend_url="http://localhost:8888",
        )
        # Mock _resolve_engine_target to return a fake engine address
        initiator._resolve_engine_target = AsyncMock(return_value="localhost:20010")

        captured: list[str] = []

        class _MockResp:
            status_code = 200
            text = "{}"

        with patch(
            "agentclaw.community.core.task.task_discovery.openapi_bot_session_initiator.httpx.AsyncClient"
        ) as mock_cls:
            cli = MagicMock()
            cli.__aenter__ = AsyncMock(return_value=cli)
            cli.__aexit__ = AsyncMock(return_value=None)

            async def _post(url, **kw):
                captured.append(url)
                return _MockResp()

            cli.post = _post
            mock_cls.return_value = cli
            _run(initiator._update_session_title(
                session_id, "[DreamMode-任务发现] Title", bot_id, owner_id,
            ))

        return captured[0] if captured else None

    def test_no_double_prefix_when_session_id_has_agent_main(self):
        """session_id already has agent:main: → no double prefix in URL."""
        url = self._capture_update_url("agent:main:session:abc:owner-1")
        assert url is not None
        assert "agent:main:agent:main:" not in url
        assert "agent:main:session:abc:owner-1" in url

    def test_prefix_added_when_session_id_lacks_agent_main(self):
        """session_id without agent:main: → prefix is correctly prepended."""
        url = self._capture_update_url("session:abc:owner-1")
        assert url is not None
        assert "agent:main:session:abc:owner-1" in url
        assert "agent:main:agent:main:" not in url

    def test_calls_engine_directly_not_openapi_v1(self):
        """Title update goes to engine's /api/sessions/{id}/update,
        NOT the backend's /openapi/v1/ surface (which requires auth)."""
        url = self._capture_update_url("session:abc:owner-1")
        assert url is not None
        assert "/api/sessions/" in url
        assert "/update" in url
        assert "/openapi/v1/" not in url
