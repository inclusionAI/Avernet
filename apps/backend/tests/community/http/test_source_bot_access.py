"""Submission-time source-Bot access gate for ``from: from_bot`` local skills.

A manifest declaring a ``protocol: bot`` source (referenced by a
``from: from_bot`` local skill member) reads the source Bot's ``ac_skill`` row
during apply. Before the manifest is stored or an apply task is created, the
submitting dst owner must have access to every declared bot source — the same
edit-access rule the Copy endpoint enforces. A plain Manifest PUT (and a
create-with-manifest) reach the from_bot path without the Copy endpoint, so
this gate closes that bypass at the shared submission boundary.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.openapi_v1.bots.source_bot_access import (
    assert_from_bot_source_access,
)


class _BotRepo:
    """``BotRepository.get_by_id`` stand-in: returns the seeded row or ``None``."""

    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self._rows = rows

    def get_by_id(self, bot_id: str) -> dict[str, Any] | None:
        return self._rows.get(bot_id)


_DOC_WITH_BOT_SOURCE = """
sources:
  from_bot:
    protocol: bot
    bot_id: src-bot
    owner_id: src-owner
"""

_DOC_WITH_GIT_SOURCE_ONLY = """
sources:
  git1:
    protocol: git
    url: https://example.invalid/repo
    ref: main
"""


def test_denies_when_dst_owner_is_neither_owner_nor_edit_collaborator():
    repo = _BotRepo({"src-bot": {"owner_id": "someone-else", "actions": []}})
    with pytest.raises(HTTPException) as exc:
        assert_from_bot_source_access(
            document=_DOC_WITH_BOT_SOURCE, owner_id="dst-owner", bot_repo=repo
        )
    assert exc.value.status_code == 403
    assert "src-bot" in exc.value.detail


def test_allows_when_dst_owner_owns_the_source_bot():
    repo = _BotRepo({"src-bot": {"owner_id": "dst-owner", "actions": []}})
    # No raise: the caller owns the source bot.
    assert_from_bot_source_access(
        document=_DOC_WITH_BOT_SOURCE, owner_id="dst-owner", bot_repo=repo
    )


def test_allows_when_dst_owner_holds_edit_action_on_source_bot():
    repo = _BotRepo(
        {"src-bot": {"owner_id": "someone-else", "actions": ["read", "edit"]}}
    )
    # No raise: a collaborator with the edit action may copy from the source.
    assert_from_bot_source_access(
        document=_DOC_WITH_BOT_SOURCE, owner_id="dst-owner", bot_repo=repo
    )


def test_rejects_unknown_source_bot_as_409():
    repo = _BotRepo({})
    with pytest.raises(HTTPException) as exc:
        assert_from_bot_source_access(
            document=_DOC_WITH_BOT_SOURCE, owner_id="dst-owner", bot_repo=repo
        )
    assert exc.value.status_code == 409


def test_document_without_bot_sources_is_a_noop():
    repo = _BotRepo({})
    # No raise: a git-only document declares no source bot to authorize.
    assert_from_bot_source_access(
        document=_DOC_WITH_GIT_SOURCE_ONLY, owner_id="dst-owner", bot_repo=repo
    )


def test_each_distinct_bot_source_is_checked():
    repo = _BotRepo(
        {
            "src-a": {"owner_id": "dst-owner", "actions": []},
            "src-b": {"owner_id": "someone-else", "actions": []},  # unauthorized
        }
    )
    document = """
sources:
  a:
    protocol: bot
    bot_id: src-a
    owner_id: owner-a
  b:
    protocol: bot
    bot_id: src-b
    owner_id: owner-b
"""
    with pytest.raises(HTTPException) as exc:
        assert_from_bot_source_access(
            document=document, owner_id="dst-owner", bot_repo=repo
        )
    assert exc.value.status_code == 403
    assert "src-b" in exc.value.detail


@pytest.mark.asyncio
async def test_submit_manifest_creation_refuses_unauthorized_from_bot_before_creating():
    """The create-with-manifest handler runs the access gate as its first act:
    an unauthorized ``from_bot`` source bot is refused with 403 before any bot
    is created or manifest stored (no creation-side dependency is touched)."""
    from unittest.mock import MagicMock

    # Lazy import: the route module sits behind a post-top-import ordering
    # edge (bot_service ↔ workspace_service); importing inside the test matches
    # the way the router composes it.
    from agentclaw.community.adapters.http.openapi_v1.bots.create_with_manifest import (
        submit_manifest_creation,
    )

    body = MagicMock()
    body.config_manifest = _DOC_WITH_BOT_SOURCE
    repo = _BotRepo({"src-bot": {"owner_id": "someone-else", "actions": []}})
    untouched = MagicMock()  # bot_service / passport_plugin / manifest_seam / …

    with pytest.raises(HTTPException) as exc:
        await submit_manifest_creation(
            body=body,
            request=MagicMock(),
            owner_id="dst-owner",
            owner_name="n",
            caller=MagicMock(),
            bot_service=untouched,
            bot_repo=repo,
            passport_plugin=untouched,
            skill_set_factory=untouched,
            space_context=untouched,
            manifest_seam=untouched,
        )
    assert exc.value.status_code == 403
    # The gate raised before submission: no creation-side dependency was used.
    assert untouched.method_calls == []
