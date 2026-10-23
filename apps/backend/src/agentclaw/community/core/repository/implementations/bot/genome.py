"""Bot Genome Registry repositories (OceanBase + SQLite, one ORM body each).

The injected :class:`DatabasePlugin`'s ``orm_session()`` yields a SQLAlchemy
``Session`` in both runtimes; a ``with`` block is one transaction (commit on
exit, rollback on an exception), which is what the compare-and-swap moves
and the ``active``/``previous`` pair rely on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from injector import inject
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_genome.repository.models import (
    CandidateCollision,
    CandidateRefRequest,
    GenomePatchModel,
    GenomePatchRow,
    GenomePromotionModel,
    GenomePromotionRow,
    GenomeRefEventModel,
    GenomeRefEventRow,
    GenomeRefModel,
    GenomeRefRow,
    GenomeRevisionModel,
    GenomeRevisionRow,
)
from agentclaw.community.core.repository.protocols.bot.genome import (
    GenomePatchRepositoryProtocol,
    GenomePromotionRepositoryProtocol,
    GenomeRefRepositoryProtocol,
    GenomeRevisionRepositoryProtocol,
)
from agentclaw.community.plugin_api.database import DatabasePlugin

#: Insert attempts on a ``seq`` race before giving up (each attempt re-reads MAX(seq)).
SEQ_RETRIES = 5
_BOT_AXES = ("env", "entity_id", "bot_id")


class _Conflict(Exception):
    """A compare-and-swap lost; raised inside the transaction so the context manager rolls it back."""


def _transaction(db: DatabasePlugin):
    """A real commit/rollback transaction: ``orm_session`` runs at AUTOCOMMIT on the production plugin."""
    return getattr(db, "transactional_orm_session", db.orm_session)()


def _bot(model, *, env: str, entity_id: str, bot_id: str) -> list:
    """The filter naming one bot's rows; the tenant half is the model guard's."""
    return [model.env == env, model.entity_id == entity_id, model.bot_id == bot_id]


class GenomeRevisionRepository(GenomeRevisionRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def insert(self, row: GenomeRevisionRow) -> tuple[GenomeRevisionRow, bool]:
        axes = {k: getattr(row, k) for k in _BOT_AXES}
        last: IntegrityError | None = None
        for _ in range(SEQ_RETRIES):
            existing = self.get(**axes, revision_id=row.revision_id)
            if existing is not None:
                return existing, False
            try:
                with self._db.orm_session() as session:
                    seq = self._next_seq(session, **axes)
                    orm = GenomeRevisionModel(
                        **row.model_dump(exclude={"id", "seq", "gmt_create", "gmt_modified"}), seq=seq
                    )
                    session.add(orm)
                    session.flush()
                    session.refresh(orm)
                    return orm.to_record(), True
            except IntegrityError as exc:
                last = exc  # uk_bot_revision (a concurrent record) or uk_bot_seq (a seq race): re-read, retry
        raise RuntimeError(f"could not record revision {row.revision_id} after {SEQ_RETRIES} attempts") from last

    def record(
        self,
        row: GenomeRevisionRow,
        *,
        patch: Optional[GenomePatchRow] = None,
        candidate: Optional[CandidateRefRequest] = None,
        parent_from_latest: bool = False,
    ) -> tuple[GenomeRevisionRow, bool, Optional[GenomeRefRow]]:
        axes = {k: getattr(row, k) for k in _BOT_AXES}
        last: IntegrityError | None = None
        for _ in range(SEQ_RETRIES):
            try:
                with _transaction(self._db) as session:
                    orm = _revision_row(session, **axes, revision_id=row.revision_id)
                    created = orm is None
                    named = _run_candidate_refs(session, **axes, run_id=candidate.run_id) if candidate else []
                    if (
                        orm is not None
                        and candidate is not None
                        and not any(r.revision_id == orm.revision_id for r in named)
                    ):
                        # The content exists but this run has no candidate pointing at it: it was recorded by
                        # another run, as an owner's draft, from another patch or from a capture. Only the run
                        # that first recorded it may retry. Rolls the transaction back.
                        raise CandidateCollision(orm.revision_id, orm.seq, orm.patch_digest)
                    if orm is None:
                        values = row.model_dump(exclude={"id", "seq", "gmt_create", "gmt_modified"})
                        if parent_from_latest:
                            # Read with the seq, so a race that collides on uk_bot_seq recomputes both on retry.
                            latest = _latest_revision(session, **axes)
                            values["parent_id"] = latest.revision_id if latest is not None else None
                        orm = GenomeRevisionModel(**values, seq=self._next_seq(session, **axes))
                        session.add(orm)
                        session.flush()
                    if patch is not None and _patch_row(session, **axes, digest=patch.digest) is None:
                        session.add(GenomePatchModel(**patch.model_dump(exclude={"id"})))
                        session.flush()
                    ref = (
                        _candidate_ref(session, **axes, revision_id=row.revision_id, candidate=candidate, named=named)
                        if candidate is not None
                        else None
                    )
                    session.refresh(orm)
                    return orm.to_record(), created, ref.to_record() if ref is not None else None
            except IntegrityError as exc:
                last = (
                    exc  # a concurrent record (uk_bot_revision), a seq race, or a candidate number taken: retry whole
                )
        raise RuntimeError(f"could not record revision {row.revision_id} after {SEQ_RETRIES} attempts") from last

    @staticmethod
    def _next_seq(session, *, env: str, entity_id: str, bot_id: str) -> int:
        current = (
            session.query(func.max(GenomeRevisionModel.seq))
            .filter(*_bot(GenomeRevisionModel, env=env, entity_id=entity_id, bot_id=bot_id))
            .scalar()
        )
        return (current or 0) + 1

    def get(self, *, env: str, entity_id: str, bot_id: str, revision_id: str) -> Optional[GenomeRevisionRow]:
        with self._db.orm_session() as session:
            row = (
                session.query(GenomeRevisionModel)
                .filter(*_bot(GenomeRevisionModel, env=env, entity_id=entity_id, bot_id=bot_id))
                .filter(GenomeRevisionModel.revision_id == revision_id)
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def get_by_seq(self, *, env: str, entity_id: str, bot_id: str, seq: int) -> Optional[GenomeRevisionRow]:
        with self._db.orm_session() as session:
            row = (
                session.query(GenomeRevisionModel)
                .filter(*_bot(GenomeRevisionModel, env=env, entity_id=entity_id, bot_id=bot_id))
                .filter(GenomeRevisionModel.seq == seq)
                .one_or_none()
            )
            return row.to_record() if row is not None else None

    def list(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        status: Optional[str] = None,
        parent_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[GenomeRevisionRow]]:
        if page < 1 or page_size < 1:
            raise ValueError(f"page and page_size must be positive, got {page}, {page_size}")
        with self._db.orm_session() as session:
            query = session.query(GenomeRevisionModel).filter(
                *_bot(GenomeRevisionModel, env=env, entity_id=entity_id, bot_id=bot_id)
            )
            if status is not None:
                query = query.filter(GenomeRevisionModel.status == status)
            if parent_id is not None:
                query = query.filter(GenomeRevisionModel.parent_id == parent_id)
            total = query.count()
            rows = query.order_by(GenomeRevisionModel.seq.desc()).offset((page - 1) * page_size).limit(page_size).all()
            return total, [row.to_record() for row in rows]

    def update_metadata(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        revision_id: str,
        status: Optional[str] = None,
        evaluations: Optional[str] = None,
        annotations: Optional[str] = None,
    ) -> Optional[GenomeRevisionRow]:
        with self._db.orm_session() as session:
            row = (
                session.query(GenomeRevisionModel)
                .filter(*_bot(GenomeRevisionModel, env=env, entity_id=entity_id, bot_id=bot_id))
                .filter(GenomeRevisionModel.revision_id == revision_id)
                .one_or_none()
            )
            if row is None:
                return None
            if status is not None:
                row.status = status
            if evaluations is not None:
                row.evaluations = evaluations
            if annotations is not None:
                row.annotations = annotations
            session.flush()
            session.refresh(row)
            return row.to_record()


class GenomeRefRepository(GenomeRefRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def get(self, *, env: str, entity_id: str, bot_id: str, name: str) -> Optional[GenomeRefRow]:
        with self._db.orm_session() as session:
            row = self._row(session, env=env, entity_id=entity_id, bot_id=bot_id, name=name)
            return row.to_record() if row is not None else None

    def list(self, *, env: str, entity_id: str, bot_id: str, name_prefix: Optional[str] = None) -> list[GenomeRefRow]:
        with self._db.orm_session() as session:
            query = session.query(GenomeRefModel).filter(
                *_bot(GenomeRefModel, env=env, entity_id=entity_id, bot_id=bot_id)
            )
            if name_prefix is not None:
                query = query.filter(GenomeRefModel.name.startswith(name_prefix, autoescape=True))
            return [row.to_record() for row in query.order_by(GenomeRefModel.name).all()]

    def move(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        name: str,
        revision_id: str,
        expected_revision_id: Optional[str],
        moved_kind: str,
        moved_actor: str,
        run_id: Optional[str],
        reason: Optional[str],
        moved_at: datetime,
    ) -> Optional[GenomeRefRow]:
        stamp = dict(moved_kind=moved_kind, moved_actor=moved_actor, run_id=run_id, reason=reason, moved_at=moved_at)
        try:
            with _transaction(self._db) as session:
                row = self._cas(
                    session,
                    env=env,
                    entity_id=entity_id,
                    bot_id=bot_id,
                    name=name,
                    revision_id=revision_id,
                    expected=expected_revision_id,
                    **stamp,
                )
                session.flush()
                session.refresh(row)
                return row.to_record()
        except (_Conflict, IntegrityError):
            return None  # the UPDATE matched nothing, or a concurrent creator won uk_bot_ref

    def move_pair(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        revision_id: str,
        expected_active: Optional[str],
        moved_kind: str,
        moved_actor: str,
        run_id: Optional[str],
        reason: Optional[str],
        moved_at: datetime,
    ) -> Optional[tuple[GenomeRefRow, Optional[GenomeRefRow]]]:
        stamp = dict(moved_kind=moved_kind, moved_actor=moved_actor, run_id=run_id, reason=reason, moved_at=moved_at)
        try:
            with _transaction(self._db) as session:
                active, previous = _move_pair(
                    session,
                    env=env,
                    entity_id=entity_id,
                    bot_id=bot_id,
                    revision_id=revision_id,
                    expected_active=expected_active,
                    **stamp,
                )
                session.flush()
                session.refresh(active)
                if previous is not None:
                    session.refresh(previous)
                return active.to_record(), previous.to_record() if previous is not None else None
        except (_Conflict, IntegrityError):
            return None  # nothing moved: the transaction rolled back

    def _row(self, session, **kw) -> Optional[GenomeRefModel]:
        return _ref_row(session, **kw)

    def _cas(self, session, **kw) -> GenomeRefModel:
        return _cas_move(session, **kw)

    def candidate_count(self, *, env: str, entity_id: str, bot_id: str, run_id: str) -> int:
        with self._db.orm_session() as session:
            return (
                session.query(func.count(GenomeRefModel.id))
                .filter(*_bot(GenomeRefModel, env=env, entity_id=entity_id, bot_id=bot_id))
                .filter(GenomeRefModel.name.startswith(f"candidate/{run_id}/", autoescape=True))
                .scalar()
            ) or 0

    def events(self, *, env: str, entity_id: str, bot_id: str, name: str, limit: int = 50) -> list[GenomeRefEventRow]:
        if limit <= 0:
            raise ValueError(f"events limit must be positive, got {limit}")
        with self._db.orm_session() as session:
            rows = (
                session.query(GenomeRefEventModel)
                .filter(*_bot(GenomeRefEventModel, env=env, entity_id=entity_id, bot_id=bot_id))
                .filter(GenomeRefEventModel.name == name)
                .order_by(GenomeRefEventModel.moved_at.desc(), GenomeRefEventModel.id.desc())
                .limit(limit)
                .all()
            )
            return [row.to_record() for row in rows]


def _revision_row(session, *, env: str, entity_id: str, bot_id: str, revision_id: str) -> Optional[GenomeRevisionModel]:
    return (
        session.query(GenomeRevisionModel)
        .filter(*_bot(GenomeRevisionModel, env=env, entity_id=entity_id, bot_id=bot_id))
        .filter(GenomeRevisionModel.revision_id == revision_id)
        .one_or_none()
    )


def _latest_revision(session, *, env: str, entity_id: str, bot_id: str) -> Optional[GenomeRevisionModel]:
    return (
        session.query(GenomeRevisionModel)
        .filter(*_bot(GenomeRevisionModel, env=env, entity_id=entity_id, bot_id=bot_id))
        .order_by(GenomeRevisionModel.seq.desc())
        .first()
    )


def _patch_row(session, *, env: str, entity_id: str, bot_id: str, digest: str) -> Optional[GenomePatchModel]:
    return (
        session.query(GenomePatchModel)
        .filter(*_bot(GenomePatchModel, env=env, entity_id=entity_id, bot_id=bot_id))
        .filter(GenomePatchModel.digest == digest)
        .one_or_none()
    )


def _run_candidate_refs(session, *, env: str, entity_id: str, bot_id: str, run_id: str) -> list[GenomeRefModel]:
    """Every ``candidate/<run_id>/<n>`` ref of the bot, read inside the caller's transaction."""
    return (
        session.query(GenomeRefModel)
        .filter(*_bot(GenomeRefModel, env=env, entity_id=entity_id, bot_id=bot_id))
        .filter(GenomeRefModel.name.startswith(f"candidate/{run_id}/", autoescape=True))
        .all()
    )


def _candidate_ref(
    session,
    *,
    env: str,
    entity_id: str,
    bot_id: str,
    revision_id: str,
    candidate: CandidateRefRequest,
    named: list[GenomeRefModel],
) -> GenomeRefModel:
    """``candidate/<run>/<n>`` for this revision: the one of ``named`` (this run's refs) already pointing at it, or the
    next number.
    """
    axes = dict(env=env, entity_id=entity_id, bot_id=bot_id)
    for existing in named:
        if existing.revision_id == revision_id:
            return existing
    return _cas_move(
        session,
        **axes,
        name=f"candidate/{candidate.run_id}/{len(named) + 1}",
        revision_id=revision_id,
        expected=None,
        moved_kind=candidate.moved_kind,
        moved_actor=candidate.moved_actor,
        run_id=candidate.run_id,
        reason=None,
        moved_at=candidate.moved_at,
    )


def _ref_row(session, *, env: str, entity_id: str, bot_id: str, name: str) -> Optional[GenomeRefModel]:
    return (
        session.query(GenomeRefModel)
        .filter(*_bot(GenomeRefModel, env=env, entity_id=entity_id, bot_id=bot_id))
        .filter(GenomeRefModel.name == name)
        .one_or_none()
    )


def _cas_move(
    session,
    *,
    env: str,
    entity_id: str,
    bot_id: str,
    name: str,
    revision_id: str,
    expected: Optional[str],
    moved_kind: str,
    moved_actor: str,
    run_id: Optional[str],
    reason: Optional[str],
    moved_at: datetime,
) -> GenomeRefModel:
    """One conditional move inside the caller's transaction; raises ``_Conflict`` when the CAS matches nothing."""
    axes = dict(env=env, entity_id=entity_id, bot_id=bot_id)
    stamp = dict(moved_kind=moved_kind, moved_actor=moved_actor, run_id=run_id, reason=reason, moved_at=moved_at)
    if expected is None:
        row = GenomeRefModel(**axes, name=name, revision_id=revision_id, version=1, **stamp)
        session.add(row)
        session.flush()  # uk_bot_ref decides who created it
    else:
        # The CAS itself: the ref row (name → revision_id pointer) is repointed from `expected` to
        # `revision_id` only if it still points at `expected`; a revision row is never changed.
        updated = (
            session.query(GenomeRefModel)
            .filter(*_bot(GenomeRefModel, **axes))
            .filter(GenomeRefModel.name == name, GenomeRefModel.revision_id == expected)
            .update(
                {"revision_id": revision_id, "version": GenomeRefModel.version + 1, **stamp},
                synchronize_session=False,
            )
        )
        if updated != 1:
            raise _Conflict(name)
        row = _ref_row(session, **axes, name=name)
    session.add(GenomeRefEventModel(**axes, name=name, from_revision_id=expected, to_revision_id=revision_id, **stamp))
    session.flush()  # the event lands in the same transaction as the move, or neither does
    return row


def _move_pair(
    session, *, env: str, entity_id: str, bot_id: str, revision_id: str, expected_active: Optional[str], **stamp
) -> tuple[GenomeRefModel, Optional[GenomeRefModel]]:
    """``active`` → ``revision_id`` by CAS and ``previous`` → the old ``active``, inside the caller's transaction.

    Re-promoting the revision ``active`` already points at (``revision_id == expected_active``) moves
    ``active`` onto itself (a logged, versioned move) and leaves ``previous`` as the one step back.
    """
    axes = dict(env=env, entity_id=entity_id, bot_id=bot_id)
    active = _cas_move(session, **axes, name="active", revision_id=revision_id, expected=expected_active, **stamp)
    previous = None
    if expected_active is not None and expected_active != revision_id:
        current = _ref_row(session, **axes, name="previous")
        previous = _cas_move(
            session,
            **axes,
            name="previous",
            revision_id=expected_active,
            expected=current.revision_id if current is not None else None,
            **stamp,
        )
    return active, previous


class GenomePatchRepository(GenomePatchRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def insert(self, row: GenomePatchRow) -> tuple[GenomePatchRow, bool]:
        axes = {k: getattr(row, k) for k in _BOT_AXES}
        existing = self.get(**axes, digest=row.digest)
        if existing is not None:
            return existing, False
        try:
            with self._db.orm_session() as session:
                orm = GenomePatchModel(**row.model_dump(exclude={"id"}))
                session.add(orm)
                session.flush()
                session.refresh(orm)
                return orm.to_record(), True
        except IntegrityError:
            existing = self.get(**axes, digest=row.digest)
            if existing is None:
                raise
            return existing, False

    def get(self, *, env: str, entity_id: str, bot_id: str, digest: str) -> Optional[GenomePatchRow]:
        with self._db.orm_session() as session:
            row = (
                session.query(GenomePatchModel)
                .filter(*_bot(GenomePatchModel, env=env, entity_id=entity_id, bot_id=bot_id))
                .filter(GenomePatchModel.digest == digest)
                .one_or_none()
            )
            return row.to_record() if row is not None else None


class GenomePromotionRepository(GenomePromotionRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self._db = db

    def insert(self, row: GenomePromotionRow) -> GenomePromotionRow:
        with self._db.orm_session() as session:
            orm = GenomePromotionModel(**row.model_dump(exclude={"id", "gmt_create", "gmt_modified"}))
            session.add(orm)
            session.flush()
            session.refresh(orm)
            return orm.to_record()

    def start(
        self,
        row: GenomePromotionRow,
        *,
        expected_active: Optional[str],
        expected_statuses: tuple[str, ...],
        moved_at: datetime,
    ) -> Optional[GenomePromotionRow]:
        """The promotion's one write: move the ``active``/``previous`` pair, mark the revision, file the row.

        One transaction, so a lost compare-and-swap, a status that moved out
        of ``expected_statuses`` since the caller read it, or a failed write
        leaves no ref moved, no status changed and no row. ``None`` on conflict.
        """
        axes = dict(env=row.env, entity_id=row.entity_id, bot_id=row.bot_id)
        stamp = dict(
            moved_kind=row.actor_kind,
            moved_actor=row.actor_id,
            run_id=row.actor_run_id,
            reason=row.reason,
            moved_at=moved_at,
        )
        try:
            with _transaction(self._db) as session:
                _move_pair(session, **axes, revision_id=row.revision_id, expected_active=expected_active, **stamp)
                updated = (
                    session.query(GenomeRevisionModel)
                    .filter(*_bot(GenomeRevisionModel, **axes))
                    .filter(GenomeRevisionModel.revision_id == row.revision_id)
                    .filter(GenomeRevisionModel.status.in_(expected_statuses))
                    .update({"status": "promoted"}, synchronize_session=False)
                )
                if updated != 1:
                    # Not this bot's revision, or its status changed under us (verification rejected it).
                    raise _Conflict(row.revision_id)
                orm = GenomePromotionModel(**row.model_dump(exclude={"id", "gmt_create", "gmt_modified"}))
                session.add(orm)
                session.flush()
                session.refresh(orm)
                return orm.to_record()
        except (_Conflict, IntegrityError):
            return None

    def _one(
        self, session, *, env: str, entity_id: str, bot_id: str, promotion_id: str
    ) -> Optional[GenomePromotionModel]:
        return (
            session.query(GenomePromotionModel)
            .filter(*_bot(GenomePromotionModel, env=env, entity_id=entity_id, bot_id=bot_id))
            .filter(GenomePromotionModel.promotion_id == promotion_id)
            .one_or_none()
        )

    def get(self, *, env: str, entity_id: str, bot_id: str, promotion_id: str) -> Optional[GenomePromotionRow]:
        with self._db.orm_session() as session:
            row = self._one(session, env=env, entity_id=entity_id, bot_id=bot_id, promotion_id=promotion_id)
            return row.to_record() if row is not None else None

    def list(
        self, *, env: str, entity_id: str, bot_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[int, list[GenomePromotionRow]]:
        if page < 1 or page_size < 1:
            raise ValueError(f"page and page_size must be positive, got {page}, {page_size}")
        with self._db.orm_session() as session:
            query = session.query(GenomePromotionModel).filter(
                *_bot(GenomePromotionModel, env=env, entity_id=entity_id, bot_id=bot_id)
            )
            total = query.count()
            rows = (
                query.order_by(GenomePromotionModel.created_at.desc(), GenomePromotionModel.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return total, [row.to_record() for row in rows]

    def by_apply_reference(
        self, *, env: str, entity_id: str, bot_id: str, apply_reference: str
    ) -> Optional[GenomePromotionRow]:
        with self._db.orm_session() as session:
            row = (
                session.query(GenomePromotionModel)
                .filter(*_bot(GenomePromotionModel, env=env, entity_id=entity_id, bot_id=bot_id))
                .filter(GenomePromotionModel.apply_reference == apply_reference)
                .order_by(GenomePromotionModel.id.desc())
                .first()
            )
            return row.to_record() if row is not None else None

    def finish(
        self,
        *,
        env: str,
        entity_id: str,
        bot_id: str,
        promotion_id: str,
        status: str,
        apply_reference: Optional[str],
        apply_detail: str,
    ) -> Optional[GenomePromotionRow]:
        with self._db.orm_session() as session:
            row = self._one(session, env=env, entity_id=entity_id, bot_id=bot_id, promotion_id=promotion_id)
            if row is None:
                return None
            row.status = status
            row.apply_reference = apply_reference
            row.apply_detail = apply_detail
            session.flush()
            session.refresh(row)
            return row.to_record()
