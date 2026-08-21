"""
MySQL Worker Profile Content Store (production-schema aligned)

Aligns with the internal production DB schema:
    bcsfuse_worker_profile_contents (
        id VARCHAR(128) PRIMARY KEY,
        worker_id VARCHAR(128) NOT NULL,
        profile_id VARCHAR(128) NOT NULL DEFAULT 'default',
        display_name VARCHAR(255),
        soul_md MEDIUMTEXT,
        agents_md MEDIUMTEXT,
        tools_md MEDIUMTEXT,
        boot_md MEDIUMTEXT,
        heartbeat_md MEDIUMTEXT,
        contents JSON,
        skill_sets JSON,
        metadata JSON,
        content_type VARCHAR(32) NOT NULL DEFAULT 'api',
        is_active TINYINT NOT NULL DEFAULT 0,
        version INT NOT NULL DEFAULT 1,
        gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_bcsfuse_worker_profile_contents (worker_id, profile_id),
        INDEX idx_bcsfuse_worker_profile_contents_worker_id (worker_id),
        INDEX idx_bcsfuse_worker_profile_contents_active (worker_id, is_active)
    )

Provides both WorkerProfileContent object API and dict-compatible API used by
OSS routes.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

from src.domain.models.worker_profile_content import (
    ProfileContentType,
    SkillSet,
    WorkerProfileContent,
    WorkerProfileContentList,
)

logger = logging.getLogger(__name__)


class MySQLWorkerProfileContentStore:
    """MySQL Worker Profile Content Store for production OSS deployments."""

    def __init__(
        self,
        connection_pool: Optional["MySQLConnectionPoolProvider"] = None,
        **kwargs,
    ):
        """Initialize MySQL store with connection pool.

        Args:
            connection_pool: MySQLConnectionPoolProvider instance (preferred).
            **kwargs: Fallback MySQL connection parameters.
        """
        if connection_pool is None:
            from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

            self._pool = MySQLConnectionPoolProvider(
                host=kwargs.get("host") or os.getenv("MYSQL_HOST", "localhost"),
                port=kwargs.get("port") or int(os.getenv("MYSQL_PORT", "3306")),
                user=kwargs.get("user") or os.getenv("MYSQL_USER", ""),
                password=kwargs.get("password") or os.getenv("MYSQL_PASSWORD", ""),
                database=kwargs.get("database") or os.getenv("MYSQL_DATABASE", "bcsfuse"),
            )
            logger.info(
                "[MySQLWorkerProfileContentStore] Created internal connection pool (fallback mode)"
            )
        else:
            self._pool = connection_pool
            logger.info(
                "[MySQLWorkerProfileContentStore] Using injected connection pool"
            )

        self._schema_initialized = False
        self._schema_lock = threading.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Ensure database schema exists."""
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
                        CREATE TABLE IF NOT EXISTS bcsfuse_worker_profile_contents (
                            id VARCHAR(128) PRIMARY KEY,
                            worker_id VARCHAR(128) NOT NULL,
                            profile_id VARCHAR(128) NOT NULL DEFAULT 'default',
                            display_name VARCHAR(255),
                            soul_md MEDIUMTEXT,
                            agents_md MEDIUMTEXT,
                            tools_md MEDIUMTEXT,
                            boot_md MEDIUMTEXT,
                            heartbeat_md MEDIUMTEXT,
                            contents JSON,
                            skill_sets JSON,
                            metadata JSON,
                            content_type VARCHAR(32) NOT NULL DEFAULT 'api',
                            is_active TINYINT NOT NULL DEFAULT 0,
                            version INT NOT NULL DEFAULT 1,
                            gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            gmt_modify TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                            UNIQUE KEY uk_bcsfuse_worker_profile_contents (worker_id, profile_id),
                            INDEX idx_bcsfuse_worker_profile_contents_worker_id (worker_id),
                            INDEX idx_bcsfuse_worker_profile_contents_active (worker_id, is_active)
                        )
                    """)
                finally:
                    cursor.close()
                conn.commit()
                self._schema_initialized = True
                logger.info(
                    "[MySQLWorkerProfileContentStore] Schema initialized successfully"
                )
            finally:
                conn.close()

    @staticmethod
    def _generate_id(worker_id: str, profile_id: str) -> str:
        """Generate deterministic record ID."""
        return f"profile_{worker_id}_{profile_id}"

    @staticmethod
    def _serialize_json(value) -> Optional[str]:
        """Serialize value to JSON string."""
        if value is None:
            return None
        return json.dumps(value)

    @staticmethod
    def _parse_json(value) -> dict:
        """Parse JSON string to dict."""
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _parse_skill_sets(value) -> list[SkillSet]:
        """Parse skill_sets JSON to list of SkillSet."""
        data = MySQLWorkerProfileContentStore._parse_json(value)
        if not isinstance(data, list):
            return []
        result = []
        for item in data:
            try:
                if isinstance(item, SkillSet):
                    result.append(item)
                elif isinstance(item, dict):
                    result.append(SkillSet(**item))
            except Exception:
                continue
        return result

    def _row_to_content(self, row: dict) -> WorkerProfileContent:
        """Convert database row to WorkerProfileContent."""
        return WorkerProfileContent(
            worker_id=row["worker_id"],
            profile_id=row["profile_id"],
            display_name=row["display_name"],
            soul_md=row["soul_md"],
            agents_md=row["agents_md"],
            tools_md=row["tools_md"],
            boot_md=row["boot_md"],
            heartbeat_md=row["heartbeat_md"],
            contents=self._parse_json(row["contents"]),
            skill_sets=self._parse_skill_sets(row["skill_sets"]),
            metadata=self._parse_json(row["metadata"]),
            content_type=ProfileContentType(row["content_type"]),
            is_active=bool(row["is_active"]),
            version=row["version"],
            created_at=row["gmt_create"],
            updated_at=row["gmt_modify"],
        )

    def _content_to_dict(self, content: WorkerProfileContent) -> dict:
        """Convert WorkerProfileContent to dict for database insertion."""
        return {
            "id": self._generate_id(content.worker_id, content.profile_id or "default"),
            "worker_id": content.worker_id,
            "profile_id": content.profile_id or "default",
            "display_name": content.display_name,
            "soul_md": content.soul_md,
            "agents_md": content.agents_md,
            "tools_md": content.tools_md,
            "boot_md": content.boot_md,
            "heartbeat_md": content.heartbeat_md,
            "contents": self._serialize_json(content.contents),
            "skill_sets": self._serialize_json([s.model_dump() for s in content.skill_sets]),
            "metadata": self._serialize_json(content.metadata),
            "content_type": content.content_type.value,
            "is_active": 1 if content.is_active else 0,
            "version": content.version,
        }

    def save(self, content: WorkerProfileContent) -> WorkerProfileContent:
        """Save profile content (object API)."""
        now = datetime.utcnow()
        content_id = self._generate_id(content.worker_id, content.profile_id or "default")
        profile_id = content.profile_id or "default"

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                # Check for existing row to determine version
                cursor.execute(
                    "SELECT version FROM bcsfuse_worker_profile_contents WHERE worker_id = %s AND profile_id = %s",
                    (content.worker_id, profile_id),
                )
                existing = cursor.fetchone()

                data = self._content_to_dict(content)

                if existing:
                    new_version = existing["version"] + 1
                    cursor.execute("""
                        UPDATE bcsfuse_worker_profile_contents SET
                            display_name = %s,
                            soul_md = %s,
                            agents_md = %s,
                            tools_md = %s,
                            boot_md = %s,
                            heartbeat_md = %s,
                            contents = %s,
                            skill_sets = %s,
                            metadata = %s,
                            content_type = %s,
                            version = %s,
                            gmt_modify = %s
                        WHERE worker_id = %s AND profile_id = %s
                    """, (
                        data["display_name"],
                        data["soul_md"],
                        data["agents_md"],
                        data["tools_md"],
                        data["boot_md"],
                        data["heartbeat_md"],
                        data["contents"],
                        data["skill_sets"],
                        data["metadata"],
                        data["content_type"],
                        new_version,
                        now,
                        content.worker_id,
                        profile_id,
                    ))
                    content.version = new_version
                else:
                    cursor.execute("""
                        INSERT INTO bcsfuse_worker_profile_contents (
                            id, worker_id, profile_id, display_name,
                            soul_md, agents_md, tools_md, boot_md, heartbeat_md,
                            contents, skill_sets, metadata,
                            content_type, is_active, version, gmt_create, gmt_modify
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        content_id,
                        data["worker_id"],
                        data["profile_id"],
                        data["display_name"],
                        data["soul_md"],
                        data["agents_md"],
                        data["tools_md"],
                        data["boot_md"],
                        data["heartbeat_md"],
                        data["contents"],
                        data["skill_sets"],
                        data["metadata"],
                        data["content_type"],
                        data["is_active"],
                        data["version"],
                        now,
                        now,
                    ))
                    content.version = data["version"]

                conn.commit()
                content.created_at = content.created_at or now
                content.updated_at = now
                return content
            finally:
                cursor.close()
        finally:
            conn.close()

    def get(self, worker_id: str, profile_id: str) -> Optional[WorkerProfileContent]:
        """Get profile content (object API)."""
        profile_id = profile_id or "default"

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT * FROM bcsfuse_worker_profile_contents WHERE worker_id = %s AND profile_id = %s",
                    (worker_id, profile_id),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return self._row_to_content(row)
            finally:
                cursor.close()
        finally:
            conn.close()

    def list_by_worker(self, worker_id: str) -> WorkerProfileContentList:
        """List all profiles for a worker (object API)."""
        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT * FROM bcsfuse_worker_profile_contents WHERE worker_id = %s ORDER BY is_active DESC, gmt_modify DESC",
                    (worker_id,),
                )
                rows = cursor.fetchall()
                items = [self._row_to_content(row) for row in rows]
                active_profile_id = None
                for item in items:
                    if item.is_active:
                        active_profile_id = item.profile_id
                        break
                return WorkerProfileContentList(
                    items=items,
                    total=len(items),
                    active_profile_id=active_profile_id,
                )
            finally:
                cursor.close()
        finally:
            conn.close()

    def delete(self, worker_id: str, profile_id: str) -> bool:
        """Delete profile content (object API)."""
        profile_id = profile_id or "default"

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "DELETE FROM bcsfuse_worker_profile_contents WHERE worker_id = %s AND profile_id = %s",
                    (worker_id, profile_id),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                cursor.close()
        finally:
            conn.close()

    def activate(self, worker_id: str, profile_id: str) -> Optional[WorkerProfileContent]:
        """Set active profile (object API)."""
        profile_id = profile_id or "default"

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()
            try:
                now = datetime.utcnow()
                conn.autocommit = False
                try:
                    cursor.execute(
                        "UPDATE bcsfuse_worker_profile_contents SET is_active = 0 WHERE worker_id = %s",
                        (worker_id,),
                    )
                    cursor.execute(
                        "UPDATE bcsfuse_worker_profile_contents SET is_active = 1, gmt_modify = %s WHERE worker_id = %s AND profile_id = %s",
                        (now, worker_id, profile_id),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.autocommit = True

                if cursor.rowcount == 0:
                    return None
                return self.get(worker_id, profile_id)
            finally:
                cursor.close()
        finally:
            conn.close()

    def get_active(self, worker_id: str) -> Optional[WorkerProfileContent]:
        """Get active profile for worker (object API)."""
        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT * FROM bcsfuse_worker_profile_contents WHERE worker_id = %s AND is_active = 1",
                    (worker_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return self._row_to_content(row)
            finally:
                cursor.close()
        finally:
            conn.close()

    def exists(self, worker_id: str, profile_id: str) -> bool:
        """Check if profile exists (object API)."""
        profile_id = profile_id or "default"

        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "SELECT 1 FROM bcsfuse_worker_profile_contents WHERE worker_id = %s AND profile_id = %s",
                    (worker_id, profile_id),
                )
                return cursor.fetchone() is not None
            finally:
                cursor.close()
        finally:
            conn.close()

    def count(self, worker_id: Optional[str] = None) -> int:
        """Count profiles (object API)."""
        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor()
            try:
                if worker_id:
                    cursor.execute(
                        "SELECT COUNT(*) FROM bcsfuse_worker_profile_contents WHERE worker_id = %s",
                        (worker_id,),
                    )
                else:
                    cursor.execute("SELECT COUNT(*) FROM bcsfuse_worker_profile_contents")
                return cursor.fetchone()[0]
            finally:
                cursor.close()
        finally:
            conn.close()

    def get_all_active(self) -> list[WorkerProfileContent]:
        """Get all active profiles (object API)."""
        conn = self._pool.get_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(
                    "SELECT * FROM bcsfuse_worker_profile_contents WHERE is_active = 1"
                )
                rows = cursor.fetchall()
                return [self._row_to_content(row) for row in rows]
            finally:
                cursor.close()
        finally:
            conn.close()

    # =========================================================
    # Dict-compatible API used by OSS routes
    # =========================================================

    def upsert_profile(self, worker_id: str, profile_id: str, content: dict) -> bool:
        """Upsert profile content from dict (OSS route compatibility)."""
        profile_id = profile_id or "default"

        if isinstance(content, dict):
            profile_data = dict(content)
            # Map 'content' to 'soul_md' if provided
            if "content" in profile_data and "soul_md" not in profile_data:
                profile_data["soul_md"] = profile_data.pop("content")

            # Ensure worker_id/profile_id are correct
            profile_data["worker_id"] = worker_id
            profile_data["profile_id"] = profile_id

            # Convert skill_sets dicts to SkillSet objects
            skill_sets_data = profile_data.get("skill_sets") or []
            skill_sets = []
            for s in skill_sets_data:
                if isinstance(s, SkillSet):
                    skill_sets.append(s)
                elif isinstance(s, dict):
                    skill_sets.append(SkillSet(**s))
            profile_data["skill_sets"] = skill_sets

            # Convert content_type string to enum if needed
            content_type = profile_data.get("content_type", "api")
            if isinstance(content_type, str):
                profile_data["content_type"] = ProfileContentType(content_type)

            # Remove timestamps so save() can set them reliably
            profile_data.pop("created_at", None)
            profile_data.pop("updated_at", None)

            content_obj = WorkerProfileContent(**profile_data)
        elif isinstance(content, WorkerProfileContent):
            content_obj = content
        else:
            raise ValueError(f"content must be dict or WorkerProfileContent, got {type(content)}")

        self.save(content_obj)
        return True

    def create_profile(self, worker_id: str, profile_id: str, content: dict) -> bool:
        """Create profile (alias for upsert_profile)."""
        return self.upsert_profile(worker_id, profile_id, content)

    def get_profile(self, worker_id: str, profile_id: str) -> Optional[dict]:
        """Get profile content as dict (OSS route compatibility)."""
        profile = self.get(worker_id, profile_id)
        if profile is None:
            return None
        return self._content_to_response_dict(profile)

    def list_profiles(self, worker_id: str) -> List[dict]:
        """List all profiles for worker as dicts (OSS route compatibility)."""
        result = self.list_by_worker(worker_id)
        return [self._content_to_response_dict(item) for item in result.items]

    def delete_profile(self, worker_id: str, profile_id: str) -> bool:
        """Delete profile (OSS route compatibility)."""
        return self.delete(worker_id, profile_id)

    def activate_profile(self, worker_id: str, profile_id: str) -> bool:
        """Activate profile (OSS route compatibility)."""
        result = self.activate(worker_id, profile_id)
        return result is not None

    def get_active_profiles(self, worker_ids: Optional[List[str]] = None) -> List[dict]:
        """Get all active profiles as dicts (OSS route compatibility)."""
        all_active = self.get_all_active()
        if worker_ids:
            all_active = [p for p in all_active if p.worker_id in worker_ids]
        return [self._content_to_response_dict(p) for p in all_active]

    def get_active_profile_for_worker(self, worker_id: str) -> Optional[dict]:
        """Get active profile for worker as dict (OSS route compatibility)."""
        profile = self.get_active(worker_id)
        if profile is None:
            return None
        return self._content_to_response_dict(profile)

    def _content_to_response_dict(self, profile: WorkerProfileContent) -> dict:
        """Convert WorkerProfileContent to dict response."""
        return {
            "worker_id": profile.worker_id,
            "profile_id": profile.profile_id,
            "display_name": profile.display_name,
            "soul_md": profile.soul_md,
            "agents_md": profile.agents_md,
            "tools_md": profile.tools_md,
            "boot_md": profile.boot_md,
            "heartbeat_md": profile.heartbeat_md,
            "contents": profile.contents,
            "skill_sets": profile.skill_sets,
            "metadata": profile.metadata,
            "content_type": profile.content_type.value if profile.content_type else "api",
            "is_active": profile.is_active,
            "version": profile.version,
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        }

    def close(self) -> None:
        """Close connection pool (for application shutdown)."""
        if self._pool:
            self._pool.close()
            logger.info("[MySQLWorkerProfileContentStore] Connection pool closed")


__all__ = ["MySQLWorkerProfileContentStore"]
