"""Unified Template repository (prod ZDAS + local SQLite).

One ORM implementation behind ``TemplateRepository``. The only
per-environment difference is the injected :class:`DatabasePlugin`:
``orm_session()`` yields a SQLAlchemy ``Session`` in both runtimes,
so this single body runs unchanged on OceanBase (prod) and SQLite
(local), collapsing the previous raw-SQL/ORM twins.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.bot_management.repository.models import TemplateModel

logger = get_logger()


class TemplateRepository:
    """Unified ORM-backed TemplateRepository.

    Uses DatabasePlugin for database session management.
    Compatible with both prod (OceanBase/ZDAS) and local (SQLite) modes.
    """

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def insert(self, template_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new template record.

        Args:
            template_data: Dictionary with template fields (bot_id, ext, etc.)

        Returns:
            Created template record as dictionary
        """
        with self._db.orm_session() as db:
            data = dict(template_data)
            if data.get("ext") is not None:
                data["ext"] = json.dumps(data["ext"])

            template = TemplateModel(**data)
            db.add(template)
            db.flush()
            return template.to_dict()

    def get_by_bot_id(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """Get template by bot_id.

        Args:
            bot_id: Bot ID

        Returns:
            Template record as dictionary, or None if not found
        """
        with self._db.orm_session() as db:
            template = db.query(TemplateModel).filter(
                TemplateModel.bot_id == bot_id
            ).first()
            return template.to_dict() if template else None

    def update_by_bot_id(self, bot_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update template by bot_id.

        Args:
            bot_id: Bot ID
            update_data: Dictionary with fields to update

        Returns:
            Updated template record as dictionary, or None if not found
        """
        with self._db.orm_session() as db:
            template = db.query(TemplateModel).filter(
                TemplateModel.bot_id == bot_id
            ).first()
            if not template:
                return None

            data = dict(update_data)
            if "ext" in data and data["ext"] is not None:
                data["ext"] = json.dumps(data["ext"])

            for key, value in data.items():
                if hasattr(template, key):
                    setattr(template, key, value)

            db.flush()
            return template.to_dict()

    def delete_by_bot_id(self, bot_id: str) -> bool:
        """Delete template by bot_id.

        Args:
            bot_id: Bot ID

        Returns:
            True if deleted, False if not found
        """
        with self._db.orm_session() as db:
            template = db.query(TemplateModel).filter(
                TemplateModel.bot_id == bot_id
            ).first()
            if not template:
                return False
            db.delete(template)
            db.flush()
            return True

    def exists_by_bot_id(self, bot_id: str) -> bool:
        """Check if a template exists for the given bot_id.

        Args:
            bot_id: Bot ID

        Returns:
            True if template exists, False otherwise
        """
        with self._db.orm_session() as db:
            count = db.query(TemplateModel).filter(
                TemplateModel.bot_id == bot_id
            ).count()
            return count > 0

    def list_by_bot_ids(self, bot_ids: List[str]) -> List[Dict[str, Any]]:
        """List templates by bot IDs.

        Used by bot list APIs to enrich list items with template_config without
        issuing one template query per bot.
        """
        if not bot_ids:
            return []

        # Preserve only meaningful, unique IDs while keeping the IN clause small.
        unique_bot_ids = list(dict.fromkeys(str(bot_id) for bot_id in bot_ids if bot_id))
        if not unique_bot_ids:
            return []

        with self._db.orm_session() as db:
            templates = db.query(TemplateModel).filter(
                TemplateModel.bot_id.in_(unique_bot_ids)
            ).all()
            return [t.to_dict() for t in templates]

    def list_by_architect_bot_id(self, architect_bot_id: str) -> List[Dict[str, Any]]:
        """List templates whose ext JSON contains the given architect_bot_id.

        Uses JSON_EXTRACT to query the ext field.
        Works on both MySQL (OceanBase) and SQLite.

        Args:
            architect_bot_id: The architect bot's bot_id

        Returns:
            List of template records
        """
        with self._db.orm_session() as db:
            templates = db.query(TemplateModel).filter(
                TemplateModel.ext.isnot(None),
                func.json_extract(TemplateModel.ext, '$.architect_bot_id') == architect_bot_id,
            ).all()
            return [t.to_dict() for t in templates]
