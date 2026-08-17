"""Unit tests for BotMcpStateService — the bot-scoped MCP activation surface.

No HTTP and no database: the service is driven with a mocked skill-set
repository, bot repository, MCP Center and sync service, so the assertions are
about the state machine rather than about SQLAlchemy.

The community device plugins are no-ops whose ``refresh_mcp_scope`` always
succeeds, so reconciliation failure — which every mutation must roll back — can
only be exercised with a stubbed sync service. That is what ``_sync(ok=False)``
is for.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.mcp.errors import (
    McpBotServerNotFoundError,
    McpDefaultServerNotRemovableError,
    McpServerNotFoundError,
    McpSyncFailedError,
)
from agentclaw.community.core.mcp.services.bot_mcp_state_service import (
    BotMcpStateService,
)

BOT = "b-1"
OWNER = "u1"
STORED = "mcp.stored"
DEFAULT_CODE = "mcp.engine.default"


def _bot_repo(owns=True):
    m = MagicMock()
    m.get_by_id_and_owner.return_value = (
        {"id": 1, "bot_id": BOT, "active_engine": "openclaw"} if owns else None
    )
    return m


def _skill_set_repo(*, rows=None, excluded=(), default_set=True):
    m = MagicMock()
    m.get_default.return_value = {"id": 42} if default_set else None
    m.get_mcp_servers_in_set.return_value = list(rows or [])
    m.get_all_excluded_mcps.return_value = list(excluded)
    m.add_mcp_to_set.return_value = True
    m.remove_mcp_from_set.return_value = True
    m.add_default_mcp_exclusion.return_value = True
    m.remove_all_default_mcp_exclusions.return_value = True
    return m


def _mcp_center(detail=None):
    m = MagicMock()
    m.get_mcp_detail.return_value = (
        detail
        if detail is not None
        else {
            "serverCode": STORED,
            "name": "Stored",
            "description": "d",
            "networkTypes": ["INTERNET"],
        }
    )
    return m


def _sync(ok=True):
    m = MagicMock()
    m.refresh_mcp_scope = AsyncMock(
        return_value={"success": ok, "error": None if ok else "device down"}
    )
    return m


def _svc(*, bots=None, sets=None, center=None, sync=None):
    return BotMcpStateService(
        skill_set_repo=sets or _skill_set_repo(),
        bot_repo=bots or _bot_repo(),
        mcp_center=center or _mcp_center(),
        sync_service=sync or _sync(),
    )


def _row(code=STORED, name="Stored"):
    return {"id": 7, "server_code": code, "name": name, "description": "d"}


def _patch_defaults(monkeypatch, codes=(DEFAULT_CODE,)):
    monkeypatch.setattr(
        "agentclaw.community.core.mcp.services.bot_mcp_state_service."
        "get_default_mcp_servers",
        lambda *a, **k: [
            {"server_code": c, "name": c.split(".")[-1], "description": "engine default"}
            for c in codes
        ],
    )


@pytest.fixture(autouse=True)
def _no_engine_defaults(monkeypatch):
    """Default to no engine-supplied MCPs; tests that need them opt in."""
    _patch_defaults(monkeypatch, codes=())


# ── authorization ───────────────────────────────────────────────────


def test_a_bot_the_caller_does_not_own_is_not_found():
    svc = _svc(bots=_bot_repo(owns=False))
    with pytest.raises(McpBotServerNotFoundError):
        svc.list_bot_servers(bot_id=BOT, owner_id=OWNER)


def test_a_bot_without_a_default_set_is_not_found_never_an_implicit_create():
    sets = _skill_set_repo(default_set=False)
    with pytest.raises(McpBotServerNotFoundError):
        _svc(sets=sets).list_bot_servers(bot_id=BOT, owner_id=OWNER)
    # Nothing was created as a side effect of a read.
    sets.create.assert_not_called()


# ── listing ─────────────────────────────────────────────────────────


def test_a_stored_row_reads_active_when_not_excluded():
    svc = _svc(sets=_skill_set_repo(rows=[_row()]))
    (entry,) = svc.list_bot_servers(bot_id=BOT, owner_id=OWNER)
    assert entry["server_code"] == STORED
    assert entry["active"] is True
    assert entry["is_default"] is False


def test_a_stored_row_reads_inactive_when_excluded():
    svc = _svc(sets=_skill_set_repo(rows=[_row()], excluded=[STORED]))
    (entry,) = svc.list_bot_servers(bot_id=BOT, owner_id=OWNER)
    assert entry["active"] is False


def test_engine_defaults_are_listed_and_read_active_until_excluded(monkeypatch):
    _patch_defaults(monkeypatch)
    svc = _svc(sets=_skill_set_repo())
    (entry,) = svc.list_bot_servers(bot_id=BOT, owner_id=OWNER)
    assert entry["server_code"] == DEFAULT_CODE
    assert entry["is_default"] is True
    assert entry["active"] is True

    svc = _svc(sets=_skill_set_repo(excluded=[DEFAULT_CODE]))
    (entry,) = svc.list_bot_servers(bot_id=BOT, owner_id=OWNER)
    assert entry["active"] is False


def test_a_code_that_is_both_stored_and_default_appears_once_as_the_stored_row(
    monkeypatch,
):
    # The stored row is the one an operation can act on, so it must win — a
    # duplicate would make remove refuse a row it could actually delete.
    _patch_defaults(monkeypatch, codes=(STORED,))
    svc = _svc(sets=_skill_set_repo(rows=[_row()]))
    entries = svc.list_bot_servers(bot_id=BOT, owner_id=OWNER)
    assert len(entries) == 1
    assert entries[0]["is_default"] is False


def test_get_one_server_not_on_the_bot_is_not_found():
    svc = _svc(sets=_skill_set_repo())
    with pytest.raises(McpBotServerNotFoundError):
        svc.get_bot_server(bot_id=BOT, owner_id=OWNER, server_code="mcp.absent")


# ── add ─────────────────────────────────────────────────────────────


def _add(svc, code=STORED):
    return asyncio.run(
        svc.add_bot_server(bot_id=BOT, owner_id=OWNER, server_code=code)
    )


def test_add_lands_the_server_deactivated():
    """Adding must never change what the agent can call."""
    sets = _skill_set_repo()

    def _rows_after_add(_set_id):
        return [_row()] if sets.add_mcp_to_set.called else []

    sets.get_mcp_servers_in_set.side_effect = _rows_after_add
    sets.get_all_excluded_mcps.side_effect = (
        lambda *_: [STORED] if sets.add_default_mcp_exclusion.called else []
    )

    out = _add(_svc(sets=sets))

    assert out["changed"] is True
    assert out["server"]["active"] is False
    sets.add_default_mcp_exclusion.assert_called_once()


def test_add_is_idempotent_and_reports_unchanged():
    sets = _skill_set_repo(rows=[_row()])
    out = _add(_svc(sets=sets))
    assert out["changed"] is False
    sets.add_mcp_to_set.assert_not_called()


def test_add_of_an_unknown_server_is_not_found_and_writes_nothing():
    sets = _skill_set_repo()
    with pytest.raises(McpServerNotFoundError):
        _add(_svc(sets=sets, center=_mcp_center(detail={})))
    sets.add_mcp_to_set.assert_not_called()


def test_add_of_a_network_hidden_server_is_the_same_not_found():
    sets = _skill_set_repo()
    hidden = {"serverCode": STORED, "name": "S", "networkTypes": ["SECRET"]}
    with pytest.raises(McpServerNotFoundError):
        _add(_svc(sets=sets, center=_mcp_center(detail=hidden)))
    sets.add_mcp_to_set.assert_not_called()


def test_add_rolls_back_membership_when_the_runtime_cannot_be_reconciled():
    sets = _skill_set_repo()
    with pytest.raises(McpSyncFailedError):
        _add(_svc(sets=sets, sync=_sync(ok=False)))
    sets.remove_mcp_from_set.assert_called_once()
    sets.remove_all_default_mcp_exclusions.assert_called_once()


# ── activate / deactivate ───────────────────────────────────────────


def _set_active(svc, active, code=STORED):
    return asyncio.run(
        svc.set_bot_server_active(
            bot_id=BOT, owner_id=OWNER, server_code=code, active=active
        )
    )


def test_activate_clears_exclusions_across_every_default_set():
    # Not the per-set delete: an exclusion stranded on a former default set
    # would otherwise keep the server off while this reported success.
    sets = _skill_set_repo(rows=[_row()], excluded=[STORED])
    sets.get_all_excluded_mcps.side_effect = [
        [STORED],  # pre-state read
        [],  # after the clear
    ]
    out = _set_active(_svc(sets=sets), True)
    assert out["changed"] is True
    sets.remove_all_default_mcp_exclusions.assert_called_once_with(
        OWNER, BOT, STORED
    )


def test_deactivate_writes_an_exclusion():
    sets = _skill_set_repo(rows=[_row()])
    sets.get_all_excluded_mcps.side_effect = [[], [STORED]]
    out = _set_active(_svc(sets=sets), False)
    assert out["changed"] is True
    assert out["server"]["active"] is False
    sets.add_default_mcp_exclusion.assert_called_once()


def test_activating_an_already_active_server_is_a_no_op():
    sets = _skill_set_repo(rows=[_row()])
    out = _set_active(_svc(sets=sets), True)
    assert out["changed"] is False
    sets.remove_all_default_mcp_exclusions.assert_not_called()


def test_deactivating_an_already_inactive_server_is_a_no_op():
    sets = _skill_set_repo(rows=[_row()], excluded=[STORED])
    out = _set_active(_svc(sets=sets), False)
    assert out["changed"] is False
    sets.add_default_mcp_exclusion.assert_not_called()


def test_engine_defaults_deactivate_through_the_same_path(monkeypatch):
    _patch_defaults(monkeypatch)
    sets = _skill_set_repo()
    sets.get_all_excluded_mcps.side_effect = [[], [DEFAULT_CODE]]
    out = _set_active(_svc(sets=sets), False, code=DEFAULT_CODE)
    assert out["changed"] is True
    assert out["server"]["active"] is False


def test_activating_a_server_not_on_the_bot_is_not_found_never_an_add():
    sets = _skill_set_repo()
    with pytest.raises(McpBotServerNotFoundError):
        _set_active(_svc(sets=sets), True, code="mcp.absent")
    sets.add_mcp_to_set.assert_not_called()


def test_deactivate_rolls_back_when_the_runtime_cannot_be_reconciled():
    sets = _skill_set_repo(rows=[_row()])
    sets.get_all_excluded_mcps.side_effect = [[], [], []]
    with pytest.raises(McpSyncFailedError):
        _set_active(_svc(sets=sets, sync=_sync(ok=False)), False)
    # The exclusion it just wrote is taken back off.
    sets.remove_all_default_mcp_exclusions.assert_called_once()


def test_activate_rolls_back_when_the_runtime_cannot_be_reconciled():
    sets = _skill_set_repo(rows=[_row()], excluded=[STORED])
    with pytest.raises(McpSyncFailedError):
        _set_active(_svc(sets=sets, sync=_sync(ok=False)), True)
    # The exclusion it just cleared is written back.
    sets.add_default_mcp_exclusion.assert_called_once()


# ── remove ──────────────────────────────────────────────────────────


def _remove(svc, code=STORED):
    return asyncio.run(
        svc.remove_bot_server(bot_id=BOT, owner_id=OWNER, server_code=code)
    )


def test_remove_deletes_the_row_and_clears_its_exclusions():
    sets = _skill_set_repo(rows=[_row()])
    assert _remove(_svc(sets=sets)) is True
    sets.remove_mcp_from_set.assert_called_once_with("42", STORED)
    # Or a later re-add would come back off for an invisible reason.
    sets.remove_all_default_mcp_exclusions.assert_called_once()


def test_remove_of_a_server_not_on_the_bot_reports_nothing_removed():
    sets = _skill_set_repo()
    assert _remove(_svc(sets=sets)) is False
    sets.remove_mcp_from_set.assert_not_called()


def test_remove_of_an_engine_default_is_refused(monkeypatch):
    _patch_defaults(monkeypatch)
    sets = _skill_set_repo()
    with pytest.raises(McpDefaultServerNotRemovableError):
        _remove(_svc(sets=sets), code=DEFAULT_CODE)
    sets.remove_mcp_from_set.assert_not_called()


def test_remove_never_touches_the_stored_credential():
    """The credential is account state and outlives any one bot.

    The service is constructed without a config service at all, so this is
    structural rather than a behavioural assertion: there is no path from here
    to ``ac_user_mcp_config``.
    """
    svc = _svc(sets=_skill_set_repo(rows=[_row()]))
    assert not hasattr(svc, "_config_service")
    assert _remove(svc) is True
