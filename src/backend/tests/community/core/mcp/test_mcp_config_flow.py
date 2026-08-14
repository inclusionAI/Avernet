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
