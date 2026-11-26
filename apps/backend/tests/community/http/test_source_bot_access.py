"""Submission-time source-Bot access gate for ``from: from_bot`` local skills.

A manifest declaring a ``protocol: bot`` source (referenced by a
``from: from_bot`` local skill member) reads the source Bot's ``ac_skill`` row
during apply. Before the manifest is stored or an apply task is created, the
submitting dst owner must have access to every declared bot source — the same
access rule the Copy endpoint enforces. A plain Manifest PUT (and a
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
    """``BotRepository.get_by_id_and_owner`` stand-in: returns the seeded row
    (keyed by ``bot_id``) regardless of owner — the gate's addressing lookup,
    not an access decision, so the owner filter is exercised only through the
    returned row's ``owner_id``. ``None`` stands in for a not-found source."""

    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self._rows = rows

    def get_by_id_and_owner(
        self, bot_id: str, owner_id: str
    ) -> dict[str, Any] | None:
        return self._rows.get(bot_id)


class _CollabRepo:
    """``ac_bot_collaborator`` stand-in: returns truthy when the caller
    (``user_id``) holds a row on the source bot. Any role grants access; the
    gate does not distinguish admin/member."""

    def __init__(self, collaborators: set[tuple[int, str]] | None = None) -> None:
        self._collaborators = collaborators or set()

    def get_by_bot_and_user(self, bot_pk: int, user_id: str, env: str):
        if (bot_pk, user_id) in self._collaborators:
            return object()  # truthy: a row exists
        return None


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


def test_denies_when_dst_owner_is_neither_owner_nor_collaborator():
    repo = _BotRepo({"src-bot": {"id": 1, "owner_id": "someone-else"}})
    with pytest.raises(HTTPException) as exc:
        assert_from_bot_source_access(
            document=_DOC_WITH_BOT_SOURCE,
            owner_id="dst-owner",
            bot_repo=repo,
            collaborator_repository=_CollabRepo(),
        )
    assert exc.value.status_code == 403
    assert "src-bot" in exc.value.detail


def test_allows_when_dst_owner_owns_the_source_bot():
    repo = _BotRepo({"src-bot": {"id": 1, "owner_id": "dst-owner"}})
    # No raise: the caller owns the source bot (collaborator check is skipped).
    assert_from_bot_source_access(
        document=_DOC_WITH_BOT_SOURCE,
        owner_id="dst-owner",
        bot_repo=repo,
        collaborator_repository=_CollabRepo(),
    )


def test_allows_when_dst_owner_is_a_collaborator_on_the_source_bot():
    repo = _BotRepo({"src-bot": {"id": 7, "owner_id": "someone-else"}})
    # No raise: a collaborator (any role) on the source may copy from it.
    assert_from_bot_source_access(
        document=_DOC_WITH_BOT_SOURCE,
        owner_id="dst-owner",
        bot_repo=repo,
        collaborator_repository=_CollabRepo({(7, "dst-owner")}),
    )


def test_rejects_unknown_source_bot_as_409():
    repo = _BotRepo({})
    with pytest.raises(HTTPException) as exc:
        assert_from_bot_source_access(
            document=_DOC_WITH_BOT_SOURCE,
            owner_id="dst-owner",
            bot_repo=repo,
            collaborator_repository=_CollabRepo(),
        )
    assert exc.value.status_code == 409


def test_document_without_bot_sources_is_a_noop():
    repo = _BotRepo({})
    # No raise: a git-only document declares no source bot to authorize.
    assert_from_bot_source_access(
        document=_DOC_WITH_GIT_SOURCE_ONLY,
        owner_id="dst-owner",
        bot_repo=repo,
        collaborator_repository=_CollabRepo(),
    )


def test_each_distinct_bot_source_is_checked():
    repo = _BotRepo(
        {
            "src-a": {"id": 1, "owner_id": "dst-owner"},
            "src-b": {"id": 2, "owner_id": "someone-else"},  # unauthorized
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
            document=document,
            owner_id="dst-owner",
            bot_repo=repo,
            collaborator_repository=_CollabRepo(),
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
    repo = _BotRepo({"src-bot": {"id": 1, "owner_id": "someone-else"}})
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
            collaborator_repository=_CollabRepo(),
        )
    assert exc.value.status_code == 403
    # The gate raised before submission: no creation-side dependency was used.
    assert untouched.method_calls == []
