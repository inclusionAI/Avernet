"""Unified UserMCPConfig repository (prod OceanBase + local SQLite).

One ORM implementation behind the ``UserMCPConfigRepository`` Protocol.
The only per-environment difference is the injected
:class:`DatabasePlugin`: ``orm_session()`` yields a SQLAlchemy ``Session``
in both runtimes, so this single body runs unchanged on OceanBase (prod)
and SQLite (local), collapsing the previous raw-SQL/ORM twins so CI
exercises the prod path too.

Behavior matches the prior prod twin: strict ``env = current_env``
filtering (no ``env IS NULL`` fallback — that was a local-twin-only
addition and is dropped here) and ``list_by_user`` ordered by
``gmt_created DESC``. It additionally handles ``custom_headers`` (a
benign superset the raw twin ignored). JSON columns
(``extra_config``/``custom_headers``) are (de)serialized exactly as
before via ``UserMCPConfig.to_dict()``. The pilot's Pre probe already
confirmed this model's columns match live ``ac_user_mcp_config``; the
formal DDL parity record is a Pre acceptance gate (round-2 spec).
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.repository.protocols.bot import UserMCPConfigRepository


def _normalise_extra_config(value: Any) -> str:
    """Match the prior prod twin: dict -> JSON, None/absent -> "{}"."""
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return "{}"
    return value


class UserMCPConfigRepository(
    UserMCPConfigRepository,
):
    """Unified ``UserMCPConfigRepository`` Protocol implementation."""

    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        from agentclaw.community.core.models.mcp import UserMCPConfig

        self._db = db
        self.UserMCPConfig = UserMCPConfig

    def _get_session(self):
        return self._db.orm_session()

    def get_by_id(self, config_id: str) -> Optional[dict[str, Any]]:
        with self._get_session() as db:
            config = (
                db.query(self.UserMCPConfig)
                .filter(self.UserMCPConfig.id == int(config_id))
                .first()
            )
            return config.to_dict() if config else None

    def get_by_user_and_server_code(
        self, user_id: str, server_code: str
    ) -> Optional[dict[str, Any]]:
        from agentclaw.community.utils.env_utils import get_current_env

        with self._get_session() as db:
            config = (
                db.query(self.UserMCPConfig)
                .filter(
                    self.UserMCPConfig.user_id == user_id,
                    self.UserMCPConfig.server_code == server_code,
                    self.UserMCPConfig.env == get_current_env(),
                )
                .first()
            )
            return config.to_dict() if config else None

    def list_by_user(self, user_id: str) -> List[dict[str, Any]]:
        from agentclaw.community.utils.env_utils import get_current_env

        with self._get_session() as db:
            configs = (
                db.query(self.UserMCPConfig)
                .filter(
                    self.UserMCPConfig.user_id == user_id,
                    self.UserMCPConfig.env == get_current_env(),
                )
                .order_by(self.UserMCPConfig.gmt_created.desc())
                .all()
            )
            return [c.to_dict() for c in configs]

    def create(self, config_data: dict[str, Any]) -> dict[str, Any]:
        # Prod-twin column scope: only these 5 columns are inserted
        # (custom_headers is NOT — matches the prior prod twin).
        # gmt_created/gmt_modified are DB server defaults (func.now()).
        with self._get_session() as db:
            config = self.UserMCPConfig(
                user_id=config_data.get("user_id"),
                server_code=config_data.get("server_code"),
                api_key=config_data.get("api_key"),
                extra_config=_normalise_extra_config(
                    config_data.get("extra_config")
                ),
                env=config_data.get("env"),
            )
            db.add(config)
            db.flush()
            db.refresh(config)
            return config.to_dict()

    def update(
        self, config_id: str, config_data: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        # Prod-twin semantics: a single atomic UPDATE that only ever
        # writes api_key / extra_config (+ gmt_modified). Other keys are
        # ignored (server_code/user_id/env/custom_headers were never
        # updatable in prod). 0 rows -> not found -> None; else one
        # SELECT to return (matches prod's trailing get_by_id).
        values: dict[Any, Any] = {self.UserMCPConfig.gmt_modified: func.now()}
        if "api_key" in config_data:
            values[self.UserMCPConfig.api_key] = config_data["api_key"]
        if "extra_config" in config_data:
            values[self.UserMCPConfig.extra_config] = _normalise_extra_config(
                config_data["extra_config"]
            )
        with self._get_session() as db:
            db.query(self.UserMCPConfig).filter(
                self.UserMCPConfig.id == int(config_id)
            ).update(values, synchronize_session=False)
        # Return shape matches the prior prod twin exactly: it ended with
        # `return self.get_by_id(config_id)` and decided existence via its
        # leading get_by_id, never via rowcount. get_by_id => None iff the
        # row is absent, the row otherwise.
        return self.get_by_id(config_id)

    def delete(self, config_id: str) -> bool:
        # Single atomic DELETE (matches prod's
        # `DELETE FROM ac_user_mcp_config WHERE id=%s; rowcount > 0`).
        with self._get_session() as db:
            deleted = (
                db.query(self.UserMCPConfig)
                .filter(self.UserMCPConfig.id == int(config_id))
                .delete(synchronize_session=False)
            )
        return deleted > 0
