"""SwarmIdentityRepository — ac_agent_swarm_identity persistence.

Follows the ac_agent_swarm_* family's shape: a tenant/env-scoped row, a unique key
per logical identity, and JSON in text columns for the parts that are lists.

The one asymmetry worth stating: ``put`` is an upsert on ``run_id``. A run mints its
identity exactly once, so a second ``put`` for the same run is a replay, not a new
identity — the existing row wins, which is what keeps a retried phase one from
overwriting an agent code the service already honoured.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.agentswarm.repository.identity import model as om
from agentclaw.community.core.agentswarm.repository.identity.protocol import (
    SwarmIdentityRecord,
    SwarmIdentityRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


def _dumps_list(values: Sequence[Any]) -> str:
    return json.dumps(list(values), ensure_ascii=False, default=str)


def _loads_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


class SwarmIdentityRepository(SwarmIdentityRepositoryProtocol):
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def get(self, run_id: str) -> SwarmIdentityRecord | None:
        with self._db.orm_session() as session:
            row = session.execute(
                select(om.AcAgentSwarmIdentityModel).where(
                    om.AcAgentSwarmIdentityModel.env == get_current_env(),
                    om.AcAgentSwarmIdentityModel.tenant == get_current_avernet_tenant(),
                    om.AcAgentSwarmIdentityModel.run_id == run_id,
                    om.AcAgentSwarmIdentityModel.is_deleted.is_(False),
                )
            ).scalar_one_or_none()
            return _to_record(row) if row is not None else None

    def put(self, record: SwarmIdentityRecord) -> SwarmIdentityRecord:
        try:
            with self._db.orm_session() as session:
                session.add(
                    om.AcAgentSwarmIdentityModel(
                        run_id=record.run_id,
                        env=get_current_env(),
                        tenant=get_current_avernet_tenant(),
                        swarm_bot_id=record.swarm_bot_id,
                        agent_code=record.agent_code,
                        status=record.status,
                        mcp_codes=_dumps_list(record.mcp_codes),
                        cli_items=_dumps_list(record.cli_items),
                        worker_count=record.worker_count,
                        owner_id=record.owner_id,
                    )
                )
        except IntegrityError:
            # A replay of phase one: the run already has its identity. The stored
            # row wins, so the agent code the service honoured is never replaced.
            existing = self.get(record.run_id)
            if existing is None:
                raise
            return existing
        return self.get(record.run_id) or record

    def update_scope(
        self,
        run_id: str,
        *,
        mcp_codes: Sequence[str],
        cli_items: Sequence[Mapping[str, Any]],
    ) -> bool:
        return self._update(
            run_id,
            mcp_codes=_dumps_list(mcp_codes),
            cli_items=_dumps_list(cli_items),
        )

    def increment_workers(self, run_id: str) -> bool:
        return self._update(
            run_id,
            worker_count=om.AcAgentSwarmIdentityModel.worker_count + 1,
        )

    def list_stale(
        self, *, cutoff: Any, limit: int | None = None
    ) -> list[SwarmIdentityRecord]:
        """Issued identities untouched since ``cutoff``, oldest first.

        The backstop for runs whose terminal write never landed: nothing else will
        ever release these, and without a reader the table only grows.
        """
        with self._db.orm_session() as session:
            query = (
                select(om.AcAgentSwarmIdentityModel)
                .where(
                    om.AcAgentSwarmIdentityModel.env == get_current_env(),
                    om.AcAgentSwarmIdentityModel.tenant
                    == get_current_avernet_tenant(),
                    om.AcAgentSwarmIdentityModel.status == "ISSUED",
                    om.AcAgentSwarmIdentityModel.is_deleted.is_(False),
                    om.AcAgentSwarmIdentityModel.gmt_modified < cutoff,
                )
                .order_by(om.AcAgentSwarmIdentityModel.gmt_modified.asc())
            )
            if limit is not None:
                query = query.limit(int(limit))
            rows = session.execute(query).scalars().all()
            return [_to_record(row) for row in rows]

    def mark_destroyed(self, run_id: str) -> bool:
        return self._update(run_id, status="DESTROYED")

    def _update(self, run_id: str, **values: Any) -> bool:
        with self._db.orm_session() as session:
            result = session.execute(
                update(om.AcAgentSwarmIdentityModel)
                .where(
                    om.AcAgentSwarmIdentityModel.env == get_current_env(),
                    om.AcAgentSwarmIdentityModel.tenant == get_current_avernet_tenant(),
                    om.AcAgentSwarmIdentityModel.run_id == run_id,
                    om.AcAgentSwarmIdentityModel.is_deleted.is_(False),
                )
                .values(**values)
            )
            return bool(result.rowcount)


def _to_record(row: om.AcAgentSwarmIdentityModel) -> SwarmIdentityRecord:
    return SwarmIdentityRecord(
        run_id=row.run_id,
        swarm_bot_id=row.swarm_bot_id,
        agent_code=row.agent_code,
        status=row.status,
        mcp_codes=tuple(str(c) for c in _loads_list(row.mcp_codes)),
        cli_items=tuple(
            item for item in _loads_list(row.cli_items) if isinstance(item, dict)
        ),
        worker_count=int(row.worker_count or 0),
        owner_id=row.owner_id or "",
    )