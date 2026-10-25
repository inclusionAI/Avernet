"""Transactional membership outbox; network calls never run under these locks.

All membership writers lock the source space before this independent state.
Queue payloads are immutable history until acknowledged: do not purge pending
revisions. Removed-then-readded members must not lose their removal snapshot.
"""

from __future__ import annotations

import base64
import json

from injector import inject
from sqlalchemy import func, text

from agentclaw.community.core.repository.protocols.spaces import (
    SpaceKnowledgeSyncRepositoryProtocol,
)
from agentclaw.community.core.space_knowledge.repository.models import (
    SpaceKnowledgeSyncModel as State,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceModel,
    SpaceMemberModel,
)
from agentclaw.community.core.task_queue.repository.models import TaskQueueModel as Task
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSnapshot

logger = get_logger()
TASK_TYPE = "space.membership.sync"
DEADLINE_SECONDS = 7 * 24 * 3600


def _key(space_id, revision, part=0):
    base = f"space:{space_id}:membership:{revision}"
    return f"{base}:part:{part}" if part else base


def _enqueue(db, state, body):
    deadline = (
        func.datetime(func.now(), text(f"'+{DEADLINE_SECONDS} seconds'"))
        if db.bind.dialect.name == "sqlite"
        else func.date_add(func.now(), text(f"INTERVAL {DEADLINE_SECONDS} SECOND"))
    )
    # Existing queue payload is MySQL TEXT (64 KiB). Keep the queue schema
    # unchanged: large immutable snapshots are split within this transaction,
    # then reassembled before the ONE full-snapshot HTTP request.
    encoded = json.dumps(
        {**body, "_space_id": state.space_id}, ensure_ascii=False
    ).encode()
    pieces = [encoded[i : i + 36000] for i in range(0, len(encoded), 36000)]
    for part, data in enumerate(pieces):
        payload = (
            encoded.decode()
            if len(pieces) == 1
            else json.dumps(
                {
                    "_space_id": state.space_id,
                    "revision": body["revision"],
                    "_parts": len(pieces),
                    "_data": base64.b64encode(data).decode(),
                }
            )
        )
        part_key = _key(state.space_id, body["revision"], part)
        db.add(
            Task(
                task_type=TASK_TYPE,
                env=state.env,
                app=state.app,
                payload=payload,
                status="PENDING",
                run_at=func.now(),
                deadline_at=deadline,
                attempts=0,
                idempotency_key=part_key,
                active_idempotency_key=part_key,
            )
        )
    db.flush()


def _state(db, space_id, environment):
    return (
        db.query(State)
        .filter(State.env == environment, State.space_id == space_id)
        .with_for_update()
        .one_or_none()
    )


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


def record_membership_snapshot(db, space, *, disabled=False):
    """Caller owns the transaction and holds the source space lock. No-op until activated."""
    state = _state(db, space.id, space.env)
    if state is None:
        return
    db.flush()
    # A locking read sees the latest committed members under MySQL REPEATABLE READ,
    # even if an earlier permission query established an older consistent snapshot.
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
        revision=state.revision + 1,
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
    # Exclude revision from the no-op comparison, but retain it in the ECB digest.
    content = SpaceMembershipSnapshot(
        space.env, space.id, {**body, "revision": 0}
    ).digest()
    if state.snapshot_hash == content:
        return
    state.revision += 1
    state.snapshot_hash = content
    state.status = body["status"]
    _enqueue(db, state, body)


def _history(db, state, revision, part=0):
    return (
        db.query(Task)
        .filter(
            Task.env == state.env,
            Task.app == state.app,
            Task.task_type == TASK_TYPE,
            Task.idempotency_key == _key(state.space_id, revision, part),
        )
        .order_by(Task.id.desc())
    )


def _part_count(data):
    if not isinstance(data, dict):
        raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
    count = data.get("_parts", 1)
    if type(count) is not int or not 1 <= count <= 128:
        raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
    return count


def _decode(db, state, row):
    try:
        data = json.loads(row.payload)
        count = _part_count(data)
        revision = state.synced_revision + 1
        if count > 1:
            parts = [data]
            for part in range(1, count):
                item = _history(db, state, revision, part).first()
                if item is None:
                    raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
                parts.append(json.loads(item.payload))
            if any(
                _part_count(p) != count
                or p.get("_space_id") != state.space_id
                or p.get("revision") != revision
                for p in parts
            ):
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
            data = json.loads(
                b"".join(base64.b64decode(p["_data"], validate=True) for p in parts)
            )
        if (
            not isinstance(data, dict)
            or data.get("_space_id") != state.space_id
            or type(data.get("revision")) is not int
            or data["revision"] != revision
            or "_data" in data
            or "_parts" in data
        ):
            raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
        return {k: v for k, v in data.items() if k != "_space_id"}
    except (ValueError, KeyError, TypeError):
        raise SpaceKnowledgeError("SPACE_NOT_SYNCED") from None


class SpaceKnowledgeSyncRepository(SpaceKnowledgeSyncRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin):
        self._db = db

    def activate(self, *, space_id, environment, app):
        with self._db.transactional_orm_session() as db:
            space = lock_space(db, space_id, environment)
            if space is None:
                raise SpaceKnowledgeError("RESOURCE_NOT_FOUND")
            state = _state(db, space_id, environment)
            if state is None:
                state = State(
                    env=environment,
                    space_id=space_id,
                    app=app,
                    revision=0,
                    synced_revision=0,
                    snapshot_hash="",
                    status="active",
                )
                db.add(state)
                db.flush()
                record_membership_snapshot(db, space)
            elif state.app != app or state.status != "active":
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")

    def progress(self, *, space_id, environment, app):
        with self._db.orm_session() as db:
            state = (
                db.query(State)
                .filter(
                    State.env == environment,
                    State.space_id == space_id,
                    State.app == app,
                )
                .one_or_none()
            )
            return (
                (state.revision, state.synced_revision, state.status) if state else None
            )

    def next_snapshot(self, *, space_id, environment, app):
        with self._db.transactional_orm_session() as db:
            state = _state(db, space_id, environment)
            if (
                state is None
                or state.app != app
                or state.revision == state.synced_revision
            ):
                return None
            row = _history(db, state, state.synced_revision + 1).first()
            if row is None:
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
            return SpaceMembershipSnapshot(
                environment,
                space_id,
                _decode(db, state, row),
            )

    def acknowledge(self, *, snapshot, app):
        with self._db.transactional_orm_session() as db:
            state = _state(db, snapshot.space_id, snapshot.environment)
            if state is None or state.app != app:
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
            if snapshot.revision <= state.synced_revision:
                return
            row = _history(db, state, state.synced_revision + 1).first()
            if (
                snapshot.revision != state.synced_revision + 1
                or row is None
                or _decode(db, state, row) != snapshot.body
            ):
                raise SpaceKnowledgeError("SPACE_NOT_SYNCED")
            state.synced_revision = snapshot.revision

    def repair_pending(self, *, environment, app, after_space_id, limit):
        with self._db.transactional_orm_session() as db:
            states = (
                db.query(State)
                .filter(
                    State.env == environment,
                    State.app == app,
                    State.space_id > after_space_id,
                    State.synced_revision < State.revision,
                )
                .order_by(State.space_id)
                .limit(limit)
                .with_for_update()
                .all()
            )
            for state in states:
                history = _history(db, state, state.synced_revision + 1)
                latest = history.first()
                if latest is None:
                    logger.warning(
                        "membership history gap; delivery remains unacknowledged"
                    )
                    continue
                try:
                    count = _part_count(json.loads(latest.payload))
                except (ValueError, SpaceKnowledgeError):
                    logger.warning("membership history invalid; not replacing history")
                    continue
                keys = [
                    _key(state.space_id, state.synced_revision + 1, part)
                    for part in range(count)
                ]
                live = (
                    db.query(Task.id)
                    .filter(
                        Task.env == state.env,
                        Task.app == state.app,
                        Task.task_type == TASK_TYPE,
                        Task.idempotency_key.in_(keys),
                        Task.status.in_(["PENDING", "RUNNING"]),
                    )
                    .first()
                )
                if live is None:
                    try:
                        body = _decode(db, state, latest)
                    except SpaceKnowledgeError:
                        logger.warning(
                            "membership fragment missing; not replacing history"
                        )
                        continue
                    _enqueue(db, state, body)
            return states[-1].space_id if len(states) == limit else 0
