"""The inverse of ``ApplyReport.as_payload``: a stored row back into a report.

Its own module rather than private helpers on the apply service, because it is
the one part of that service with no dependency on the service at all — it takes
a payload and a record and returns a report, and every read path (``get_apply``,
``last_apply``, and W13's phase carry) goes through it. Keeping it here means the
storage shape has one reader, and the service file holds only behaviour.

The stored JSON is the wire shape, so the decode is deliberately strict about
vocabulary: an entry naming a construct the enum does not know is dropped rather
than served onward as if the name meant something.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.log import get_logger
from agentclaw.community.core.apply_engine.compiler import state_ref_from_payload
from agentclaw.community.core.apply_engine.outcomes import (
    SourceMode,
    ApplyReport,
    ApplyStatus,
    CategoryResult,
    EntryOutcome,
    EntryResult,
    SourceResolution,
)
from agentclaw.community.core.apply_engine.capabilities import (
    Category,
    Section,
    parse_category,
)


logger = get_logger()

def report_from_payload(
    payload: dict, *, record: Any, status: ApplyStatus
) -> ApplyReport:
    """Rebuild the in-memory report from what was stored.

    The stored JSON is the wire shape, so this is its inverse. Entry outcomes
    round-trip through the enum rather than being carried as raw strings: a
    value the enum does not know is a corrupted row, and failing here is better
    than serving it onward as if it meant something. A source ``mode`` the
    enum does not know is the one field read leniently (``None``, logged at
    warning): the Manifest compiler decodes the last reports on **every**
    compile for its baselines, so a loud failure there would stop the bot from
    ever applying again over one unreadable row of immutable history, while
    ``None`` costs that row its baseline opinion and nothing else.
    """
    categories: list[CategoryResult] = []
    by_category: dict[str, list[EntryResult]] = {}
    for entry in payload.get("entries") or []:
        construct = _construct_of(entry.get("category"))
        if construct is None:
            continue
        by_category.setdefault(entry["category"], []).append(
            EntryResult(
                construct=construct,
                identity=entry.get("name") or "",
                outcome=EntryOutcome(entry["action"]),
                reason=entry.get("error"),
                note=entry.get("note"),
            )
        )
    for category in payload.get("categories") or []:
        construct = _construct_of(category.get("category"))
        if construct is None:
            continue
        categories.append(
            CategoryResult(
                construct=construct,
                entries=tuple(by_category.get(category["category"], ())),
                removals=tuple(category.get("removed") or ()),
                aborted=bool(category.get("aborted")),
                # Load-bearing, and easy to leave out: POST returns only a
                # handle, so every caller reads its report through here. A
                # field written by ``as_dict`` but not read back is a field
                # that does not exist as far as the API is concerned — and this
                # one is the only signal that an aborted category may already
                # have changed the bot.
                partially_written=bool(category.get("partially_written")),
            )
        )
    return ApplyReport(
        apply_id=record.apply_id,
        bot_id=record.bot_id,
        trigger=record.trigger,
        status=status,
        started_at=record.started_at,
        finished_at=record.finished_at,
        categories=tuple(categories),
        # Absent on rows written before the field existed: those were Manifest applies.
        state=state_ref_from_payload(payload.get("state")),
        sources=tuple(
            SourceResolution(
                name=source.get("name", ""),
                url=source.get("url"),
                ref=source.get("ref"),
                mode=_source_mode(source.get("mode"), apply_id=record.apply_id, name=source.get("name")),
                resolved_sha=source.get("resolved_sha"),
                auth=source.get("auth"),
            )
            for source in payload.get("sources") or []
        ),
        notes=tuple(str(note) for note in payload.get("notes") or ()),
    )


def _source_mode(value: Any, *, apply_id: str, name: Any) -> SourceMode | None:
    """The stored mode, or ``None`` (logged) for a value outside the enum; see ``report_from_payload``."""
    if value is None:
        return None
    try:
        return SourceMode(value)
    except ValueError:
        logger.warning(
            "[manifest_apply] stored report %s carries source %r with an unreadable mode %r; "
            "read as no baseline opinion",
            apply_id,
            name,
            value,
        )
        return None


def _construct_of(name: Any) -> Category | Section | None:
    """A stored category name back into its construct, or ``None`` if unknown."""
    category = parse_category(name)
    if category is not None:
        return category
    try:
        return Section(name)
    except ValueError:
        return None


__all__ = ["report_from_payload"]
