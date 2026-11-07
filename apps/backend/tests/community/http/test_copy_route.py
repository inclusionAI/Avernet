"""Handler-unit tests for POST /openapi/v1/bots/copy step1a (submit_copy, Route B).

Route B: capture source's script → serialize a manifest document → persist under
dst key → apply Passport → hand off to create_job via manifest_seam.start_job.
Passes deps by keyword. Asserts source validation, manifest persist + hand-off,
the ledger write, and the success/auth_required split. Teclaw / no-perm /
not-found / non-copyable-lifecycle refused before any side effect.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request

from agentclaw.community.adapters.http.openapi_v1.bots.copy import (
    BotCopyRequest,
    copy_bot,
    retry_copy_bot,
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
        "entity_id": _OWNER,
    }
    base.update(over)
    return base


def _mocks(script_body="echo hello"):
    bot_repo = MagicMock()
    bot_copy_repo = MagicMock()
    bot_script_service = MagicMock()
    bot_script_service.get_body.return_value = script_body
    manifest_seam = MagicMock()
    passport = MagicMock()
    space_context = MagicMock()
    space_context.resolve_current.return_value = SimpleNamespace(
        numeric_id=100, kind="personal"
    )
    return bot_repo, bot_copy_repo, bot_script_service, manifest_seam, passport, space_context


def _call(body_over, source_over, mocks=None):
    mocks = mocks or _mocks()
    bot_repo, bot_copy_repo, bot_script_service, manifest_seam, passport, space_context = mocks
    body_kwargs = {"source_bot_id": _SOURCE_BOT_ID, "bot_name": "copied"}
    body_kwargs.update(body_over)
    resp = submit_copy(
        body=BotCopyRequest(**body_kwargs),
        owner_id=_OWNER,
        owner_name="owner",
        bot_repo=bot_repo,
        bot_copy_repo=bot_copy_repo,
        bot_script_service=bot_script_service,
        manifest_seam=manifest_seam,
        passport_plugin=passport,
        space_context=space_context,
    )
    return resp, mocks


def test_submit_copy_oss_noop_persists_manifest_and_hands_off():
    mocks = _mocks(script_body="echo hello")
    bot_repo, bot_copy_repo, bot_script_service, manifest_seam, passport, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot()
    passport.apply_agent_passport.return_value = {"token": "tok_1"}

    resp, _ = _call({}, {}, mocks=mocks)

    assert resp.type == "success"
    assert resp.operation_id and resp.bot_id

    # source's script read + manifest doc persisted under dst key
    bot_script_service.get_body.assert_called_once_with(
        entity_id=_OWNER, bot_id=_SOURCE_BOT_ID
    )
    persist_kwargs = manifest_seam.persist.call_args.kwargs
    assert persist_kwargs["bot_id"] == resp.bot_id
    assert persist_kwargs["modifier"] == _OWNER
    assert "echo hello" in persist_kwargs["document"]
    assert persist_kwargs["document"].startswith("schema_version: 1")  # manifest YAML

    # ledger row written INITIAL
    inserted = bot_copy_repo.insert.call_args.args[0]
    assert inserted.status == BotCopyStatus.INITIAL
    assert inserted.source_bot_id == _SOURCE_BOT_ID
    assert inserted.dst_bot_id == resp.bot_id

    # create_job handed off via start_job — payload spec carries source_bot_id,
    # NO state_ref (Route B uses manifest doc, not copy state)
    start_kwargs = manifest_seam.start_job.call_args.kwargs
    assert start_kwargs["bot_id"] == resp.bot_id
    assert start_kwargs["spec"]["source_bot_id"] == _SOURCE_BOT_ID
    assert "state_ref" not in start_kwargs["spec"]


def test_submit_copy_internal_pending_returns_auth_required_with_iframe():
    mocks = _mocks()
    bot_repo, _, _, _, passport, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot()
    passport.apply_agent_passport.return_value = {"iframe_url": "https://auth/iframe", "redirect_url": ""}

    resp, _ = _call({}, {}, mocks=mocks)
    assert resp.type == "auth_required"
    assert resp.iframe_url == "https://auth/iframe"
    assert resp.operation_id and resp.bot_id


def test_submit_copy_rejects_teclaw_source_before_side_effects():
    mocks = _mocks()
    bot_repo, bot_copy_repo, bot_script_service, manifest_seam, passport, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot(active_engine="teclaw")

    with pytest.raises(HTTPException) as exc_info:
        _call({}, {}, mocks=mocks)
    assert exc_info.value.status_code == 409
    bot_copy_repo.insert.assert_not_called()
    manifest_seam.persist.assert_not_called()
    manifest_seam.start_job.assert_not_called()
    passport.apply_agent_passport.assert_not_called()
    bot_script_service.get_body.assert_not_called()


def test_submit_copy_rejects_source_not_found():
    mocks = _mocks()
    bot_repo, bot_copy_repo, manifest_seam, _, _, _ = mocks
    bot_repo.get_by_id.return_value = None
    with pytest.raises(HTTPException) as exc_info:
        _call({}, {}, mocks=mocks)
    assert exc_info.value.status_code == 409
    bot_copy_repo.insert.assert_not_called()
    manifest_seam.persist.assert_not_called()


def test_submit_copy_rejects_non_copyable_lifecycle():
    mocks = _mocks()
    bot_repo, bot_copy_repo, manifest_seam, _, _, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot(status="FAILED")
    with pytest.raises(HTTPException) as exc_info:
        _call({}, {}, mocks=mocks)
    assert exc_info.value.status_code == 409
    bot_copy_repo.insert.assert_not_called()


def test_submit_copy_rejects_no_edit_permission_non_owner():
    mocks = _mocks()
    bot_repo, bot_copy_repo, manifest_seam, _, _, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot(owner_id="other_owner", actions=["view"])
    with pytest.raises(HTTPException) as exc_info:
        _call({}, {}, mocks=mocks)
    assert exc_info.value.status_code == 403
    bot_copy_repo.insert.assert_not_called()


def test_submit_copy_passport_no_token_no_url_is_500_after_persist():
    # Passport returning nothing usable is an internal error; the manifest doc +
    # ledger row were already written (compensation is the retry path's job),
    # but no create_job hand-off happens.
    mocks = _mocks()
    bot_repo, _, _, manifest_seam, passport, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot()
    passport.apply_agent_passport.return_value = {}
    with pytest.raises(HTTPException) as exc_info:
        _call({}, {}, mocks=mocks)
    assert exc_info.value.status_code == 500
    manifest_seam.start_job.assert_not_called()


def _request():
    # A minimal ASGI request scope is enough for `envelope(result, request)`,
    # which reads only request_id and the app's envelope helper.
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/openapi/v1/bots/copy",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 8000),
        }
    )


def test_copy_bot_handler_returns_envelope_with_result():
    # Handler-level (not submit_copy-level): covers the `del caller` + the
    # `return envelope(result, request)` path the ASGI adapter answers with.
    mocks = _mocks()
    bot_repo, bot_copy_repo, _, manifest_seam, passport, space_context = mocks
    bot_repo.get_by_id.return_value = _source_bot()
    passport.apply_agent_passport.return_value = {"token": "tok_1"}

    result = asyncio.run(
        copy_bot(
            body=BotCopyRequest(source_bot_id=_SOURCE_BOT_ID, bot_name="copied"),
            request=_request(),
            owner_id=_OWNER,
            owner_name="owner",
            caller=MagicMock(),
            bot_repo=bot_repo,
            bot_copy_repo=bot_copy_repo,
            bot_script_service=mocks[2],
            manifest_seam=manifest_seam,
            passport_plugin=passport,
            space_context=space_context,
        )
    )
    # envelope(result, request) → a populated Envelope carrying the submit_copy result.
    assert result.data is not None
    assert result.data.type == "success"
    assert result.code == 200000


def test_retry_copy_bot_handler_returns_envelope_with_result():
    # Handler-level: covers retry_copy_bot's `del caller` + `return envelope(...)`.
    mocks = _mocks()
    bot_repo, bot_copy_repo, _, manifest_seam, _, space_context = mocks
    bot_repo.get_by_id.return_value = _source_bot(status="ACTIVE")
    bot_copy_repo.get_by_dst_bot_id.return_value = MagicMock(
        operation_id="op_1",
        source_bot_id=_SOURCE_BOT_ID,
        dst_owner_id=_OWNER,
        operator_id=_OWNER,
        env="dev",
    )
    bot_copy_repo.cas_retry.return_value = True
    apply_repo = MagicMock()
    apply_repo.recent.return_value = []

    result = asyncio.run(
        retry_copy_bot(
            bot_id="dst_1",
            request=_request(),
            owner_id=_OWNER,
            caller=MagicMock(),
            bot_copy_repo=bot_copy_repo,
            apply_repo=apply_repo,
            bot_repo=bot_repo,
            manifest_seam=manifest_seam,
            space_context=space_context,
        )
    )
    assert result.data is not None
    assert result.data.operation_id == "op_1"
    assert result.code == 200000

