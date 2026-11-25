"""Submission-time source-Bot access gate for ``from: from_bot`` local skills.

A manifest that declares a ``protocol: bot`` source — referenced by a
``from: from_bot`` local skill member — reads the source Bot's ``ac_skill`` row
during apply (the capabilities writer's ``ensure_dst_local_skill_asset`` reverse
lookup). Before such a manifest is stored or an apply task is created, the
submitting dst owner must have access to every declared bot source.

The Copy endpoint enforces this at ``copy.py`` (``source.owner_id != owner_id``
plus a collaborator check). A plain Manifest PUT and a create-with-manifest both
feed a ``from_bot`` source to the apply engine without going through the Copy
endpoint, so this module is the shared submission boundary that closes that
bypass. It is read-only against the document and the bot record: nothing is
written, and a refusal raises before any storage or task creation.

Access decision (shared with the Copy endpoint): the caller passes when they are
the source Bot's owner, or when they hold a live ``ac_bot_collaborator`` row on
it (any role — admin or member). The previous ``"edit" not in actions`` test was
unreachable in practice: ``bot_repo`` returns the ``ac_bots`` row, which has no
``actions`` column (actions are a listing-time, per-caller computation), so the
test only ever matched owners — explicit collaborators were wrongly refused.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml
from fastapi import HTTPException

from agentclaw.community.core.bot_config_manifest.schema.sources import parse_source
from agentclaw.community.utils.env_utils import get_current_env


def _from_bot_source_identities(document: str) -> list[tuple[str, str]]:
    """The ``(bot_id, owner_id)`` of every declared ``protocol: bot`` source.

    Pure read of the document: ``yaml.safe_load`` then ``parse_source`` per
    named source, the same parser the schema and the compiler use. Non-bot
    sources and malformed entries are skipped — the validator rejects those at
    ``put`` with its own reasons; this gate only enumerates bot sources.
    """
    parsed = yaml.safe_load(document)
    if not isinstance(parsed, Mapping):
        return []
    sources = parsed.get("sources")
    if not isinstance(sources, Mapping):
        return []
    identities: list[tuple[str, str]] = []
    for raw in sources.values():
        if not isinstance(raw, Mapping):
            continue
        decl, _ = parse_source(raw)
        if decl is None or not decl.is_bot:
            continue
        if decl.bot_id and decl.owner_id:
            identities.append((decl.bot_id, decl.owner_id))
    return identities


def _caller_is_source_collaborator(
    *, collaborator_repository: Any, bot_pk: int, user_id: str
) -> bool:
    """Whether ``user_id`` holds any ``ac_bot_collaborator`` row on the source.

    A row of either role (admin/member) grants edit content access on the
    source Bot, which is what copying-from / referencing a ``from_bot`` source
    needs. ``bot_pk`` is the source's ``ac_bots.id`` (primary key), the key the
    collaborator table is indexed by.
    """
    return (
        collaborator_repository.get_by_bot_and_user(
            bot_pk, user_id, get_current_env()
        )
        is not None
    )


def assert_from_bot_source_access(
    *,
    document: str,
    owner_id: str,
    bot_repo: Any,
    collaborator_repository: Any,
) -> None:
    """Refuse submission when the dst owner lacks access to any declared
    ``protocol: bot`` source.

    Raises ``409`` for a source bot that does not exist and ``403`` when the
    caller is neither its owner nor a collaborator (any role) on it — the Copy
    endpoint's rule, applied to every bot source the document declares. The
    declared ``owner_id`` on the source addresses the lookup (a ``bot_id`` is
    not globally unique, so the owner disambiguates the row): the source is
    read through ``bot_repo.get_by_id_and_owner(bot_id, declared_owner_id)``
    rather than a bare ``get_by_id``, which would otherwise return any live row
    sharing that bot_id (e.g. the per-account ``default`` template).
    """
    for source_bot_id, declared_owner_id in _from_bot_source_identities(document):
        source = bot_repo.get_by_id_and_owner(source_bot_id, declared_owner_id)
        if source is None:
            raise HTTPException(
                status_code=409,
                detail=f"source bot {source_bot_id!r} not found",
            )
        if source.get("owner_id") != owner_id and not _caller_is_source_collaborator(
            collaborator_repository=collaborator_repository,
            bot_pk=int(source["id"]),
            user_id=owner_id,
        ):
            raise HTTPException(
                status_code=403,
                detail=f"no edit permission on source bot {source_bot_id!r}",
            )
