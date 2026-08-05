"""Unified HarnessPatch repository (prod ZDAS + local SQLite).

One ORM implementation behind ``HarnessPatchRepository``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local),
collapsing the previous raw-SQL/ORM twins so CI exercises the prod path
too.

The Protocol speaks the ``PatchDefinition`` domain object; the ORM↔domain
mapping is copied verbatim from the prior local twin. Production DDL
parity for ``ac_harness_patch`` is a Pre acceptance record (round-2 spec).
"""
from __future__ import annotations

from injector import inject

from agentclaw.community.core.harness.models import Layer, PatchDefinition
from agentclaw.community.core.harness.sqlite_models import HarnessPatchModel
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()


class HarnessPatchRepository:
    """Unified ``HarnessPatchRepository`` Protocol implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    @staticmethod
    def _row_to_domain(row: HarnessPatchModel) -> PatchDefinition:
        return PatchDefinition(
            id=row.id,
            template_id=row.template_id or 0,
            scan_id=row.record_id,
            name=row.name or "",
            layer=Layer(row.layer) if row.layer else Layer.L1,
            description=row.description,
            scope=row.scope or "all",
            content=row.content or "[]",
            advise=row.advise,
            is_applied=(row.is_applied == "Y"),
            env=row.env or "dev",
            gmt_create=row.gmt_create,
            gmt_modified=row.gmt_modified,
        )

    def create(self, patch: PatchDefinition) -> int:
        scope = (
            patch.scope_value
            if patch.scope == "bot" and patch.scope_value
            else patch.scope
        )
        with self._db.orm_session() as s:
            row = HarnessPatchModel(
                template_id=patch.template_id,
                record_id=patch.scan_id or 0,
                name=patch.name,
                layer=patch.layer.value
                if hasattr(patch.layer, "value")
                else str(patch.layer),
                description=patch.description,
                scope=scope,
                content=patch.content or "[]",
                is_applied="Y" if patch.is_applied else "N",
                env=patch.env,
            )
            s.add(row)
            s.flush()
            s.refresh(row)
            patch.id = row.id
            return row.id

    def get_by_id(self, patch_id: int) -> PatchDefinition | None:
        with self._db.orm_session() as s:
            row = (
                s.query(HarnessPatchModel)
                .filter_by(id=patch_id)
                .first()
            )
            return self._row_to_domain(row) if row else None

    def list_by_ids(self, patch_ids: list[int]) -> list[PatchDefinition]:
        """Fetch multiple patches by their IDs (batch query)."""
        if not patch_ids:
            return []
        # Deduplicate ids while preserving order
        seen = set()
        unique_ids = [pid for pid in patch_ids if not (pid in seen or seen.add(pid))]

        with self._db.orm_session() as s:
            rows = (
                s.query(HarnessPatchModel)
                .filter(HarnessPatchModel.id.in_(unique_ids))
                .all()
            )
            return [self._row_to_domain(r) for r in rows]

    def list_by_record(self, record_id: int) -> list[PatchDefinition]:
        with self._db.orm_session() as s:
            rows = (
                s.query(HarnessPatchModel)
                .filter_by(record_id=record_id)
                .order_by(HarnessPatchModel.gmt_create.desc())
                .all()
            )
            return [self._row_to_domain(r) for r in rows]

    def update_is_applied(self, patch_id: int, is_applied: bool) -> None:
        # Single atomic UPDATE (one query, atomic on both backends incl.
        # prod AUTOCOMMIT) — matches the prior prod twin's
        # `UPDATE ac_harness_patch SET is_applied=%s WHERE id=%s`. No
        # SELECT-then-UPDATE.
        with self._db.orm_session() as s:
            s.query(HarnessPatchModel).filter_by(id=patch_id).update(
                {HarnessPatchModel.is_applied: "Y" if is_applied else "N"},
                synchronize_session=False,
            )
