"""Dormant-bot candidate dataclass and two-stage filter.

Extracted from service.py to keep that module under the 1000-line guard.

Exposes:
  ``Candidate`` — frozen dataclass representing a bot that passed both stages.
  ``filter_candidates(session, N)`` — Stage-1/2 SQL + in-memory filter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from agentclaw.community.core.bot_dormant.sqlite_models import DormantWhitelist
from agentclaw.community.plugin_api.models import BotModel


if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Candidate:
    """A bot that has passed both filter stages and is a dormancy candidate."""
    bot_id: str
    entity_id: str
    owner_id: str
    bot_name: str | None
    gmt_create: datetime


def owner_is_protected(
    owner_id: str,
    protected_owner_ids: frozenset[str],
) -> bool:
    """Return whether an owner ID belongs to the protected set."""
    return owner_id in protected_owner_ids


def partition_by_protected_owner(
    candidates: list[Candidate],
    protected_owner_ids: frozenset[str],
) -> tuple[list[Candidate], list[Candidate]]:
    """Split candidates into protected and unprotected lists in one pass."""
    protected: list[Candidate] = []
    unprotected: list[Candidate] = []
    for candidate in candidates:
        target = protected if owner_is_protected(
            candidate.owner_id, protected_owner_ids
        ) else unprotected
        target.append(candidate)
    return protected, unprotected


def filter_candidates(session: Session, N: int) -> list[Candidate]:
    """Return bots eligible for dormancy governance.

    Args:
        session: SQLAlchemy session (works with both SQLite and MySQL).
        N: Age threshold in days. Bots created fewer than N days ago
           are excluded.

    Returns:
        List of :class:`Candidate` after both filter stages.
    """
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=N)

    # Stage 1: SQL filter
    rows = (
        session.query(BotModel)
        .filter(
            BotModel.status == "ACTIVE",
            BotModel.is_delete == 0,
            BotModel.bot_type == "personal",
            BotModel.gmt_create < cutoff,
        )
        .order_by(BotModel.gmt_modified.desc(), BotModel.id.desc())
        .all()
    )

    # Stage 2a: Drop bots with non-numeric entity_id
    rows = [
        r
        for r in rows
        if r.entity_id and r.entity_id.isdigit()
    ]

    # Stage 2b: Subtract whitelist.
    # Key by (bot_id, owner_id) — bot_id='default' is per-owner, single-key
    # set would white-list all owners' default bots in one shot.
    whitelist: set[tuple[str, str]] = {
        (r.bot_id, r.owner_id) for r in session.query(DormantWhitelist).all()
    }
    rows = [
        r
        for r in rows
        if (r.bot_id, r.owner_id) not in whitelist
    ]

    # ac_bots can contain multiple active rows for the same logical personal
    # bot. The notify table is keyed by (bot_id, owner_id, dt, notify_type), so
    # process each logical bot once per scan and keep the freshest row above.
    seen: set[tuple[str, str]] = set()
    deduped = []
    for r in rows:
        key = (r.bot_id, r.owner_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    rows = deduped

    return [
        Candidate(
            bot_id=r.bot_id,
            entity_id=r.entity_id,
            owner_id=r.owner_id,
            bot_name=r.bot_name,
            gmt_create=r.gmt_create,
        )
        for r in rows
    ]
