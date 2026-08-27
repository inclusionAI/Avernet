"""
MySQL Worker Registry Store (production-schema aligned)

This store aligns with the internal production DB schema used by the SQLite
registry store: table names carry the bcsfuse_ prefix, primary key is id,
and worker state is split into state_* columns plus a config JSON column.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Optional, Union

from mysql.connector import Error

from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider
from src.domain.models.worker import (
    Worker,
    WorkerType,
    WorkerIdentity,
    WorkerState,
    Availability,
    TrustLevel,
    Capability,
    CapabilityLevel,
    SkillRef,
    ResourceRef,
)
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.domain.models.worker_source_info import WorkerSourceType
from src.domain.models.worker_config import WorkerConfig
from src.domain.exceptions import DuplicateWorkerException, WorkerNotFoundException

logger = logging.getLogger(__name__)


class MySQLWorkerRegistryStore:
    """
    Worker registry store backed by MySQL.

    Schema mirrors the production SQLite store:
        bcsfuse_workers (
            id VARCHAR(128) PRIMARY KEY,
            type VARCHAR(32) NOT NULL,
            identity_name VARCHAR(255) NOT NULL,
            identity_handle VARCHAR(255) NOT NULL,
            identity_description TEXT,
            responsibilities JSON NOT NULL,
            capabilities JSON NOT NULL,
            skills JSON NOT NULL,
            resources JSON NOT NULL,
            state_availability VARCHAR(32) NOT NULL,
            state_trust_level VARCHAR(32) NOT NULL,
            state_runtime_state VARCHAR(32) NOT NULL DEFAULT 'offline',
            domains JSON NOT NULL,
            lifecycle_state VARCHAR(32) NOT NULL DEFAULT 'active',
            source_type VARCHAR(32) NOT NULL DEFAULT 'api',
            source_ref VARCHAR(255),
            external_id VARCHAR(255),
            active_profile_key VARCHAR(255),
            config JSON,
            version INT NOT NULL DEFAULT 1,
            created_by VARCHAR(128),
            updated_by VARCHAR(128),
            gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_bcsfuse_workers_lifecycle_state (lifecycle_state),
            INDEX idx_bcsfuse_workers_source_type (source_type),
            INDEX idx_bcsfuse_workers_external_id (external_id)
        )
    """

    def __init__(self, connection_pool: MySQLConnectionPoolProvider):
        self._pool = connection_pool
        self._schema_initialized = False
        self._schema_lock = threading.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        if self._schema_initialized:
            return
        with self._schema_lock:
            if self._schema_initialized:
                return
            conn = self._pool.get_connection()
            try:
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS bcsfuse_workers (
                            id VARCHAR(128) PRIMARY KEY,
                            type VARCHAR(32) NOT NULL,
                            identity_name VARCHAR(255) NOT NULL,
                            identity_handle VARCHAR(255) NOT NULL,
                            identity_description TEXT,
                            responsibilities JSON NOT NULL,
                            capabilities JSON NOT NULL,
                            skills JSON NOT NULL,
                            resources JSON NOT NULL,
                            state_availability VARCHAR(32) NOT NULL,
                            state_trust_level VARCHAR(32) NOT NULL,
                            state_runtime_state VARCHAR(32) NOT NULL DEFAULT 'offline',
                            domains JSON NOT NULL,
                            lifecycle_state VARCHAR(32) NOT NULL DEFAULT 'active',
                            source_type VARCHAR(32) NOT NULL DEFAULT 'api',
                            source_ref VARCHAR(255),
                            external_id VARCHAR(255),
                            active_profile_key VARCHAR(255),
                            config JSON,
                            version INT NOT NULL DEFAULT 1,
                            created_by VARCHAR(128),
                            updated_by VARCHAR(128),
                            gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            INDEX idx_bcsfuse_workers_lifecycle_state (lifecycle_state),
                            INDEX idx_bcsfuse_workers_source_type (source_type),
                            INDEX idx_bcsfuse_workers_external_id (external_id)
                        )
                    """)
                finally:
                    cursor.close()
                conn.commit()
                self._schema_initialized = True
                logger.info("[MySQLWorkerRegistryStore] Schema initialized successfully")
            finally:
                conn.close()

    @staticmethod
    def _get_enum_value(value) -> str:
        if hasattr(value, "value"):
            return value.value
        return str(value)

    def _worker_to_dict(self, worker: Worker) -> dict:
        return {
            "id": worker.id,
            "type": self._get_enum_value(worker.type),
            "identity_name": worker.identity.name,
            "identity_handle": worker.identity.handle,
            "identity_description": worker.identity.description,
            "responsibilities": json.dumps(worker.responsibilities),
            "capabilities": json.dumps([
                {"name": c.name, "level": self._get_enum_value(c.level)}
                for c in worker.capabilities
            ]),
            "skills": json.dumps([
                {
                    "name": s.name,
                    "source": self._get_enum_value(s.source),
                    "description": s.description,
                    "trust_level": self._get_enum_value(s.trust_level),
                    "approval_required": s.approval_required,
                    "openclaw_skill_path": s.openclaw_skill_path,
                    "tool_names": s.tool_names,
                }
                for s in worker.skills
            ]),
            "resources": json.dumps([
                {
                    "id": r.id,
                    "kind": self._get_enum_value(r.kind),
                    "name": r.name,
                    "description": r.description,
                    "uri": r.uri,
                    "access": self._get_enum_value(r.access),
                    "owner": r.owner,
                    "tags": r.tags,
                }
                for r in worker.resources
            ]),
            "state_availability": self._get_enum_value(worker.state.availability),
            "state_trust_level": self._get_enum_value(worker.state.trust_level),
            "state_runtime_state": self._get_enum_value(worker.state.runtime_state),
            "domains": json.dumps(worker.domains),
            "lifecycle_state": self._get_enum_value(worker.lifecycle_state),
            "source_type": self._get_enum_value(worker.source_type),
            "source_ref": worker.source_ref,
            "external_id": worker.external_id,
            "active_profile_key": worker.active_profile_key,
            "config": json.dumps(worker.config.model_dump()) if worker.config else None,
            "version": worker.version,
            "created_by": worker.created_by,
            "updated_by": worker.updated_by,
            "gmt_create": worker.created_at.isoformat() if worker.created_at else None,
            "gmt_modify": worker.updated_at.isoformat() if worker.updated_at else None,
        }

    def _row_to_worker(self, row: dict) -> Worker:
        identity = WorkerIdentity(
            name=row["identity_name"],
            handle=row["identity_handle"],
            description=row["identity_description"],
        )

        capabilities_data = row["capabilities"]
        if not isinstance(capabilities_data, str):
            capabilities_data = json.dumps(capabilities_data)
        capabilities = [
            Capability(name=c["name"], level=CapabilityLevel(c["level"]))
            for c in json.loads(capabilities_data)
        ]

        skills_data = row["skills"]
        if skills_data is None:
            skills_data = "[]"
        elif not isinstance(skills_data, str):
            skills_data = json.dumps(skills_data)
        skills = [
            SkillRef(
                name=s["name"],
                source=s.get("source", "file"),
                description=s.get("description"),
                trust_level=s.get("trust_level", "trusted"),
                approval_required=s.get("approval_required", False),
                openclaw_skill_path=s.get("openclaw_skill_path"),
                tool_names=s.get("tool_names", []),
            )
            for s in json.loads(skills_data)
        ]

        resources_data = row["resources"]
        if resources_data is None:
            resources_data = "[]"
        elif not isinstance(resources_data, str):
            resources_data = json.dumps(resources_data)
        resources = [
            ResourceRef(
                id=r.get("id", f"res_{r['name']}"),
                kind=r.get("kind", "knowledge"),
                name=r["name"],
                description=r.get("description"),
                uri=r.get("uri"),
                access=r.get("access", "read"),
                owner=r.get("owner"),
                tags=r.get("tags", []),
            )
            for r in json.loads(resources_data)
        ]

        state = WorkerState(
            availability=Availability(row["state_availability"]),
            trust_level=TrustLevel(row["state_trust_level"]),
            runtime_state=WorkerRuntimeState(row["state_runtime_state"]),
        )

        config_data = row["config"]
        if config_data:
            if not isinstance(config_data, str):
                config_data = json.dumps(config_data)
            config = WorkerConfig(**json.loads(config_data))
        else:
            config = WorkerConfig()

        gmt_create = row["gmt_create"]
        gmt_modify = row["gmt_modify"]
        created_at = gmt_create if isinstance(gmt_create, datetime) else datetime.fromisoformat(str(gmt_create))
        updated_at = gmt_modify if isinstance(gmt_modify, datetime) else datetime.fromisoformat(str(gmt_modify))

        return Worker(
            id=row["id"],
            type=WorkerType(row["type"]),
            identity=identity,
            responsibilities=json.loads(row["responsibilities"]),
            capabilities=capabilities,
            skills=skills,
            resources=resources,
            state=state,
            domains=json.loads(row["domains"]),
            lifecycle_state=WorkerLifecycleState(row["lifecycle_state"]),
            source_type=WorkerSourceType(row["source_type"]),
            source_ref=row["source_ref"],
            external_id=row["external_id"],
            active_profile_key=row["active_profile_key"],
            config=config,
            version=row["version"],
            created_at=created_at,
            updated_at=updated_at,
            created_by=row["created_by"],
            updated_by=row["updated_by"],
        )

    def _execute(self, sql: str, params: Optional[tuple] = None, commit: bool = False) -> Optional[dict]:
        """Simple helper for one-off execution; returns last row if any."""
        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(sql, params or ())
                if commit:
                    conn.commit()
                return cursor.fetchone() if cursor.with_rows else None
            finally:
                cursor.close()
        finally:
            conn.close()

    def create(self, worker: Worker) -> Optional[Worker]:
        if self.exists(worker.id):
            raise DuplicateWorkerException(worker.id)
        now = datetime.utcnow()
        worker.created_at = now
        worker.updated_at = now

        data = self._worker_to_dict(worker)
        columns = ", ".join(data.keys())
        placeholders = ", ".join(["%s"] * len(data))
        sql = f"INSERT INTO bcsfuse_workers ({columns}) VALUES ({placeholders})"

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(sql, list(data.values()))
                conn.commit()
                return worker.model_copy(deep=True)
            except Error as e:
                if e.errno == 1062:
                    raise DuplicateWorkerException(worker.id)
                raise
            finally:
                cursor.close()
        finally:
            conn.close()

    def get(self, worker_id: str) -> Optional[Worker]:
        return self.get_by_id(worker_id)

    def get_by_id(self, worker_id: str) -> Optional[Worker]:
        sql = "SELECT * FROM bcsfuse_workers WHERE id = %s"
        row = self._execute(sql, (worker_id,))
        if row is None:
            return None
        return self._row_to_worker(row)

    def exists(self, worker_id: str) -> bool:
        sql = "SELECT 1 FROM bcsfuse_workers WHERE id = %s LIMIT 1"
        row = self._execute(sql, (worker_id,))
        return row is not None

    def update(self, worker: Worker) -> Worker:
        existing = self.get_by_id(worker.id)
        if existing is None:
            raise WorkerNotFoundException(worker.id)
        if worker.version != existing.version:
            raise ValueError(f"Version conflict: expected {existing.version}, got {worker.version}")

        updated = worker.model_copy(deep=True)
        updated.updated_at = datetime.utcnow()
        updated.version = existing.version + 1

        data = self._worker_to_dict(updated)
        set_clause = ", ".join([f"{k} = %s" for k in data.keys() if k not in ("id", "gmt_create")])
        sql = f"UPDATE bcsfuse_workers SET {set_clause} WHERE id = %s"
        params = [v for k, v in data.items() if k not in ("id", "gmt_create")] + [updated.id]

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(sql, params)
                conn.commit()
                return updated.model_copy(deep=True)
            finally:
                cursor.close()
        finally:
            conn.close()

    def update_lifecycle_state(
        self,
        worker_id: str,
        lifecycle_state: WorkerLifecycleState,
        version: Optional[int] = None,
    ) -> Optional[Union[Worker, dict]]:
        worker = self.get_by_id(worker_id)
        if worker is None:
            return None
        if version is not None and worker.version != version:
            raise ValueError(f"Version conflict: expected {version}, got {worker.version}")
        worker.lifecycle_state = lifecycle_state
        return self.update(worker)

    def update_trust_level(self, worker_id: str, trust_level: TrustLevel) -> Optional[Worker]:
        worker = self.get_by_id(worker_id)
        if worker is None:
            raise WorkerNotFoundException(worker_id)
        new_state = WorkerState(
            availability=worker.state.availability,
            trust_level=trust_level,
            runtime_state=worker.state.runtime_state,
        )
        worker.state = new_state
        return self.update(worker)

    def delete(self, worker_id: str) -> bool:
        if not self.exists(worker_id):
            raise WorkerNotFoundException(worker_id)
        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("DELETE FROM bcsfuse_worker_profile_contents WHERE worker_id = %s", (worker_id,))
                cursor.execute("DELETE FROM bcsfuse_worker_runtime_states WHERE worker_id = %s", (worker_id,))
                cursor.execute("DELETE FROM bcsfuse_worker_profile_bindings WHERE worker_id = %s", (worker_id,))
                cursor.execute("DELETE FROM bcsfuse_worker_audit_logs WHERE worker_id = %s", (worker_id,))
                cursor.execute("DELETE FROM bcsfuse_workers WHERE id = %s", (worker_id,))
                conn.commit()
                return True
            finally:
                cursor.close()
        finally:
            conn.close()

    def count(
        self,
        lifecycle_states: Optional[list[WorkerLifecycleState]] = None,
    ) -> int:
        sql = "SELECT COUNT(*) AS cnt FROM bcsfuse_workers WHERE 1=1"
        params = []
        if lifecycle_states:
            placeholders = ", ".join(["%s"] * len(lifecycle_states))
            sql += f" AND lifecycle_state IN ({placeholders})"
            params.extend([s.value for s in lifecycle_states])
        row = self._execute(sql, tuple(params))
        return row["cnt"] if row else 0

    def list(
        self,
        lifecycle_states: Optional[list[WorkerLifecycleState]] = None,
        source_types: Optional[list[WorkerSourceType]] = None,
        domains: Optional[list[str]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> list[Worker]:
        sql = "SELECT * FROM bcsfuse_workers WHERE 1=1"
        params = []
        if lifecycle_states:
            placeholders = ", ".join(["%s"] * len(lifecycle_states))
            sql += f" AND lifecycle_state IN ({placeholders})"
            params.extend([s.value for s in lifecycle_states])
        if source_types:
            placeholders = ", ".join(["%s"] * len(source_types))
            sql += f" AND source_type IN ({placeholders})"
            params.extend([t.value for t in source_types])
        sql += " ORDER BY gmt_create DESC"
        if limit:
            sql += " LIMIT %s"
            params.append(limit)
        if offset:
            sql += " OFFSET %s"
            params.append(offset)

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(sql, tuple(params))
                rows = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            conn.close()

        workers = [self._row_to_worker(row) for row in rows]
        if domains:
            workers = [w for w in workers if any(d in w.domains for d in domains)]
        return workers

    def list_workers(self) -> list[dict]:
        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute("SELECT * FROM bcsfuse_workers ORDER BY gmt_create DESC")
                return cursor.fetchall()
            finally:
                cursor.close()
        finally:
            conn.close()

    def close(self) -> None:
        pass

    def find_by_capability(
        self,
        capability_name: str,
        lifecycle_states: Optional[list[WorkerLifecycleState]] = None,
        source_types: Optional[list[WorkerSourceType]] = None,
    ) -> list[Worker]:
        workers = self.list(
            lifecycle_states=lifecycle_states,
            source_types=source_types,
        )
        return [w for w in workers if any(c.name == capability_name for c in w.capabilities)]
