"""Single-table transactional membership outbox; no queue storage dependency.

Membership writers lock the source space before reading its latest outbox row.
SQLite takes a real database write guard before any source/roster decision.
Snapshots and source changes commit together. The shared queue is only a wakeup:
its history, retention and availability cannot change the durable snapshots.
"""

from __future__ import annotations

import json

from injector import inject
from sqlalchemy import false, true

from agentclaw.community.core.repository.implementations.spaces.source_lock import (
    lock_source_space,
)
from agentclaw.community.core.repository.protocols.spaces import (
    SpaceKnowledgeSyncRepositoryProtocol,
)
from agentclaw.community.core.space_knowledge.capture_guard import (
    SpaceKnowledgeCaptureGuard,
)
from agentclaw.community.core.space_knowledge.repository.models import (
    SpaceKnowledgeSyncModel as Snapshot,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSnapshot


logger = get_logger()


def _rows(db, space_id, environment):
    return db.query(Snapshot).filter(
        Snapshot.env == environment, Snapshot.space_id == space_id
    )


def _latest(db, space_id, environment, *, lock=False):
    query = _rows(db, space_id, environment).order_by(Snapshot.revision.desc())
    return (query.with_for_update() if lock else query).first()


def _latest_state(db, space_id, environment, *, lock=False):
    query = (
        db.query(Snapshot.revision, Snapshot.app, Snapshot.status)
        .filter(Snapshot.env == environment, Snapshot.space_id == space_id)
        .order_by(Snapshot.revision.desc())
    )
    return (query.with_for_update() if lock else query).first()


def _acknowledged(db, space_id, environment, app, *, lock=False):
    query = (
        db.query(Snapshot.revision)
        .filter(
            Snapshot.env == environment,
            Snapshot.space_id == space_id,
            Snapshot.app == app,
            Snapshot.acknowledged == true(),
        )
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
    return lock_source_space(
        db, space_id=space_id, environment=environment, sqlite_write_guard=True
    )


def _save_snapshot(db, *, environment, space_id, app, body):
    # Match the receiver's UTF-8 JSON encoding, not Python character count or
    # the storage column capacity. Reject before committing an immutable head.
    payload = json.dumps(body, ensure_ascii=False)
    payload_bytes = len(payload.encode("utf-8"))
    if len(body["members"]) > 2000 or payload_bytes > 1024 * 1024:
        logger.warning(
            "membership snapshot exceeds receiver limit env=%s space_id=%s "
            "revision=%s member_count=%s payload_bytes=%s",
            environment,
            space_id,
            body["revision"],
            len(body["members"]),
            payload_bytes,
        )
        raise SpaceKnowledgeError("SPACE_MEMBERSHIP_LIMIT_EXCEEDED")
    snapshot = SpaceMembershipSnapshot(environment, space_id, body)
    db.add(
        Snapshot(
            env=environment,
            space_id=space_id,
            revision=snapshot.revision,
            app=app,
            payload=payload,
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
    def __init__(self, db: DatabasePlugin, capture: SpaceKnowledgeCaptureGuard):
        self._db = db
        self._capture = capture

    def activate(self, *, space_id, environment, app):
        with self._db.transactional_orm_session() as db:
            self._capture.require_enabled(db)
            space = lock_space(db, space_id, environment)
            if space is None:
                raise SpaceKnowledgeError("RESOURCE_NOT_FOUND")
            latest = _latest_state(db, space_id, environment, lock=True)
            if latest is None:
                record_membership_snapshot(db, space, app=app)
            elif latest.app != app or latest.status != "active":
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")

    def progress(self, *, space_id, environment, app):
        with self._db.orm_session() as db:
            self._capture.require_enabled(db)
            latest = _latest_state(db, space_id, environment)
            if latest is None or latest.app != app:
                return None
            return (
                latest.revision,
                _acknowledged(db, space_id, environment, app),
                latest.status,
            )

    def next_snapshot(self, *, space_id, environment, app):
        with self._db.orm_session() as db:
            self._capture.require_enabled(db)
            latest = _latest_state(db, space_id, environment)
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
            self._capture.require_enabled(db)
            latest = _latest_state(
                db, snapshot.space_id, snapshot.environment, lock=True
            )
            if latest is None or latest.app != app:
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
            acknowledged = _acknowledged(
                db, snapshot.space_id, snapshot.environment, app, lock=True
            )
            row = (
                db.query(Snapshot.snapshot_hash, Snapshot.status)
                .filter(
                    Snapshot.env == snapshot.environment,
                    Snapshot.space_id == snapshot.space_id,
                    Snapshot.revision == snapshot.revision,
                    Snapshot.app == app,
                )
                .with_for_update()
                .one_or_none()
            )
            # Payloads are immutable. Compare the delivered content's canonical hash
            # and envelope to the locked row without reloading the full roster.
            if (
                row is None
                or type(snapshot.revision) is not int
                or snapshot.body.get("full_snapshot") is not True
                or row.status != snapshot.body.get("status")
                or row.snapshot_hash != snapshot.digest()
            ):
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
            if snapshot.revision <= acknowledged:
                return
            if snapshot.revision != acknowledged + 1:
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
            _rows(db, snapshot.space_id, snapshot.environment).filter(
                Snapshot.revision == snapshot.revision, Snapshot.app == app
            ).update({Snapshot.acknowledged: True}, synchronize_session=False)

    def pending_revisions(self, *, environment, app, after_space_id, limit):
        with self._db.orm_session() as db:
            self._capture.require_enabled(db)
            result = []
            cursor = after_space_id
            # The existing (env, app, acknowledged, space_id, revision) index
            # seeks directly past each space's backlog. GROUP BY/MIN would
            # visit every pending revision even with a small result LIMIT.
            for _ in range(min(max(limit, 0), 32)):
                row = (
                    db.query(Snapshot.space_id, Snapshot.revision)
                    .filter(
                        Snapshot.env == environment,
                        Snapshot.app == app,
                        Snapshot.acknowledged == false(),
                        Snapshot.space_id > cursor,
                    )
                    .order_by(Snapshot.space_id, Snapshot.revision)
                    .limit(1)
                    .first()
                )
                if row is None:
                    break
                result.append((row.space_id, row.revision))
                cursor = row.space_id
            return result
