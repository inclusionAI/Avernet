"""Unit tests for the extracted MCP config read/write flow.

No HTTP: the flow is driven directly with mocked services, asserting the
load-bearing ordering (an unknown server never reaches the database), the
rollback-on-sync-failure guarantee (restore on update, delete on create), and
that the returned key is masked.

The suite has no async plugin, so the one ``async def`` under test is driven
through ``asyncio.run`` from sync test functions.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.mcp.config_flow import (
    delete_unified_config,
    list_unified_configs,
    read_unified_config,
    write_unified_config,
)
from agentclaw.community.core.mcp.errors import (
    McpConfigValueError,
    McpHeadersInvalidError,
    McpServerNotFoundError,
    McpSyncFailedError,
)


def _config_service(*, existing=None):
    m = MagicMock()
    m.get_user_unified_config.return_value = existing
    m.validate_headers_for_mcp.return_value = {"valid": True, "error": None}
    # update returns the *prior* config (None when the row was newly created).
    m.update_user_unified_config.return_value = None
    return m


_UNSET = object()


def _market_service(*, detail=_UNSET):
    m = MagicMock()
    m.get_mcp_detail.return_value = (
        {"serverCode": "s"} if detail is _UNSET else detail
    )
    return m


def _sync_service(*, success=True, sync_results=None, error=None):
    m = MagicMock()
    m.sync_mcp_detail_to_all_bots = AsyncMock(
        return_value={
            "success": success,
            "sync_results": sync_results if sync_results is not None else [],
            "error": error,
        }
    )
    return m


def _write(cfg=None, mkt=None, sync=None, **overrides):
    params = dict(
        user_id="u1",
        server_code="mcp.s",
        entity_id="u1",
        entity_type="staff",
        api_key="sk-abcdefghijkl",
        headers=None,
        endpoint_env="PROD",
        transport_protocol="sse",
    )
    params.update(overrides)
    return asyncio.run(
        write_unified_config(
            **params,
            config_service=cfg or _config_service(),
            market_service=mkt or _market_service(),
            sync_service=sync or _sync_service(),
        )
    )


# ── read ────────────────────────────────────────────────────────────


def test_read_absent_reports_no_config_with_defaults():
    out = read_unified_config(
        user_id="u1", server_code="mcp.s", config_service=_config_service()
    )
    assert out.has_config is False
    assert out.endpoint_env == "PROD"
    assert out.headers == {}
    assert out.api_key is None


def test_read_present_masks_key_and_carries_values():
    cfg = _config_service(
        existing={
            "api_key": "sk-abcdefghijkl",
            "headers": {"h": "v"},
            "endpoint_env": "PRE",
            "transport_protocol": "SSE",
        }
    )
    out = read_unified_config(user_id="u1", server_code="mcp.s", config_service=cfg)
    assert out.api_key == "sk-a****ijkl"
    assert out.endpoint_env == "PRE"
    assert out.headers == {"h": "v"}
    assert out.has_config is True


# ── write: success ──────────────────────────────────────────────────


def test_write_success_returns_masked_write_shaped_config():
    out = _write()
    assert out.api_key == "sk-a****ijkl"  # masked
    assert out.transport_protocol == "SSE"  # upper-cased
    assert out.headers is None  # write path does not echo headers
    assert out.has_config is True


def test_write_forwards_params_for_merge_and_push():
    cfg = _config_service()
    sync = _sync_service()
    _write(cfg=cfg, sync=sync, endpoint_env=None, api_key=None, headers=None)
    # An omitted field is forwarded as None so config_service merges (not replace).
    _, kw = cfg.update_user_unified_config.call_args
    assert kw["endpoint_env"] is None and kw["api_key"] is None
    # And the same identity/values reach the device push.
    _, sync_kw = sync.sync_mcp_detail_to_all_bots.call_args
    assert sync_kw["entity_id"] == "u1" and sync_kw["entity_type"] == "staff"


# ── write: ordering — bad server never reaches the DB ───────────────


def test_unknown_server_raises_before_any_write():
    cfg = _config_service()
    with pytest.raises(McpServerNotFoundError):
        _write(cfg=cfg, mkt=_market_service(detail=None))
    cfg.update_user_unified_config.assert_not_called()


def test_bad_endpoint_env_raises_before_any_write():
    cfg = _config_service()
    with pytest.raises(McpConfigValueError):
        _write(cfg=cfg, endpoint_env="NOPE")
    cfg.update_user_unified_config.assert_not_called()
    cfg.get_user_unified_config.assert_not_called()  # nothing touched


def test_invalid_headers_raise_before_any_write():
    cfg = _config_service()
    cfg.validate_headers_for_mcp.return_value = {"valid": False, "error": "bad"}
    with pytest.raises(McpHeadersInvalidError):
        _write(cfg=cfg, headers={"": "x"})
    cfg.update_user_unified_config.assert_not_called()


# ── write: rollback on sync failure ─────────────────────────────────


def test_sync_failure_rolls_back_update_and_raises():
    # The row existed before this call → update returns the prior config.
    prior = {"api_key": "old", "headers": {}, "endpoint_env": "PROD"}
    cfg = _config_service()
    cfg.update_user_unified_config.return_value = prior
    sync = _sync_service(success=False, error="device down")
    with pytest.raises(McpSyncFailedError):
        _write(cfg=cfg, sync=sync)
    # Rollback restores the prior config, not None.
    _, kw = cfg.rollback_unified_config.call_args
    assert kw["old_config"] == prior


def test_sync_failure_after_create_rolls_back_as_delete():
    # New row → update returns None → rollback receives None → delete path.
    cfg = _config_service()
    cfg.update_user_unified_config.return_value = None
    sync = _sync_service(success=False, error="device down")
    with pytest.raises(McpSyncFailedError):
        _write(cfg=cfg, sync=sync)
    _, kw = cfg.rollback_unified_config.call_args
    assert kw["old_config"] is None


def test_sync_raising_also_rolls_back_and_raises_sync_failure():
    # The sync service contracts to return a failure dict, but if the push
    # raises instead the freshly written row must still be rolled back — a
    # stored-but-unpushed credential would violate the atomic write-and-push
    # contract. The exception surfaces as McpSyncFailedError like any other
    # push failure, so each surface maps it with the row already restored.
    prior = {"api_key": "old", "headers": {}, "endpoint_env": "PROD"}
    cfg = _config_service()
    cfg.update_user_unified_config.return_value = prior
    sync = MagicMock()
    sync.sync_mcp_detail_to_all_bots = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(McpSyncFailedError, match="boom"):
        _write(cfg=cfg, sync=sync)
    cfg.rollback_unified_config.assert_called_once()
    _, kw = cfg.rollback_unified_config.call_args
    assert kw["old_config"] == prior


# ── list ────────────────────────────────────────────────────────────


def _listing_service(rows):
    m = MagicMock()
    m.list_user_unified_configs.return_value = rows
    return m


def _row(server_code, **over):
    row = {
        "server_code": server_code,
        "api_key": None,
        "headers": {},
        "endpoint_env": "PROD",
        "transport_protocol": None,
    }
    row.update(over)
    return row


def test_list_returns_total_across_all_rows_not_just_the_page():
    cfg = _listing_service([_row(f"mcp.s{i}") for i in range(5)])
    total, items = list_unified_configs(
        user_id="u1", page=1, page_size=2, config_service=cfg
    )
    assert total == 5
    assert [i.server_code for i in items] == ["mcp.s0", "mcp.s1"]


def test_list_pages_through_and_last_page_is_short():
    cfg = _listing_service([_row(f"mcp.s{i}") for i in range(5)])
    _, page2 = list_unified_configs(
        user_id="u1", page=2, page_size=2, config_service=cfg
    )
    _, page3 = list_unified_configs(
        user_id="u1", page=3, page_size=2, config_service=cfg
    )
    assert [i.server_code for i in page2] == ["mcp.s2", "mcp.s3"]
    assert [i.server_code for i in page3] == ["mcp.s4"]


def test_list_past_the_end_is_empty_not_an_error():
    cfg = _listing_service([_row("mcp.s")])
    total, items = list_unified_configs(
        user_id="u1", page=9, page_size=10, config_service=cfg
    )
    assert (total, items) == (1, [])


def test_list_of_nothing_is_an_empty_page():
    total, items = list_unified_configs(
        user_id="u1", page=1, page_size=10, config_service=_listing_service([])
    )
    assert (total, items) == (0, [])


@pytest.mark.parametrize("api_key", ["sk-abcdefghijkl", "xy", "abcdefgh", ""])
def test_list_masks_exactly_as_the_single_read_does(api_key):
    # The property that matters is not the mask's shape but that enumerating
    # cannot reveal more than reading one server does — including for short
    # keys, where the mask has to hide the whole value.
    single = read_unified_config(
        user_id="u1",
        server_code="mcp.s",
        config_service=_config_service(
            existing={
                "api_key": api_key,
                "headers": {},
                "endpoint_env": "PROD",
                "transport_protocol": None,
            }
        ),
    )
    _, items = list_unified_configs(
        user_id="u1",
        page=1,
        page_size=10,
        config_service=_listing_service([_row("mcp.s", api_key=api_key)]),
    )
    assert items[0].api_key == single.api_key
    if api_key:
        assert api_key not in (items[0].api_key or "")


def test_list_reports_has_config_off_content_and_exists_always_true():
    cfg = _listing_service(
        [
            _row("mcp.bare"),  # endpoint_env only
            _row("mcp.keyed", api_key="k"),
        ]
    )
    _, items = list_unified_configs(
        user_id="u1", page=1, page_size=10, config_service=cfg
    )
    by_code = {i.server_code: i for i in items}
    assert by_code["mcp.bare"].has_config is False
    assert by_code["mcp.keyed"].has_config is True
    # Every listed entry came from a stored row.
    assert all(i.exists for i in items)


# ── delete ──────────────────────────────────────────────────────────


_OLD = {"api_key": "old", "headers": {}, "endpoint_env": "PROD"}


def _delete_service(*, old=_OLD):
    m = MagicMock()
    m.delete_user_unified_config.return_value = old
    return m


def _delete(cfg=None, mkt=None, sync=None, **overrides):
    params = dict(
        user_id="u1",
        server_code="mcp.s",
        entity_id="u1",
        entity_type="staff",
    )
    params.update(overrides)
    return asyncio.run(
        delete_unified_config(
            **params,
            config_service=cfg or _delete_service(),
            market_service=mkt or _market_service(),
            sync_service=sync or _sync_service(),
        )
    )


def test_delete_removes_the_row_and_reports_true():
    cfg = _delete_service()
    assert _delete(cfg=cfg) is True
    cfg.delete_user_unified_config.assert_called_once()


def test_delete_of_absent_config_is_success_reporting_nothing_deleted():
    # Revoking twice is not a failure, and must not answer not-found.
    cfg = _delete_service(old=None)
    sync = _sync_service()
    assert _delete(cfg=cfg, sync=sync) is False
    # Nothing was deleted, so no device is touched.
    sync.sync_mcp_detail_to_all_bots.assert_not_called()


def test_delete_of_unknown_server_is_not_found_before_any_write():
    cfg = _delete_service()
    with pytest.raises(McpServerNotFoundError):
        _delete(cfg=cfg, mkt=_market_service(detail=None))
    cfg.delete_user_unified_config.assert_not_called()


def test_delete_pushes_with_the_credential_cleared_not_a_removal():
    # Deleting a config is not deactivation: the MCP must stay installed, so
    # the push is a re-sync carrying no credential, never remove_mcp_detail.
    sync = _sync_service()
    _delete(sync=sync)
    sync.remove_mcp_detail.assert_not_called()
    _, kw = sync.sync_mcp_detail_to_all_bots.call_args
    assert kw["api_key"] is None
    assert kw["custom_headers"] is None


def test_delete_push_failure_restores_the_row_and_raises():
    cfg = _delete_service()
    with pytest.raises(McpSyncFailedError):
        _delete(cfg=cfg, sync=_sync_service(success=False, error="device down"))
    _, kw = cfg.rollback_unified_config.call_args
    assert kw["old_config"] == _OLD


def test_delete_push_raising_also_restores_the_row_and_raises_sync_failure():
    cfg = _delete_service()
    sync = MagicMock()
    sync.sync_mcp_detail_to_all_bots = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(McpSyncFailedError, match="boom"):
        _delete(cfg=cfg, sync=sync)
    cfg.rollback_unified_config.assert_called_once()
    _, kw = cfg.rollback_unified_config.call_args
    assert kw["old_config"] == _OLD
