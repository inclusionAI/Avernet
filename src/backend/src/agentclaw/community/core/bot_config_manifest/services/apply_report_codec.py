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

from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyReport,
    ApplyStatus,
    CategoryResult,
    EntryOutcome,
    EntryResult,
    SourceResolution,
)
from agentclaw.community.core.bot_config_manifest.capabilities import (
    ManifestCategory,
    ManifestSection,
    parse_category,
)


def report_from_payload(
    payload: dict, *, record: Any, status: ApplyStatus
) -> ApplyReport:
    """Rebuild the in-memory report from what was stored.

    The stored JSON is the wire shape, so this is its inverse. Entry outcomes
    round-trip through the enum rather than being carried as raw strings: a
    value the enum does not know is a corrupted row, and failing here is better
    than serving it onward as if it meant something.
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
        sources=tuple(
            SourceResolution(
                name=source.get("name", ""),
                ref=source.get("ref"),
                resolved_sha=source.get("resolved_sha"),
                auth=source.get("auth"),
            )
            for source in payload.get("sources") or []
        ),
        notes=tuple(str(note) for note in payload.get("notes") or ()),
    )


def _construct_of(name: Any) -> ManifestCategory | ManifestSection | None:
    """A stored category name back into its construct, or ``None`` if unknown."""
    category = parse_category(name)
    if category is not None:
        return category
    try:
        return ManifestSection(name)
    except ValueError:
        return None


__all__ = ["report_from_payload"]
