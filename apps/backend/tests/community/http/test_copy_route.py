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
from agentclaw.community.core.bot_management.bot_quota import (
    BotQuotaExceededError,
    BotQuotaScope,
    BotQuotaSnapshot,
)
from agentclaw.community.core.spaces.models import SpaceType

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


def _quota_snapshot():
    # The concrete shape ``BotQuotaExceededError`` wraps — an opaque snapshot
    # whose only requirement is that ``scope.space_ref`` formats. Personal scope
    # is the openclaw-personal copy target's natural quota scope.
    return BotQuotaSnapshot(
        scope=BotQuotaScope(
            owner_id=_OWNER,
            space_id=None,
            space_name="Personal",
            space_type=SpaceType.PERSONAL,
        ),
        ceiling=5,
        used=5,
    )


def _mocks(script_body="echo hello", source_render_screens=None):
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
    render_screen_service = MagicMock()
    # Default to an empty list — a screenless source omits bot_config, the
    # path the existing tests exercise. Tests that carry screens pass rows.
    render_screen_service.list_render_screens.return_value = source_render_screens or []
    bot_service = MagicMock()
    bot_service.check_create_bot_preflight.return_value = None  # over-quota → raises
    return bot_repo, bot_copy_repo, bot_script_service, manifest_seam, passport, space_context, render_screen_service, bot_service


def _call(body_over, source_over, mocks=None):
    mocks = mocks or _mocks()
    (
        bot_repo,
        bot_copy_repo,
        bot_script_service,
        manifest_seam,
        passport,
        space_context,
        render_screen_service,
        bot_service,
    ) = mocks
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
        render_screen_service=render_screen_service,
        bot_service=bot_service,
    )
    return resp, mocks


def test_submit_copy_oss_noop_persists_manifest_and_hands_off():
    mocks = _mocks(script_body="echo hello")
    bot_repo, bot_copy_repo, bot_script_service, manifest_seam, passport, _, _, _ = mocks
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


def test_submit_copy_carries_caller_excludes_into_the_manifest_entry():
    # The caller's excludes ride both the ledger (request_detail) and the
    # manifest resources entry, so the compiler and the sync read one source of
    # truth. An empty document omits them entirely — the empty branch.
    mocks = _mocks()
    bot_repo, bot_copy_repo, _bot_script_service, manifest_seam, _passport, _, _, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot()

    body, _ = _call(
        {"request_detail": {"excludes": ["secrets/", "large/tmp"]}},
        {},
        mocks=mocks,
    )
    assert body.type == "success"
    document = manifest_seam.persist.call_args.kwargs["document"]
    assert "excludes:" in document
    assert "secrets/" in document
    assert "large/tmp" in document
    # The ledger row keeps the caller's intent too.
    inserted = bot_copy_repo.insert.call_args.args[0]
    assert inserted.request_detail == {"excludes": ["secrets/", "large/tmp"]}


def test_submit_copy_an_empty_excludes_list_is_omitted_from_the_manifest():
    # No excludes → the entry carries no 'excludes' key at all (the falsy branch
    # of the serializer), so the compiler never sees an empty list.
    mocks = _mocks()
    bot_repo, _bot_copy_repo, _bot_script_service, manifest_seam, _passport, _, _, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot()

    _call({"request_detail": {"excludes": []}}, {}, mocks=mocks)
    document = manifest_seam.persist.call_args.kwargs["document"]
    assert "excludes" not in document


def _record(name, cdn_url):
    from agentclaw.community.core.bot_management.render_screen.models import (
        RenderScreenRecord,
    )
    from datetime import datetime

    return RenderScreenRecord(
        id=1, bot_id=_SOURCE_BOT_ID, owner_id=_OWNER, name=name, cdn_url=cdn_url,
        env="dev", creator_id=_OWNER, is_delete=0,
        gmt_create=datetime(2026, 1, 1), gmt_modified=datetime(2026, 1, 1),
    )


def test_submit_copy_serializes_source_render_screens_into_bot_config():
    # The source's render-screen set is the v1 ``bot_config.render_screen``
    # kind: each (name, cdn_url) row serialised verbatim into the manifest
    # document the compiler reads — one source of truth, the source's live rows.
    mocks = _mocks(
        source_render_screens=[_record("lib-a", "https://cdn/a.js"), _record("lib-b", "https://cdn/b.js")],
    )
    bot_repo, _bot_copy_repo, _bot_script_service, manifest_seam, _passport, _, render_screen_service, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot()

    resp, _ = _call({}, {}, mocks=mocks)
    assert resp.type == "success"

    # The service was read with the source's owner + the caller as the reader.
    list_kwargs = render_screen_service.list_render_screens.call_args.kwargs
    assert list_kwargs["bot_id"] == _SOURCE_BOT_ID
    document = manifest_seam.persist.call_args.kwargs["document"]
    assert "bot_config:" in document
    assert "render_screen:" in document
    assert "lib-a" in document
    assert "https://cdn/a.js" in document
    assert "lib-b" in document


def test_submit_copy_a_screenless_source_omits_bot_config_entirely():
    # No render screens → the ``bot_config`` key is omitted, so the validator
    # and compiler treat it as undeclared (untouched) rather than overwrite-to-empty.
    mocks = _mocks(source_render_screens=[])
    bot_repo, _bot_copy_repo, _bot_script_service, manifest_seam, _passport, _, _, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot()

    _call({}, {}, mocks=mocks)
    document = manifest_seam.persist.call_args.kwargs["document"]
    assert "bot_config" not in document



def test_submit_copy_internal_pending_returns_auth_required_with_iframe():
    mocks = _mocks()
    bot_repo, _, _, _, passport, _, _, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot()
    passport.apply_agent_passport.return_value = {"iframe_url": "https://auth/iframe", "redirect_url": ""}

    resp, _ = _call({}, {}, mocks=mocks)
    assert resp.type == "auth_required"
    assert resp.iframe_url == "https://auth/iframe"
    assert resp.operation_id and resp.bot_id


def test_submit_copy_rejects_teclaw_source_before_side_effects():
    mocks = _mocks()
    bot_repo, bot_copy_repo, bot_script_service, manifest_seam, passport, _, _, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot(active_engine="teclaw")

    with pytest.raises(HTTPException) as exc_info:
        _call({}, {}, mocks=mocks)
    assert exc_info.value.status_code == 409
    bot_copy_repo.insert.assert_not_called()
    manifest_seam.persist.assert_not_called()
    manifest_seam.start_job.assert_not_called()
    passport.apply_agent_passport.assert_not_called()
    bot_script_service.get_body.assert_not_called()


def test_submit_copy_rejects_over_quota_before_passport_or_manifest_side_effects():
    # The quota / name / reserved-engine pre-flight runs before Passport auth,
    # manifest serialization or the ledger write — the same window ordinary
    # creation narrows. An over-quota copy must propagate the platform error
    # without minting an AgentPassport identity or leaving a ledger row the FE
    # detail poll would chase into a 60s timeout.
    mocks = _mocks()
    bot_repo, bot_copy_repo, _bot_script_service, manifest_seam, passport, _, _, bot_service = mocks
    bot_repo.get_by_id.return_value = _source_bot()
    bot_service.check_create_bot_preflight.side_effect = BotQuotaExceededError(
        _quota_snapshot()
    )

    with pytest.raises(BotQuotaExceededError):
        _call({}, {}, mocks=mocks)
    bot_copy_repo.insert.assert_not_called()
    manifest_seam.persist.assert_not_called()
    manifest_seam.start_job.assert_not_called()
    passport.apply_agent_passport.assert_not_called()
    bot_script_service = mocks[2]
    bot_script_service.get_body.assert_not_called()


def test_submit_copy_rejects_source_not_found():
    mocks = _mocks()
    bot_repo, bot_copy_repo, manifest_seam, _, _, _, _, _ = mocks
    bot_repo.get_by_id.return_value = None
    with pytest.raises(HTTPException) as exc_info:
        _call({}, {}, mocks=mocks)
    assert exc_info.value.status_code == 409
    bot_copy_repo.insert.assert_not_called()
    manifest_seam.persist.assert_not_called()


def test_submit_copy_rejects_non_copyable_lifecycle():
    mocks = _mocks()
    bot_repo, bot_copy_repo, manifest_seam, _, _, _, _, _ = mocks
    bot_repo.get_by_id.return_value = _source_bot(status="FAILED")
    with pytest.raises(HTTPException) as exc_info:
        _call({}, {}, mocks=mocks)
    assert exc_info.value.status_code == 409
    bot_copy_repo.insert.assert_not_called()


def test_submit_copy_rejects_no_edit_permission_non_owner():
    mocks = _mocks()
    bot_repo, bot_copy_repo, manifest_seam, _, _, _, _, _ = mocks
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
    bot_repo, _, _, manifest_seam, passport, _, _, _ = mocks
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
    bot_repo, bot_copy_repo, _, manifest_seam, passport, space_context, render_screen_service, _ = mocks
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
            render_screen_service=render_screen_service,
            bot_service=mocks[-1],
        )
    )
    # envelope(result, request) → a populated Envelope carrying the submit_copy result.
    assert result.data is not None
    assert result.data.type == "success"
    assert result.code == 200000


def test_retry_copy_bot_handler_returns_envelope_with_result():
    # Handler-level: covers retry_copy_bot's `del caller` + `return envelope(...)`.
    mocks = _mocks()
    bot_repo, bot_copy_repo, _, manifest_seam, _, space_context, _, _ = mocks
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

