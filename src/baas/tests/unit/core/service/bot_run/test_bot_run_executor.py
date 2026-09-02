"""QueueTaskMessageDispatcher + BotRunRequestExecutor 单元测试（增量 5；双表）。

QueueTaskMessageDispatcher 双写 baas_bot_run（结果）+ baas_bot_run_queue（工作项）；
BotRunRequestExecutor 入参是队列工作项，按 run_id 读 baas_bot_run 执行并写结果。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.bcn import Attachment
from secbaas.community.api.bot_runtime import (
    BotBindingInfo,
    BotChatContext,
    BotResponse,
)
from secbaas.community.api.device_manage import ErrorCode, PaasError
from secbaas.community.api.sse import StreamChunk
from secbaas.community.core.repository.api_gateway import APIKeyRecord
from secbaas.community.core.repository.bot_run import BotRunRecord
from secbaas.community.core.repository.bot_run_queue import BotRunQueueRecord
from secbaas.community.core.service.bot_run._executor import (
    BotRunRequestExecutor,
    _rebuild_context,
)
from secbaas.community.spi.bot_service import BotBindingData


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


def test_rebuild_context_from_metadata():
    """metadata 中有 app_id 时直接重建，不查 api_key_repository。"""
    repo = MagicMock()
    repo.get_by_prefix.side_effect = AssertionError("should not call get_by_prefix")
    ctx = _rebuild_context(
        "rk-prefix",
        repo,
        metadata={"app_id": "app-x", "app_type": "app", "tenant": "tn"},
    )
    assert ctx.api_key_prefix == "rk-prefix"
    assert ctx.app_id == "app-x"
    assert ctx.app_type == "app"
    assert ctx.tenant == "tn"
    assert ctx.build_auth_token() == "OPEN_API:app:rk-prefix"


def test_rebuild_context_metadata_without_app_id_falls_back():
    """metadata 中无 app_id 时 fallback 到 api_key_repository 反查。"""
    ctx = _rebuild_context(
        "sk-abc",
        _api_key_repo("sk-abc"),
        metadata={"request_type": "chat"},
    )
    assert ctx.api_key_prefix == "sk-abc"
    assert ctx.app_id == "app-1"


def test_rebuild_context_api_key_not_found():
    repo = MagicMock()
    repo.get_by_prefix.return_value = None
    with pytest.raises(ValueError, match="api key not found"):
        _rebuild_context("sk-gone", repo)


# ----------------------------- QueueTaskMessageDispatcher.dispatch_* (双写) -----------------------------


def _dispatcher_fresh(repo, queue):
    from secbaas.community.core.service.bot_run import QueueTaskMessageDispatcher

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
        repo, plugin, selector, MagicMock(), MagicMock(), _api_key_repo(), MagicMock()
    )
    await executor.execute(
        _queue_rec(run_id="r1", bot_id="bot-1:ent", session_id="sess-1")
    )

    repo.update_status.assert_called_once_with("r1", "RUNNING")
    bot_svc.create_session.assert_not_awaited()
    bot_svc.send_message.assert_awaited_once()
    repo.update_result.assert_called_once()
    assert repo.update_result.call_args[1]["content_long"] == "hello back"


async def test_executor_timeout_marks_timeout():
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="r-timeout",
        bot_id="bot-1:ent",
        metadata={"request_type": "chat"},
    )
    plugin.get_binding = AsyncMock(return_value=_binding_data())

    bot_svc = MagicMock()
    bot_svc.create_session = AsyncMock(return_value=MagicMock(session_id="sess-new"))
    bot_svc.send_message = AsyncMock(side_effect=TimeoutError())
    selector.select.return_value = bot_svc

    executor = BotRunRequestExecutor(
        repo, plugin, selector, MagicMock(), MagicMock(), _api_key_repo(), MagicMock()
    )
    await executor.execute(
        _queue_rec(run_id="r-timeout", bot_id="bot-1:ent", session_id="sess-1")
    )

    repo.update_timeout.assert_called_once()
    assert repo.update_timeout.call_args[0][0] == "r-timeout"
    repo.update_result.assert_not_called()


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
        repo, plugin, selector, MagicMock(), MagicMock(), _api_key_repo(), MagicMock()
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
        repo, plugin, selector, MagicMock(), MagicMock(), MagicMock(), MagicMock()
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
        repo, plugin, MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
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
        repo, plugin, selector, chunk_repo, MagicMock(), _api_key_repo(), MagicMock()
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
        repo, plugin, selector, chunk_repo, MagicMock(), _api_key_repo(), MagicMock()
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


async def test_executor_stream_error_chunk_marks_failed():
    """stream 模式：error chunk 不再 fallthrough 为 COMPLETED，而是 update_error(FAILED)。"""
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="r-err-chunk",
        bot_id="bot-1:ent",
        metadata={"request_type": "chat", "stream": "true"},
    )
    plugin.get_binding = AsyncMock(return_value=_binding_data())

    chunks = [
        StreamChunk(type="delta", content="partial"),
        StreamChunk(type="error", content="CONNECTION_ERROR"),
    ]

    async def _stream_gen(*a, **kw):
        for c in chunks:
            yield c

    bot_svc = MagicMock()
    bot_svc.send_message_stream = _stream_gen
    selector.select.return_value = bot_svc

    chunk_repo = MagicMock()
    executor = BotRunRequestExecutor(
        repo, plugin, selector, chunk_repo, MagicMock(), _api_key_repo(), MagicMock()
    )
    await executor.execute(
        _queue_rec(run_id="r-err-chunk", bot_id="bot-1:ent", session_id="sess-e")
    )

    # error chunk 应写入 DB
    error_calls = [
        c
        for c in chunk_repo.insert_chunk.call_args_list
        if c[1]["chunk_type"] == "error"
    ]
    assert len(error_calls) == 1
    assert error_calls[0][1]["content"] == "CONNECTION_ERROR"

    # bot_run 应标记为 FAILED，而非 COMPLETED
    repo.update_error.assert_called_once_with("r-err-chunk", "CONNECTION_ERROR")
    repo.update_result.assert_not_called()


# ----------------------------- stream engine_type 透传 -----------------------------


async def test_executor_stream_engine_type_in_delta():
    """delta chunk 携带 engine_type 时，flush 写入 metadata JSON。"""
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="r-et-d",
        bot_id="bot-1:ent",
        metadata={"request_type": "chat", "stream": "true"},
    )
    plugin.get_binding = AsyncMock(return_value=_binding_data())

    chunks = [
        StreamChunk(type="delta", content="hi", engine_type="openclaw"),
        StreamChunk(type="final", content="done"),
    ]

    async def _stream_gen(*a, **kw):
        for c in chunks:
            yield c

    bot_svc = MagicMock()
    bot_svc.send_message_stream = _stream_gen
    selector.select.return_value = bot_svc

    chunk_repo = MagicMock()
    executor = BotRunRequestExecutor(
        repo, plugin, selector, chunk_repo, MagicMock(), _api_key_repo(), MagicMock()
    )
    await executor.execute(
        _queue_rec(run_id="r-et-d", bot_id="bot-1:ent", session_id="sess-d")
    )

    delta_calls = [
        c
        for c in chunk_repo.insert_chunk.call_args_list
        if c[1]["chunk_type"] == "delta"
    ]
    assert len(delta_calls) == 1
    meta = json.loads(delta_calls[0][1]["metadata"])
    assert meta["engine_type"] == "openclaw"


async def test_executor_stream_engine_type_in_agent():
    """agent chunk 携带 engine_type 时，flush 写入 metadata JSON。"""
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="r-et-a",
        bot_id="bot-1:ent",
        metadata={"request_type": "chat", "stream": "true"},
    )
    plugin.get_binding = AsyncMock(return_value=_binding_data())

    chunks = [
        StreamChunk(
            type="agent",
            content="",
            metadata={"frame": 1},
            engine_type="dify",
        ),
        StreamChunk(type="final", content="done"),
    ]

    async def _stream_gen(*a, **kw):
        for c in chunks:
            yield c

    bot_svc = MagicMock()
    bot_svc.send_message_stream = _stream_gen
    selector.select.return_value = bot_svc

    chunk_repo = MagicMock()
    executor = BotRunRequestExecutor(
        repo, plugin, selector, chunk_repo, MagicMock(), _api_key_repo(), MagicMock()
    )
    await executor.execute(
        _queue_rec(run_id="r-et-a", bot_id="bot-1:ent", session_id="sess-a")
    )

    agent_calls = [
        c
        for c in chunk_repo.insert_chunk.call_args_list
        if c[1]["chunk_type"] == "agent"
    ]
    assert len(agent_calls) == 1
    meta = json.loads(agent_calls[0][1]["metadata"])
    assert meta["engine_type"] == "dify"


async def test_executor_stream_engine_type_in_final_with_metadata():
    """final chunk 同时携带 metadata 和 engine_type 时，合并写入 metadata。"""
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="r-et-f",
        bot_id="bot-1:ent",
        metadata={"request_type": "chat", "stream": "true"},
    )
    plugin.get_binding = AsyncMock(return_value=_binding_data())

    chunks = [
        StreamChunk(
            type="final",
            content="result",
            metadata={"extra": "val"},
            engine_type="openclaw",
        ),
    ]

    async def _stream_gen(*a, **kw):
        for c in chunks:
            yield c

    bot_svc = MagicMock()
    bot_svc.send_message_stream = _stream_gen
    selector.select.return_value = bot_svc

    chunk_repo = MagicMock()
    executor = BotRunRequestExecutor(
        repo, plugin, selector, chunk_repo, MagicMock(), _api_key_repo(), MagicMock()
    )
    await executor.execute(
        _queue_rec(run_id="r-et-f", bot_id="bot-1:ent", session_id="sess-f")
    )

    final_calls = [
        c
        for c in chunk_repo.insert_chunk.call_args_list
        if c[1]["chunk_type"] == "final"
    ]
    assert len(final_calls) == 1
    meta = json.loads(final_calls[0][1]["metadata"])
    assert meta["extra"] == "val"
    assert meta["engine_type"] == "openclaw"


async def test_executor_stream_engine_type_in_error_with_metadata():
    """error chunk 携带 metadata + engine_type 时，合并写入 metadata。"""
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="r-et-e",
        bot_id="bot-1:ent",
        metadata={"request_type": "chat", "stream": "true"},
    )
    plugin.get_binding = AsyncMock(return_value=_binding_data())

    chunks = [
        StreamChunk(
            type="error",
            content="boom",
            metadata={"code": 500},
            engine_type="dify",
        ),
    ]

    async def _stream_gen(*a, **kw):
        for c in chunks:
            yield c

    bot_svc = MagicMock()
    bot_svc.send_message_stream = _stream_gen
    selector.select.return_value = bot_svc

    chunk_repo = MagicMock()
    executor = BotRunRequestExecutor(
        repo, plugin, selector, chunk_repo, MagicMock(), _api_key_repo(), MagicMock()
    )
    await executor.execute(
        _queue_rec(run_id="r-et-e", bot_id="bot-1:ent", session_id="sess-e")
    )

    error_calls = [
        c
        for c in chunk_repo.insert_chunk.call_args_list
        if c[1]["chunk_type"] == "error"
    ]
    assert len(error_calls) == 1
    meta = json.loads(error_calls[0][1]["metadata"])
    assert meta["code"] == 500
    assert meta["engine_type"] == "dify"


# ----------------------------- stream 字节阈值提前 flush（规避 ZDAS 1064） -----------------------------


async def test_executor_stream_agent_byte_threshold_splits_chunks():
    """stream 模式：agent buffer 累积字节 >= 阈值时提前 flush，拆为多条 agent chunk，
    每条 content 仍是合法 JSON array，回放侧按 seq 顺序 json.loads 拼接后等于原始事件。"""
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="r-ag-thr",
        bot_id="bot-1:ent",
        metadata={"request_type": "chat", "stream": "true"},
    )
    plugin.get_binding = AsyncMock(return_value=_binding_data())

    # 构造 N 个 agent 事件，使合并 content 字节超过阈值（默认 64KB）。
    # 每个 metadata 约 100B，N=800 -> ~80KB > 64KB，应触发至少一次字节阈值 flush。
    per_frame = {"engine_frame": {"stream": "tool", "phase": "x"}, "data": "x" * 80}
    n_frames = 800
    chunks = [
        StreamChunk(type="agent", content="", metadata=per_frame)
        for _ in range(n_frames)
    ]
    chunks.append(StreamChunk(type="final", content="done"))

    async def _stream_gen(*a, **kw):
        for c in chunks:
            yield c

    bot_svc = MagicMock()
    bot_svc.send_message_stream = _stream_gen
    selector.select.return_value = bot_svc

    chunk_repo = MagicMock()
    executor = BotRunRequestExecutor(
        repo,
        plugin,
        selector,
        chunk_repo,
        MagicMock(),
        _api_key_repo(),
        MagicMock(),
        stream_flush_max_content_bytes=4096,
    )
    await executor.execute(
        _queue_rec(run_id="r-ag-thr", bot_id="bot-1:ent", session_id="sess-a")
    )

    insert_calls = chunk_repo.insert_chunk.call_args_list
    agent_calls = [c for c in insert_calls if c[1]["chunk_type"] == "agent"]
    final_calls = [c for c in insert_calls if c[1]["chunk_type"] == "final"]

    # 字节阈值触发拆分：agent chunk 数 > 1
    assert len(agent_calls) > 1, "agent buffer should be split by byte threshold"
    # final 仍为 1 行
    assert len(final_calls) == 1

    # 每条 agent chunk content 仍是合法 JSON array
    all_frames = []
    for ac in agent_calls:
        frames = json.loads(ac[1]["content"])
        assert isinstance(frames, list)
        all_frames.extend(frames)

    # 按 seq 顺序拼接的帧等于原始事件集合
    assert len(all_frames) == n_frames

    # seq 严格递增
    seqs = [ac[1]["seq"] for ac in agent_calls] + [final_calls[0][1]["seq"]]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


async def test_executor_stream_delta_byte_threshold_splits_chunks():
    """stream 模式：delta buffer 累积字节 >= 阈值时提前 flush，拆为多条 delta chunk。"""
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="r-dl-thr",
        bot_id="bot-1:ent",
        metadata={"request_type": "chat", "stream": "true"},
    )
    plugin.get_binding = AsyncMock(return_value=_binding_data())

    # 构造连续 delta 事件，累积字节超过阈值。
    piece = "x" * 1024
    n_pieces = 10  # 10KB > 4KB 阈值
    chunks = [StreamChunk(type="delta", content=piece) for _ in range(n_pieces)]
    chunks.append(StreamChunk(type="final", content="done"))

    async def _stream_gen(*a, **kw):
        for c in chunks:
            yield c

    bot_svc = MagicMock()
    bot_svc.send_message_stream = _stream_gen
    selector.select.return_value = bot_svc

    chunk_repo = MagicMock()
    executor = BotRunRequestExecutor(
        repo,
        plugin,
        selector,
        chunk_repo,
        MagicMock(),
        _api_key_repo(),
        MagicMock(),
        stream_flush_max_content_bytes=4096,
    )
    await executor.execute(
        _queue_rec(run_id="r-dl-thr", bot_id="bot-1:ent", session_id="sess-d")
    )

    insert_calls = chunk_repo.insert_chunk.call_args_list
    delta_calls = [c for c in insert_calls if c[1]["chunk_type"] == "delta"]
    final_calls = [c for c in insert_calls if c[1]["chunk_type"] == "final"]

    # 字节阈值触发拆分：delta chunk 数 > 1
    assert len(delta_calls) > 1, "delta buffer should be split by byte threshold"
    assert len(final_calls) == 1

    # 各 delta chunk content 拼接后等于原始 delta 内容
    merged = "".join(c[1]["content"] for c in delta_calls)
    assert merged == piece * n_pieces


async def test_executor_stream_byte_threshold_not_triggered_preserves_merge():
    """累积字节 < 阈值时不触发拆分，既有 agent/delta 合并语义不变。"""
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="r-no-thr",
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

    async def _stream_gen(*a, **kw):
        for c in chunks:
            yield c

    bot_svc = MagicMock()
    bot_svc.send_message_stream = _stream_gen
    selector.select.return_value = bot_svc

    chunk_repo = MagicMock()
    # 阈值足够大，这批小 payload 不会触发字节 flush
    executor = BotRunRequestExecutor(
        repo,
        plugin,
        selector,
        chunk_repo,
        MagicMock(),
        _api_key_repo(),
        MagicMock(),
        stream_flush_max_content_bytes=1 << 20,
    )
    await executor.execute(
        _queue_rec(run_id="r-no-thr", bot_id="bot-1:ent", session_id="sess-n")
    )

    insert_calls = chunk_repo.insert_chunk.call_args_list
    agent_calls = [c for c in insert_calls if c[1]["chunk_type"] == "agent"]
    delta_calls = [c for c in insert_calls if c[1]["chunk_type"] == "delta"]
    final_calls = [c for c in insert_calls if c[1]["chunk_type"] == "final"]

    # 与既有 test_executor_stream_agent_merge 行为一致：不拆分
    assert len(agent_calls) == 1
    assert len(delta_calls) == 1
    assert len(final_calls) == 1
    agent_data = json.loads(agent_calls[0][1]["content"])
    assert len(agent_data) == 2
    assert delta_calls[0][1]["content"] == "hello"


# ----------------------------- 背压（队列深度 → 429） -----------------------------


def test_dispatch_send_backpressure_rejects():
    from secbaas.community.api.bot_runtime import TooManyRequestsError

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
    from secbaas.community.core.service.bot_run import QueueTaskMessageDispatcher

    return QueueTaskMessageDispatcher(
        run_repository=repo,
        queue_repository=queue,
        chunk_repository=MagicMock(),
        cache_plugin=MagicMock(),
        max_queue_depth=depth,
    )


# ----------------------------- _should_cleanup_chunks config tests -----------------------------


def _dispatcher_with_config(config_service=None):
    from secbaas.community.core.service.bot_run import QueueTaskMessageDispatcher

    return QueueTaskMessageDispatcher(
        run_repository=MagicMock(),
        queue_repository=MagicMock(),
        chunk_repository=MagicMock(),
        cache_plugin=MagicMock(),
        system_config_service=config_service,
    )


def _config_response(value: str | None):
    from datetime import datetime

    from secbaas.community.api.config_manage import SystemConfigResponse

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

    def test_config_none_returns_true(self):
        """When config record does not exist (None), cleanup is enabled by default."""
        mock_service = MagicMock()
        mock_service.get_config.return_value = None
        dispatcher = _dispatcher_with_config(config_service=mock_service)
        assert dispatcher._should_cleanup_chunks() is True

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

    def test_config_read_exception_returns_true(self):
        """When get_config raises, cleanup is enabled by default (fail-open)."""
        mock_service = MagicMock()
        mock_service.get_config.side_effect = RuntimeError("db down")
        dispatcher = _dispatcher_with_config(config_service=mock_service)
        assert dispatcher._should_cleanup_chunks() is True


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
        from secbaas.community.core.service.bot_run import QueueTaskMessageDispatcher

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
        from secbaas.community.core.service.bot_run import QueueTaskMessageDispatcher

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


# ----------------------------- Attachment reconstruction from meta (D-04 Step B) -----------------------------


async def test_executor_rebuilds_attachments_from_meta():
    """Worker reads attachments from queue record meta and rebuilds Attachment dataclass objects."""
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
    bot_svc.send_message = AsyncMock(return_value=BotResponse(content="ok", usage=None))
    selector.select.return_value = bot_svc

    executor = BotRunRequestExecutor(
        repo, plugin, selector, MagicMock(), MagicMock(), _api_key_repo(), MagicMock()
    )
    await executor.execute(
        _queue_rec(
            run_id="r1",
            bot_id="bot-1:ent",
            session_id="sess-1",
            meta={
                "attachments": [
                    {
                        "attachment_id": "att_1",
                        "type": "image",
                        "file_name": "f1.png",
                        "url": "https://cdn.example.com/f1",
                    },
                    {
                        "attachment_id": "att_2",
                        "type": "image",
                        "file_name": "f2.png",
                        "url": "https://cdn.example.com/f2",
                    },
                ],
            },
        )
    )

    bot_svc.send_message.assert_awaited_once()
    call_kwargs = bot_svc.send_message.call_args.kwargs
    attachments = call_kwargs["attachments"]

    assert len(attachments) == 2
    # Verify rebuilt objects are domain Attachment dataclass instances
    assert isinstance(attachments[0], Attachment)
    assert attachments[0].attachment_id == "att_1"
    assert attachments[0].type == "image"
    assert attachments[0].file_name == "f1.png"
    assert attachments[0].url == "https://cdn.example.com/f1"

    assert isinstance(attachments[1], Attachment)
    assert attachments[1].attachment_id == "att_2"

    # Verify repo state updates still called
    repo.update_status.assert_called_once_with("r1", "RUNNING")
    repo.update_result.assert_called_once()


async def test_executor_handles_missing_attachments_in_meta():
    """Worker does not crash when queue record meta has no 'attachments' key."""
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
    bot_svc.send_message = AsyncMock(return_value=BotResponse(content="ok", usage=None))
    selector.select.return_value = bot_svc

    executor = BotRunRequestExecutor(
        repo, plugin, selector, MagicMock(), MagicMock(), _api_key_repo(), MagicMock()
    )
    await executor.execute(
        _queue_rec(run_id="r1", bot_id="bot-1:ent", session_id="sess-1")
    )

    bot_svc.send_message.assert_awaited_once()
    call_kwargs = bot_svc.send_message.call_args.kwargs
    # attachments should be None when meta has no attachments key
    assert call_kwargs["attachments"] is None

    # Verify repo state updates still called normally
    repo.update_status.assert_called_once_with("r1", "RUNNING")
    repo.update_result.assert_called_once()


# ----------------------------- eval_session_log 注入 -----------------------------


async def test_executor_eval_session_log_enriches_chat_metadata():
    """eval_session_log 非空时，build_chat_metadata 传入 eval_session_log，enrich_chat_metadata 被调用。"""
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="r-eval",
        bot_id="bot-1:ent",
        metadata={
            "app_id": "a",
            "app_type": "T",
            "tenant": "t",
            "request_type": "chat",
            "eval_id": "eval-abc",
            "default_tag": "eval",
        },
    )
    plugin.get_binding = AsyncMock(return_value=_binding_data())

    bot_svc = MagicMock()
    bot_svc.send_message = AsyncMock(
        return_value=BotResponse(content="eval response", usage=None)
    )
    selector.select.return_value = bot_svc

    # 构造 mock eval_session_log
    eval_log = MagicMock()
    eval_log.enrich_chat_metadata.return_value = {
        "biz_task_id": "r-eval",
        "biz_scene": "eval:eval",
        "eval_observed": "true",
        "eval_run_id": "eval-abc",
    }

    executor = BotRunRequestExecutor(
        repo, plugin, selector, MagicMock(), MagicMock(), _api_key_repo(),
        eval_session_log=eval_log,
    )
    await executor.execute(
        _queue_rec(run_id="r-eval", bot_id="bot-1:ent", session_id="sess-eval")
    )

    # enrich_chat_metadata 应被调用
    eval_log.enrich_chat_metadata.assert_called_once()

    # log_eval_session 应被调用
    eval_log.log_eval_session.assert_called_once_with(
        eval_id="eval-abc",
        bot_id="bot-1:ent",
        session_id="sess-eval",
        method="execute",
    )

    # chat_metadata 传入 send_message 应包含 eval 观测字段
    call_kwargs = bot_svc.send_message.call_args.kwargs
    assert call_kwargs["chat_metadata"]["eval_observed"] == "true"
    assert call_kwargs["chat_metadata"]["eval_run_id"] == "eval-abc"


async def test_executor_without_eval_id_does_not_write_eval_session_log():
    """The required eval plugin enriches metadata without recording a normal run."""
    repo = MagicMock()
    plugin = MagicMock()
    selector = MagicMock()

    repo.get_by_run_id.return_value = _run(
        run_id="r-no-eval",
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
    bot_svc.send_message = AsyncMock(
        return_value=BotResponse(content="normal response", usage=None)
    )
    selector.select.return_value = bot_svc
    eval_log = MagicMock()
    eval_log.enrich_chat_metadata.return_value = {
        "biz_task_id": "r-no-eval",
        "biz_scene": "default",
    }

    executor = BotRunRequestExecutor(
        repo, plugin, selector, MagicMock(), MagicMock(), _api_key_repo(),
        eval_session_log=eval_log,
    )
    await executor.execute(
        _queue_rec(run_id="r-no-eval", bot_id="bot-1:ent", session_id="sess-normal")
    )

    bot_svc.send_message.assert_awaited_once()
    eval_log.enrich_chat_metadata.assert_called_once()
    eval_log.log_eval_session.assert_not_called()
