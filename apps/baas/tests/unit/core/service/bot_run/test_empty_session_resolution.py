"""Regression coverage for empty session IDs in BotRunner."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.service.bot_run import BotRunner
from tests.unit.core.service.bot_run.test_runner import BOT_ID, ENTITY_ID, _make_runner

pytest_plugins = ("tests.unit.core.service.bot_run.test_runner",)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_type", ["openclaw", "teclaw"])
@pytest.mark.parametrize("session_id", [None, ""])
async def test_missing_and_empty_plan_a_session(engine_type, session_id):
    runner = MagicMock()
    route = SimpleNamespace(
        binding_info=SimpleNamespace(engine_type=engine_type, bot_id="bot"),
        route_bot_id="bot",
    )
    with (
        patch(
            "secbaas.community.core.service.bot_run._runner.resolve_user_id",
            return_value="user",
        ),
        patch(
            "secbaas.community.core.service.bot_run._runner.plan_session_id",
            return_value="planned-session",
        ),
    ):
        result = await BotRunner._resolve_session(
            runner,
            run_id="run",
            session_id=session_id,
            metadata={},
            route=route,
            context=MagicMock(),
        )
    assert result == ("planned-session", True)
    runner._run_repository.update_session_id.assert_called_once_with(
        "run", "planned-session"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method", ["deliver_message", "inject_message", "deliver_message_stream"]
)
async def test_empty_session_is_planned_across_message_entrypoints(
    method,
    mock_selector,
    mock_run_repo,
    mock_bot_service_plugin,
    arca_binding_data,
    context,
):
    mock_bot_service_plugin.get_binding.return_value = arca_binding_data
    mock_run_repo.get_by_run_id.return_value = None
    runner = _make_runner(mock_selector, mock_run_repo, mock_bot_service_plugin)

    async def empty_stream():
        if False:
            yield None

    runner._dispatchers[0].dispatch_send_stream = MagicMock(return_value=empty_stream())
    result = await getattr(runner, method)(
        bot_id=f"{BOT_ID}:{ENTITY_ID}",
        message="hello",
        context=context,
        metadata={"session_id": ""},
        message_id="empty-session-run",
    )
    assert result[1]
    dispatch_name = {
        "deliver_message": "dispatch_send",
        "inject_message": "dispatch_inject",
        "deliver_message_stream": "dispatch_send_stream",
    }[method]
    kwargs = getattr(runner._dispatchers[0], dispatch_name).call_args.kwargs
    assert kwargs["session_id"] == result[1]
    assert kwargs["session_pending"] is True
    mock_run_repo.update_session_id.assert_called_once_with(
        "empty-session-run", result[1]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_type", ["openclaw", "teclaw"])
@pytest.mark.parametrize("eval_id", [None, "eval-1"])
async def test_empty_session_uses_real_adapter_planning(engine_type, eval_id):
    from secbaas.community.plugins.bot.engine_adapter.openclaw.real import (
        OpenClawAdapter,
    )
    from secbaas.community.plugins.bot.engine_adapter.teclaw.real import TeClawAdapter

    adapter = {"openclaw": OpenClawAdapter(), "teclaw": TeClawAdapter()}[engine_type]
    runner = MagicMock()
    runner._adapter_for.return_value = adapter
    route = SimpleNamespace(
        binding_info=SimpleNamespace(
            engine_type=engine_type, bot_id="bot", bot_type="shared", entity_id="owner"
        ),
        route_bot_id="bot",
    )

    async def resolve(run_id, user_id, session_id=""):
        return await BotRunner._resolve_session(
            runner,
            run_id=run_id,
            session_id=session_id,
            metadata={"eval_id": eval_id},
            route=route,
            context=SimpleNamespace(app_type="baas", app_id=user_id),
        )

    first = await resolve("run-1", "user-1")
    omitted = await resolve("run-1", "user-1", None)
    other_run = await resolve("run-2", "user-1")
    other_user = await resolve("run-1", "user-2")
    marker = "session" if engine_type == "openclaw" else "default"
    assert (
        first
        == omitted
        == (f"agent:main:{marker}:{eval_id or 'run-1'}:user:user-1", True)
    )
    assert (first == other_run) is bool(eval_id)
    assert first != other_user


@pytest.mark.asyncio
async def test_teclaw_empty_session_materializes_with_planned_key():
    from unittest.mock import AsyncMock

    from secbaas.community.plugins.bot.engine_adapter.teclaw.real import TeClawAdapter

    adapter = TeClawAdapter()
    runner = MagicMock()
    runner._adapter_for.return_value = adapter
    route = SimpleNamespace(
        binding_info=SimpleNamespace(
            engine_type="teclaw", bot_id="bot", bot_type="shared", entity_id="owner"
        ),
        route_bot_id="bot",
    )
    planned_id, pending = await BotRunner._resolve_session(
        runner,
        run_id="run-1",
        session_id="",
        metadata={},
        route=route,
        context=SimpleNamespace(app_type="baas", app_id="user-1"),
    )
    client = SimpleNamespace(
        get_session=AsyncMock(side_effect=LookupError("missing")),
        create_session=AsyncMock(return_value=SimpleNamespace(id=planned_id)),
    )
    result = await adapter.create_adapter_session(
        session_client=client,
        planned_id=planned_id,
        user_id="user-1",
        metadata={},
        bot_id="bot",
        session_pending=pending,
    )
    assert result == (planned_id, False)
    client.get_session.assert_awaited_once_with(planned_id, "teclaw")
    assert client.create_session.await_args.kwargs["session_id"] == planned_id
    assert planned_id
