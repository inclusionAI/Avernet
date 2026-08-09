"""Unified OssToNasRecord repository (prod the relational store + local SQLite).

One ORM implementation behind the ``OssToNasRecordRepository``
Protocol. The only per-environment difference is the injected
:class:`DatabasePlugin`: ``orm_session()`` yields a SQLAlchemy
``Session`` in both runtimes, so this single body runs unchanged on
OceanBase (prod) and SQLite (local), collapsing the previous
raw-SQL/ORM twins so CI exercises the prod path too.

Prod-twin parity: every mutate is a single atomic statement;
``update_status``/``update_record``/``batch_update_status`` set
``gmt_modified=func.now()`` (DB-side, matching the prod twins' literal
``NOW()``); ``gmt_create``/``gmt_modified`` are model server defaults,
never assigned in business code. ``insert_record``/``update_record``
re-read and return the row (prod re-selected via ``get_record``).
DDL parity: ``specs/2026-05-18-unified-repository-round-3-session-1``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from injector import inject
from sqlalchemy import func

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.repository.protocols.devices import OssToNasRecordRepository


logger = get_logger()

_ALLOWED_UPDATE_FIELDS = {
    "env",
    "batch_no",
    "sub_batch_no",
    "bot_info",
    "storage_status",
}


class OssToNasRecordRepository(
    OssToNasRecordRepository,
):
    """Unified ``OssToNasRecordRepository`` Protocol implementation."""

    @inject
    def __init__(self, database: DatabasePlugin) -> None:
        from agentclaw.community.plugin_api.models import OssToNasRecord

        self._db = database
        self.Model = OssToNasRecord

    def get_record(
        self,
        staff_no: str,
        bot_id: str,
        env: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if env is None:
            env = get_current_env()
        with self._db.orm_session() as db:
            row = (
                db.query(self.Model)
                .filter(
                    self.Model.staff_no == staff_no,
                    self.Model.bot_id == bot_id,
                    self.Model.env == env,
                )
                .first()
            )
            return row.to_dict() if row else None

    def query_records_by_batch(
        self,
        env: str,
        batch_no: str,
        sub_batch_no: str,
        status_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._db.orm_session() as db:
            q = db.query(self.Model).filter(
                self.Model.env == env,
                self.Model.batch_no == batch_no,
                self.Model.sub_batch_no == sub_batch_no,
            )
            if status_filter:
                q = q.filter(self.Model.storage_status == status_filter)
            return [r.to_dict() for r in q.all()]

    def update_status(
        self,
        staff_no: str,
        bot_id: str,
        new_status: str,
        env: Optional[str] = None,
    ) -> None:
        if env is None:
            env = get_current_env()
        with self._db.orm_session() as db:
            db.query(self.Model).filter(
                self.Model.env == env,
                self.Model.staff_no == staff_no,
                self.Model.bot_id == bot_id,
            ).update(
                {
                    self.Model.storage_status: new_status,
                    self.Model.gmt_modified: func.now(),
                },
                synchronize_session=False,
            )

    def insert_record(
        self,
        staff_no: str,
        bot_id: str,
        env: str,
        batch_no: str,
        sub_batch_no: str,
        bot_info: Optional[Dict[str, Any]] = None,
        storage_status: str = "oss",
    ) -> Dict[str, Any]:
        bot_info_str = json.dumps(bot_info) if bot_info else None
        # gmt_create/gmt_modified are DB server defaults (func.now()).
        with self._db.orm_session() as db:
            record = self.Model(
                staff_no=staff_no,
                bot_id=bot_id,
                bot_info=bot_info_str,
                env=env,
                batch_no=batch_no,
                sub_batch_no=sub_batch_no,
                storage_status=storage_status,
            )
            db.add(record)
            db.flush()
            db.refresh(record)
            return record.to_dict()

    def update_record(
        self,
        staff_no: str,
        bot_id: str,
        updates: Dict[str, Any],
        env: Optional[str] = None,
    ) -> Dict[str, Any]:
        if env is None:
            env = get_current_env()
        filtered = {
            k: v for k, v in updates.items() if k in _ALLOWED_UPDATE_FIELDS
        }
        if not filtered:
            raise ValueError("没有可更新的字段")
        if "bot_info" in filtered and isinstance(filtered["bot_info"], dict):
            filtered["bot_info"] = json.dumps(filtered["bot_info"])

        values: Dict[Any, Any] = {
            getattr(self.Model, k): v for k, v in filtered.items()
        }
        values[self.Model.gmt_modified] = func.now()
        with self._db.orm_session() as db:
            db.query(self.Model).filter(
                self.Model.env == env,
                self.Model.staff_no == staff_no,
                self.Model.bot_id == bot_id,
            ).update(values, synchronize_session=False)
        result = self.get_record(staff_no, bot_id, env=env)
        if result is None:
            raise RuntimeError(
                f"update_record: 记录不存在 {staff_no}/{bot_id}"
            )
        return result

    def delete_record(
        self,
        staff_no: str,
        bot_id: str,
        env: Optional[str] = None,
    ) -> bool:
        if env is None:
            env = get_current_env()
        with self._db.orm_session() as db:
            count = (
                db.query(self.Model)
                .filter(
                    self.Model.env == env,
                    self.Model.staff_no == staff_no,
                    self.Model.bot_id == bot_id,
                )
                .delete(synchronize_session=False)
            )
            return count > 0

    def batch_update_status(
        self,
        env: str,
        batch_no: str,
        sub_batch_no: str,
        new_status: str,
    ) -> int:
        with self._db.orm_session() as db:
            count = (
                db.query(self.Model)
                .filter(
                    self.Model.env == env,
                    self.Model.batch_no == batch_no,
                    self.Model.sub_batch_no == sub_batch_no,
                )
                .update(
                    {
                        self.Model.storage_status: new_status,
                        self.Model.gmt_modified: func.now(),
                    },
                    synchronize_session=False,
                )
            )
            return count
