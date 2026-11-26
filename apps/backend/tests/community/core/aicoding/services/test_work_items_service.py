"""Core behavior tests for the cross-Bot AICoding work-items snapshot."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.aicoding.services.work_items_service import (
    AICodingWorkItemsService,
)
from agentclaw.community.core.aicoding.work_item_bots import WorkItemsBotTarget
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)

_TARGET_1 = WorkItemsBotTarget("bot-1", "Bot 1", "owner-1")
_TARGET_2 = WorkItemsBotTarget("bot-2", "Bot 2", "owner-2")
_SESSION_PATH = "/api/aicoding/sessions"


def _target(bot_id: str = "bot-1", owner_id: str = "owner-1") -> WorkItemsBotTarget:
    return WorkItemsBotTarget(bot_id, f"Bot {bot_id}", owner_id)


def _service(
    *, targets: list[WorkItemsBotTarget] | None = None, contexts: list[object]
) -> tuple[AICodingWorkItemsService, MagicMock, MagicMock]:
    lister = MagicMock()
    lister.list_bot_targets.return_value = targets or []
    resolver = MagicMock(spec=DeviceContextResolver)
    if isinstance(contexts, list):
        resolver.resolve_for_bot.side_effect = contexts
    else:
        resolver.resolve_for_bot.return_value = contexts
    transport = MagicMock(spec=DeviceAdapterTransport)
    transport.invoke = AsyncMock()
    return (
        AICodingWorkItemsService(lister, resolver, transport),  # type: ignore[arg-type]
        resolver,
        transport,
    )


def _context(
    conn_info: dict[str, str] | None = None, provider: str = "baas"
) -> SimpleNamespace:
    return SimpleNamespace(provider=provider, conn_info=conn_info or {"type": "baas"})


def _session(
    session_key: str, *, run_status: str = "running", extra: dict | None = None
) -> dict:
    return {
        "id": session_key,
        "user_id": "315629",
        "title": "Task",
        "gmt_modified": "1794000000000Z",
        "run_status": run_status,
        "cwd": "/workspace",
        "model": "model-1",
        **(extra or {}),
    }


@pytest.mark.asyncio
async def test_relay_query_uses_user_id_as_the_classification_viewer():
    service, _, transport = _service(
        targets=[_TARGET_1], contexts=[_context({"type": "baas"})]
    )
    transport.invoke.return_value = {"success": True, "data": []}

    data = await service.get_work_items(
        current_user_id="315629",
        source_filter=["mine"],
        since_ms=1_794_000_000_000,
        run_status_filter=["non_idle"],
        scan_limit=25,
    )

    assert data.partial is False
    assert transport.invoke.call_args.args[:3] == (
        {"type": "baas"},
        "GET",
        _SESSION_PATH,
    )
    params = transport.invoke.call_args.kwargs["params"]
    assert params == {
        "user_id": "315629",
        "session_kind": "user",
        "limit": 25,
        "offset": 0,
        "source": "mine",
        "since_ms": 1_794_000_000_000,
        "run_status": "non_idle",
    }


@pytest.mark.asyncio
async def test_bot_lookup_ambiguity_is_isolated_to_one_bot():
    service, _, transport = _service(
        targets=[_TARGET_1, _TARGET_2],
        contexts=[
            BotLookupAmbiguousError("ambiguous bot"),
            _context({"type": "baas"}),
        ],
    )
    transport.invoke.return_value = {"success": True, "data": []}

    data = await service.get_work_items(
        current_user_id="315629",
        source_filter=None,
        since_ms=None,
        run_status_filter=None,
        scan_limit=50,
    )

    assert data.partial is True
    assert [summary.bot_id for summary in data.bots] == ["bot-1", "bot-2"]
    assert data.bots[0].status == "error"
    assert data.bots[0].error_code == "RESOLVE_ERROR"
    assert data.bots[1].status == "ok"


@pytest.mark.parametrize(
    ("bad_response",),
    [
        ({"success": True},),
        ({"success": True, "data": {"id": "not-a-list"}},),
    ],
    ids=["missing-data", "non-list-data"],
)
@pytest.mark.asyncio
async def test_malformed_success_data_is_isolated_to_one_bot(bad_response):
    service, _, transport = _service(
        targets=[_TARGET_1, _TARGET_2],
        contexts=[_context(), _context({"type": "baas", "target": "bot-2"})],
    )
    transport.invoke.side_effect = [
        bad_response,
        {
            "success": True,
            "data": [
                _session(
                    "user:315629:session:s2:agent:bot-2",
                    run_status="awaitingHuman",
                )
            ],
        },
    ]

    data = await service.get_work_items(
        current_user_id="315629",
        source_filter=None,
        since_ms=None,
        run_status_filter=None,
        scan_limit=50,
    )

    assert data.partial is True
    assert data.bots[0].status == "error"
    assert data.bots[0].error_code == "ENGINE_MALFORMED_RESPONSE"
    assert data.bots[1].status == "ok"
    assert [item.session_key for item in data.items] == [
        "user:315629:session:s2:agent:bot-2"
    ]


@pytest.mark.asyncio
async def test_local_provider_uses_each_resolved_bot_connection():
    local_1 = _context({"type": "local", "port": 31001}, provider="local")
    local_2 = _context({"type": "local", "port": 31002}, provider="local")
    service, _, transport = _service(
        targets=[_TARGET_1, _TARGET_2], contexts=[local_1, local_2]
    )
    transport.invoke.side_effect = [
        {
            "success": True,
            "data": [_session("user:315629:session:s1:agent:bot-1")],
        },
        {
            "success": True,
            "data": [_session("user:315629:session:s2:agent:bot-2")],
        },
    ]

    data = await service.get_work_items(
        current_user_id="315629",
        source_filter=None,
        since_ms=None,
        run_status_filter=None,
        scan_limit=50,
    )

    assert data.partial is False
    invoked_conn_infos = [call.args[0] for call in transport.invoke.await_args_list]
    assert invoked_conn_infos == [
        {"type": "local", "port": 31001},
        {"type": "local", "port": 31002},
    ]
    assert [summary.status for summary in data.bots] == ["ok", "ok"]
    assert [item.bot_id for item in data.items] == ["bot-1", "bot-2"]


@pytest.mark.asyncio
async def test_work_item_projection_closes_engine_ext_info():
    service, _, transport = _service(
        targets=[_TARGET_1], contexts=[_context({"type": "baas"})]
    )
    transport.invoke.return_value = {
        "success": True,
        "data": [
            _session(
                "user:315629:session:s1:agent:bot-1",
                extra={
                    "ext_info": {
                        "dimaItemId": "internal-item-id",
                        "pullRequestUrl": "https://example.test/private-pr",
                    }
                },
            )
        ],
    }

    data = await service.get_work_items(
        current_user_id="315629",
        source_filter=None,
        since_ms=None,
        run_status_filter=None,
        scan_limit=50,
    )

    assert len(data.items) == 1
    assert "ext_info" not in data.items[0].model_dump()
    assert data.items[0].source == "mine"
    assert data.items[0].run_status == "running"


@pytest.mark.asyncio
async def test_old_relay_page_before_filter_can_return_an_empty_snapshot():
    """Old Relay truncates before applying the newer filters.

    A page containing only an idle row therefore means the recent scan window
    has no work item; it is not a Bot failure and must not set ``partial``.
    New Relay avoids this window miss by filtering before pagination.
    """
    service, _, transport = _service(
        targets=[_TARGET_1], contexts=[_context({"type": "baas"})]
    )
    transport.invoke.return_value = {
        "success": True,
        "data": [_session("user:315629:session:s1:agent:bot-1", run_status="idle")],
    }

    data = await service.get_work_items(
        current_user_id="315629",
        source_filter=None,
        since_ms=None,
        run_status_filter=["running"],
        scan_limit=1,
    )

    assert data.items == []
    assert data.bots[0].status == "ok"
    assert data.bots[0].count == 0
    assert data.partial is False
    assert data.scan_limit == 1
    assert data.filters.run_status == ["running"]
