"""Unified Bot Collaborator repository (prod ZDAS + local SQLite).

One ORM implementation behind the ``CollaboratorRepositoryProtocol`` Protocol.
The only per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes, so this
single body runs unchanged on OceanBase (prod) and SQLite (local).

Behavior:
- Uses ORM for all database operations
- gmt_create/gmt_modified are DB server defaults (func.now())
- Unique constraint on (bot_pk, user_id, env) to prevent duplicate collaborators
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRecord,
    BotCollaboratorModel,
    CollaboratorRole,
)
from agentclaw.community.core.repository.protocols.bot import CollaboratorRepositoryProtocol
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


class CollaboratorRepository(CollaboratorRepositoryProtocol):
    """Unified ``CollaboratorRepositoryProtocol`` implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        """Initialize with DatabasePlugin instance.

        Args:
            db: DatabasePlugin providing orm_session() context manager.
        """
        self._db = db
        self._Collaborator = BotCollaboratorModel

    # ========================================================================
    # Insert Operations
    # ========================================================================

    def insert(self, data: Dict[str, Any]) -> CollaboratorRecord:
        """Create a new collaborator record."""
        with self._db.orm_session() as db:
            row = self._Collaborator(
                bot_pk=data["bot_pk"],
                bot_id=data["bot_id"],
                owner_id=data["owner_id"],
                user_id=data["user_id"],
                user_name=data.get("user_name"),
                role=data.get("role", CollaboratorRole.ADMIN),
                operator_id=data["operator_id"],
                env=data.get("env", get_current_env()),
            )
            db.add(row)
            db.flush()
            db.refresh(row)
            logger.info(
                "[insert] inserted collaborator id=%s, bot_pk=%s, user_id=%s",
                row.id, row.bot_pk, row.user_id
            )
            return row.to_record()

    # ========================================================================
    # Query Operations
    # ========================================================================

    def get_by_id(self, collaborator_id: int) -> Optional[CollaboratorRecord]:
        """Get collaborator record by id."""
        with self._db.orm_session() as db:
            row = (
                db.query(self._Collaborator)
                .filter(self._Collaborator.id == collaborator_id)
                .first()
            )
            return row.to_record() if row else None

    def get_by_bot_and_user(
        self,
        bot_pk: int,
        user_id: str,
        env: str,
    ) -> Optional[CollaboratorRecord]:
        """Get collaborator record by bot_pk and user_id."""
        with self._db.orm_session() as db:
            row = (
                db.query(self._Collaborator)
                .filter(
                    self._Collaborator.bot_pk == bot_pk,
                    self._Collaborator.user_id == user_id,
                    self._Collaborator.env == env,
                )
                .first()
            )
            return row.to_record() if row else None

    def list_by_bot(
        self,
        bot_id: str,
        owner_id: str,
        env: str,
        role: Optional[str] = None,
    ) -> List[CollaboratorRecord]:
        """List collaborators for a bot."""
        with self._db.orm_session() as db:
            query = db.query(self._Collaborator).filter(
                self._Collaborator.bot_id == bot_id,
                self._Collaborator.owner_id == owner_id,
                self._Collaborator.env == env,
            )
            if role:
                query = query.filter(self._Collaborator.role == role)
            rows = query.order_by(self._Collaborator.gmt_create.asc()).all()
            return [r.to_record() for r in rows]

    def list_by_user(
        self,
        user_id: str,
        env: str,
    ) -> List[CollaboratorRecord]:
        """List all bots a user collaborates on."""
        with self._db.orm_session() as db:
            rows = (
                db.query(self._Collaborator)
                .filter(
                    self._Collaborator.user_id == user_id,
                    self._Collaborator.env == env,
                )
                .order_by(self._Collaborator.gmt_create.desc())
                .all()
            )
            return [r.to_record() for r in rows]

    def get_user_role(
        self,
        bot_pk: int,
        user_id: str,
        env: str,
    ) -> Optional[str]:
        """Get user's role for a bot."""
        with self._db.orm_session() as db:
            row = (
                db.query(self._Collaborator.role)
                .filter(
                    self._Collaborator.bot_pk == bot_pk,
                    self._Collaborator.user_id == user_id,
                    self._Collaborator.env == env,
                )
                .first()
            )
            return row[0] if row else None

    # ========================================================================
    # Update Operations
    # ========================================================================

    def update(
        self,
        collaborator_id: int,
        data: Dict[str, Any],
    ) -> Optional[CollaboratorRecord]:
        """Update collaborator record."""
        # Build update dict with only provided fields
        update_data = {}
        if "user_id" in data:
            update_data[self._Collaborator.user_id] = data["user_id"]
        if "user_name" in data:
            update_data[self._Collaborator.user_name] = data["user_name"]
        if "role" in data:
            update_data[self._Collaborator.role] = data["role"]
        if "operator_id" in data:
            update_data[self._Collaborator.operator_id] = data["operator_id"]

        if not update_data:
            return self.get_by_id(collaborator_id)

        # Always update gmt_modified
        update_data[self._Collaborator.gmt_modified] = func.now()

        with self._db.orm_session() as db:
            result = db.query(self._Collaborator).filter(
                self._Collaborator.id == collaborator_id,
            ).update(update_data, synchronize_session=False)

            if result == 0:
                return None

        logger.info("[update] updated collaborator id=%s", collaborator_id)
        return self.get_by_id(collaborator_id)

    # ========================================================================
    # Delete Operations
    # ========================================================================

    def delete(self, collaborator_id: int) -> bool:
        """Delete collaborator record by id."""
        with self._db.orm_session() as db:
            result = db.query(self._Collaborator).filter(
                self._Collaborator.id == collaborator_id,
            ).delete(synchronize_session=False)

            if result > 0:
                logger.info("[delete] deleted collaborator id=%s", collaborator_id)
            return result > 0

    def delete_by_bot(self, bot_pk: int, env: str) -> int:
        """Delete all collaborators for a bot."""
        with self._db.orm_session() as db:
            result = db.query(self._Collaborator).filter(
                self._Collaborator.bot_pk == bot_pk,
                self._Collaborator.env == env,
            ).delete(synchronize_session=False)

            logger.info("[delete_by_bot] deleted %s collaborators for bot_pk=%s", result, bot_pk)
            return result
