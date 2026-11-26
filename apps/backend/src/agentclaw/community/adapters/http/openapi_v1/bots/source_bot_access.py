"""Submission-time source-Bot access gate for ``from: from_bot`` local skills.

A manifest that declares a ``protocol: bot`` source — referenced by a
``from: from_bot`` local skill member — reads the source Bot's ``ac_skill`` row
during apply (the capabilities writer's ``ensure_dst_local_skill_asset`` reverse
lookup). Before such a manifest is stored or an apply task is created, the
submitting dst owner must have access to every declared bot source.

The Copy endpoint already enforces this at ``copy.py:446`` (``source.owner_id !=
owner_id and "edit" not in actions``). A plain Manifest PUT and a create-with-
manifest both feed a ``from_bot`` source to the apply engine without going
through the Copy endpoint, so this module is the shared submission boundary that
closes that bypass. It is read-only against the document and the bot record:
nothing is written, and a refusal raises before any storage or task creation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml
from fastapi import HTTPException

from agentclaw.community.core.bot_config_manifest.schema.sources import parse_source


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


def assert_from_bot_source_access(
    *, document: str, owner_id: str, bot_repo: Any
) -> None:
    """Refuse submission when the dst owner lacks access to any declared
    ``protocol: bot`` source.

    Raises ``409`` for a source bot that does not exist and ``403`` when the
    caller is neither its owner nor holds the ``edit`` action — the Copy
    endpoint's rule, applied to every bot source the document declares. The
    declared ``owner_id`` on the source is addressing for the lookup, not the
    access decision: access is the source row's ``owner_id`` and ``actions``,
    read through ``bot_repo.get_by_id``.
    """
    for source_bot_id, _declared_owner_id in _from_bot_source_identities(document):
        source = bot_repo.get_by_id(source_bot_id)
        if source is None:
            raise HTTPException(
                status_code=409,
                detail=f"source bot {source_bot_id!r} not found",
            )
        actions = source.get("actions") or []
        if source.get("owner_id") != owner_id and "edit" not in actions:
            raise HTTPException(
                status_code=403,
                detail=f"no edit permission on source bot {source_bot_id!r}",
            )
