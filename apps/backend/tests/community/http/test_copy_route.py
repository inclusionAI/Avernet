"""Handler-unit tests for POST /openapi/v1/bots/copy step1a (submit_copy).

Passes deps by keyword (codebase convention for handler unit tests — the
FastAPI wiring and injector never run). Asserts source validation, the
ac_bot_copy ledger write, the create_job enqueue with state_ref=copy_state in
payload.spec, and the success/auth_required response split. Teclaw + no-perm +
not-found + non-copyable-lifecycle are refused before any side effect.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.openapi_v1.bots.copy import (
    BotCopyRequest,
    submit_copy,
)
from agentclaw.community.core.bot_copy.types import BotCopyStatus

_OWNER = "u1"
_SOURCE_BOT_ID = "src_1"


def _source_bot(**over):
    base = {
        "bot_id": _SOURCE_BOT_ID,
        "active_engine": "openclaw",
        "status": "ACTIVE",
        "owner_id": _OWNER,
        "actions": ["edit", "chat"],
        "bot_type": "personal",
    }
    base.update(over)
    return base


def _mocks():
    bot_repo = MagicMock()
    bot_copy_repo = MagicMock()
    manifest_seam = MagicMock()
    passport = MagicMock()
    space_context = MagicMock()
    space_context.resolve_current.return_value = SimpleNamespace(numeric_id=100)
    return bot_repo, bot_copy_repo, manifest_seam, passport, space_context


def _call(body_over, source_over, mocks=None):
    mocks = mocks or _mocks()
    bot_repo, bot_copy_repo, manifest_seam, passport, space_context = mocks
    body_kwargs = {"source_bot_id": _SOURCE_BOT_ID, "bot_name": "copied"}
    body_kwargs.update(body_over)
    resp = submit_copy(
        body=BotCopyRequest(**body_kwargs),
        owner_id=_OWNER,
        owner_name="owner",
        bot_repo=bot_repo,
        bot_copy_repo=bot_copy_repo,
        manifest_seam=manifest_seam,
        passport_plugin=passport,
        space_context=space_context,
    )
    return resp, mocks


def test_submit_copy_oss_noop_enqueues_create_job_with_copy_state():
    mocks = _mocks()
    bot_repo, bot_copy_repo, manifest_seam, passport, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot()
    passport.apply_agent_passport.return_value = {"token": "tok_1"}

    resp, _ = _call({}, {}, mocks=mocks)

    assert resp.type == "success"
    assert resp.operation_id and resp.bot_id

    inserted = bot_copy_repo.insert.call_args.args[0]
    assert inserted.status == BotCopyStatus.INITIAL
    assert inserted.source_bot_id == _SOURCE_BOT_ID
    assert inserted.dst_bot_id == resp.bot_id

    start_kwargs = manifest_seam.start_job.call_args.kwargs
    assert start_kwargs["bot_id"] == resp.bot_id
    assert start_kwargs["spec"]["state_ref"] == {"kind": "copy", "reference": resp.operation_id}
    assert start_kwargs["spec"]["source_bot_id"] == _SOURCE_BOT_ID


def test_submit_copy_internal_pending_returns_auth_required_with_iframe():
    mocks = _mocks()
    bot_repo, _, _, passport, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot()
    passport.apply_agent_passport.return_value = {"iframe_url": "https://auth/iframe", "redirect_url": ""}

    resp, _ = _call({}, {}, mocks=mocks)
    assert resp.type == "auth_required"
    assert resp.iframe_url == "https://auth/iframe"
    assert resp.operation_id and resp.bot_id


def test_submit_copy_rejects_teclaw_source_before_side_effects():
    mocks = _mocks()
    bot_repo, bot_copy_repo, manifest_seam, passport, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot(active_engine="teclaw")

    with pytest.raises(HTTPException) as exc_info:
        _call({}, {}, mocks=mocks)
    assert exc_info.value.status_code == 409
    bot_copy_repo.insert.assert_not_called()
    manifest_seam.start_job.assert_not_called()
    passport.apply_agent_passport.assert_not_called()


def test_submit_copy_rejects_source_not_found():
    mocks = _mocks()
    bot_repo, bot_copy_repo, manifest_seam, _, _ = mocks
    bot_repo.get_by_id.return_value = None
    with pytest.raises(HTTPException) as exc_info:
        _call({}, {}, mocks=mocks)
    assert exc_info.value.status_code == 409
    bot_copy_repo.insert.assert_not_called()
    manifest_seam.start_job.assert_not_called()


def test_submit_copy_rejects_non_copyable_lifecycle():
    mocks = _mocks()
    bot_repo, bot_copy_repo, manifest_seam, _, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot(status="FAILED")
    with pytest.raises(HTTPException) as exc_info:
        _call({}, {}, mocks=mocks)
    assert exc_info.value.status_code == 409
    bot_copy_repo.insert.assert_not_called()


def test_submit_copy_rejects_no_edit_permission_non_owner():
    mocks = _mocks()
    bot_repo, bot_copy_repo, manifest_seam, _, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot(owner_id="other_owner", actions=["view"])
    with pytest.raises(HTTPException) as exc_info:
        _call({}, {}, mocks=mocks)
    assert exc_info.value.status_code == 403
    bot_copy_repo.insert.assert_not_called()


def test_submit_copy_passport_no_token_no_url_is_500_after_ledger_write():
    # Passport returning nothing usable is an internal error; the ledger row was
    # already written (compensation is the operator-retry path's job, not the
    # handler's), but no create_job enqueue happens.
    mocks = _mocks()
    bot_repo, bot_copy_repo, manifest_seam, passport, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot()
    passport.apply_agent_passport.return_value = {}
    with pytest.raises(HTTPException) as exc_info:
        _call({}, {}, mocks=mocks)
    assert exc_info.value.status_code == 500
    manifest_seam.start_job.assert_not_called()
