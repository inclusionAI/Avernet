"""Unified HarnessTemplate repository (prod the relational store + local SQLite).

One ORM implementation behind the ``HarnessTemplateRepository``
Protocol. The only per-environment difference is the injected
:class:`DatabasePlugin`: ``orm_session()`` yields a SQLAlchemy
``Session`` in both runtimes, so this single body runs unchanged on
OceanBase (prod) and SQLite (local), collapsing the previous
raw-SQL/ORM twins so CI exercises the prod path too.

Prod-twin parity:

- ``create`` is a plain INSERT (``db.add`` + ``db.flush``), exactly
  like both legacy twins. A duplicate ``(name, env)`` violates
  ``uk_harness_template_name_env`` and raises ``IntegrityError`` —
  the router catches it as a 500, same as prod always did. No upsert
  (an earlier S2 draft added one; reverted to preserve prod
  semantics — the duplicate-raises contract is intentional).
- ``update(tpl_id, **fields)`` is a by-id UPDATE that bumps
  ``version`` by 1 (matches both twins).
- ``soft_delete`` is a blind UPDATE returning ``rowcount > 0`` (prod
  parity; SQLAlchemy mysqlconnector sets ``CLIENT_FOUND_ROWS`` so
  rowcount = rows matched, same as SQLite).

Dead-field removal: ``source_file`` (column 11 in the prod DDL ran
silently absent — prod twin never wrote or read it). Removed from
domain dataclass, API schemas, router, and the ORM model in this
commit.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.harness.models import (
    Layer,
    PatchOperation,
    PatchTarget,
    PatchTemplate,
    PatchTemplateStatus,
    RiskLevel,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.repository.protocols.harness import HarnessTemplateRepository


logger = get_logger()


class HarnessTemplateRepository(
    HarnessTemplateRepository,
):
    """Unified ORM ``HarnessTemplateRepository`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        from agentclaw.community.core.harness.sqlite_models import (
            HarnessPatchTemplateModel,
        )

        self._db = db
        self.Model = HarnessPatchTemplateModel

    @staticmethod
    def _row_to_domain(row) -> PatchTemplate:
        # Normalize layer value: handle both "L1" and "Layer.L1" formats
        # (preserved from both twins — older rows can carry the prefixed
        # form).
        layer_raw = row.layer
        layer_value = (
            layer_raw.replace("Layer.", "")
            if layer_raw and layer_raw.startswith("Layer.")
            else layer_raw
        )
        return PatchTemplate(
            id=row.id,
            name=row.name,
            layer=Layer(layer_value),
            target=(
                PatchTarget(**json.loads(row.target))
                if row.target
                else PatchTarget()
            ),
            version=row.version,
            description=row.description,
            applicable_when=(
                json.loads(row.applicable_when) if row.applicable_when else None
            ),
            operations=(
                [PatchOperation(**op) for op in json.loads(row.operations)]
                if row.operations
                else []
            ),
            risk_level=RiskLevel(row.risk_level),
            status=PatchTemplateStatus(row.status),
            env=row.env,
        )

    def create(self, tpl: PatchTemplate) -> PatchTemplate:
        """Plain INSERT (prod parity). Duplicate (name, env) raises
        IntegrityError on uk_harness_template_name_env — same as both
        legacy twins; the router surfaces it as a 500."""
        layer_val = (
            tpl.layer.value
            if isinstance(tpl.layer, Layer)
            else str(tpl.layer).replace("Layer.", "")
        )
        with self._db.orm_session() as db:
            row = self.Model(
                name=tpl.name,
                layer=layer_val,
                target=json.dumps(
                    {
                        "files": tpl.target.files,
                        "sections": tpl.target.sections,
                    }
                ),
                version=tpl.version,
                description=tpl.description,
                applicable_when=(
                    json.dumps(tpl.applicable_when)
                    if tpl.applicable_when
                    else None
                ),
                operations=json.dumps([asdict(op) for op in tpl.operations]),
                risk_level=(
                    tpl.risk_level.value
                    if isinstance(tpl.risk_level, RiskLevel)
                    else str(tpl.risk_level)
                ),
                status=(
                    tpl.status.value
                    if isinstance(tpl.status, PatchTemplateStatus)
                    else str(tpl.status)
                ),
                env=tpl.env,
            )
            db.add(row)
            db.flush()  # populates row.id without ending the session
            tpl.id = row.id
            logger.info(
                "[HarnessTemplateRepo] create name=%s env=%s id=%s",
                tpl.name, tpl.env, row.id,
            )
            return tpl

    def get_by_id(self, tpl_id: int) -> Optional[PatchTemplate]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(self.Model.id == tpl_id)
                .first()
            )
            return self._row_to_domain(row) if row else None

    def get_by_name(self, name: str, env: str) -> Optional[PatchTemplate]:
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(self.Model.name == name, self.Model.env == env)
                .first()
            )
            return self._row_to_domain(row) if row else None

    def list(
        self,
        layer: str | None = None,
        status: str | None = None,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[PatchTemplate], int]:
        env = get_current_env()
        with self._db.orm_session() as db:
            query = db.query(self.Model).filter(self.Model.env == env)
            if layer:
                query = query.filter(self.Model.layer == layer)
            if status:
                query = query.filter(self.Model.status == status)
            if keyword:
                query = query.filter(self.Model.name.contains(keyword))
            total = query.count()
            rows = (
                query.order_by(self.Model.gmt_create.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [self._row_to_domain(r) for r in rows], total

    def update(self, tpl_id: int, **fields) -> Optional[PatchTemplate]:
        # Strip caller-supplied version — version is bumped by the
        # repo on every update (parity with both twins).
        fields.pop("version", None)
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(self.Model.id == tpl_id)
                .first()
            )
            if row is None:
                return None
            for key, val in fields.items():
                if val is None:
                    continue
                if key == "target":
                    val = json.dumps(val)
                elif key == "applicable_when":
                    val = json.dumps(val) if val is not None else None
                elif key == "operations":
                    val = json.dumps(val)
                elif key in {"layer", "risk_level", "status"}:
                    val = val.value if hasattr(val, "value") else str(val)
                setattr(row, key, val)
            row.version = (row.version or 0) + 1
            db.flush()
            return self._row_to_domain(row)

    def soft_delete(self, tpl_id: int) -> bool:
        with self._db.orm_session() as db:
            rowcount = (
                db.query(self.Model)
                .filter(self.Model.id == tpl_id)
                .update(
                    {
                        self.Model.status: "deprecated",
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
            return rowcount > 0

    def load_all_active(self) -> list[PatchTemplate]:
        env = get_current_env()
        with self._db.orm_session() as db:
            rows = (
                db.query(self.Model)
                .filter(self.Model.status == "active", self.Model.env == env)
                .all()
            )
            return [self._row_to_domain(r) for r in rows]
