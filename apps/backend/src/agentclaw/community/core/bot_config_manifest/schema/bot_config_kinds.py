"""Per-kind entry rules for ``bot_config`` — the schema half of the kind seam.

``bot_config`` is a mapping-valued Category: its keys are **sync kinds**
(``render_screen``, a future ``task_nav``, …) and each value that kind's entry
list. Unlike ``resources``/``skills``, the entries under one key are not the
same shape as the entries under another — a render-screen row carries
``name``/``cdn_url``, a future task-nav row would carry something else — so a
single per-entry rule cannot cover the Category. Instead each kind owns one
validator, and this table names them.

**Two consumers, one table.** The manifest validator dispatches on it at
``PUT`` to refuse a document naming a kind no handler resolves (``unknown_bot_config_kind``)
before the bot's apply lock is ever held, and to check each kind's entry shape
before the handler at apply time is asked to act on it. The apply-time kind
registry (:mod:`.apply_engine.materialisers.bot_config`) imports the same set
so the validator's surface and a handler's dispatch surface cannot drift: a
kind accepted here is a kind with a handler there, and vice versa.

This mirrors the ``check_entry_keys`` discipline of the list categories: an
entry's field set is closed, so an unknown field is refused rather than
ignored — a field that appears to configure something and does nothing is the
failure mode this whole package exists to remove.
"""
from __future__ import annotations

from typing import Any, Callable

from agentclaw.community.core.bot_config_manifest.schema._support import (
    Context,
)

#: The fields a ``render_screen`` entry may carry. Closed: a render-screen row
#: is the ``ac_bot_render_screen`` (name, cdn_url) pair the
#: :class:`RenderScreenService` writes, serialised verbatim — an extra key is a
#: caller believing they configured a column the platform does not store.
_RENDER_SCREEN_ENTRY_KEYS: frozenset[str] = frozenset({"name", "cdn_url"})


def validate_render_screen_entry(ctx: Context, location: str, entry: dict[str, Any]) -> None:
    """One render-screen row: a non-empty ``name`` and ``cdn_url``, nothing else.

    The shape the :class:`RenderScreenService` create path admits — duplicate
    name/clone checks live there, not here, because they need the destination
    bot's existing rows and that is an apply-time fact. This validator answers
    from the row alone so ``PUT`` can run it before a bot record exists.
    """
    for key in entry:
        if key not in _RENDER_SCREEN_ENTRY_KEYS:
            ctx.add(
                f"{location}.{key}",
                "unknown_field",
                f"unknown field '{key}' for a render_screen entry",
            )
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        ctx.add(
            f"{location}.name",
            "missing_name",
            "a render_screen entry must have a non-empty 'name'",
        )
    cdn_url = entry.get("cdn_url")
    if not isinstance(cdn_url, str) or not cdn_url:
        ctx.add(
            f"{location}.cdn_url",
            "missing_cdn_url",
            "a render_screen entry must have a non-empty 'cdn_url'",
        )


#: The fields a ``task_nav`` entry may carry — closed. A task-nav row is a
#: cross-bot reference whose workflow permissions (the
#: ``bot-workflow-permissions`` clawweb owns) copy converges onto the
#: destination: ``type`` discriminates the clone shape, ``source_bot_id``
#: names the source row, and ``source_owner_id`` disambiguates it because a
#: ``bot_id`` is not globally unique (the same ``(bot_id, owner_id)``
#: addressing the bot-file-sync source uses). The destination side
#: (``target_bot_id`` / ``target_owner_id``) is the apply context itself,
#: and ``scope`` / ``dry_run`` are constants the handler writes (``exact`` /
#: ``false``), so none of the three is an entry field — an extra key is a
#: caller believing they configured a knob the platform does not expose on
#: this surface.
_TASK_NAV_ENTRY_KEYS: frozenset[str] = frozenset({"type", "source_bot_id", "source_owner_id"})

#: The ``type`` values this build knows. v1 ships ``"bot"`` (clone the source
#: bot's whole workflow-permission set); a future ``"item"`` will clone a
#: named workflow and carry ``workflow_id`` — added here as a new member and
#: as a conditional allowed-field below, never a schema break. Closed so a
#: type the platform does not act on is refused at ``PUT`` rather than
#: silently no-opping at apply.
_TASK_NAV_KINDS: frozenset[str] = frozenset({"bot"})


def validate_task_nav_entry(ctx: Context, location: str, entry: dict[str, Any]) -> None:
    """One task-nav row: a ``type`` this build knows, a non-empty
    ``source_bot_id`` and ``source_owner_id``, nothing else.

    The handler does not read the destination's current workflow permissions
    to converge (the clawweb ``clone`` endpoint is idempotent and is the single
    converge), so the row is just the source identity plus the clone shape —
    the same closed-set discipline ``render_screen`` keeps, only the fields
    are a reference rather than a verbatim value row.

    ``type`` is the discriminator a future ``"item"`` clone is additive on:
    today only ``"bot"`` is admitted, and any other value (including a missing
    one) is one ``invalid_type`` violation, never a no-op. When ``"item"``
    lands the allowed-field set for that branch widens to include
    ``workflow_id``; ``"bot"``'s set does not, so the two shapes cannot drift
    into each other.
    """
    for key in entry:
        if key not in _TASK_NAV_ENTRY_KEYS:
            ctx.add(
                f"{location}.{key}",
                "unknown_field",
                f"unknown field '{key}' for a task_nav entry",
            )
    kind = entry.get("type")
    if not isinstance(kind, str) or kind not in _TASK_NAV_KINDS:
        ctx.add(
            f"{location}.type",
            "invalid_type",
            f"a task_nav entry must have a 'type' one of {sorted(_TASK_NAV_KINDS)}",
        )
    source_bot_id = entry.get("source_bot_id")
    if not isinstance(source_bot_id, str) or not source_bot_id:
        ctx.add(
            f"{location}.source_bot_id",
            "missing_source_bot_id",
            "a task_nav entry must have a non-empty 'source_bot_id'",
        )
    source_owner_id = entry.get("source_owner_id")
    if not isinstance(source_owner_id, str) or not source_owner_id:
        ctx.add(
            f"{location}.source_owner_id",
            "missing_source_owner_id",
            "a task_nav entry must have a non-empty 'source_owner_id'",
        )


#: The kinds this build knows, each mapped to its per-entry validator. A kind
#: present here is one the manifest accepts; a kind absent here is one no
#: apply-time handler resolves either, refused as ``unknown_bot_config_kind``.
#: Adding a kind = adding a validator here and a handler in
#: :mod:`.apply_engine.materialisers.bot_config` — two edits, one surface.
BOT_CONFIG_KIND_VALIDATORS: dict[str, Callable[[Context, str, dict[str, Any]], None]] = {
    "render_screen": validate_render_screen_entry,
    "task_nav": validate_task_nav_entry,
}


__all__ = [
    "BOT_CONFIG_KIND_VALIDATORS",
    "validate_render_screen_entry",
    "validate_task_nav_entry",
]
