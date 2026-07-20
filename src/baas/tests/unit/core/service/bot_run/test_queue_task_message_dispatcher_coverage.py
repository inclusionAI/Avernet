import json
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.bot_runtime import (
    BotBindingInfo,
    BotChatContext,
    TooManyRequestsError,
)
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.service.bot_run._queue_task_message_dispatcher import (
    QueueTaskMessageDispatcher,
)


def _make_dispatcher(**overrides):
    defaults = dict(
        run_repository=MagicMock(),
        queue_repository=MagicMock(),
        chunk_repository=MagicMock(),
        cache_plugin=MagicMock(),
        max_queue_depth=0,
        system_config_service=None,
    )
    defaults.update(overrides)
    return QueueTaskMessageDispatcher(**defaults)


def _make_binding_info():
    return BotBindingInfo(
        bot_id="bot-1",
        entity_id="ent-1",
        sandbox_id="sb-1",
        device_id="dev-1",
        device_provider="docker",
        binding_id=1,
    )


class TestProperties:
    def test_order(self):
        d = _make_dispatcher()
        assert d.order == 100

    def test_accepts(self):
        d = _make_dispatcher()
        assert d.accepts("any-bot") is True


class TestDispatchSend:
    @pytest.mark.asyncio
    async def test_basic_send(self):
        d = _make_dispatcher()
        await d.dispatch_send(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        )
        d._queue_repository.insert_queue.assert_called_once()
        call_kwargs = d._queue_repository.insert_queue.call_args.kwargs
        assert call_kwargs["run_id"] == "run-1"
        assert call_kwargs["bot_id"] == "bot-1"
        assert call_kwargs["meta"]["request_type"] == "chat"

    @pytest.mark.asyncio
    async def test_send_with_callback_string(self):
        d = _make_dispatcher()
        await d.dispatch_send(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
            callback="my_callback",
        )
        call_kwargs = d._queue_repository.insert_queue.call_args.kwargs
        assert call_kwargs["meta"]["callback_function"] == "my_callback"

    @pytest.mark.asyncio
    async def test_send_with_callback_non_string(self):
        d = _make_dispatcher()
        await d.dispatch_send(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
            callback=MagicMock(),
        )
        call_kwargs = d._queue_repository.insert_queue.call_args.kwargs
        assert "callback_function" not in call_kwargs["meta"]

    @pytest.mark.asyncio
    async def test_send_with_timeout(self):
        d = _make_dispatcher()
        await d.dispatch_send(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
            timeout=30,
        )
        call_kwargs = d._queue_repository.insert_queue.call_args.kwargs
        assert call_kwargs["meta"]["timeout"] == 30

    @pytest.mark.asyncio
    async def test_send_with_context(self):
        d = _make_dispatcher()
        ctx = BotChatContext(
            api_key_prefix="prefix", app_id="app-1", app_type="web", tenant="t-1"
        )
        await d.dispatch_send(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            context=ctx,
            bot_id="bot-1",
        )
        d._queue_repository.insert_queue.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_backpressure_raises(self):
        d = _make_dispatcher(max_queue_depth=5)
        d._queue_repository.count_pending_by_bot.return_value = 5
        with pytest.raises(TooManyRequestsError):
            await d.dispatch_send(
                bot_service=MagicMock(),
                run_id="run-1",
                session_id="sess-1",
                message="hello",
                binding_info=_make_binding_info(),
                bot_id="bot-1",
            )

    @pytest.mark.asyncio
    async def test_send_backpressure_query_fails_does_not_raise(self):
        d = _make_dispatcher(max_queue_depth=5)
        d._queue_repository.count_pending_by_bot.side_effect = RuntimeError("db error")
        await d.dispatch_send(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        )
        d._queue_repository.insert_queue.assert_called_once()


class TestDispatchInject:
    @pytest.mark.asyncio
    async def test_basic_inject(self):
        d = _make_dispatcher()
        await d.dispatch_inject(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="inject",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        )
        d._queue_repository.insert_queue.assert_called_once()
        call_kwargs = d._queue_repository.insert_queue.call_args.kwargs
        assert call_kwargs["meta"]["request_type"] == "inject"

    @pytest.mark.asyncio
    async def test_inject_backpressure_raises(self):
        d = _make_dispatcher(max_queue_depth=3)
        d._queue_repository.count_pending_by_bot.return_value = 3
        with pytest.raises(TooManyRequestsError):
            await d.dispatch_inject(
                bot_service=MagicMock(),
                run_id="run-1",
                session_id="sess-1",
                message="inject",
                binding_info=_make_binding_info(),
                bot_id="bot-1",
            )


class TestDispatchSendStreamEngineType:
    """engine_type 从 chunk 表 metadata 还原到 StreamChunk 的测试。"""

    @pytest.mark.asyncio
    async def test_agent_chunk_with_engine_type(self):
        """agent chunk 的 metadata 含 engine_type 时，还原到 StreamChunk。"""
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = "1:agent"
        chunk_rec = MagicMock()
        chunk_rec.seq = 1
        chunk_rec.chunk_type = "agent"
        chunk_rec.content = json.dumps([{"frame": 1}])
        chunk_rec.metadata = json.dumps({"engine_type": "dify"})
        d._chunk_repository.get_chunks_after.return_value = [chunk_rec]
        run = MagicMock()
        run.status = "FAILED"
        d._run_repository.get_by_run_id.return_value = run

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        agent_chunks = [c for c in chunks if c.type == "agent"]
        assert len(agent_chunks) == 1
        assert agent_chunks[0].engine_type == "dify"

    @pytest.mark.asyncio
    async def test_agent_chunk_engine_type_invalid_metadata_json(self):
        """agent chunk 的 metadata 非法 JSON 时，engine_type 为 None。"""
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = "1:agent"
        chunk_rec = MagicMock()
        chunk_rec.seq = 1
        chunk_rec.chunk_type = "agent"
        chunk_rec.content = json.dumps([{"frame": 1}])
        chunk_rec.metadata = "bad json"
        d._chunk_repository.get_chunks_after.return_value = [chunk_rec]
        run = MagicMock()
        run.status = "FAILED"
        d._run_repository.get_by_run_id.return_value = run

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        agent_chunks = [c for c in chunks if c.type == "agent"]
        assert len(agent_chunks) == 1
        assert agent_chunks[0].engine_type is None

    @pytest.mark.asyncio
    async def test_non_agent_chunk_engine_type_only_metadata(self):
        """非 agent chunk 的 metadata 仅含 engine_type 时，pop 后 metadata 为 None。"""
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = "1:final"
        chunk_rec = MagicMock()
        chunk_rec.seq = 1
        chunk_rec.chunk_type = "final"
        chunk_rec.content = "done"
        chunk_rec.metadata = json.dumps({"engine_type": "openclaw"})
        d._chunk_repository.get_chunks_after.return_value = [chunk_rec]
        d._run_repository.get_by_run_id.return_value = None

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        assert len(chunks) == 1
        assert chunks[0].engine_type == "openclaw"
        assert chunks[0].metadata is None

    @pytest.mark.asyncio
    async def test_non_agent_chunk_engine_type_with_other_metadata(self):
        """非 agent chunk 的 metadata 同时含 engine_type 和其他字段时，保留剩余 metadata。"""
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = "1:final"
        chunk_rec = MagicMock()
        chunk_rec.seq = 1
        chunk_rec.chunk_type = "final"
        chunk_rec.content = "done"
        chunk_rec.metadata = json.dumps({"engine_type": "openclaw", "extra": "val"})
        d._chunk_repository.get_chunks_after.return_value = [chunk_rec]
        d._run_repository.get_by_run_id.return_value = None

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        assert len(chunks) == 1
        assert chunks[0].engine_type == "openclaw"
        assert chunks[0].metadata == {"extra": "val"}

    @pytest.mark.asyncio
    async def test_non_agent_chunk_engine_type_invalid_metadata_json(self):
        """非 agent chunk 的 metadata 非法 JSON 时，engine_type 为 None。"""
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = "1:final"
        chunk_rec = MagicMock()
        chunk_rec.seq = 1
        chunk_rec.chunk_type = "final"
        chunk_rec.content = "done"
        chunk_rec.metadata = "bad json"
        d._chunk_repository.get_chunks_after.return_value = [chunk_rec]
        d._run_repository.get_by_run_id.return_value = None

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        assert len(chunks) == 1
        assert chunks[0].engine_type is None
        assert chunks[0].metadata is None


class TestDispatchSendStream:
    @pytest.mark.asyncio
    async def test_stream_basic_with_final(self):
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = "1:final"
        chunk_rec = MagicMock()
        chunk_rec.seq = 1
        chunk_rec.chunk_type = "final"
        chunk_rec.content = "done"
        chunk_rec.metadata = None
        d._chunk_repository.get_chunks_after.return_value = [chunk_rec]
        d._run_repository.get_by_run_id.return_value = None

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        assert len(chunks) == 1
        assert chunks[0].type == "final"

    @pytest.mark.asyncio
    async def test_stream_with_error_chunk(self):
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = "1:error"
        chunk_rec = MagicMock()
        chunk_rec.seq = 1
        chunk_rec.chunk_type = "error"
        chunk_rec.content = "fail"
        chunk_rec.metadata = None
        d._chunk_repository.get_chunks_after.return_value = [chunk_rec]
        d._run_repository.get_by_run_id.return_value = None

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        assert len(chunks) == 1
        assert chunks[0].type == "error"

    @pytest.mark.asyncio
    async def test_stream_with_agent_chunk(self):
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = "1:agent"
        chunk_rec = MagicMock()
        chunk_rec.seq = 1
        chunk_rec.chunk_type = "agent"
        chunk_rec.content = json.dumps([{"frame": 1}, {"frame": 2}])
        chunk_rec.metadata = None
        d._chunk_repository.get_chunks_after.return_value = [chunk_rec]
        run = MagicMock()
        run.status = "FAILED"
        d._run_repository.get_by_run_id.return_value = run

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        agent_chunks = [c for c in chunks if c.type == "agent"]
        assert len(agent_chunks) == 2

    @pytest.mark.asyncio
    async def test_stream_with_agent_chunk_invalid_json(self):
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = "1:agent"
        chunk_rec = MagicMock()
        chunk_rec.seq = 1
        chunk_rec.chunk_type = "agent"
        chunk_rec.content = "not json"
        chunk_rec.metadata = None
        d._chunk_repository.get_chunks_after.return_value = [chunk_rec]
        run = MagicMock()
        run.status = "FAILED"
        d._run_repository.get_by_run_id.return_value = run

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        agent_chunks = [c for c in chunks if c.type == "agent"]
        assert len(agent_chunks) == 0

    @pytest.mark.asyncio
    async def test_stream_with_metadata_json(self):
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = "1:text"
        chunk_rec = MagicMock()
        chunk_rec.seq = 1
        chunk_rec.chunk_type = "text"
        chunk_rec.content = "hello"
        chunk_rec.metadata = '{"key": "value"}'
        d._chunk_repository.get_chunks_after.return_value = [chunk_rec]
        run = MagicMock()
        run.status = "FAILED"
        d._run_repository.get_by_run_id.return_value = run

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        text_chunks = [c for c in chunks if c.type == "text"]
        assert len(text_chunks) == 1
        assert text_chunks[0].metadata == {"key": "value"}

    @pytest.mark.asyncio
    async def test_stream_with_metadata_invalid_json(self):
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = "1:text"
        chunk_rec = MagicMock()
        chunk_rec.seq = 1
        chunk_rec.chunk_type = "text"
        chunk_rec.content = "hello"
        chunk_rec.metadata = "bad json"
        d._chunk_repository.get_chunks_after.return_value = [chunk_rec]
        run = MagicMock()
        run.status = "FAILED"
        d._run_repository.get_by_run_id.return_value = run

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        text_chunks = [c for c in chunks if c.type == "text"]
        assert len(text_chunks) == 1
        assert text_chunks[0].metadata is None

    @pytest.mark.asyncio
    async def test_stream_timeout_exceeded(self):
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = None
        d._run_repository.get_by_run_id.return_value = None

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
            timeout=1,
        ):
            chunks.append(c)
        timeout_chunks = [
            c for c in chunks if c.type == "error" and "timeout" in c.content.lower()
        ]
        assert len(timeout_chunks) == 1

    @pytest.mark.asyncio
    async def test_stream_run_terminated_failed(self):
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = None
        run = MagicMock()
        run.status = "FAILED"
        d._run_repository.get_by_run_id.return_value = run

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        assert len(chunks) == 1
        assert chunks[0].type == "error"
        assert "FAILED" in chunks[0].content

    @pytest.mark.asyncio
    async def test_stream_run_terminated_timeout(self):
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = None
        run = MagicMock()
        run.status = "TIMEOUT"
        d._run_repository.get_by_run_id.return_value = run

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        assert len(chunks) == 1
        assert chunks[0].type == "error"

    @pytest.mark.asyncio
    async def test_stream_cache_get_exception(self):
        d = _make_dispatcher()
        d._cache_plugin.get.side_effect = RuntimeError("cache error")
        d._run_repository.get_by_run_id.return_value = None
        run = MagicMock()
        run.status = "FAILED"
        d._run_repository.get_by_run_id.return_value = run

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        assert len(chunks) >= 1

    @pytest.mark.asyncio
    async def test_stream_watermark_invalid_seq(self):
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = "abc:text"
        d._run_repository.get_by_run_id.return_value = None
        run = MagicMock()
        run.status = "FAILED"
        d._run_repository.get_by_run_id.return_value = run

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        assert len(chunks) >= 1

    @pytest.mark.asyncio
    async def test_stream_cleanup_called(self):
        d = _make_dispatcher()
        d._cache_plugin.get.return_value = None
        run = MagicMock()
        run.status = "FAILED"
        d._run_repository.get_by_run_id.return_value = run

        chunks = []
        async for c in d.dispatch_send_stream(
            bot_service=MagicMock(),
            run_id="run-1",
            session_id="sess-1",
            message="hello",
            binding_info=_make_binding_info(),
            bot_id="bot-1",
        ):
            chunks.append(c)
        d._chunk_repository.delete_chunks_by_run.assert_called_once_with("run-1")


class TestShouldCleanupChunks:
    def test_no_system_config_service(self):
        d = _make_dispatcher(system_config_service=None)
        assert d._should_cleanup_chunks() is True

    def test_config_service_raises(self):
        svc = MagicMock()
        svc.get_config.side_effect = RuntimeError("db error")
        d = _make_dispatcher(system_config_service=svc)
        assert d._should_cleanup_chunks() is False

    def test_config_returns_none(self):
        svc = MagicMock()
        svc.get_config.return_value = None
        d = _make_dispatcher(system_config_service=svc)
        assert d._should_cleanup_chunks() is False

    def test_config_true(self):
        svc = MagicMock()
        cfg = MagicMock()
        cfg.conf_value = "true"
        svc.get_config.return_value = cfg
        d = _make_dispatcher(system_config_service=svc)
        assert d._should_cleanup_chunks() is True

    def test_config_false(self):
        svc = MagicMock()
        cfg = MagicMock()
        cfg.conf_value = "false"
        svc.get_config.return_value = cfg
        d = _make_dispatcher(system_config_service=svc)
        assert d._should_cleanup_chunks() is False

    def test_config_true_with_whitespace(self):
        svc = MagicMock()
        cfg = MagicMock()
        cfg.conf_value = "  True  "
        svc.get_config.return_value = cfg
        d = _make_dispatcher(system_config_service=svc)
        assert d._should_cleanup_chunks() is True


class TestCleanupChunks:
    def test_cleanup_success(self):
        d = _make_dispatcher()
        d._cleanup_chunks("run-1")
        d._chunk_repository.delete_chunks_by_run.assert_called_once_with("run-1")

    def test_cleanup_exception(self):
        d = _make_dispatcher()
        d._chunk_repository.delete_chunks_by_run.side_effect = RuntimeError("db error")
        d._cleanup_chunks("run-1")


class TestCheckBackpressure:
    def test_no_limit(self):
        d = _make_dispatcher(max_queue_depth=0)
        d._check_backpressure("bot-1")

    def test_under_limit(self):
        d = _make_dispatcher(max_queue_depth=10)
        d._queue_repository.count_pending_by_bot.return_value = 5
        d._check_backpressure("bot-1")

    def test_at_limit_raises(self):
        d = _make_dispatcher(max_queue_depth=5)
        d._queue_repository.count_pending_by_bot.return_value = 5
        with pytest.raises(TooManyRequestsError):
            d._check_backpressure("bot-1")

    def test_over_limit_raises(self):
        d = _make_dispatcher(max_queue_depth=3)
        d._queue_repository.count_pending_by_bot.return_value = 10
        with pytest.raises(TooManyRequestsError):
            d._check_backpressure("bot-1")

    def test_query_fails_does_not_raise(self):
        d = _make_dispatcher(max_queue_depth=5)
        d._queue_repository.count_pending_by_bot.side_effect = RuntimeError("db error")
        d._check_backpressure("bot-1")


class TestEnqueueWork:
    def test_basic_enqueue(self):
        d = _make_dispatcher()
        d._enqueue_work("run-1", "bot-1", "sess-1", meta={"k": "v"})
        d._queue_repository.insert_queue.assert_called_once_with(
            run_id="run-1", bot_id="bot-1", session_id="sess-1", meta={"k": "v"}
        )

    def test_enqueue_no_meta(self):
        d = _make_dispatcher()
        d._enqueue_work("run-1", "bot-1", None)
        d._queue_repository.insert_queue.assert_called_once_with(
            run_id="run-1", bot_id="bot-1", session_id=None, meta=None
        )

    def test_enqueue_injects_traceparent_when_tracer_active(self):
        """When a trace span is active, inject_context produces a carrier
        that gets written into meta["traceparent"]."""
        carrier = {"traceparent": "00-abc-def-03"}
        mock_tracer = MagicMock()
        mock_tracer.inject_context = MagicMock(
            side_effect=lambda c: c.update(carrier)
        )
        d = _make_dispatcher()
        with patch(
            "secbaas.community.core.service.bot_run."
            "_queue_task_message_dispatcher.get_tracer_plugin",
            return_value=mock_tracer,
        ):
            d._enqueue_work("run-1", "bot-1", "sess-1", meta={"k": "v"})
        args = d._queue_repository.insert_queue.call_args
        assert args.kwargs["meta"]["traceparent"] == carrier
        assert args.kwargs["meta"]["k"] == "v"

    def test_enqueue_injects_traceparent_into_none_meta(self):
        """When meta is None and tracer produces a carrier, a fresh dict
        is created with traceparent."""
        carrier = {"traceparent": "00-abc-def-03"}
        mock_tracer = MagicMock()
        mock_tracer.inject_context = MagicMock(
            side_effect=lambda c: c.update(carrier)
        )
        d = _make_dispatcher()
        with patch(
            "secbaas.community.core.service.bot_run."
            "_queue_task_message_dispatcher.get_tracer_plugin",
            return_value=mock_tracer,
        ):
            d._enqueue_work("run-1", "bot-1", None)
        args = d._queue_repository.insert_queue.call_args
        assert args.kwargs["meta"] == {"traceparent": carrier}


class TestBuildMetadata:
    def test_no_context_no_session(self):
        result = QueueTaskMessageDispatcher._build_metadata(None)
        assert result == {"request_type": "chat"}

    def test_with_session_only(self):
        result = QueueTaskMessageDispatcher._build_metadata(None, session_id="s-1")
        assert result["session_id"] == "s-1"
        assert result["request_type"] == "chat"

    def test_with_context(self):
        ctx = BotChatContext(
            api_key_prefix="prefix", app_id="app-1", app_type="web", tenant="t-1"
        )
        result = QueueTaskMessageDispatcher._build_metadata(
            ctx, session_id="s-1", request_type="inject"
        )
        assert result["session_id"] == "s-1"
        assert result["app_id"] == "app-1"
        assert result["app_type"] == "web"
        assert result["tenant"] == "t-1"
        assert result["request_type"] == "inject"

    def test_with_context_no_session(self):
        ctx = BotChatContext(
            api_key_prefix="prefix", app_id="app-1", app_type="web", tenant="t-1"
        )
        result = QueueTaskMessageDispatcher._build_metadata(ctx)
        assert "session_id" not in result
        assert result["app_id"] == "app-1"
        assert result["request_type"] == "chat"
