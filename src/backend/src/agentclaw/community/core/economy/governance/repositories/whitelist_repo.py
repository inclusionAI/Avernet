"""Governance whitelist repository — ``ac_bot_whitelist``.

Provides batch-add, lookup, and list operations for the unified whitelist
table.  Governance uses ``whitelist_type='governance'``.

Renamed from the former ``whitelist_service.GovernanceWhitelistService`` — it
is a table-scoped repository (IO), not a domain service.

Follows the ``DatabasePlugin`` self-managed session pattern
(see ``harness_patch_record_repository``): each method opens its
own ``orm_session()`` — self-managed sessions.  Env is resolved
internally via ``get_current_env()``.
"""
from __future__ import annotations

import logging
from datetime import datetime

from injector import inject
from sqlalchemy import or_

from agentclaw.community.core.economy.governance.contracts.models import BotWhitelist
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env

log = get_logger()


class GovernanceWhitelistRepository:
    """Manage governance whitelist entries in ``ac_bot_whitelist``."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def batch_add(
        self,
        entries: list[dict],
        created_by: str,
        *,
        whitelist_type: str = "governance",
        source: str = "manual",
    ) -> dict:
        """Batch-add whitelist entries. Idempotent on UK conflict.

        Args:
            entries: List of dicts with at least ``bot_id`` and ``owner_id``.
                     Optional: ``reason``, ``expires_at``.
            created_by: Who triggered the add.
            whitelist_type: Discriminator (default ``'governance'``).
            source: Origin of the add (manual / owner / admin / system / emergency).

        Returns:
            ``{"inserted": N, "skipped": N}``
        """
        if not entries:
            return {"inserted": 0, "skipped": 0}

        _env = get_current_env()
        inserted = 0
        skipped = 0

        with self._db.orm_session() as session:
            for entry in entries:
                bot_id = entry.get("bot_id")
                owner_id = entry.get("owner_id")
                if not bot_id or not owner_id:
                    skipped += 1
                    continue

                # Check for existing entry (idempotent skip, scoped to env)
                exists = (
                    session.query(BotWhitelist)
                    .filter(
                        BotWhitelist.bot_id == bot_id,
                        BotWhitelist.owner_id == owner_id,
                        BotWhitelist.whitelist_type == whitelist_type,
                        BotWhitelist.env == _env,
                    )
                    .first()
                )
                if exists:
                    skipped += 1
                    continue

                row = BotWhitelist(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    whitelist_type=whitelist_type,
                    source=source,
                    reason=(entry.get("reason") or "")[:500],
                    created_by=created_by,
                    expires_at=self._parse_expires_at(entry.get("expires_at")),
                )
                # Explicitly set env (ORM default won't fire when constructor
                # doesn't mention the column — SQLAlchemy only calls default
                # for columns absent from the constructor kwargs).
                row.env = _env
                session.add(row)
                inserted += 1

            try:
                session.commit()
            except Exception:
                log.exception("[GovernanceWhitelist] batch_add commit failed")
                session.rollback()
                raise

        log.info(
            "[GovernanceWhitelist] batch_add: inserted=%d, skipped=%d, type=%s, env=%s",
            inserted, skipped, whitelist_type, _env,
        )
        return {"inserted": inserted, "skipped": skipped}

    def get_whitelist_set(
        self,
        *,
        whitelist_type: str = "governance",
    ) -> set[tuple[str, str]]:
        """Return the set of (bot_id, owner_id) for un-expired whitelist entries.

        Used by the scan for efficient in-memory filter.
        """
        _env = get_current_env()
        now = datetime.now()
        with self._db.orm_session() as s:
            rows = (
                s.query(BotWhitelist.bot_id, BotWhitelist.owner_id)
                .filter(
                    BotWhitelist.whitelist_type == whitelist_type,
                    BotWhitelist.env == _env,
                    or_(
                        BotWhitelist.expires_at.is_(None),
                        BotWhitelist.expires_at > now,
                    ),
                )
                .all()
            )
            return {(r.bot_id, r.owner_id) for r in rows}

    def count_by_type(
        self,
        *,
        whitelist_type: str = "governance",
    ) -> int:
        """Count whitelist entries of a given type (self-managed session)."""
        _env = get_current_env()
        with self._db.orm_session() as s:
            return (
                s.query(BotWhitelist)
                .filter(
                    BotWhitelist.whitelist_type == whitelist_type,
                    BotWhitelist.env == _env,
                )
                .count()
            )

    def list_all(
        self,
        owner_id: str | None = None,
        *,
        whitelist_type: str = "governance",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """List whitelist entries (optionally filtered by owner_id).

        Returns:
            List of dicts with whitelist entry fields.
        """
        _env = get_current_env()
        with self._db.orm_session() as session:
            query = session.query(BotWhitelist).filter(
                BotWhitelist.whitelist_type == whitelist_type,
                BotWhitelist.env == _env,
            )
            if owner_id:
                query = query.filter(BotWhitelist.owner_id == owner_id)

            rows = query.order_by(BotWhitelist.gmt_create.desc()).offset(offset).limit(limit).all()

            return [r.to_dict() for r in rows]

    def batch_remove(
        self,
        *,
        ids: list[int] | None = None,
        bot_owner_pairs: list[dict] | None = None,
        whitelist_type: str = "governance",
        dry_run: bool = False,
    ) -> dict:
        """Remove whitelist entries by IDs or (bot_id, owner_id) pairs.

        Idempotent — silently skips non-existent entries (reported in
        ``not_found``).  Entries matched by both ``ids`` and
        ``bot_owner_pairs`` are deduplicated (deleted only once).

        Args:
            ids: Primary key IDs to remove.
            bot_owner_pairs: List of dicts with ``bot_id`` and ``owner_id``.
            whitelist_type: Discriminator (default ``'governance'``).
            dry_run: If True, only count and return matches without deleting.

        Returns:
            ``{"deleted": N, "not_found": [...], "affected_pairs": [...]}``
        """
        _env = get_current_env()
        matched_rows: list[BotWhitelist] = []
        not_found: list[dict] = []

        with self._db.orm_session() as session:
            # --- Collect by IDs ---
            if ids:
                for pk in ids:
                    row = (
                        session.query(BotWhitelist)
                        .filter(
                            BotWhitelist.id == pk,
                            BotWhitelist.whitelist_type == whitelist_type,
                            BotWhitelist.env == _env,
                        )
                        .first()
                    )
                    if row:
                        matched_rows.append(row)
                    else:
                        not_found.append({"id": pk, "hint": "id not found"})

            # --- Collect by (bot_id, owner_id) pairs ---
            if bot_owner_pairs:
                for pair in bot_owner_pairs:
                    bid = pair.get("bot_id", "")
                    oid = pair.get("owner_id", "")
                    if not bid or not oid:
                        not_found.append({
                            "bot_id": bid,
                            "owner_id": oid,
                            "hint": "missing bot_id or owner_id",
                        })
                        continue

                    row = (
                        session.query(BotWhitelist)
                        .filter(
                            BotWhitelist.bot_id == bid,
                            BotWhitelist.owner_id == oid,
                            BotWhitelist.whitelist_type == whitelist_type,
                            BotWhitelist.env == _env,
                        )
                        .first()
                    )
                    if row:
                        matched_rows.append(row)
                    else:
                        not_found.append({
                            "bot_id": bid,
                            "owner_id": oid,
                            "hint": "pair not found",
                        })

            # --- Deduplicate by primary key (same row may be found via id AND pair) ---
            seen_ids: set[int] = set()
            unique_rows: list[BotWhitelist] = []
            for row in matched_rows:
                if row.id not in seen_ids:
                    seen_ids.add(row.id)
                    unique_rows.append(row)

            # --- Delete (only when dry_run=false) ---
            deleted = 0
            affected_pairs: list[dict] = []
            for row in unique_rows:
                affected_pairs.append({
                    "bot_id": row.bot_id,
                    "owner_id": row.owner_id,
                })
                if not dry_run:
                    session.delete(row)
                deleted += 1

            if not dry_run:
                try:
                    session.commit()
                except Exception:
                    log.exception("[GovernanceWhitelist] batch_remove commit failed")
                    session.rollback()
                    raise

        log.info(
            "[GovernanceWhitelist] batch_remove: deleted=%d, not_found=%d, type=%s, env=%s",
            deleted, len(not_found), whitelist_type, _env,
        )
        return {
            "deleted": deleted,
            "not_found": not_found,
            "affected_pairs": affected_pairs,
        }

    @staticmethod
    def _parse_expires_at(value: object) -> datetime | None:
        """Parse expires_at from various formats."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue
        return None