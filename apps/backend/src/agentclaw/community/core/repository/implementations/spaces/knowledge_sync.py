"""Single-table transactional membership outbox; no queue storage dependency.

Membership writers lock the source space before reading its latest outbox row.
Snapshots and source changes commit together. The shared queue is only a wakeup:
its history, retention and availability cannot change the durable snapshots.
"""

from __future__ import annotations

import json

from injector import inject
from sqlalchemy import false, func, true

from agentclaw.community.core.repository.protocols.spaces import (
    SpaceKnowledgeSyncRepositoryProtocol,
)
from agentclaw.community.core.space_knowledge.repository.models import (
    SpaceKnowledgeSyncModel as Snapshot,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceModel,
    SpaceMemberModel,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSnapshot


def _rows(db, space_id, environment):
    return db.query(Snapshot).filter(
        Snapshot.env == environment, Snapshot.space_id == space_id
    )


def _latest(db, space_id, environment, *, lock=False):
    query = _rows(db, space_id, environment).order_by(Snapshot.revision.desc())
    return (query.with_for_update() if lock else query).first()


def _acknowledged(db, space_id, environment, app, *, lock=False):
    query = (
        _rows(db, space_id, environment)
        .filter(Snapshot.app == app, Snapshot.acknowledged == true())
        .order_by(Snapshot.revision.desc())
    )
    row = (query.with_for_update() if lock else query).first()
    return row.revision if row else 0


def _decode(row):
    try:
        body = json.loads(row.payload)
        if (
            not isinstance(body, dict)
            or type(body.get("revision")) is not int
            or body["revision"] != row.revision
            or body.get("status") != row.status
            or body.get("full_snapshot") is not True
        ):
            raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
        snapshot = SpaceMembershipSnapshot(row.env, row.space_id, body)
        if snapshot.digest() != row.snapshot_hash:
            raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
        return snapshot
    except (ValueError, KeyError, TypeError):
        raise SpaceKnowledgeError("SPACE_NOT_SYNCED") from None


def lock_space(db, space_id, environment):
    return (
        db.query(SpaceModel)
        .filter(
            SpaceModel.id == space_id,
            SpaceModel.env == environment,
            SpaceModel.deleted_at.is_(None),
        )
        .with_for_update()
        .one_or_none()
    )


def _save_snapshot(db, *, environment, space_id, app, body):
    snapshot = SpaceMembershipSnapshot(environment, space_id, body)
    db.add(
        Snapshot(
            env=environment,
            space_id=space_id,
            revision=snapshot.revision,
            app=app,
            payload=json.dumps(body, ensure_ascii=False),
            snapshot_hash=snapshot.digest(),
            status=body["status"],
            acknowledged=False,
        )
    )
    db.flush()


def record_membership_snapshot(db, space, *, disabled=False, app=None):
    """Caller holds the source space lock/transaction; ordinary spaces are no-ops."""
    previous = _latest(db, space.id, space.env, lock=True)
    if previous is None and app is None:
        return
    db.flush()
    # Locking reads see current committed members under MySQL REPEATABLE READ,
    # even if earlier permission reads established an older consistent snapshot.
    members = (
        []
        if disabled
        else db.query(SpaceMemberModel)
        .filter(
            SpaceMemberModel.space_id == space.id,
            SpaceMemberModel.env == space.env,
            SpaceMemberModel.status == "ACTIVE",
        )
        .order_by(SpaceMemberModel.user_id)
        .with_for_update()
        .all()
    )
    body = dict(
        revision=previous.revision + 1 if previous else 1,
        full_snapshot=True,
        space_type=space.space_type,
        name=space.name,
        creator_staff_no=space.created_by,
        personal_owner_staff_no=space.personal_owner_id,
        status="disabled" if disabled else "active",
        members=[
            dict(
                staff_no=m.user_id,
                role="ADMIN"
                if m.role in {"ADMIN", "ADMINISTRATOR", "OWNER"}
                else "MEMBER",
            )
            for m in members
        ],
    )
    if previous is not None:
        previous_body = _decode(previous).body
        if {**previous_body, "revision": 0} == {**body, "revision": 0}:
            return
    _save_snapshot(
        db,
        environment=space.env,
        space_id=space.id,
        app=previous.app if previous else app,
        body=body,
    )


class SpaceKnowledgeSyncRepository(SpaceKnowledgeSyncRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin):
        self._db = db

    def activate(self, *, space_id, environment, app):
        with self._db.transactional_orm_session() as db:
            space = lock_space(db, space_id, environment)
            if space is None:
                raise SpaceKnowledgeError("RESOURCE_NOT_FOUND")
            latest = _latest(db, space_id, environment, lock=True)
            if latest is None:
                record_membership_snapshot(db, space, app=app)
            elif latest.app != app or latest.status != "active":
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")

    def progress(self, *, space_id, environment, app):
        with self._db.orm_session() as db:
            latest = _latest(db, space_id, environment)
            if latest is None or latest.app != app:
                return None
            return (
                latest.revision,
                _acknowledged(db, space_id, environment, app),
                latest.status,
            )

    def next_snapshot(self, *, space_id, environment, app):
        with self._db.orm_session() as db:
            latest = _latest(db, space_id, environment)
            if latest is None or latest.app != app:
                return None
            acknowledged = _acknowledged(db, space_id, environment, app)
            if latest.revision == acknowledged:
                return None
            row = (
                _rows(db, space_id, environment)
                .filter(Snapshot.revision == acknowledged + 1, Snapshot.app == app)
                .one_or_none()
            )
            if row is None:
                # Never replace missing history with today's roster or jump a
                # removed-then-rejoined member's revocation snapshot.
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
            return _decode(row)

    def acknowledge(self, *, snapshot, app):
        with self._db.transactional_orm_session() as db:
            latest = _latest(db, snapshot.space_id, snapshot.environment, lock=True)
            if latest is None or latest.app != app:
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
            acknowledged = _acknowledged(
                db, snapshot.space_id, snapshot.environment, app, lock=True
            )
            row = (
                _rows(db, snapshot.space_id, snapshot.environment)
                .filter(Snapshot.revision == snapshot.revision, Snapshot.app == app)
                .with_for_update()
                .one_or_none()
            )
            if row is None or _decode(row).body != snapshot.body:
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
            if snapshot.revision <= acknowledged:
                return
            if snapshot.revision != acknowledged + 1:
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
            row.acknowledged = True

    def pending_revisions(self, *, environment, app, after_space_id, limit):
        with self._db.orm_session() as db:
            rows = (
                db.query(Snapshot.space_id, func.min(Snapshot.revision))
                .filter(
                    Snapshot.env == environment,
                    Snapshot.app == app,
                    Snapshot.acknowledged == false(),
                    Snapshot.space_id > after_space_id,
                )
                .group_by(Snapshot.space_id)
                .order_by(Snapshot.space_id)
                .limit(limit)
                .all()
            )
            return [(space_id, revision) for space_id, revision in rows]
