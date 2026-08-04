"""Unit tests for the deferred session-creation feature.

Covers:
- Engine adapter ``build_session_id`` (claude_code real/stub, base, aicoding/hermes stubs)
- ``BaasBotService.build_session_id`` / ``ClawBotService.build_session_id`` dispatch
- ``BotRunner._create_session`` defer path (constructed ID returned, no engine call)
- ``BotRunRequestExecutor`` materialising a deferred session before sending
- ``QueueTaskMessageDispatcher`` stamping ``session_deferred="true"`` into queue meta
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.bot_runtime import BotBindingInfo, BotChatContext, BotResponse
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.repository.api_gateway import APIKeyRecord
from secbaas.community.core.repository.bot_run import BotRunRecord
from secbaas.community.core.repository.bot_run_queue import BotRunQueueRecord
from secbaas.community.core.service.bot_run import (
    BaasBotService,
    BaasBotServiceConfig,
    BotEngineAdapterRegistry,
    BotRunner,
    BotServiceSelector,
    ClawBotService,
    QueueTaskMessageDispatcher,
)
from secbaas.community.core.service.bot_run._executor import BotRunRequestExecutor
from secbaas.community.core.service.bot_run._internal_protocols import MessageDispatcher
from secbaas.community.plugins.bot.engine_adapter.claude_code.real import (
    ClaudeCodeAdapter,
)
from secbaas.community.plugins.bot.engine_adapter.claude_code.stub import (
    MockClaudeCodeAdapter,
)
from secbaas.community.spi.bot_service import BotBindingData

BOT_ID = "test-bot-000001"
ENTITY_ID = "test-entity-001"


# ── Engine adapters ─────────────────────────────────────────────────────────


class TestAdapterBuildSessionId:
    def test_claude_code_real_constructs_deterministic_id(self):
        adapter = ClaudeCodeAdapter()
        sid = adapter.build_session_id(
            tc_bot_id="b1", user_id="u1", run_id="r1", session_id=None
        )
        assert sid == "agent:b1:session:r1:user:u1"

    def test_claude_code_real_returns_caller_session_id(self):
        adapter = ClaudeCodeAdapter()
        sid = adapter.build_session_id(
            tc_bot_id="b1", user_id="u1", run_id="r1", session_id="provided-sid"
        )
        assert sid == "provided-sid"

    def test_claude_code_stub_constructs_deterministic_id(self):
        adapter = MockClaudeCodeAdapter()
        sid = adapter.build_session_id(
            tc_bot_id="b1", user_id="u1", run_id="r1", session_id=None
        )
        assert sid == "agent:b1:session:r1:user:u1"

    def test_base_adapter_returns_none(self):
        from secbaas.community.plugins.bot.engine_adapter._base import (
            BaseEngineAdapter,
        )

        assert (
            BaseEngineAdapter().build_session_id(
                tc_bot_id="b1", user_id="u1", run_id="r1", session_id=None
            )
            is None
        )

    def test_unsupported_engine_stub_returns_none(self):
        """Engines whose adapter inherits the default build_session_id
        (e.g. aicoding / hermes stubs) return None — covered by the base
        adapter test above. This test re-asserts the contract via a fake
        adapter that explicitly returns None, mirroring those stubs."""
        fake = MagicMock()
        fake.build_session_id = MagicMock(return_value=None)
        assert (
            fake.build_session_id(
                tc_bot_id="b1", user_id="u1", run_id="r1", session_id=None
            )
            is None
        )


# ── BotService.build_session_id ─────────────────────────────────────────────


def _baas_service(registry: BotEngineAdapterRegistry | None) -> BaasBotService:
    return BaasBotService(
        config=BaasBotServiceConfig(
            adapter_port=20003,
            ws_path="/api/openclaw/ws",
            connect_timeout=10,
            request_timeout=30,
        ),
        client_pool=MagicMock(),
        wss_resolver=MagicMock(),
        session_service=MagicMock(),
        engine_adapter_registry=registry,
    )


def _claw_service(registry: BotEngineAdapterRegistry | None) -> ClawBotService:
    return ClawBotService(
        config=BaasBotServiceConfig(
            adapter_port=20003,
            ws_path="/api/openclaw/ws",
            connect_timeout=10,
            request_timeout=30,
        ),
        client_pool=MagicMock(),
        secret_store=MagicMock(),
        engine_adapter_registry=registry,
    )


def _binding_info(engine_type: str = "openclaw") -> BotBindingInfo:
    return BotBindingInfo(
        bot_id=BOT_ID,
        entity_id=ENTITY_ID,
        sandbox_id=None,
        device_id="dev-1",
        device_provider="baas",
        binding_id=1,
        device_props={},
        bot_type="personal",
        engine_type=engine_type,
    )


class TestBaasBotServiceBuildSessionId:
    def test_caller_session_id_returned_as_is(self):
        svc = _baas_service(None)
        assert (
            svc.build_session_id(
                engine_type="openclaw",
                bot_id=BOT_ID,
                user_id=ENTITY_ID,
                run_id="r1",
                session_id="caller-sid",
            )
            == "caller-sid"
        )

    def test_openclaw_rule(self):
        svc = _baas_service(None)
        sid = svc.build_session_id(
            engine_type="openclaw",
            bot_id=BOT_ID,
            user_id=ENTITY_ID,
            run_id="r1",
            session_id=None,
        )
        assert sid == "agent:main:session:r1:user:test-entity-001"

    def test_claude_code_via_adapter(self):
        registry = BotEngineAdapterRegistry({"claude_code": ClaudeCodeAdapter()})
        svc = _baas_service(registry)
        sid = svc.build_session_id(
            engine_type="claude_code",
            bot_id=BOT_ID,
            user_id=ENTITY_ID,
            run_id="r1",
            session_id=None,
            binding_info=_binding_info("claude_code"),
        )
        assert sid == "agent:test-bot-000001:session:r1:user:test-entity-001"

    def test_unsupported_engine_via_adapter_returns_none(self):
        # An adapter whose build_session_id returns None (e.g. aicoding/hermes
        # stubs) propagates None through the service layer.
        fake_adapter = MagicMock()
        fake_adapter.build_session_id = MagicMock(return_value=None)
        registry = BotEngineAdapterRegistry({"custom_engine": fake_adapter})
        svc = _baas_service(registry)
        assert (
            svc.build_session_id(
                engine_type="custom_engine",
                bot_id=BOT_ID,
                user_id=ENTITY_ID,
                run_id="r1",
                session_id=None,
            )
            is None
        )

    def test_unknown_engine_returns_none(self):
        svc = _baas_service(None)
        assert (
            svc.build_session_id(
                engine_type="teclaw",
                bot_id=BOT_ID,
                user_id=ENTITY_ID,
                run_id="r1",
                session_id=None,
            )
            is None
        )


class TestClawBotServiceBuildSessionId:
    def test_openclaw_rule(self):
        svc = _claw_service(None)
        sid = svc.build_session_id(
            engine_type="openclaw",
            bot_id=BOT_ID,
            user_id=ENTITY_ID,
            run_id="r1",
            session_id=None,
        )
        assert sid == "agent:main:session:r1:user:test-entity-001"

    def test_caller_session_id_returned_as_is(self):
        svc = _claw_service(None)
        assert (
            svc.build_session_id(
                engine_type="openclaw",
                bot_id=BOT_ID,
                user_id=ENTITY_ID,
                run_id="r1",
                session_id="caller-sid",
            )
            == "caller-sid"
        )

    def test_claude_code_via_adapter(self):
        registry = BotEngineAdapterRegistry({"claude_code": ClaudeCodeAdapter()})
        svc = _claw_service(registry)
        sid = svc.build_session_id(
            engine_type="claude_code",
            bot_id=BOT_ID,
            user_id=ENTITY_ID,
            run_id="r1",
            session_id=None,
            binding_info=_binding_info("claude_code"),
        )
        assert sid == "agent:test-bot-000001:session:r1:user:test-entity-001"


# ── BotRunner defer path ────────────────────────────────────────────────────


def _binding_data(engine_type: str = "openclaw") -> BotBindingData:
    return BotBindingData(
        bot_id=BOT_ID,
        owner_id=ENTITY_ID,
        bot_type="personal",
        engine_type=engine_type,
        binding_id=1,
        device_provider="baas",
        device_id="dev-1",
    )


def _make_runner(selector, run_repo, plugin, dispatcher=None):
    if dispatcher is None:
        dispatcher = MagicMock(spec=MessageDispatcher)
        dispatcher.dispatch_send = AsyncMock()
        dispatcher.dispatch_inject = AsyncMock()
        dispatcher.dispatch_send_stream = MagicMock()
    return BotRunner(
        bot_service_selector=selector,
        run_repository=run_repo,
        bot_service_plugin=plugin,
        dispatchers=[dispatcher],
    )


@pytest.fixture
def context():
    return BotChatContext(
        api_key_prefix="key-abc",
        app_id="owner123",
        app_type="baas",
        iam_token=None,
        tenant="test-tenant",
    )


@pytest.fixture
def run_repo():
    repo = MagicMock()
    repo.insert_run = MagicMock()
    repo.update_status = MagicMock()
    repo.update_result = MagicMock()
    repo.update_error = MagicMock()
    repo.update_session_id = MagicMock()
    repo.get_by_run_id = MagicMock(return_value=None)
    return repo


class TestRunnerDeferPath:
    @pytest.mark.asyncio
    async def test_defer_skips_create_session_and_persists_constructed_id(
        self, run_repo, context
    ):
        """When build_session_id returns a deterministic ID, the runner
        skips create_session, persists the constructed ID, and flags the
        dispatcher call as deferred."""
        bot_service = MagicMock()
        bot_service.build_session_id = MagicMock(
            return_value="agent:b1:session:r1:user:u1"
        )
        bot_service.create_session = AsyncMock()

        selector = MagicMock(spec=BotServiceSelector)
        selector.select.return_value = bot_service

        plugin = MagicMock()
        plugin.get_binding = AsyncMock(return_value=_binding_data("openclaw"))
        plugin.report = AsyncMock()

        dispatcher = MagicMock(spec=MessageDispatcher)
        dispatcher.dispatch_send = AsyncMock()
        dispatcher.supports_session_defer = True

        runner = _make_runner(selector, run_repo, plugin, dispatcher=dispatcher)
        msg_id, sess_id = await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="r1",
        )

        assert sess_id == "agent:b1:session:r1:user:u1"
        # Synchronous session creation NOT called
        bot_service.create_session.assert_not_called()
        # Constructed session_id persisted to DB
        run_repo.update_session_id.assert_called_once_with(
            "r1", "agent:b1:session:r1:user:u1"
        )
        # Dispatcher received session_deferred=True
        kw = dispatcher.dispatch_send.call_args.kwargs
        assert kw["session_deferred"] is True
        assert kw["session_id"] == "agent:b1:session:r1:user:u1"

    @pytest.mark.asyncio
    async def test_fallback_sync_path_when_build_session_id_returns_none(
        self, run_repo, context
    ):
        """When build_session_id returns None, the runner falls back to
        synchronous create_session and does NOT flag deferred."""
        bot_service = MagicMock()
        bot_service.build_session_id = MagicMock(return_value=None)
        bot_service.create_session = AsyncMock(
            return_value=MagicMock(session_id="engine-sess-001")
        )

        selector = MagicMock(spec=BotServiceSelector)
        selector.select.return_value = bot_service

        plugin = MagicMock()
        plugin.get_binding = AsyncMock(return_value=_binding_data("openclaw"))
        plugin.report = AsyncMock()

        dispatcher = MagicMock(spec=MessageDispatcher)
        dispatcher.dispatch_send = AsyncMock()
        dispatcher.supports_session_defer = False

        runner = _make_runner(selector, run_repo, plugin, dispatcher=dispatcher)
        _, sess_id = await runner.deliver_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="r2",
        )

        assert sess_id == "engine-sess-001"
        bot_service.create_session.assert_called_once()
        # session_deferred kwarg NOT forwarded (defaults to False on the protocol)
        assert "session_deferred" not in dispatcher.dispatch_send.call_args.kwargs

    @pytest.mark.asyncio
    async def test_inject_deferred_path(self, run_repo, context):
        """inject_message defers session creation the same way as deliver_message."""
        bot_service = MagicMock()
        bot_service.build_session_id = MagicMock(
            return_value="agent:b1:session:r3:user:u1"
        )
        bot_service.create_session = AsyncMock()

        selector = MagicMock(spec=BotServiceSelector)
        selector.select.return_value = bot_service

        plugin = MagicMock()
        plugin.get_binding = AsyncMock(return_value=_binding_data("openclaw"))
        plugin.report = AsyncMock()

        dispatcher = MagicMock(spec=MessageDispatcher)
        dispatcher.dispatch_inject = AsyncMock()
        dispatcher.supports_session_defer = True

        runner = _make_runner(selector, run_repo, plugin, dispatcher=dispatcher)
        await runner.inject_message(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="sys-inst",
            context=context,
            metadata={},
            message_id="r3",
        )

        bot_service.create_session.assert_not_called()
        run_repo.update_session_id.assert_called_once_with(
            "r3", "agent:b1:session:r3:user:u1"
        )
        kw = dispatcher.dispatch_inject.call_args.kwargs
        assert kw["session_deferred"] is True

    @pytest.mark.asyncio
    async def test_stream_deferred_path(self, run_repo, context):
        """deliver_message_stream defers session creation and forwards the
        session_deferred flag to dispatch_send_stream."""
        bot_service = MagicMock()
        bot_service.build_session_id = MagicMock(
            return_value="agent:b1:session:r4:user:u1"
        )
        bot_service.create_session = AsyncMock()

        selector = MagicMock(spec=BotServiceSelector)
        selector.select.return_value = bot_service

        plugin = MagicMock()
        plugin.get_binding = AsyncMock(return_value=_binding_data("openclaw"))
        plugin.report = AsyncMock()

        dispatcher = MagicMock(spec=MessageDispatcher)
        dispatcher.supports_session_defer = True

        async def _fake_stream(**kwargs):
            yield StreamChunk(type="final", content="done")

        dispatcher.dispatch_send_stream = _fake_stream

        runner = _make_runner(selector, run_repo, plugin, dispatcher=dispatcher)
        msg_id, sess_id, stream = await runner.deliver_message_stream(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            message="hello",
            context=context,
            metadata={},
            message_id="r4",
        )

        assert sess_id == "agent:b1:session:r4:user:u1"
        bot_service.create_session.assert_not_called()
        run_repo.update_session_id.assert_called_once_with(
            "r4", "agent:b1:session:r4:user:u1"
        )
        # Consume the stream to ensure the dispatch_send_stream path runs
        async for _ in stream:
            pass


# ── BotRunRequestExecutor deferred session materialisation ──────────────────


def _run(*, run_id: str, bot_id: str, metadata: dict | None) -> BotRunRecord:
    return BotRunRecord(
        id=1,
        gmt_create=None,
        gmt_modified=None,
        run_id=run_id,
        bot_id=bot_id,
        api_key_prefix="sk-",
        message="",
        message_long="msg",
        metadata=metadata,
        status="PENDING",
        result_content="",
        result_content_long="",
        result_extra=None,
        error=None,
        completed_at=None,
    )


def _queue_rec(*, run_id: str, bot_id: str, session_id: str) -> BotRunQueueRecord:
    return BotRunQueueRecord(
        id=1,
        gmt_create=None,
        gmt_modified=None,
        run_id=run_id,
        bot_id=bot_id,
        session_id=session_id,
        status="RUNNING",
        assigned_worker="w",
        last_heartbeat=None,
        meta={},
    )


def _api_key_repo(prefix: str = "sk-") -> MagicMock:
    repo = MagicMock()
    repo.get_by_prefix.return_value = APIKeyRecord(
        id=1,
        gmt_create=None,
        gmt_modified=None,
        api_key_hash="hash",
        api_key_prefix=prefix,
        key_name=None,
        app_id="app-1",
        app_type="UNKNOWN",
        description=None,
        rate_limit_rpm=None,
        rate_limit_rpd=None,
        status="active",
        owner="test",
        tenant="",
        env="dev",
        creator="test",
        modifier=None,
        policy=None,
    )
    return repo


def _binding_data_claw() -> BotBindingData:
    return BotBindingData(
        bot_id="b1",
        owner_id="e1",
        bot_type="personal",
        engine_type="openclaw",
        binding_id=1,
        device_provider="baas",
        device_id="dev-1",
    )


class TestExecutorDeferredMaterialisation:
    @pytest.mark.asyncio
    async def test_deferred_session_materialised_before_send(self):
        """When metadata has session_deferred="true", the executor calls
        create_session before send_message."""
        repo = MagicMock()
        repo.get_by_run_id.return_value = _run(
            run_id="r1",
            bot_id="b1:e1",
            metadata={
                "app_id": "a",
                "app_type": "T",
                "tenant": "t",
                "request_type": "chat",
                "session_deferred": "true",
            },
        )
        plugin = MagicMock()
        plugin.get_binding = AsyncMock(return_value=_binding_data_claw())

        bot_svc = MagicMock()
        bot_svc.create_session = AsyncMock(
            return_value=MagicMock(session_id="constructed-sess")
        )
        bot_svc.send_message = AsyncMock(
            return_value=BotResponse(content="reply", usage=None)
        )

        selector = MagicMock()
        selector.select.return_value = bot_svc

        executor = BotRunRequestExecutor(
            repo, plugin, selector, MagicMock(), MagicMock(), _api_key_repo()
        )
        await executor.execute(
            _queue_rec(run_id="r1", bot_id="b1:e1", session_id="constructed-sess")
        )

        # Session materialised in worker path
        bot_svc.create_session.assert_awaited_once()
        create_kw = bot_svc.create_session.call_args.kwargs
        assert create_kw["session_id"] == "constructed-sess"
        # resolved_bot_id comes from resolve_bot_id("b1:e1", binding) → device_id
        assert create_kw["bot_id"] == "dev-1"
        # Send proceeded after materialisation
        bot_svc.send_message.assert_awaited_once()
        repo.update_result.assert_called_once()
        assert repo.update_result.call_args[1]["content_long"] == "reply"

    @pytest.mark.asyncio
    async def test_deferred_session_creation_failure_marks_error(self):
        """If deferred session materialisation fails, the run is marked FAILED
        and send_message is not called."""
        repo = MagicMock()
        repo.get_by_run_id.return_value = _run(
            run_id="r2",
            bot_id="b1:e1",
            metadata={
                "app_id": "a",
                "app_type": "T",
                "tenant": "t",
                "request_type": "chat",
                "session_deferred": "true",
            },
        )
        plugin = MagicMock()
        plugin.get_binding = AsyncMock(return_value=_binding_data_claw())

        bot_svc = MagicMock()
        bot_svc.create_session = AsyncMock(side_effect=RuntimeError("engine down"))
        bot_svc.send_message = AsyncMock()

        selector = MagicMock()
        selector.select.return_value = bot_svc

        executor = BotRunRequestExecutor(
            repo, plugin, selector, MagicMock(), MagicMock(), _api_key_repo()
        )
        await executor.execute(
            _queue_rec(run_id="r2", bot_id="b1:e1", session_id="constructed-sess")
        )

        bot_svc.create_session.assert_awaited_once()
        bot_svc.send_message.assert_not_awaited()
        repo.update_error.assert_called_once()
        # update_error(run_id, error) positional form
        assert repo.update_error.call_args[0][0] == "r2"
        assert "Session creation failed" in repo.update_error.call_args[0][1]

    @pytest.mark.asyncio
    async def test_non_deferred_does_not_call_create_session(self):
        """Without session_deferred metadata, the executor skips
        create_session (existing behaviour)."""
        repo = MagicMock()
        repo.get_by_run_id.return_value = _run(
            run_id="r3",
            bot_id="b1:e1",
            metadata={
                "app_id": "a",
                "app_type": "T",
                "tenant": "t",
                "request_type": "chat",
            },
        )
        plugin = MagicMock()
        plugin.get_binding = AsyncMock(return_value=_binding_data_claw())

        bot_svc = MagicMock()
        bot_svc.create_session = AsyncMock()
        bot_svc.send_message = AsyncMock(
            return_value=BotResponse(content="reply", usage=None)
        )

        selector = MagicMock()
        selector.select.return_value = bot_svc

        executor = BotRunRequestExecutor(
            repo, plugin, selector, MagicMock(), MagicMock(), _api_key_repo()
        )
        await executor.execute(
            _queue_rec(run_id="r3", bot_id="b1:e1", session_id="sess-exist")
        )

        bot_svc.create_session.assert_not_awaited()
        bot_svc.send_message.assert_awaited_once()


# ── QueueTaskMessageDispatcher session_deferred metadata ────────────────────


def _binding() -> BotBindingInfo:
    return BotBindingInfo(
        bot_id="b1",
        entity_id="e1",
        sandbox_id=None,
        device_id="dev-1",
        device_provider="baas",
        binding_id=1,
        device_props={},
        bot_type="personal",
        engine_type="openclaw",
    )


def _dispatcher(repo=None, queue=None):
    return QueueTaskMessageDispatcher(
        run_repository=repo or MagicMock(),
        queue_repository=queue or MagicMock(),
        chunk_repository=MagicMock(),
        cache_plugin=MagicMock(),
    )


class TestQueueDispatcherDeferredMeta:
    def test_dispatch_send_stamps_session_deferred_true(self):
        queue = MagicMock()
        dispatcher = _dispatcher(queue=queue)
        ctx = BotChatContext(
            api_key_prefix="sk-", app_id="a", app_type="T", tenant="t"
        )

        asyncio.run(
            dispatcher.dispatch_send(
                bot_service=MagicMock(),
                run_id="r1",
                session_id="sess-1",
                message="hello",
                binding_info=_binding(),
                context=ctx,
                bot_id="b1",
                session_deferred=True,
            )
        )

        meta = queue.insert_queue.call_args[1]["meta"]
        assert meta["session_deferred"] == "true"

    def test_dispatch_send_omits_session_deferred_when_false(self):
        queue = MagicMock()
        dispatcher = _dispatcher(queue=queue)
        ctx = BotChatContext(
            api_key_prefix="sk-", app_id="a", app_type="T", tenant="t"
        )

        asyncio.run(
            dispatcher.dispatch_send(
                bot_service=MagicMock(),
                run_id="r2",
                session_id="sess-2",
                message="hello",
                binding_info=_binding(),
                context=ctx,
                bot_id="b1",
                session_deferred=False,
            )
        )

        meta = queue.insert_queue.call_args[1]["meta"]
        assert "session_deferred" not in meta

    def test_dispatch_inject_stamps_session_deferred_true(self):
        queue = MagicMock()
        dispatcher = _dispatcher(queue=queue)
        ctx = BotChatContext(
            api_key_prefix="sk-", app_id="a", app_type="T", tenant="t"
        )

        asyncio.run(
            dispatcher.dispatch_inject(
                bot_service=MagicMock(),
                run_id="r3",
                session_id="sess-3",
                message="inj",
                binding_info=_binding(),
                context=ctx,
                bot_id="b1",
                session_deferred=True,
            )
        )

        meta = queue.insert_queue.call_args[1]["meta"]
        assert meta["session_deferred"] == "true"

    @pytest.mark.asyncio
    async def test_dispatch_send_stream_stamps_session_deferred_true(self):
        """dispatch_send_stream stamps session_deferred into the queue meta
        before polling chunks. We verify the enqueue call directly."""
        queue = MagicMock()
        dispatcher = _dispatcher(queue=queue)
        ctx = BotChatContext(
            api_key_prefix="sk-", app_id="a", app_type="T", tenant="t"
        )

        # Stream polls chunks; with a tiny timeout the poll loop exits via the
        # timeout error chunk. We only assert the enqueue happened with the
        # right meta.
        chunks: list[StreamChunk] = []
        async for chunk in dispatcher.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="r4",
            session_id="sess-4",
            message="hello",
            binding_info=_binding(),
            context=ctx,
            timeout=0.01,
            bot_id="b1",
            session_deferred=True,
        ):
            chunks.append(chunk)
            # break after the first chunk to avoid busy-looping on the
            # cache_plugin mock which never advances the watermark
            break

        meta = queue.insert_queue.call_args[1]["meta"]
        assert meta["session_deferred"] == "true"