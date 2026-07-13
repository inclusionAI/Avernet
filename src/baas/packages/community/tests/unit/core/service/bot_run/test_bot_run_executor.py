"""QueueTaskMessageDispatcher + BotRunRequestExecutor 单元测试（增量 5；双表）。

QueueTaskMessageDispatcher 双写 baas_bot_run（结果）+ baas_bot_run_queue（工作项）；
BotRunRequestExecutor 入参是队列工作项，按 run_id 读 baas_bot_run 执行并写结果。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.api.bot_runtime import BotBindingInfo, BotChatContext, BotResponse
from secbaas.api.device_manage import ErrorCode, PaasError
from secbaas.api.sse import StreamChunk
from secbaas.core.repository.api_gateway import APIKeyRecord
from secbaas.core.repository.bot_run import BotRunRecord
from secbaas.core.repository.bot_run_queue import BotRunQueueRecord
from secbaas.core.service.bot_run._executor import (
    BotRunRequestExecutor,
    _rebuild_context,
)
from secbaas.spi.bot_service import BotBindingData


def _binding(**overrides) -> BotBindingInfo:
    defaults = dict(
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
    defaults.update(overrides)
    return BotBindingInfo(**defaults)


def _binding_data(**overrides) -> BotBindingData:
    """Create BotBindingData for mocking get_binding()."""
    defaults = dict(
        bot_id="b1",
        owner_id="e1",
        bot_type="personal",
        engine_type="openclaw",
        binding_id=1,
        device_provider="baas",
        device_id="dev-1",
    )
    defaults.update(overrides)
    return BotBindingData(**defaults)


def _run(
    *, run_id: str, bot_id: str, metadata: dict | None, message_long: str = "msg"
) -> BotRunRecord:
    return BotRunRecord(
        id=1,
        gmt_create=None,
        gmt_modified=None,
        run_id=run_id,
        bot_id=bot_id,
        api_key_prefix="sk-",
        message="",
        message_long=message_long,
        metadata=metadata,
        status="PENDING",
        result_content="",
        result_content_long="",
        result_extra=None,
        error=None,
        completed_at=None,
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


def _queue_rec(
    *,
    run_id: str,
    bot_id: str,
    session_id: str | None,
    meta: dict | None = None,
) -> BotRunQueueRecord:
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
        meta=meta or {},
    )


# ----------------------------- _rebuild_context -----------------------------


def test_rebuild_context_from_api_key():
    ctx = _rebuild_context("sk-abc", _api_key_repo("sk-abc"))
    assert ctx.api_key_prefix == "sk-abc"
    assert ctx.app_id == "app-1"
    assert ctx.app_type == "UNKNOWN"
    assert ctx.tenant == ""
    assert ctx.build_auth_token() == "OPEN_API:app:sk-abc"


def test_rebuild_context_api_key_not_found():
    repo = MagicMock()
    repo.get_by_prefix.return_value = None
    with pytest.raises(ValueError, match="api key not found"):
        _rebuild_context("sk-gone", repo)


# ----------------------------- QueueTaskMessageDispatcher.dispatch_* (双写) -----------------------------


def _dispatcher_fresh(repo, queue):
    from secbaas.core.service.bot_run import QueueTaskMessageDispatcher

    return QueueTaskMessageDispatcher(
        run_repository=repo,
        queue_repository=queue,
        chunk_repository=MagicMock(),
        cache_plugin=MagicMock(),
    )


def test_dispatch_send_inserts_pending():
    repo, queue = MagicMock(), MagicMock()
    dispatcher = _dispatcher_fresh(repo, queue)
    ctx = BotChatContext(api_key_prefix="sk-", app_id="a", app_type="T", tenant="t")

    asyncio.run(
        dispatcher.dispatch_send(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_binding(),
            context=ctx,
            bot_id="bot-1",
        )
    )

    # dispatch_send 只入队列工作项，不写结果行（由 BotRunner.deliver_message 写）
    repo.insert_run.assert_not_called()
    # 队列工作项
    q_kw = queue.insert_queue.call_args[1]
    assert q_kw["run_id"] == "run-1"
    assert q_kw["bot_id"] == "bot-1"
    assert q_kw["session_id"] == "sess-1"


def test_dispatch_send_no_session_id():
    repo, queue = MagicMock(), MagicMock()
    dispatcher = _dispatcher_fresh(repo, queue)
    ctx = BotChatContext(api_key_prefix="sk-", app_id="a", app_type="T")

    asyncio.run(
        dispatcher.dispatch_send(
            bot_service=MagicMock(),
            run_id="run-2",
            session_id="",
            message="hello",
            binding_info=_binding(),
            context=ctx,
            bot_id="bot-1",
        )
    )
    repo.insert_run.assert_not_called()
    assert queue.insert_queue.call_args[1]["session_id"] == ""


def test_dispatch_inject_inserts_pending():
    repo, queue = MagicMock(), MagicMock()
    dispatcher = _dispatcher_fresh(repo, queue)
    ctx = BotChatContext(api_key_prefix="sk-", app_id="a", app_type="T")

    asyncio.run(
        dispatcher.dispatch_inject(
            bot_service=MagicMock(),
            run_id="run-3",
            session_id="sess-1",
            message="inject-msg",
            binding_info=_binding(),
            context=ctx,
            bot_id="bot-1",
        )
    )

    repo.insert_run.assert_not_called()
    q_kw = queue.insert_queue.call_args[1]
    assert q_kw["run_id"] == "run-3"
    assert q_kw["session_id"] == "sess-1"


# ----------------------------- BotRunRequestExecutor -----------------------------


async def test_executor_send_flow():
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="r1",
        bot_id="bot-1:ent",
        metadata={
            "app_id": "a",
            "app_type": "T",
            "tenant": "t",
            "request_type": "chat",
        },
    )
    plugin.get_binding = AsyncMock(return_value=_binding_data())

    bot_svc = MagicMock()
    bot_svc.create_session = AsyncMock(return_value=MagicMock(session_id="sess-new"))
    bot_svc.send_message = AsyncMock(
        return_value=BotResponse(content="hello back", usage=None)
    )
    selector.select.return_value = bot_svc

    executor = BotRunRequestExecutor(
        repo, plugin, selector, MagicMock(), MagicMock(), _api_key_repo()
    )
    await executor.execute(
        _queue_rec(run_id="r1", bot_id="bot-1:ent", session_id="sess-1")
    )

    repo.update_status.assert_called_once_with("r1", "RUNNING")
    bot_svc.create_session.assert_not_awaited()
    bot_svc.send_message.assert_awaited_once()
    repo.update_result.assert_called_once()
    assert repo.update_result.call_args[1]["content_long"] == "hello back"


async def test_executor_inject_flow():
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="r2", bot_id="bot-1:ent", metadata={"request_type": "inject"}
    )
    plugin.get_binding = AsyncMock(return_value=_binding_data())

    bot_svc = MagicMock()
    bot_svc.create_session = AsyncMock()
    bot_svc.inject_message = AsyncMock()
    selector.select.return_value = bot_svc

    executor = BotRunRequestExecutor(
        repo, plugin, selector, MagicMock(), MagicMock(), _api_key_repo()
    )
    await executor.execute(
        _queue_rec(
            run_id="r2",
            bot_id="bot-1:ent",
            session_id="sess-exist",
        )
    )

    bot_svc.create_session.assert_not_awaited()
    bot_svc.inject_message.assert_awaited_once()
    repo.update_result.assert_called_once()
    assert repo.update_result.call_args[1]["extra"]["injected"] == "true"


async def test_executor_binding_not_found():
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()
    repo.get_by_run_id.return_value = _run(run_id="r3", bot_id="bad-bot", metadata=None)
    plugin.get_binding = AsyncMock(
        side_effect=PaasError(ErrorCode.NOT_FOUND, "not found")
    )

    executor = BotRunRequestExecutor(
        repo, plugin, selector, MagicMock(), MagicMock(), MagicMock()
    )
    await executor.execute(
        _queue_rec(run_id="r3", bot_id="bad-bot", session_id="sess-3")
    )

    repo.update_error.assert_called_once()
    assert "binding not found" in repo.update_error.call_args[0][1]


async def test_executor_run_row_missing_is_noop():
    repo = MagicMock()
    plugin = MagicMock()
    repo.get_by_run_id.return_value = None
    executor = BotRunRequestExecutor(
        repo, plugin, MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    await executor.execute(_queue_rec(run_id="gone", bot_id="b", session_id="sess-x"))
    repo.update_error.assert_not_called()
    repo.update_result.assert_not_called()


# ----------------------------- BotRunRequestExecutor stream (agent merge) -----------------------------


async def _async_iter(chunks):
    for c in chunks:
        yield c


async def test_executor_stream_agent_merge():
    """stream 模式：agent 事件合并写 DB（JSON array），delta 合并写 DB。"""
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="rs",
        bot_id="bot-1:ent",
        metadata={"request_type": "chat", "stream": "true"},
    )
    plugin.get_binding = AsyncMock(return_value=_binding_data())

    chunks = [
        StreamChunk(
            type="agent",
            content="",
            metadata={"engine_frame": {"stream": "tool", "phase": "start"}},
        ),
        StreamChunk(
            type="agent",
            content="",
            metadata={"engine_frame": {"stream": "tool", "phase": "result"}},
        ),
        StreamChunk(type="delta", content="hel"),
        StreamChunk(type="delta", content="lo"),
        StreamChunk(type="final", content="hello world"),
    ]
    bot_svc = MagicMock()

    async def _stream_gen(*a, **kw):
        for c in chunks:
            yield c

    bot_svc.send_message_stream = _stream_gen
    selector.select.return_value = bot_svc

    chunk_repo = MagicMock()
    executor = BotRunRequestExecutor(
        repo, plugin, selector, chunk_repo, MagicMock(), _api_key_repo()
    )
    await executor.execute(
        _queue_rec(run_id="rs", bot_id="bot-1:ent", session_id="sess-s")
    )

    insert_calls = chunk_repo.insert_chunk.call_args_list

    # agent: 2 个 agent 事件合并为 1 行，content 是 JSON array
    agent_calls = [c for c in insert_calls if c[1]["chunk_type"] == "agent"]
    assert len(agent_calls) == 1
    agent_data = json.loads(agent_calls[0][1]["content"])
    assert len(agent_data) == 2
    assert agent_data[0]["engine_frame"]["phase"] == "start"
    assert agent_data[1]["engine_frame"]["phase"] == "result"

    # delta: 2 个 delta 合并为 1 行
    delta_calls = [c for c in insert_calls if c[1]["chunk_type"] == "delta"]
    assert len(delta_calls) == 1
    assert delta_calls[0][1]["content"] == "hello"

    # final: 1 行
    final_calls = [c for c in insert_calls if c[1]["chunk_type"] == "final"]
    assert len(final_calls) == 1
    assert final_calls[0][1]["content"] == "hello world"

    # seq 递增：delta(1) → agent(2) → final(3)
    # _flush_buffers() 先 flush delta 再 flush agent
    assert delta_calls[0][1]["seq"] == 1
    assert agent_calls[0][1]["seq"] == 2
    assert final_calls[0][1]["seq"] == 3

    # update_result 写入 final content
    repo.update_result.assert_called_once()
    assert repo.update_result.call_args[1]["content_long"] == "hello world"


async def test_executor_stream_error_flushes_agent_buffer():
    """stream 模式：异常时 flush agent buffer + 写 error chunk。"""
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="re",
        bot_id="bot-1:ent",
        metadata={"request_type": "chat", "stream": "true"},
    )
    plugin.get_binding = AsyncMock(return_value=_binding_data())

    async def _boom_stream(*a, **kw):
        raise RuntimeError("engine down")
        yield  # never reached, makes this an async generator

    bot_svc = MagicMock()
    bot_svc.send_message_stream = _boom_stream
    selector.select.return_value = bot_svc

    chunk_repo = MagicMock()
    executor = BotRunRequestExecutor(
        repo, plugin, selector, chunk_repo, MagicMock(), _api_key_repo()
    )
    await executor.execute(
        _queue_rec(run_id="re", bot_id="bot-1:ent", session_id="sess-e")
    )

    # error chunk 写入
    error_calls = [
        c
        for c in chunk_repo.insert_chunk.call_args_list
        if c[1]["chunk_type"] == "error"
    ]
    assert len(error_calls) == 1
    assert error_calls[0][1]["content"] == "stream execution failed"

    repo.update_error.assert_called_once_with("re", "stream execution failed")


# ----------------------------- 背压（队列深度 → 429） -----------------------------


def test_dispatch_send_backpressure_rejects():
    from secbaas.api.bot_runtime import TooManyRequestsError

    repo, queue = MagicMock(), MagicMock()
    repo.get_by_run_id.return_value = None
    queue.count_pending_by_bot.return_value = 100
    dispatcher = _dispatcher_with_depth(repo, queue, 100)
    ctx = BotChatContext(api_key_prefix="sk-", app_id="a", app_type="T")

    with pytest.raises(TooManyRequestsError):
        asyncio.run(
            dispatcher.dispatch_send(
                bot_service=MagicMock(),
                run_id="run-1",
                session_id="",
                message="hi",
                binding_info=_binding(),
                context=ctx,
                bot_id="bot-1",
            )
        )
    repo.insert_run.assert_not_called()
    queue.insert_queue.assert_not_called()


def test_dispatch_send_backpressure_allows_under_threshold():
    repo, queue = MagicMock(), MagicMock()
    repo.get_by_run_id.return_value = None
    queue.count_pending_by_bot.return_value = 99
    dispatcher = _dispatcher_with_depth(repo, queue, 100)
    ctx = BotChatContext(api_key_prefix="sk-", app_id="a", app_type="T")

    asyncio.run(
        dispatcher.dispatch_send(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="",
            message="hi",
            binding_info=_binding(),
            context=ctx,
            bot_id="bot-1",
        )
    )
    repo.insert_run.assert_not_called()
    queue.insert_queue.assert_called_once()


def test_dispatch_disabled_backpressure_skips_count():
    repo, queue = MagicMock(), MagicMock()
    repo.get_by_run_id.return_value = None
    dispatcher = _dispatcher_with_depth(repo, queue, 0)  # 关闭
    ctx = BotChatContext(api_key_prefix="sk-", app_id="a", app_type="T")

    asyncio.run(
        dispatcher.dispatch_send(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="",
            message="hi",
            binding_info=_binding(),
            context=ctx,
            bot_id="bot-1",
        )
    )
    queue.count_pending_by_bot.assert_not_called()
    queue.insert_queue.assert_called_once()


def _dispatcher_with_depth(repo, queue, depth):
    from secbaas.core.service.bot_run import QueueTaskMessageDispatcher

    return QueueTaskMessageDispatcher(
        run_repository=repo,
        queue_repository=queue,
        chunk_repository=MagicMock(),
        cache_plugin=MagicMock(),
        max_queue_depth=depth,
    )


# ----------------------------- _should_cleanup_chunks config tests -----------------------------


def _dispatcher_with_config(config_service=None):
    from secbaas.core.service.bot_run import QueueTaskMessageDispatcher

    return QueueTaskMessageDispatcher(
        run_repository=MagicMock(),
        queue_repository=MagicMock(),
        chunk_repository=MagicMock(),
        cache_plugin=MagicMock(),
        system_config_service=config_service,
    )


def _config_response(value: str | None):
    from datetime import datetime

    from secbaas.api.config_manage import SystemConfigResponse

    return SystemConfigResponse(
        id=1,
        conf_key="bot_run.chunk_cleanup_enabled",
        conf_value=value,
        env="dev",
        name="chunk_cleanup",
        description=None,
        creator="test",
        modifier="test",
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
    )


class TestShouldCleanupChunks:
    def test_no_config_service_returns_true(self):
        """When system_config_service is None, cleanup is enabled by default."""
        dispatcher = _dispatcher_with_config(config_service=None)
        assert dispatcher._should_cleanup_chunks() is True

    def test_config_value_true_returns_true(self):
        """When conf_value is 'true', cleanup is enabled."""
        mock_service = MagicMock()
        mock_service.get_config.return_value = _config_response("true")
        dispatcher = _dispatcher_with_config(config_service=mock_service)
        assert dispatcher._should_cleanup_chunks() is True

    def test_config_value_false_returns_false(self):
        """When conf_value is 'false', cleanup is disabled."""
        mock_service = MagicMock()
        mock_service.get_config.return_value = _config_response("false")
        dispatcher = _dispatcher_with_config(config_service=mock_service)
        assert dispatcher._should_cleanup_chunks() is False

    def test_config_value_true_uppercase_returns_true(self):
        """When conf_value is 'TRUE' (case-insensitive), cleanup is enabled."""
        mock_service = MagicMock()
        mock_service.get_config.return_value = _config_response("TRUE")
        dispatcher = _dispatcher_with_config(config_service=mock_service)
        assert dispatcher._should_cleanup_chunks() is True

    def test_config_value_true_with_spaces_returns_true(self):
        """When conf_value has surrounding whitespace, cleanup is enabled."""
        mock_service = MagicMock()
        mock_service.get_config.return_value = _config_response("  true  ")
        dispatcher = _dispatcher_with_config(config_service=mock_service)
        assert dispatcher._should_cleanup_chunks() is True

    def test_config_none_returns_false(self):
        """When config record does not exist (None), cleanup is disabled."""
        mock_service = MagicMock()
        mock_service.get_config.return_value = None
        dispatcher = _dispatcher_with_config(config_service=mock_service)
        assert dispatcher._should_cleanup_chunks() is False

    def test_config_value_empty_returns_false(self):
        """When conf_value is empty string, cleanup is disabled."""
        mock_service = MagicMock()
        mock_service.get_config.return_value = _config_response("")
        dispatcher = _dispatcher_with_config(config_service=mock_service)
        assert dispatcher._should_cleanup_chunks() is False

    def test_config_value_none_returns_false(self):
        """When conf_value is None, cleanup is disabled."""
        mock_service = MagicMock()
        mock_service.get_config.return_value = _config_response(None)
        dispatcher = _dispatcher_with_config(config_service=mock_service)
        assert dispatcher._should_cleanup_chunks() is False

    def test_config_value_arbitrary_returns_false(self):
        """When conf_value is not 'true', cleanup is disabled."""
        mock_service = MagicMock()
        mock_service.get_config.return_value = _config_response("yes")
        dispatcher = _dispatcher_with_config(config_service=mock_service)
        assert dispatcher._should_cleanup_chunks() is False

    def test_config_read_exception_returns_false(self):
        """When get_config raises, cleanup is disabled (fail-safe)."""
        mock_service = MagicMock()
        mock_service.get_config.side_effect = RuntimeError("db down")
        dispatcher = _dispatcher_with_config(config_service=mock_service)
        assert dispatcher._should_cleanup_chunks() is False


# ----------------------------- accepts -----------------------------


class TestAccepts:
    def test_accepts_returns_true(self):
        """QueueTaskMessageDispatcher.accepts always returns True."""
        dispatcher = _dispatcher_fresh(MagicMock(), MagicMock())
        assert dispatcher.accepts("any-bot-id") is True


# ----------------------------- _cleanup_chunks -----------------------------


class TestCleanupChunks:
    def test_should_cleanup_true_calls_delete(self):
        """When _should_cleanup_chunks is True, _cleanup_chunks deletes records."""
        from secbaas.core.service.bot_run import QueueTaskMessageDispatcher

        chunk_repo = MagicMock()
        dispatcher = QueueTaskMessageDispatcher(
            run_repository=MagicMock(),
            queue_repository=MagicMock(),
            chunk_repository=chunk_repo,
            cache_plugin=MagicMock(),
        )
        dispatcher._cleanup_chunks("run-1")
        chunk_repo.delete_chunks_by_run.assert_called_once_with("run-1")

    def test_cleanup_exception_is_swallowed(self):
        """_cleanup_chunks swallows exceptions from chunk_repository."""
        from secbaas.core.service.bot_run import QueueTaskMessageDispatcher

        chunk_repo = MagicMock()
        chunk_repo.delete_chunks_by_run.side_effect = RuntimeError("db error")
        dispatcher = QueueTaskMessageDispatcher(
            run_repository=MagicMock(),
            queue_repository=MagicMock(),
            chunk_repository=chunk_repo,
            cache_plugin=MagicMock(),
        )
        # should not raise
        dispatcher._cleanup_chunks("run-1")
