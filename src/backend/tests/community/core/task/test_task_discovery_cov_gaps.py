"""补充单测 — task_discovery 行覆盖缺口清理。

覆盖既有测试未触达的源文件/分支:
  - models.py:            DiscoveredTask.to_notification_message (带/不带 session_url)
  - lifecycle.py:         TaskDiscoveryLifecycle 全量 (startup/shutdown/_run_daily_schedule/
                          _seconds_until/_discover_once/_list_all_bots/_resolve_data_file)
  - session_creator.py:   HttpSessionCreator 全量 (connection API 定位 + 直连建 session +
                          全部错误分支 + URL 构建 provider/fallback)
  - session_initiator.py: UnavailableSessionInitiator + _resolve_engine_target +
                          _update_session_title 的非致命分支
  - task_reader.py:       _row_to_task / init_discovered_tasks_db / SqliteTaskReader /
                          MockTaskReader / upsert 既有行更新分支
  - discovery_service.py: discover_all_bots 的 pending 空跑/无 bot service/list 失败/
                          分布式锁 skip/成功/异常 + create_default_service
  - scheduler.py:         _run_discovery 成败 / enable_for_bot /
                          disable_for_bot / reschedule / get_status

风格与既有 tests/community/core/task/test_task_discovery_unit.py 保持一致:
fake 就地定义, 断言真实行为 (请求形状/返回值/副作用), 不做空转刷行。
"""
from __future__ import annotations

import asyncio
import datetime as _dt_module
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import agentclaw.community.core.task.task_discovery.lifecycle as lifecycle_mod
import agentclaw.community.core.task.task_discovery.session_initiator as session_initiator_mod
from agentclaw.community.core.base import Base  # noqa: F401
from agentclaw.community.core.task.task_discovery.discovered_task_models import (  # noqa: F401
    DiscoveredTaskModel,
)
from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryResult,
    DiscoveryService,
    create_default_service,
)
from agentclaw.community.core.task.task_discovery.lifecycle import (
    TaskDiscoveryLifecycle,
)
from agentclaw.community.core.task.task_discovery.lock_models import (
    TaskDiscoveryLockRecord,
)
from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_discovery.scheduler import (
    TaskDiscoveryScheduler,
)
from agentclaw.community.core.task.task_discovery.session_creator import (
    HttpSessionCreator,
)
from agentclaw.community.core.task.task_discovery.session_initiator import (
    OpenApiBotSessionInitiator,
    UnavailableSessionInitiator,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    MockTaskReader,
    OrmTaskReader,
    SqliteTaskReader,
    clear_discovered_tasks,
    init_discovered_tasks_db,
    seed_discovered_tasks,
    upsert_discovered_tasks,
)

_DT = "2026-09-23"


# ---------------------------------------------------------------------------
# Shared helpers / fakes
# ---------------------------------------------------------------------------

def _task(task_id: str = "t1", bot_id: str = "bot-1", owner_id: str = "owner-1",
          **overrides) -> DiscoveredTask:
    defaults = dict(
        task_id=task_id,
        bot_id=bot_id,
        owner_id=owner_id,
        dt=_DT,
        title="TestSkill",
        instruction="Do something useful",
        background="Some context",
        discovery_basis="unit test",
        priority="medium",
        status="pending_confirmation",
        objective="Ship the thing",
        acceptances=[{"id": "a1", "description": "acceptance one"}],
    )
    defaults.update(overrides)
    return DiscoveredTask(**defaults)


def _session(session_id: str = "sess-1") -> DiscoverySession:
    return DiscoverySession(
        task_id="t1",
        session_id=session_id,
        session_url=f"http://localhost:8000/assistant?sessionId={session_id}",
    )


class _FakeResponse:
    """Minimal httpx.Response stand-in used by the HTTP router fake."""

    def __init__(self, *, status_code: int = 200, payload: dict | None = None,
                 text: str = ""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPError(f"HTTP {self.status_code} error")


class _HttpRouter:
    """Routes (method, url-contains) -> response-or-exception for faked httpx."""

    def __init__(self):
        self.routes: list[tuple[str, str, object]] = []
        self.calls: list[tuple[str, str, dict]] = []

    def add(self, method: str, contains: str, response) -> None:
        self.routes.append((method, contains, response))

    def handle(self, method: str, url: str, kwargs: dict):
        self.calls.append((method, url, kwargs))
        for m, contains, resp in self.routes:
            if m == method and contains in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return _FakeResponse(status_code=404)


@pytest.fixture
def http_router(monkeypatch):
    """Replace httpx.AsyncClient globally with a router-backed fake client."""
    router = _HttpRouter()

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kw):
            return router.handle("GET", url, kw)

        async def post(self, url, **kw):
            return router.handle("POST", url, kw)

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return router


class _FakeBotService:
    """BotServiceProtocol fake — records list_bots calls."""

    def __init__(self, items: list[dict] | None = None,
                 exc: Exception | None = None, result: dict | None = None):
        self._items = items if items is not None else []
        self._exc = exc
        self._result = result
        self.calls: list[dict] = []

    def list_bots(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        if self._result is not None:
            return self._result
        return {"items": self._items}


class _MemDB:
    """In-memory SQLite DatabasePlugin stand-in (mirrors test_task_discovery_unit)."""

    def __init__(self):
        self._engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(self._engine)
        self._factory = sessionmaker(bind=self._engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        s = self._factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    @contextmanager
    def transactional_orm_session(self):
        s = self._factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


# ===========================================================================
# models.DiscoveredTask.to_notification_message
# ===========================================================================

class TestDiscoveredTaskNotificationMessage:
    def test_without_session_url(self):
        msg = _task().to_notification_message()
        assert "发现待执行任务" in msg
        assert "项目名称：TestSkill" in msg
        assert "项目简介：Do something useful" in msg
        assert "业务场景：Some context" in msg
        assert "挖掘依据：unit test" in msg
        assert "请确认是否执行此任务。" in msg
        assert "Session 链接" not in msg

    def test_with_session_url(self):
        url = "http://localhost:8000/assistant?sessionId=sess-9"
        msg = _task().to_notification_message(url)
        assert msg.endswith(f"Session 链接: {url}")
        assert msg.count("─" * 40) == 1


# ===========================================================================
# session_initiator.UnavailableSessionInitiator
# ===========================================================================

class TestUnavailableSessionInitiator:
    def test_default_reason_used_when_blank(self):
        initiator = UnavailableSessionInitiator("")
        with pytest.raises(RuntimeError) as excinfo:
            asyncio.run(initiator.initiate_session(
                [_task()], bot_id="bot-1", owner_id="owner-1", agent_id="bot-1",
            ))
        msg = str(excinfo.value)
        assert "SessionInitiator unavailable" in msg
        assert "OpenApiBotPort 未绑定" in msg
        assert "fail-closed" in msg

    def test_custom_reason_surfaced(self):
        initiator = UnavailableSessionInitiator(reason="custom missing port")
        with pytest.raises(RuntimeError, match="custom missing port"):
            asyncio.run(initiator.initiate_session(
                [], bot_id="b", owner_id="o", agent_id="b",
            ))


# ===========================================================================
# session_initiator._resolve_engine_target
# ===========================================================================

class TestOpenApiResolveEngineTarget:
    def _make(self) -> OpenApiBotSessionInitiator:
        return OpenApiBotSessionInitiator(
            openapi_bot=MagicMock(),
            backend_url="http://localhost:8888/",
        )

    @staticmethod
    def _add_bot_detail_route(router, binding_id="bnd-1"):
        router.add(
            "GET", "/api/bots/bot-1",
            _FakeResponse(payload={"data": {"binding_id": binding_id}}),
        )

    def test_success_resolves_target(self, http_router):
        self._add_bot_detail_route(http_router)
        http_router.add(
            "GET", "/api/v1/devices/bnd-1/connection",
            _FakeResponse(payload={"data": {"target": "127.0.0.1:20010"}}),
        )
        target = asyncio.run(
            self._make()._resolve_engine_target("bot-1", "owner-1")
        )
        assert target == "127.0.0.1:20010"
        # Verify request shape of the bot-detail lookup (params + headers).
        method, url, kw = http_router.calls[0]
        assert method == "GET"
        assert url == "http://localhost:8888/api/bots/bot-1"
        assert kw["params"] == {"owner_id": "owner-1"}
        assert kw["headers"] == {"x-user-id": "owner-1"}

    def test_no_binding_id_returns_none(self, http_router):
        self._add_bot_detail_route(http_router, binding_id="")
        assert asyncio.run(
            self._make()._resolve_engine_target("bot-1", "owner-1")
        ) is None

    def test_empty_target_returns_none(self, http_router):
        self._add_bot_detail_route(http_router)
        http_router.add(
            "GET", "/api/v1/devices/bnd-1/connection",
            _FakeResponse(payload={"data": {"target": ""}}),
        )
        assert asyncio.run(
            self._make()._resolve_engine_target("bot-1", "owner-1")
        ) is None

    def test_bot_detail_http_error_returns_none(self, http_router):
        http_router.add(
            "GET", "/api/bots/bot-1", _FakeResponse(status_code=500)
        )
        assert asyncio.run(
            self._make()._resolve_engine_target("bot-1", "owner-1")
        ) is None


# ===========================================================================
# session_initiator._update_session_title (directly exercised; non-fatal paths)
# ===========================================================================

class TestOpenApiUpdateSessionTitle:
    def _make(self, target: str | None = "127.0.0.1:20010"):
        initiator = OpenApiBotSessionInitiator(
            openapi_bot=MagicMock(),
            backend_url="http://localhost:8888",
        )
        initiator._resolve_engine_target = AsyncMock(return_value=target)
        return initiator

    @staticmethod
    def _patched_httpx_client(status_code: int = 200, text: str = "{}",
                              exc: Exception | None = None):
        cli = MagicMock()
        cli.__aenter__ = AsyncMock(return_value=cli)
        cli.__aexit__ = AsyncMock(return_value=False)
        if exc is not None:
            async def _post(url, **kw):
                raise exc
            cli.post = _post
        else:
            cli.post = AsyncMock(
                return_value=SimpleNamespace(status_code=status_code, text=text)
            )
        cls = MagicMock(return_value=cli)
        return cls, cli

    def test_no_engine_target_returns_early_without_http_call(self):
        initiator = self._make(target=None)
        # No httpx patch needed: must not raise, must not touch any client.
        asyncio.run(initiator._update_session_title(
            "agent:main:sess-9", "Title", "bot-1", "owner-1"
        ))
        initiator._resolve_engine_target.assert_awaited_once_with(
            "bot-1", "owner-1"
        )

    def test_success_prepends_http_when_target_is_bare_host(self):
        initiator = self._make(target="127.0.0.1:20010")
        cls, cli = self._patched_httpx_client(status_code=200)
        with patch.object(
            session_initiator_mod.httpx, "AsyncClient", cls
        ):
            asyncio.run(initiator._update_session_title(
                "sess-9", "My Title", "bot-1", "owner-1"
            ))
        url = cli.post.call_args.args[0]
        # bare target gets an http:// prefix; raw session id gets agent:main:
        assert url == "http://127.0.0.1:20010/api/sessions/agent:main:sess-9/update"
        assert cli.post.call_args.kwargs["params"] == {"title": "My Title"}
        assert cli.post.call_args.kwargs["headers"] == {"x-user-id": "owner-1"}

    def test_success_keeps_target_scheme_when_already_http(self):
        initiator = self._make(target="http://10.0.0.1:1234")
        cls, cli = self._patched_httpx_client(status_code=200)
        with patch.object(
            session_initiator_mod.httpx, "AsyncClient", cls
        ):
            asyncio.run(initiator._update_session_title(
                "agent:main:sess-9", "T", "bot-1", "owner-1"
            ))
        url = cli.post.call_args.args[0]
        assert url == "http://10.0.0.1:1234/api/sessions/agent:main:sess-9/update"
        # no double prefix on session id
        assert "agent:main:agent:main:" not in url

    def test_non_200_response_is_non_fatal(self):
        initiator = self._make()
        cls, cli = self._patched_httpx_client(
            status_code=502, text="bad gateway"
        )
        with patch.object(
            session_initiator_mod.httpx, "AsyncClient", cls
        ):
            asyncio.run(initiator._update_session_title(
                "sess-9", "T", "bot-1", "owner-1"
            ))
        cli.post.assert_awaited_once()

    def test_post_exception_is_non_fatal(self):
        initiator = self._make()
        cls, cli = self._patched_httpx_client(
            exc=httpx.ConnectError("connection refused")
        )
        with patch.object(
            session_initiator_mod.httpx, "AsyncClient", cls
        ):
            asyncio.run(initiator._update_session_title(
                "sess-9", "T", "bot-1", "owner-1"
            ))
        # Coroutine ran to completion — exception swallowed with a warning.
        assert cli.__aexit__.await_count == 1


# ===========================================================================
# session_creator.HttpSessionCreator
# ===========================================================================

class TestHttpSessionCreatorUrls:
    def test_ctor_attributes(self):
        sc = HttpSessionCreator(
            backend_url="http://backend:8888",
            frontend_url="http://fe:8000",
        )
        assert sc._backend_url == "http://backend:8888"
        assert sc._frontend_url == "http://fe:8000"
        assert sc._frontend_url_provider is None

    def test_build_session_url_falls_back_to_ctor_url(self):
        sc = HttpSessionCreator(frontend_url="http://fe:8000/")
        url = sc._build_session_url("sess-1", "bot-1")
        expected = (
            "http://fe:8000/assistant"
            f"?botId=bot-1&sessionId={quote('agent:main:sess-1', safe='')}"
        )
        assert url == expected

    def test_build_session_url_prefers_provider(self):
        class _Provider:
            def get(self) -> str:
                return "http://override:7777"

        sc = HttpSessionCreator(
            frontend_url="http://ignored:8000",
            frontend_url_provider=_Provider(),
        )
        url = sc._build_session_url("sess-1", "bot-1")
        assert url.startswith("http://override:7777/assistant")
        assert "sessionId=agent%3Amain%3Asess-1" in url

    def test_build_session_url_provider_empty_falls_back(self):
        class _EmptyProvider:
            def get(self) -> str:
                return ""

        sc = HttpSessionCreator(
            frontend_url="http://ctor:8000",
            frontend_url_provider=_EmptyProvider(),
        )
        assert sc._build_session_url("s", "b").startswith(
            "http://ctor:8000/assistant"
        )


class TestHttpSessionCreatorCreateSession:
    def _make(self, frontend_url: str = "http://fe:8000") -> HttpSessionCreator:
        return HttpSessionCreator(
            backend_url="http://localhost:8888",
            frontend_url=frontend_url,
        )

    @staticmethod
    def _add_success_routes(router):
        router.add("GET", "/api/bots/bot-1", _FakeResponse(
            payload={"data": {"binding_id": "bnd-1"}},
        ))
        router.add("GET", "/api/v1/devices/bnd-1/connection", _FakeResponse(
            payload={"data": {"target": "127.0.0.1:20010"}},
        ))
        router.add("POST", "/api/sessions", _FakeResponse(
            payload={"success": True, "data": {"id": "sess-9"}},
        ))

    def test_happy_path_with_model(self, http_router):
        self._add_success_routes(http_router)
        sc = self._make()
        result = asyncio.run(sc.create_session(
            _task(),
            user_id="user-1",
            agent_id="bot-1",
            bot_id="bot-1",
            owner_id="owner-1",
            model="test-model",
        ))
        assert isinstance(result, DiscoverySession)
        assert result.task_id == "t1"
        assert result.session_id == "sess-9"
        assert result.session_url == (
            "http://fe:8000/assistant"
            f"?botId=bot-1&sessionId={quote('agent:main:sess-9', safe='')}"
        )
        # Session POST went to the resolved per-bot engine target.
        post_method, post_url, post_kw = http_router.calls[-1]
        assert post_url == "http://127.0.0.1:20010/api/sessions"
        assert post_kw["headers"] == {"x-user-id": "user-1"}
        body = post_kw["json"]
        assert body["model"] == "test-model"
        assert body["title"] == "TestSkill"
        assert body["user_id"] == "user-1"
        assert body["agent_id"] == "bot-1"
        assert body["extInfo"]["source"] == "task_discovery"
        assert body["extInfo"]["task_id"] == "t1"

    def test_happy_path_without_model_omits_key(self, http_router):
        self._add_success_routes(http_router)
        sc = self._make()
        asyncio.run(sc.create_session(
            _task(), user_id="u", agent_id="a",
            bot_id="bot-1", owner_id="o",
        ))
        _, _, post_kw = http_router.calls[-1]
        assert "model" not in post_kw["json"]

    def test_no_binding_id_raises(self, http_router):
        router = http_router
        router.add("GET", "/api/bots/bot-1", _FakeResponse(
            payload={"data": {"binding_id": ""}},
        ))
        with pytest.raises(RuntimeError, match="binding_id"):
            asyncio.run(self._make().create_session(
                _task(), user_id="u", agent_id="a",
                bot_id="bot-1", owner_id="o",
            ))

    def test_no_engine_target_raises(self, http_router):
        router = http_router
        router.add("GET", "/api/bots/bot-1", _FakeResponse(
            payload={"data": {"binding_id": "bnd-1"}},
        ))
        router.add("GET", "/api/v1/devices/bnd-1/connection", _FakeResponse(
            payload={"data": {"target": ""}},
        ))
        with pytest.raises(RuntimeError, match="no target"):
            asyncio.run(self._make().create_session(
                _task(), user_id="u", agent_id="a",
                bot_id="bot-1", owner_id="o",
            ))

    def test_bot_detail_http_error_propagates(self, http_router):
        http_router.add(
            "GET", "/api/bots/bot-1", _FakeResponse(status_code=404)
        )
        with pytest.raises(httpx.HTTPError):
            asyncio.run(self._make().create_session(
                _task(), user_id="u", agent_id="a",
                bot_id="bot-1", owner_id="o",
            ))

    def test_engine_says_not_success_raises(self, http_router):
        self._add_success_routes(http_router)
        http_router.routes[-1] = (
            "POST", "/api/sessions",
            _FakeResponse(payload={"success": False, "message": "nope"}),
        )
        with pytest.raises(RuntimeError, match="engine session creation failed"):
            asyncio.run(self._make().create_session(
                _task(), user_id="u", agent_id="a",
                bot_id="bot-1", owner_id="o",
            ))

    def test_engine_response_missing_session_id_raises(self, http_router):
        self._add_success_routes(http_router)
        http_router.routes[-1] = (
            "POST", "/api/sessions",
            _FakeResponse(payload={"success": True, "data": {}}),
        )
        with pytest.raises(RuntimeError, match="missing session id"):
            asyncio.run(self._make().create_session(
                _task(), user_id="u", agent_id="a",
                bot_id="bot-1", owner_id="o",
            ))

    def test_engine_post_transport_error_propagates(self, http_router):
        self._add_success_routes(http_router)
        http_router.routes[-1] = (
            "POST", "/api/sessions", httpx.ConnectError("engine down"),
        )
        with pytest.raises(httpx.ConnectError):
            asyncio.run(self._make().create_session(
                _task(), user_id="u", agent_id="a",
                bot_id="bot-1", owner_id="o",
            ))


# ===========================================================================
# task_reader — init_discovered_tasks_db + SqliteTaskReader + MockTaskReader
# ===========================================================================

class TestInitDiscoveredTasksDb:
    DB_TASKS = [
        {
            "task_id": "t1", "bot_id": "bot-1", "owner_id": "owner-1",
            "dt": _DT, "title": "Task1", "instruction": "i1",
            "background": "b1", "discovery_basis": "d1",
            "status": "pending_confirmation", "objective": "obj1",
            "acceptances": [{"id": "a1", "description": "desc"}],
        },
        {"task_id": "t0"},  # minimal — everything else defaults
    ]

    def test_creates_db_with_rows_and_defaults(self, tmp_path):
        db = tmp_path / "discovered.db"
        init_discovered_tasks_db(db, self.DB_TASKS)
        assert db.exists()
        tasks = SqliteTaskReader(db).read_discovered_tasks()
        by_id = {t.task_id: t for t in tasks}
        assert set(by_id) == {"t1", "t0"}
        full = by_id["t1"]
        assert full.bot_id == "bot-1"
        assert full.status == "pending_confirmation"
        assert full.acceptances == [{"id": "a1", "description": "desc"}]
        minimal = by_id["t0"]
        assert minimal.bot_id == ""
        assert minimal.title == ""
        assert minimal.instruction == ""
        assert minimal.priority == "medium"
        assert minimal.acceptances == []
        assert minimal.objective == ""

    def test_reinit_is_idempotent(self, tmp_path):
        db = tmp_path / "discovered.db"
        init_discovered_tasks_db(db, self.DB_TASKS)
        init_discovered_tasks_db(db, self.DB_TASKS)
        assert len(SqliteTaskReader(db).read_discovered_tasks()) == 2


class TestSqliteTaskReader:
    @staticmethod
    def _seeded_db(tmp_path, tasks):
        db = tmp_path / "discovered.db"
        init_discovered_tasks_db(db, tasks)
        return db

    SEEDED = [
        {
            "task_id": "t1", "bot_id": "bot-1", "owner_id": "owner-1",
            "dt": _DT, "title": "T1", "instruction": "i1", "background": "b1",
            "discovery_basis": "d1", "status": "pending_confirmation",
            "objective": "o1", "acceptances": [],
        },
        {
            "task_id": "t2", "bot_id": "bot-2", "owner_id": "owner-1",
            "dt": _DT, "title": "T2", "instruction": "i2", "background": "b2",
            "discovery_basis": "d2", "status": "confirmed",
        },
        {
            "task_id": "t3", "bot_id": "bot-1", "owner_id": "owner-1",
            "dt": "2000-01-01", "title": "T3", "instruction": "i3",
            "background": "b3", "discovery_basis": "d3",
            "status": "pending_confirmation",
        },
    ]

    def test_read_discovered_tasks(self, tmp_path):
        reader = SqliteTaskReader(self._seeded_db(tmp_path, self.SEEDED))
        tasks = reader.read_discovered_tasks()
        assert {t.task_id for t in tasks} == {"t1", "t2", "t3"}

    def test_read_pending_tasks_filters_by_status(self, tmp_path):
        reader = SqliteTaskReader(self._seeded_db(tmp_path, self.SEEDED))
        pending = reader.read_pending_tasks()
        assert {t.task_id for t in pending} == {"t1", "t3"}
        assert all(t.needs_confirmation for t in pending)

    def test_read_pending_for_bot_matches_all_dimensions(self, tmp_path):
        reader = SqliteTaskReader(self._seeded_db(tmp_path, self.SEEDED))
        tasks = reader.read_pending_tasks_for_bot("bot-1", "owner-1", _DT)
        assert [t.task_id for t in tasks] == ["t1"]
        assert reader.read_pending_tasks_for_bot("bot-9", "owner-1", _DT) == []

    def test_read_missing_file_returns_empty(self, tmp_path):
        reader = SqliteTaskReader(tmp_path / "nope.db")
        assert reader.read_discovered_tasks() == []
        assert reader.read_pending_tasks() == []
        assert reader.read_pending_tasks_for_bot("b", "o", _DT) == []

    def test_read_corrupt_db_returns_empty(self, tmp_path):
        db = tmp_path / "corrupt.db"
        db.write_text("this is definitely not a sqlite database", encoding="utf-8")
        reader = SqliteTaskReader(db)
        assert reader.read_discovered_tasks() == []
        assert reader.read_pending_tasks_for_bot("b", "o", _DT) == []

    def test_invalid_acceptances_json_falls_back_to_empty_list(self, tmp_path):
        db = self._seeded_db(tmp_path, self.SEEDED)
        # Corrupt the acceptances column to exercise _row_to_task's except arm.
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(
                "UPDATE discovered_tasks SET acceptances = 'not-json{{' "
                "WHERE task_id = 't1';"
            )
            conn.commit()
        finally:
            conn.close()
        tasks = SqliteTaskReader(db).read_discovered_tasks()
        t1 = next(t for t in tasks if t.task_id == "t1")
        assert t1.acceptances == []
        assert t1.objective == "o1"
        assert t1.priority == "medium"


class TestMockTaskReader:
    @staticmethod
    def _write(tmp_path, name: str, content) -> Path:
        p = tmp_path / name
        if isinstance(content, str):
            p.write_text(content, encoding="utf-8")
        else:
            p.write_text(json.dumps(content), encoding="utf-8")
        return p

    def test_missing_file_returns_empty(self, tmp_path):
        reader = MockTaskReader(tmp_path / "nope.json")
        assert reader.read_discovered_tasks() == []
        assert reader.read_pending_tasks() == []
        assert reader.read_pending_tasks_for_bot("b", "o", _DT) == []

    def test_invalid_json_returns_empty(self, tmp_path):
        p = self._write(tmp_path, "bad.json", "??? not json")
        assert MockTaskReader(p).read_discovered_tasks() == []

    def test_tasks_not_a_list_returns_empty(self, tmp_path):
        p = self._write(tmp_path, "bad-shape.json", {"tasks": "oops"})
        assert MockTaskReader(p).read_discovered_tasks() == []

    def test_mixed_items_skip_non_dict_and_missing_fields(self, tmp_path):
        payload = {
            "tasks": [
                "not-a-dict",                       # skipped: not a mapping
                {"title": "NoId"},                   # skipped: missing task_id
                {
                    "task_id": "m1", "bot_id": "bot-1", "owner_id": "owner-1",
                    "dt": _DT, "title": "MockTask", "instruction": "mi",
                    "background": "mb", "discovery_basis": "md",
                    "status": "pending_confirmation",
                    "objective": "mo",
                    "acceptances": [{"id": "ma", "description": "md1"}],
                },
                {
                    "task_id": "m2", "bot_id": "bot-1", "owner_id": "owner-1",
                    "dt": _DT, "title": "OldTask", "instruction": "mi2",
                    "background": "mb", "discovery_basis": "md",
                    "status": "ignored",
                },
            ]
        }
        reader = MockTaskReader(self._write(tmp_path, "mixed.json", payload))
        tasks = reader.read_discovered_tasks()
        assert [t.task_id for t in tasks] == ["m1", "m2"]
        # pending filter excludes "ignored"
        assert [t.task_id for t in reader.read_pending_tasks()] == ["m1"]
        # per-bot/per-day filter
        assert [t.task_id for t in reader.read_pending_tasks_for_bot(
            "bot-1", "owner-1", _DT
        )] == ["m1"]
        assert reader.read_pending_tasks_for_bot("bot-1", "o", _DT) == []


# ===========================================================================
# task_reader — ORM upsert existing-row branch / clear / seed / reader
# ===========================================================================

class TestOrmUpsertAndReaders:
    TASK_ROW = {
        "task_id": "t1", "bot_id": "bot-1", "owner_id": "owner-1",
        "dt": _DT, "title": "Task1", "instruction": "i1", "background": "b1",
        "discovery_basis": "d1", "status": "pending_confirmation",
        "objective": "obj1",
        "acceptances": [{"id": "a1", "description": "desc"}],
    }

    def test_upsert_inserts_new_row(self):
        db = _MemDB()
        assert upsert_discovered_tasks(db, [dict(self.TASK_ROW)]) == 1
        tasks = OrmTaskReader(db).read_discovered_tasks()
        assert len(tasks) == 1
        assert tasks[0].title == "Task1"
        assert tasks[0].acceptances == [{"id": "a1", "description": "desc"}]

    def test_upsert_updates_existing_row(self):
        db = _MemDB()
        upsert_discovered_tasks(db, [dict(self.TASK_ROW)])
        updated = {
            "task_id": "t1", "bot_id": "bot-9", "owner_id": "owner-9",
            "dt": "2030-01-01", "title": "NewTitle", "instruction": "ni",
            "background": "nb", "discovery_basis": "nd",
            "priority": "high", "status": "confirmed", "objective": "new-obj",
            "acceptances": [{"id": "x", "description": "y"}],
        }
        assert upsert_discovered_tasks(db, [updated]) == 1
        rows = OrmTaskReader(db).read_discovered_tasks()
        assert len(rows) == 1  # updated in place, not duplicated
        row = rows[0]
        assert row.title == "NewTitle"
        assert row.bot_id == "bot-9"
        assert row.objective == "new-obj"
        assert row.priority == "high"
        assert row.status == "confirmed"
        assert row.acceptances == [{"id": "x", "description": "y"}]

    def test_upsert_existing_without_acceptances_key_keeps_old(self):
        db = _MemDB()
        upsert_discovered_tasks(db, [dict(self.TASK_ROW)])
        upsert_discovered_tasks(db, [{"task_id": "t1", "title": "Renamed"}])
        row = OrmTaskReader(db).read_discovered_tasks()[0]
        assert row.title == "Renamed"
        assert row.acceptances == [{"id": "a1", "description": "desc"}]

    def test_clear_discovered_tasks(self):
        db = _MemDB()
        seed_discovered_tasks(db, [dict(self.TASK_ROW), {
            "task_id": "t2", "bot_id": "bot-2", "owner_id": "owner-2",
            "dt": _DT, "title": "T2",
        }])
        assert clear_discovered_tasks(db) == 2
        assert OrmTaskReader(db).read_discovered_tasks() == []
        assert clear_discovered_tasks(db) == 0

    def test_seed_is_idempotent(self):
        db = _MemDB()
        seed_discovered_tasks(db, [dict(self.TASK_ROW)])
        seed_discovered_tasks(db, [dict(self.TASK_ROW)])
        assert len(OrmTaskReader(db).read_discovered_tasks()) == 1

    def test_orm_reader_pending_and_for_bot(self):
        db = _MemDB()
        seed_discovered_tasks(db, [
            dict(self.TASK_ROW),
            {"task_id": "t2", "bot_id": "bot-2", "owner_id": "owner-1",
             "dt": _DT, "title": "T2", "status": "pending_confirmation"},
            {"task_id": "t3", "bot_id": "bot-1", "owner_id": "owner-1",
             "dt": _DT, "title": "T3", "status": "executing"},
        ])
        reader = OrmTaskReader(db)
        assert {t.task_id for t in reader.read_pending_tasks()} == {"t1", "t2"}
        per_bot = reader.read_pending_tasks_for_bot("bot-1", "owner-1", _DT)
        assert [t.task_id for t in per_bot] == ["t1"]
        # non-pending status excluded by the query itself
        assert {t.task_id for t in reader.read_discovered_tasks()} == {
            "t1", "t2", "t3",
        }


# ===========================================================================
# discovery_service — discover_all_bots edge paths + create_default_service
# ===========================================================================

class TestDiscoverAllBots:
    def _make_service(self, reader, bot_service=None, lock_repo=None,
                      initiator=None, notify=None):
        return DiscoveryService(
            reader=reader,
            session_initiator=initiator if initiator is not None else AsyncMock(),
            notify_sender=notify if notify is not None else MagicMock(),
            bot_service=bot_service,
            discovery_lock_repo=lock_repo,
        )

    def test_no_pending_tasks_returns_empty(self):
        reader = MagicMock()
        reader.read_pending_tasks.return_value = []
        svc = self._make_service(reader, bot_service=MagicMock())
        assert asyncio.run(svc.discover_all_bots()) == []

    def test_without_bot_service_returns_empty(self):
        reader = MagicMock()
        reader.read_pending_tasks.return_value = [_task()]
        svc = self._make_service(reader, bot_service=None)
        assert asyncio.run(svc.discover_all_bots()) == []

    def test_list_bots_failure_returns_empty(self):
        reader = MagicMock()
        reader.read_pending_tasks.return_value = [_task(bot_id="bot-a")]
        bot_service = MagicMock()
        bot_service.list_bots.side_effect = RuntimeError("bot db down")
        svc = self._make_service(reader, bot_service=bot_service)
        assert asyncio.run(svc.discover_all_bots()) == []

    def test_lock_not_acquired_skips_bot(self):
        reader = MagicMock()
        reader.read_pending_tasks.return_value = [
            _task("t-a", bot_id="bot-a", owner_id="owner-1"),
        ]
        bot_service = MagicMock()
        bot_service.list_bots.return_value = {
            "items": [{"bot_id": "bot-a", "owner_id": "owner-1"}],
        }
        lock_repo = MagicMock()
        lock_repo.acquire.return_value = None  # another instance holds it
        lock_repo.get_if_stale.return_value = None  # and it is not stale
        svc = self._make_service(reader, bot_service=bot_service,
                                 lock_repo=lock_repo)
        results = asyncio.run(svc.discover_all_bots())
        assert results == []
        lock_repo.release.assert_not_called()

    def test_lock_acquired_discover_success_releases(self):
        task = _task("t-a", bot_id="bot-a", owner_id="owner-1")
        reader = MagicMock()
        reader.read_pending_tasks.return_value = [task]
        reader.read_pending_tasks_for_bot.return_value = [task]
        bot_service = MagicMock()
        bot_service.list_bots.return_value = {
            "items": [{"bot_id": "bot-a", "owner_id": "owner-1"}],
        }
        lock = TaskDiscoveryLockRecord(
            env="dev", bot_id="bot-a", discovery_date=_DT, lock_token="tok-1",
        )
        lock_repo = MagicMock()
        lock_repo.acquire.return_value = lock
        initiator = AsyncMock()
        initiator.initiate_session.return_value = _session("sess-a")
        notify = MagicMock()
        notify.send.return_value = "msg-1"
        svc = self._make_service(reader, bot_service=bot_service,
                                 lock_repo=lock_repo, initiator=initiator,
                                 notify=notify)
        results = asyncio.run(svc.discover_all_bots())
        assert len(results) == 1
        assert results[0].success
        assert results[0].notification_sent is True
        initiator.initiate_session.assert_awaited_once()
        lock_repo.release.assert_called_once_with(
            "dev", "bot-a", _DT, "tok-1"
        )

    def test_lock_acquired_discover_failure_still_releases(self):
        task = _task("t-b", bot_id="bot-b", owner_id="owner-2")
        reader = MagicMock()
        reader.read_pending_tasks.return_value = [task]
        # discover() raises while reading tasks for this bot
        reader.read_pending_tasks_for_bot.side_effect = RuntimeError("boom")
        bot_service = MagicMock()
        bot_service.list_bots.return_value = {
            "items": [{"bot_id": "bot-b", "owner_id": "owner-2"}],
        }
        lock = TaskDiscoveryLockRecord(
            env="dev", bot_id="bot-b", discovery_date=_DT, lock_token="tok-2",
        )
        lock_repo = MagicMock()
        lock_repo.acquire.return_value = lock
        svc = self._make_service(reader, bot_service=bot_service,
                                 lock_repo=lock_repo)
        results = asyncio.run(svc.discover_all_bots())
        assert results == []
        # per-bot failure does not strand the daily lock
        lock_repo.release.assert_called_once_with(
            "dev", "bot-b", _DT, "tok-2"
        )


class TestCreateDefaultService:
    def test_builds_service_with_fail_closed_initiator(self, tmp_path):
        notify = MagicMock()
        session_creator = HttpSessionCreator()
        svc = create_default_service(
            data_file=str(tmp_path / "discovered.db"),
            notify_sender=notify,
            session_creator=session_creator,
        )
        assert isinstance(svc, DiscoveryService)
        assert isinstance(svc._reader, SqliteTaskReader)
        assert isinstance(svc._session_initiator, UnavailableSessionInitiator)
        assert svc._notify_sender is notify


# ===========================================================================
# scheduler.py — _run_discovery / reschedule / get_status / DreamMode hooks
# ===========================================================================

class TestSchedulerRunDiscovery:
    def test_run_discovery_invokes_async_discover(self):
        svc = MagicMock()
        svc.discover_all_bots = AsyncMock(return_value=[])
        sched = TaskDiscoveryScheduler(discovery_service=svc)
        sched._run_discovery()  # runs asyncio.run internally, must not raise
        svc.discover_all_bots.assert_awaited_once()

    def test_run_discovery_swallows_failure(self):
        svc = MagicMock()
        svc.discover_all_bots = AsyncMock(side_effect=RuntimeError("boom"))
        sched = TaskDiscoveryScheduler(discovery_service=svc)
        sched._run_discovery()  # exception logged, not re-raised
        svc.discover_all_bots.assert_awaited_once()


class TestSchedulerLifecycleAndStatus:
    def test_startup_disabled(self, monkeypatch):
        monkeypatch.setenv("TASK_DISCOVERY_AUTO_START", "false")
        sched = TaskDiscoveryScheduler(discovery_service=MagicMock())
        asyncio.run(sched.startup())
        assert sched._scheduler is None

    def test_startup_status_reschedule_shutdown(self, monkeypatch):
        monkeypatch.setenv("TASK_DISCOVERY_AUTO_START", "true")
        monkeypatch.setenv("TASK_DISCOVERY_CRON", "0 11 * * *")
        monkeypatch.setenv("TASK_DISCOVERY_TIMEZONE", "Asia/Shanghai")
        sched = TaskDiscoveryScheduler(discovery_service=MagicMock())
        asyncio.run(sched.startup())
        try:
            status = sched.get_status()
            assert status["running"] is True
            assert status["cron"] == "0 11 * * *"
            assert status["timezone"] == "Asia/Shanghai"
            assert status["auto_start"] == "true"
            jobs = status["jobs"]
            assert len(jobs) == 1
            assert jobs[0]["id"] == "task_discovery_daily"
            assert jobs[0]["next_run_time"] is not None
            assert jobs[0]["timezone"] is not None

            assert sched.reschedule("30 14 * * *") is True
            assert os.environ["TASK_DISCOVERY_CRON"] == "30 14 * * *"
            # reschedule applies to the live job
            status = sched.get_status()
            assert status["running"] is True
        finally:
            asyncio.run(sched.shutdown())
        # scheduler stopped → reschedule refuses, status reports not-running
        assert sched.reschedule("0 12 * * *") is False
        status = sched.get_status()
        assert status["running"] is False
        assert status["jobs"] == []

    def test_reschedule_before_any_scheduler_returns_false(self):
        sched = TaskDiscoveryScheduler(discovery_service=MagicMock())
        assert sched.reschedule("0 12 * * *") is False
        assert sched.get_status() == {
            "running": False,
            "jobs": [],
            "auto_start": os.environ.get("TASK_DISCOVERY_AUTO_START", "true"),
            "cron": os.environ.get("TASK_DISCOVERY_CRON", "0 11 * * *"),
            "timezone": os.environ.get("TASK_DISCOVERY_TIMEZONE", "Asia/Shanghai"),
        }

    def test_enable_then_disable_dream_mode(self, monkeypatch):
        monkeypatch.setenv("TASK_DISCOVERY_CRON", "0 11 * * *")
        monkeypatch.setenv("TASK_DISCOVERY_TIMEZONE", "Asia/Shanghai")
        sched = TaskDiscoveryScheduler(discovery_service=MagicMock())
        try:
            # first enable: boots the global scheduler
            sched.enable_for_bot("bot-1", "owner-1")
            assert sched._scheduler is not None
            assert sched._scheduler.running is True
            assert any(
                job.id == "task_discovery_daily"
                for job in sched._scheduler.get_jobs()
            )
            status = sched.get_status()
            assert status["running"] is True
            # second enable: idempotent — already running
            sched.enable_for_bot("bot-2", "owner-2")
            assert sched._scheduler.running is True
        finally:
            sched.disable_for_bot("bot-1", "owner-1")
        assert sched._scheduler is None
        # disable when already stopped: no-op
        sched.disable_for_bot("bot-1", "owner-1")
        assert sched._scheduler is None


# ===========================================================================
# lifecycle.py — TaskDiscoveryLifecycle
# ===========================================================================

def _make_lifecycle(bot_service=None) -> TaskDiscoveryLifecycle:
    return TaskDiscoveryLifecycle(
        bot_service=bot_service if bot_service is not None else _FakeBotService(),
        notify_sender=MagicMock(),
    )


class TestLifecycleStartupShutdown:
    def test_startup_disabled_creates_no_task(self, monkeypatch):
        monkeypatch.setenv("TASK_DISCOVERY_AUTO_START", "false")
        lc = _make_lifecycle()
        asyncio.run(lc.startup())
        assert lc._task is None

    def test_startup_creates_daily_task_then_shutdown_cancels(self, monkeypatch):
        monkeypatch.setenv("TASK_DISCOVERY_AUTO_START", "true")
        monkeypatch.setenv("TASK_DISCOVERY_SCHEDULE_HOUR", "11")
        monkeypatch.setenv("TASK_DISCOVERY_SCHEDULE_MINUTE", "0")
        lc = _make_lifecycle()

        async def _pair():
            await lc.startup()
            assert lc._task is not None
            await lc.shutdown()

        asyncio.run(_pair())
        assert lc._task.done()

    def test_shutdown_without_task_is_noop(self):
        lc = _make_lifecycle()
        asyncio.run(lc.shutdown())
        assert lc._task is None


class TestLifecycleDailySchedule:
    def test_seconds_until_future_same_day(self):
        lc = _make_lifecycle()

        class _FixedDatetime(_dt_module.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 23, 10, 0, 0)

        with patch.object(lifecycle_mod, "datetime", _FixedDatetime):
            assert lc._seconds_until(11, 0) == 3600.0
            assert lc._seconds_until(10, 0) == 86400.0  # exactly now → tomorrow
            assert lc._seconds_until(9, 30) == 86400.0 - 1800.0

    def test_run_daily_schedule_sleeps_then_discovers(self):
        lc = _make_lifecycle(_FakeBotService(items=[]))
        sleeps: list[float] = []

        class _Bail(Exception):
            pass

        async def fake_sleep(delay: float):
            sleeps.append(delay)
            if len(sleeps) == 2:
                raise _Bail()

        with patch("asyncio.sleep", fake_sleep):
            with pytest.raises(_Bail):
                asyncio.run(lc._run_daily_schedule(11, 0))
        # First delay points at 11:00, then one discovery pass ran
        # (_discover_once → _list_all_bots → no bots → return) before the
        # loop recomputed the next delay.
        assert len(sleeps) == 2
        assert all(d > 0 for d in sleeps)


class TestLifecycleDiscoverOnce:
    def test_discover_once_with_no_bots_does_nothing(self):
        bot_service = _FakeBotService(items=[])
        lc = _make_lifecycle(bot_service)
        asyncio.run(lc._discover_once())
        assert bot_service.calls == [{"page": 1, "page_size": 100}]

    def test_list_all_bots_swallows_service_failure(self):
        lc = _make_lifecycle(_FakeBotService(exc=RuntimeError("bot db down")))
        assert lc._list_all_bots() == []

    def test_discover_once_skips_bots_with_missing_ids(self, monkeypatch):
        factory = MagicMock()
        monkeypatch.setattr(lifecycle_mod, "create_default_service", factory)
        lc = _make_lifecycle(_FakeBotService(items=[
            {"owner_id": "u1"},            # no bot_id
            {"bot_id": "b1"},              # no owner_id
        ]))
        asyncio.run(lc._discover_once())
        factory.assert_not_called()

    def test_discover_once_handles_success_failure_and_exception(
        self, monkeypatch,
    ):
        ok_result = DiscoveryResult(
            task=_task("t-ok"), session=_session("sess-ok"),
            notification_sent=True,
        )
        failed_result = DiscoveryResult(task=_task("t-bad"), error="boom")
        service = MagicMock()
        # bot-a: returns success+failure results; bot-b: discover raises
        service.discover = AsyncMock(
            side_effect=[[ok_result, failed_result], RuntimeError("db down")]
        )
        factory = MagicMock(return_value=service)
        monkeypatch.setattr(lifecycle_mod, "create_default_service", factory)

        notify = MagicMock()
        lc = TaskDiscoveryLifecycle(
            bot_service=_FakeBotService(items=[
                {"bot_id": "bot-a", "owner_id": "u1"},
                {"bot_id": "bot-b", "owner_id": "u2"},
            ]),
            notify_sender=notify,
        )
        # The bot-b failure is contained: the whole call must return normally.
        asyncio.run(lc._discover_once())

        assert factory.call_count == 2
        first_kwargs = factory.call_args_list[0].kwargs
        assert first_kwargs["notify_sender"] is notify
        assert "data_file" in first_kwargs
        assert isinstance(first_kwargs["session_creator"], HttpSessionCreator)
        assert service.discover.await_count == 2
        first_call = service.discover.await_args_list[0].kwargs
        assert first_call == {
            "user_id": "u1", "agent_id": "bot-a",
            "bot_id": "bot-a", "owner_id": "u1",
        }


class TestLifecycleDataFile:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        lc = _make_lifecycle()
        custom = str(tmp_path / "custom.db")
        monkeypatch.setenv("TASK_DISCOVERY_DATA_FILE", custom)
        assert lc._resolve_data_file() == custom

    def test_default_points_at_mock_data_file(self, monkeypatch):
        monkeypatch.delenv("TASK_DISCOVERY_DATA_FILE", raising=False)
        lc = _make_lifecycle()
        path = lc._resolve_data_file()
        assert path.endswith(
            os.path.join("scripts", ".dependencies", "data",
                         "discovered_tasks.db")
        )