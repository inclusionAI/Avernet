"""
MySQL Fused Profile Store (Connection Pool Version)

MySQL implementation for production OSS deployments.

S29E Status: Full CRUD implementation complete.
S30A Status: Observability logging added for real storage validation.

R12-Pool-4 Fix:
- Replaced shared self._connection with MySQLConnectionPoolProvider
- Each method gets its own connection from pool
- Connection returned to pool after use (conn.close())
- Thread-safe by design (pool handles connection distribution)
- Transaction handling: restore autocommit before returning connection
- No more Fatal Python error from concurrent MySQL connector access
"""

import os
import json
import logging
import threading
import time
from datetime import datetime
from typing import Optional, TYPE_CHECKING

import mysql.connector
from mysql.connector import Error

from src.infra.repositories.fused_profile_repository import FusedProfileRepository
from src.domain.models.profile_fusion import FusedProfileRecord, ConversationTurn
from src.domain.exceptions import FusionNotFoundException
from src.infra.public.observability.storage_logging import (
    log_storage_event,
    log_storage_error,
    mask_host,
    mask_user,
    sanitize_key_fields,
)

if TYPE_CHECKING:
    from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

logger = logging.getLogger(__name__)


class MySQLProviderNotImplementedError(RuntimeError):
    """Raised when a MySQL provider method is called but not yet implemented."""
    pass


class MySQLFusedProfileStore(FusedProfileRepository):
    """MySQL Fused Profile Store for OSS (Connection Pool Version).

    Suitable for production deployments with MySQL database.

    Thread Safety:
        - Uses connection pool for thread-safe access
        - Each method borrows connection from pool
        - Connection returned to pool after use
        - No shared connection state
        - Transaction handling: restore autocommit before returning
    """

    MAX_RECENT_MESSAGES = 100  # Conversation history sliding window

    def __init__(
        self,
        connection_pool: Optional["MySQLConnectionPoolProvider"] = None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
    ):
        """Initialize MySQL store with connection pool.

        Args:
            connection_pool: MySQLConnectionPoolProvider instance (preferred).
            host: MySQL host (fallback if no pool provided).
            port: MySQL port (fallback if no pool provided).
            user: MySQL user (fallback if no pool provided).
            password: MySQL password (fallback if no pool provided).
            database: MySQL database (fallback if no pool provided).
        """
        if connection_pool is None:
            from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider

            self._pool = MySQLConnectionPoolProvider(
                host=host or os.getenv("MYSQL_HOST", "localhost"),
                port=port or int(os.getenv("MYSQL_PORT", "3306")),
                user=user or os.getenv("MYSQL_USER", "root"),
                password=password or os.getenv("MYSQL_PASSWORD", ""),
                database=database or os.getenv("MYSQL_DATABASE", "bcsfuse"),
            )
            logger.info(
                "[MySQLFusedProfileStore] Created internal connection pool (fallback mode)"
            )
        else:
            self._pool = connection_pool
            logger.info(
                "[MySQLFusedProfileStore] Using injected connection pool"
            )

        self._schema_initialized = False
        self._schema_lock = threading.Lock()

    def _ensure_schema(self, conn) -> None:
        """Ensure database schema exists."""
        if self._schema_initialized:
            return

        with self._schema_lock:
            if self._schema_initialized:
                return

            component = "mysql_fused_profile_store"
            table_name = "bcsfuse_fused_profiles"
            start_time = time.time()

            log_storage_event(
                logger,
                logging.DEBUG,
                "mysql_schema_init_start",
                component=component,
                operation="init_schema",
                validation_phase="schema_init",
                backend="mysql",
                target_resource=table_name,
            )

            cursor = conn.cursor()

            try:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS bcsfuse_fused_profiles (
                        fusion_id VARCHAR(128) PRIMARY KEY,
                        fusion_mode VARCHAR(32) NOT NULL,
                        group_id VARCHAR(128) NULL,
                        driver_bot_id VARCHAR(128) NULL,
                        question TEXT NULL,
                        participant_ids TEXT NOT NULL,
                        participant_profile_snapshot_json JSON NULL,
                        fuse_detail_json JSON NULL,
                        conversation_recent_json JSON NULL,
                        conversation_stats_json JSON NULL,
                        status VARCHAR(32) NOT NULL DEFAULT 'pending',
                        fuse_message TEXT NULL,
                        env VARCHAR(64) NULL,
                        created_by VARCHAR(128) NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_fusion_mode (fusion_mode),
                        INDEX idx_group_id (group_id),
                        INDEX idx_status (status),
                        INDEX idx_env (env),
                        INDEX idx_created_at (created_at)
                    )
                """)

                duration_ms = (time.time() - start_time) * 1000

                log_storage_event(
                    logger,
                    logging.INFO,
                    "mysql_schema_init_success",
                    component=component,
                    operation="init_schema",
                    validation_phase="schema_init",
                    backend="mysql",
                    target_resource=table_name,
                    duration_ms=duration_ms,
                )

                self._schema_initialized = True

            except Error as e:
                duration_ms = (time.time() - start_time) * 1000

                log_storage_error(
                    logger,
                    "mysql_schema_init_failure",
                    component=component,
                    operation="init_schema",
                    validation_phase="schema_init",
                    backend="mysql",
                    target_resource=table_name,
                    error=e,
                    duration_ms=duration_ms,
                )

                raise
            finally:
                cursor.close()

    def _normalize_conversation_stats(self, stats: Optional[dict]) -> dict:
        """Normalize conversation_stats to ensure all fields exist (compatible with legacy data)."""
        if stats is None:
            return {"turns": 0, "avg_response_ms": 0.0, "avg_question_token": 0.0, "avg_response_token": 0.0}
        # Fill missing fields
        return {
            "turns": stats.get("turns", 0),
            "avg_response_ms": stats.get("avg_response_ms", 0.0),
            "avg_question_token": stats.get("avg_question_token", 0.0),
            "avg_response_token": stats.get("avg_response_token", 0.0),
        }

    def _row_to_record(self, row: dict) -> FusedProfileRecord:
        """Convert database row to FusedProfileRecord model."""
        return FusedProfileRecord(
            fusion_id=row["fusion_id"],
            fusion_mode=row["fusion_mode"],
            group_id=row["group_id"],
            driver_bot_id=row["driver_bot_id"],
            question=row["question"],
            participant_ids=row["participant_ids"],
            participant_profile_snapshot=json.loads(row["participant_profile_snapshot_json"])
                if row["participant_profile_snapshot_json"] else None,
            fuse_detail=json.loads(row["fuse_detail_json"])
                if row["fuse_detail_json"] else None,
            conversation_recent=json.loads(row["conversation_recent_json"])
                if row["conversation_recent_json"] else [],
            conversation_stats=self._normalize_conversation_stats(
                json.loads(row["conversation_stats_json"])
                if row["conversation_stats_json"] else None
            ),
            status=row["status"],
            fuse_message=row["fuse_message"],
            created_by=row["created_by"],
            gmt_create=row["created_at"],
            gmt_modify=row["updated_at"],
            env=row["env"] or "prod",
        )

    def _record_to_dict(self, record: FusedProfileRecord) -> dict:
        """Convert FusedProfileRecord to database dictionary."""
        now = datetime.utcnow()
        return {
            "fusion_id": record.fusion_id,
            "fusion_mode": record.fusion_mode,
            "group_id": record.group_id,
            "driver_bot_id": record.driver_bot_id,
            "question": record.question,
            "participant_ids": record.participant_ids,
            "participant_profile_snapshot_json": json.dumps(record.participant_profile_snapshot)
                if record.participant_profile_snapshot else None,
            "fuse_detail_json": json.dumps(record.fuse_detail)
                if record.fuse_detail else None,
            "conversation_recent_json": json.dumps(record.conversation_recent)
                if record.conversation_recent else "[]",
            "conversation_stats_json": json.dumps(record.conversation_stats)
                if record.conversation_stats else '{"turns": 0, "avg_response_ms": 0, "avg_question_token": 0, "avg_response_token": 0}',
            "status": record.status,
            "fuse_message": record.fuse_message,
            "env": record.env,
            "created_by": record.created_by,
        }

    # ========================================================================
    # FusedProfileRepository Abstract Methods
    # ========================================================================

    def save(self, record: FusedProfileRecord) -> str:
        """Save fusion result (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor()

            try:
                data = self._record_to_dict(record)
                cursor.execute("""
                    INSERT INTO bcsfuse_fused_profiles (
                        fusion_id, fusion_mode, group_id, driver_bot_id, question,
                        participant_ids, participant_profile_snapshot_json, fuse_detail_json,
                        conversation_recent_json, conversation_stats_json, status, fuse_message,
                        env, created_by
                    ) VALUES (
                        %(fusion_id)s, %(fusion_mode)s, %(group_id)s, %(driver_bot_id)s, %(question)s,
                        %(participant_ids)s, %(participant_profile_snapshot_json)s, %(fuse_detail_json)s,
                        %(conversation_recent_json)s, %(conversation_stats_json)s, %(status)s, %(fuse_message)s,
                        %(env)s, %(created_by)s
                    )
                    ON DUPLICATE KEY UPDATE
                        fusion_mode = %(fusion_mode)s,
                        group_id = %(group_id)s,
                        driver_bot_id = %(driver_bot_id)s,
                        question = %(question)s,
                        participant_ids = %(participant_ids)s,
                        participant_profile_snapshot_json = %(participant_profile_snapshot_json)s,
                        fuse_detail_json = %(fuse_detail_json)s,
                        conversation_recent_json = %(conversation_recent_json)s,
                        conversation_stats_json = %(conversation_stats_json)s,
                        status = %(status)s,
                        fuse_message = %(fuse_message)s,
                        env = %(env)s,
                        created_by = %(created_by)s,
                        updated_at = CURRENT_TIMESTAMP
                """, data)

                logger.debug("[MySQL] Saved fused profile: %s", record.fusion_id)
                return record.fusion_id

            finally:
                cursor.close()

        finally:
            conn.close()

    def find_by_key(self, fusion_id: str) -> Optional[FusedProfileRecord]:
        """Find fusion result by fusion_id (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                cursor.execute(
                    "SELECT * FROM bcsfuse_fused_profiles WHERE fusion_id = %s",
                    (fusion_id,),
                )
                row = cursor.fetchone()

                if row is None:
                    return None

                return self._row_to_record(row)

            finally:
                cursor.close()

        finally:
            conn.close()

    def find_by_participant(
        self,
        participant_id: str,
        limit: int = 20,
        fusion_mode: Optional[str] = None,
    ) -> list[FusedProfileRecord]:
        """Find fusions by participant (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                sql = """
                    SELECT * FROM bcsfuse_fused_profiles
                    WHERE participant_ids LIKE %s
                """
                params = [f"%{participant_id}%"]

                if fusion_mode:
                    sql += " AND fusion_mode = %s"
                    params.append(fusion_mode)

                sql += " ORDER BY created_at DESC LIMIT %s"
                params.append(limit)

                cursor.execute(sql, params)
                rows = cursor.fetchall()

                return [self._row_to_record(row) for row in rows]

            finally:
                cursor.close()

        finally:
            conn.close()

    def find_by_group(
        self,
        group_id: str,
        limit: int = 20,
        fusion_mode: Optional[str] = None,
    ) -> list[FusedProfileRecord]:
        """Find fusions by group (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor(dictionary=True)

            try:
                sql = "SELECT * FROM bcsfuse_fused_profiles WHERE group_id = %s"
                params = [group_id]

                if fusion_mode:
                    sql += " AND fusion_mode = %s"
                    params.append(fusion_mode)

                sql += " ORDER BY created_at DESC LIMIT %s"
                params.append(limit)

                cursor.execute(sql, params)
                rows = cursor.fetchall()

                return [self._row_to_record(row) for row in rows]

            finally:
                cursor.close()

        finally:
            conn.close()

    def append_turn(
        self,
        fusion_id: str,
        turn: ConversationTurn,
    ) -> None:
        """Append conversation turn, update statistics (thread-safe with connection pool).

        Transaction handling:
        - Sets autocommit=False for transaction
        - Locks row with FOR UPDATE
        - Updates conversation and statistics
        - Commits or rolls back
        - Restores autocommit=True before returning connection to pool
        """
        conn = self._pool.get_connection()

        component = "mysql_fused_profile_store"
        table_name = "bcsfuse_fused_profiles"
        operation_name = "append_turn"
        start_time = time.time()

        log_storage_event(
            logger,
            logging.DEBUG,
            "mysql_transaction_start",
            component=component,
            operation=operation_name,
            validation_phase="operation",
            backend="mysql",
            target_resource=table_name,
            key_fields_masked=sanitize_key_fields({"fusion_id": fusion_id}),
        )

        try:
            self._ensure_schema(conn)

            # Start transaction
            conn.autocommit = False
            cursor = conn.cursor(dictionary=True)

            try:
                # Lock the row for update
                cursor.execute(
                    "SELECT conversation_recent_json, conversation_stats_json FROM bcsfuse_fused_profiles "
                    "WHERE fusion_id = %s FOR UPDATE",
                    (fusion_id,),
                )
                row = cursor.fetchone()

                if not row:
                    duration_ms = (time.time() - start_time) * 1000

                    log_storage_error(
                        logger,
                        "mysql_crud_failure",
                        component=component,
                        operation="read",
                        validation_phase="operation",
                        backend="mysql",
                        target_resource=table_name,
                        error=FusionNotFoundException(fusion_id),
                        duration_ms=duration_ms,
                        key_fields_masked=sanitize_key_fields({"fusion_id": fusion_id}),
                    )

                    raise FusionNotFoundException(fusion_id)

                # Parse existing conversation
                conversation = json.loads(row["conversation_recent_json"]) if row["conversation_recent_json"] else []
                stats = self._normalize_conversation_stats(
                    json.loads(row["conversation_stats_json"]) if row["conversation_stats_json"] else None
                )

                # Set turn index
                turn.turn_index = stats.get("turns", 0) + 1

                # Insert at beginning (reverse order, newest first)
                conversation.insert(0, turn.to_dict())

                # Sliding window: remove oldest if exceeds limit
                while len(conversation) > self.MAX_RECENT_MESSAGES:
                    conversation.pop()

                # Update statistics
                stats["turns"] = turn.turn_index
                if turn.answer_response_ms is not None:
                    old_avg = stats.get("avg_response_ms", 0)
                    old_turns = stats["turns"] - 1
                    if old_turns > 0:
                        new_avg = (old_avg * old_turns + turn.answer_response_ms) / stats["turns"]
                    else:
                        new_avg = float(turn.answer_response_ms)
                    stats["avg_response_ms"] = round(new_avg, 2)

                if turn.question_token is not None:
                    old_avg = stats.get("avg_question_token", 0)
                    old_turns = stats["turns"] - 1
                    if old_turns > 0:
                        new_avg = (old_avg * old_turns + turn.question_token) / stats["turns"]
                    else:
                        new_avg = float(turn.question_token)
                    stats["avg_question_token"] = round(new_avg, 2)

                if turn.response_token is not None:
                    old_avg = stats.get("avg_response_token", 0)
                    old_turns = stats["turns"] - 1
                    if old_turns > 0:
                        new_avg = (old_avg * old_turns + turn.response_token) / stats["turns"]
                    else:
                        new_avg = float(turn.response_token)
                    stats["avg_response_token"] = round(new_avg, 2)

                # Update database
                cursor.execute(
                    """
                    UPDATE bcsfuse_fused_profiles
                    SET conversation_recent_json = %s,
                        conversation_stats_json = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE fusion_id = %s
                    """,
                    (json.dumps(conversation), json.dumps(stats), fusion_id),
                )

                # Commit transaction
                conn.commit()
                duration_ms = (time.time() - start_time) * 1000

                log_storage_event(
                    logger,
                    logging.INFO,
                    "mysql_transaction_commit",
                    component=component,
                    operation=operation_name,
                    validation_phase="operation",
                    backend="mysql",
                    target_resource=table_name,
                    duration_ms=duration_ms,
                )

                logger.debug(
                    "[MySQL] Appended turn to fusion: %s, turn_index=%d",
                    fusion_id,
                    turn.turn_index,
                )

            except Exception as e:
                conn.rollback()
                duration_ms = (time.time() - start_time) * 1000

                log_storage_error(
                    logger,
                    "mysql_transaction_rollback",
                    component=component,
                    operation=operation_name,
                    validation_phase="operation",
                    backend="mysql",
                    target_resource=table_name,
                    error=e,
                    duration_ms=duration_ms,
                )

                raise

            finally:
                # CRITICAL: Restore autocommit before returning connection to pool
                conn.autocommit = True
                cursor.close()

        finally:
            conn.close()

    def get_conversation(
        self,
        fusion_id: str,
        offset: int = 0,
        limit: int = 100,
    ) -> Optional[dict]:
        """Get conversation (with pagination) (thread-safe with connection pool)."""
        record = self.find_by_key(fusion_id)
        if not record:
            return None

        conversation = record.conversation_recent or []
        stats = self._normalize_conversation_stats(record.conversation_stats)
        start_turn = stats.get("turns", 0) - len(conversation) + 1 if conversation else 0

        return {
            "fusion_id": record.fusion_id,
            "turns": conversation[offset:offset + limit],
            "total_turns": stats.get("turns", 0),
            "avg_response_ms": stats.get("avg_response_ms", 0),
            "avg_question_token": stats.get("avg_question_token", 0),
            "avg_response_token": stats.get("avg_response_token", 0),
            "stored_range": {
                "start": start_turn,
                "end": stats.get("turns", 0),
                "count": len(conversation),
            },
        }

    def update_status(
        self,
        fusion_id: str,
        status: str,
        fuse_message: Optional[str] = None,
    ) -> None:
        """Update execution status (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor()

            try:
                if fuse_message is not None:
                    cursor.execute(
                        """
                        UPDATE bcsfuse_fused_profiles
                        SET status = %s, fuse_message = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE fusion_id = %s
                        """,
                        (status, fuse_message, fusion_id),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE bcsfuse_fused_profiles
                        SET status = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE fusion_id = %s
                        """,
                        (status, fusion_id),
                    )

                logger.debug("[MySQL] Updated fusion status: %s -> %s", fusion_id, status)

            finally:
                cursor.close()

        finally:
            conn.close()

    def exists(self, fusion_id: str) -> bool:
        """Check if record exists (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor()

            try:
                cursor.execute(
                    "SELECT 1 FROM bcsfuse_fused_profiles WHERE fusion_id = %s LIMIT 1",
                    (fusion_id,),
                )
                return cursor.fetchone() is not None

            finally:
                cursor.close()

        finally:
            conn.close()

    def update(self, record: FusedProfileRecord) -> str:
        """Update existing fusion result (thread-safe with connection pool)."""
        conn = self._pool.get_connection()
        try:
            self._ensure_schema(conn)

            cursor = conn.cursor()

            try:
                data = self._record_to_dict(record)
                cursor.execute(
                    """
                    UPDATE bcsfuse_fused_profiles
                    SET fusion_mode = %(fusion_mode)s,
                        group_id = %(group_id)s,
                        driver_bot_id = %(driver_bot_id)s,
                        question = %(question)s,
                        participant_ids = %(participant_ids)s,
                        participant_profile_snapshot_json = %(participant_profile_snapshot_json)s,
                        fuse_detail_json = %(fuse_detail_json)s,
                        conversation_recent_json = %(conversation_recent_json)s,
                        conversation_stats_json = %(conversation_stats_json)s,
                        status = %(status)s,
                        fuse_message = %(fuse_message)s,
                        env = %(env)s,
                        created_by = %(created_by)s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE fusion_id = %(fusion_id)s
                    """,
                    data,
                )

                if cursor.rowcount == 0:
                    raise FusionNotFoundException(record.fusion_id)

                logger.debug("[MySQL] Updated fused profile: %s", record.fusion_id)
                return record.fusion_id

            finally:
                cursor.close()

        finally:
            conn.close()

    def close(self) -> None:
        """Close connection pool (for application shutdown)."""
        if self._pool:
            self._pool.close()
            logger.info("[MySQLFusedProfileStore] Connection pool closed")


__all__ = ["MySQLFusedProfileStore", "MySQLProviderNotImplementedError"]
