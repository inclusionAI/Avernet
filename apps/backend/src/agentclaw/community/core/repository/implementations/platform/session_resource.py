"""Unified ORM repository for session resources."""
from __future__ import annotations

import json

from injector import inject
from sqlalchemy import func, update

from agentclaw.community.core.session_resources.repository.models import (
    SessionResourceModel,
)
from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.repository.protocols.platform import SessionResourceRepositoryProtocol


class SessionResourceRepository(
    SessionResourceRepositoryProtocol,
):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def create(self, record: SessionResourceRecord) -> SessionResourceRecord:
        with self._db.orm_session() as session:
            model = SessionResourceModel(
                **{
                    key: value
                    for key, value in record.__dict__.items()
                    if key
                    not in {
                        "status",
                        "materialized_ref",
                        "gmt_create",
                        "gmt_modified",
                    }
                },
                status=record.status.value,
                materialized_ref_json=(
                    json.dumps(record.materialized_ref)
                    if record.materialized_ref is not None
                    else None
                ),
            )
            session.add(model)
            session.flush()
            session.refresh(model)
            return model.to_record()

    def get_by_resource_id(self, resource_id: str) -> SessionResourceRecord | None:
        with self._db.orm_session() as session:
            model = (
                session.query(SessionResourceModel)
                .filter(SessionResourceModel.resource_id == resource_id)
                .one_or_none()
            )
            return model.to_record() if model else None

    def get_owned(
        self,
        resource_id: str,
        owner_id: str,
        bot_id: str,
        session_key_hash: str,
    ) -> SessionResourceRecord | None:
        with self._db.orm_session() as session:
            model = (
                session.query(SessionResourceModel)
                .filter(
                    SessionResourceModel.resource_id == resource_id,
                    SessionResourceModel.owner_id == owner_id,
                    SessionResourceModel.bot_id == bot_id,
                    SessionResourceModel.session_key_hash == session_key_hash,
                )
                .one_or_none()
            )
            return model.to_record() if model else None

    def list_owned(
        self,
        owner_id: str,
        bot_id: str,
        session_key_hash: str,
    ) -> list[SessionResourceRecord]:
        with self._db.orm_session() as session:
            rows = (
                session.query(SessionResourceModel)
                .filter(
                    SessionResourceModel.owner_id == owner_id,
                    SessionResourceModel.bot_id == bot_id,
                    SessionResourceModel.session_key_hash == session_key_hash,
                )
                .order_by(SessionResourceModel.id.asc())
                .all()
            )
            return [row.to_record() for row in rows]

    def cas_start_materialization(
        self,
        *,
        resource_id: str,
        owner_id: str,
        bot_id: str,
        session_key_hash: str,
        transfer_id: str,
        task_id: str,
        allow_ready: bool = False,
    ) -> SessionResourceRecord | None:
        eligible_statuses = [
            SessionResourceStatus.UPLOAD_URL_ISSUED.value,
            SessionResourceStatus.DEVICE_SYNC_FAILED.value,
        ]
        if allow_ready:
            eligible_statuses.append(SessionResourceStatus.READY.value)
        with self._db.orm_session() as session:
            statement = (
                update(SessionResourceModel)
                .where(
                    SessionResourceModel.resource_id == resource_id,
                    SessionResourceModel.owner_id == owner_id,
                    SessionResourceModel.bot_id == bot_id,
                    SessionResourceModel.session_key_hash == session_key_hash,
                    SessionResourceModel.transfer_id == transfer_id,
                    SessionResourceModel.status.in_(eligible_statuses),
                )
                .values(
                    status=SessionResourceStatus.DEVICE_SYNCING.value,
                    task_id=task_id,
                    task_version=SessionResourceModel.task_version + 1,
                    error_code=None,
                    materialized_ref_json=None,
                )
            )
            if session.execute(statement).rowcount != 1:
                return None
            session.flush()
            model = (
                session.query(SessionResourceModel)
                .filter(SessionResourceModel.resource_id == resource_id)
                .one()
            )
            return model.to_record()

    def cas_finish_materialization(
        self,
        *,
        resource_id: str,
        transfer_id: str,
        task_id: str,
        task_version: int,
        ready: bool,
        materialized_ref: dict | None,
        error_code: str | None,
    ) -> SessionResourceRecord | None:
        status = (
            SessionResourceStatus.READY
            if ready
            else SessionResourceStatus.DEVICE_SYNC_FAILED
        )
        with self._db.orm_session() as session:
            statement = (
                update(SessionResourceModel)
                .where(
                    SessionResourceModel.resource_id == resource_id,
                    SessionResourceModel.transfer_id == transfer_id,
                    SessionResourceModel.task_id == task_id,
                    SessionResourceModel.task_version == task_version,
                    SessionResourceModel.status
                    == SessionResourceStatus.DEVICE_SYNCING.value,
                )
                .values(
                    status=status.value,
                    materialized_ref_json=(
                        json.dumps(materialized_ref)
                        if materialized_ref is not None
                        else None
                    ),
                    error_code=error_code,
                )
            )
            if session.execute(statement).rowcount != 1:
                return None
            session.flush()
            model = (
                session.query(SessionResourceModel)
                .filter(SessionResourceModel.resource_id == resource_id)
                .one()
            )
            return model.to_record()

    def soft_delete(
        self,
        resource_id: str,
        owner_id: str,
        bot_id: str,
        session_key_hash: str,
    ) -> SessionResourceRecord | None:
        with self._db.orm_session() as session:
            statement = (
                update(SessionResourceModel)
                .where(
                    SessionResourceModel.resource_id == resource_id,
                    SessionResourceModel.owner_id == owner_id,
                    SessionResourceModel.bot_id == bot_id,
                    SessionResourceModel.session_key_hash == session_key_hash,
                    SessionResourceModel.status != SessionResourceStatus.DELETED.value,
                )
                .values(status=SessionResourceStatus.DELETED.value, deleted_at=func.now())
            )
            if session.execute(statement).rowcount != 1:
                model = (
                    session.query(SessionResourceModel)
                    .filter(
                        SessionResourceModel.resource_id == resource_id,
                        SessionResourceModel.owner_id == owner_id,
                        SessionResourceModel.bot_id == bot_id,
                        SessionResourceModel.session_key_hash == session_key_hash,
                    )
                    .one_or_none()
                )
                return model.to_record() if model else None
            session.flush()
            model = (
                session.query(SessionResourceModel)
                .filter(SessionResourceModel.resource_id == resource_id)
                .one()
            )
            return model.to_record()
