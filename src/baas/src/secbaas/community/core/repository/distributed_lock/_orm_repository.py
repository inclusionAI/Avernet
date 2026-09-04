"""ORM-backed distributed lock repository implementation.

Uses @with_orm_session decorator for SQLAlchemy ORM Session lifecycle.
Corresponds to ZdasDistributedLockRepository (ac_lock_table).
"""

from datetime import datetime

from sqlalchemy import case, func, or_, select

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import DistributedLockModel
from ._protocol import DistributedLockRepository
from ._record import LockRecord

log = get_logger("orm")


def _is_lock_wait_timeout(exc: Exception) -> bool:
    """Return True for OceanBase/MySQL-compatible lock wait timeouts."""
    from sqlalchemy.exc import DatabaseError as SADatabaseError

    if not isinstance(exc, SADatabaseError):
        return False

    orig = getattr(exc, "orig", None)
    errno = getattr(orig, "errno", None)
    if errno == 1205:
        return True

    msg = str(orig or exc).lower()
    args = getattr(orig, "args", ())
    return (
        "lock wait timeout" in msg
        or "lock wait timeout" in " ".join(str(arg).lower() for arg in args)
        or any(arg == 1205 or str(arg) == "1205" for arg in args)
    )


class OrmDistributedLockRepository(OrmConnectionMixin, DistributedLockRepository):
    """ORM-based distributed lock repository using SQLAlchemy."""

    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def get_by_lock_name(self, lock_name: str) -> LockRecord | None:
        """Get lock record (read-only, no FOR UPDATE)."""
        log.info("get_by_lock_name: lock_name=%s", lock_name)
        row = (
            self._session.query(DistributedLockModel)
            .filter(DistributedLockModel.lock_name == lock_name)
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[distributed-lock:get_by_lock_name] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def update_expire_time(
        self,
        *,
        lock_name: str,
        expire_time: datetime,
    ) -> int:
        log.info("update_expire_time: lock_name=%s", lock_name)
        """Update lock expire time (for renewal)."""
        from sqlalchemy import func

        result = (
            self._session.query(DistributedLockModel)
            .filter(DistributedLockModel.lock_name == lock_name)
            .update(
                {
                    "expire_time": expire_time,
                    "gmt_modified": func.now(),
                },
                synchronize_session=False,
            )
        )
        result = int(result)
        log.info("[distributed-lock:update_expire_time] result: %s rows", result)
        return result

    @with_orm_session
    def delete_lock(self, lock_name: str) -> bool:
        log.info("delete_lock: lock_name=%s", lock_name)
        """Delete a lock record."""
        result = (
            self._session.query(DistributedLockModel)
            .filter(DistributedLockModel.lock_name == lock_name)
            .delete(synchronize_session=False)
        )
        result = int(result) > 0
        log.info("[distributed-lock:delete_lock] result: %s", result)
        return result

    def _build_acquire_upsert(self, lock_name, lock_holder, expire_time, env, now):
        """Build the dialect-specific atomic acquire upsert for ``ac_lock_table``.

        Mirrors the ``arca_ttl`` dual-dialect upsert convention
        (``mysql.dialects.insert().on_duplicate_key_update()`` /
        ``sqlite.dialects.insert().on_conflict_do_update()``). A row is
        treated as acquirable when the existing holder is the same caller,
        the row has no ``expire_time``, or the row has already expired; in
        those cases the holder / expiry / env are overwritten, otherwise the
        upsert is a no-op that leaves the current holder untouched. Because a
        dialect upsert does not apply ``Column.onupdate`` (arca_ttl Pitfall 2),
        ``gmt_modified`` is set explicitly and only refreshed when the row is
        actually acquired.

        The expiry check compares ``expire_time`` against a caller-supplied
        app-clock ``now`` rather than the DB ``NOW()``: ``expire_time`` is
        written in the application's wall-clock domain, and SQLite
        ``CURRENT_TIMESTAMP`` is UTC, so a DB-side comparison would misclassify
        unexpired rows as expired (and vice versa) against locally-stamped
        rows — the same clock-domain rationale arca_ttl documents for its
        caller-supplied ``now`` gate.
        """
        from sqlalchemy.dialects.mysql import insert as mysql_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        is_sqlite = self._session.bind.dialect.name == "sqlite"
        values = {
            "lock_name": lock_name,
            "lock_holder": lock_holder,
            "expire_time": expire_time,
            "env": env,
        }

        if is_sqlite:
            stmt = sqlite_insert(DistributedLockModel).values(**values)
            acquirable = or_(
                DistributedLockModel.lock_holder == stmt.excluded.lock_holder,
                DistributedLockModel.expire_time.is_(None),
                DistributedLockModel.expire_time <= now,
            )
            return stmt.on_conflict_do_update(
                index_elements=["lock_name"],
                set_={
                    "lock_holder": stmt.excluded.lock_holder,
                    "expire_time": stmt.excluded.expire_time,
                    "env": stmt.excluded.env,
                    "gmt_modified": func.now(),
                },
                where=acquirable,
            )

        stmt = mysql_insert(DistributedLockModel).values(**values)
        acquirable = or_(
            DistributedLockModel.lock_holder == stmt.inserted.lock_holder,
            DistributedLockModel.expire_time.is_(None),
            DistributedLockModel.expire_time <= now,
        )
        return stmt.on_duplicate_key_update(
            lock_holder=case(
                (acquirable, stmt.inserted.lock_holder),
                else_=DistributedLockModel.lock_holder,
            ),
            expire_time=case(
                (acquirable, stmt.inserted.expire_time),
                else_=DistributedLockModel.expire_time,
            ),
            env=case(
                (acquirable, stmt.inserted.env),
                else_=DistributedLockModel.env,
            ),
            gmt_modified=case(
                (acquirable, func.now()),
                else_=DistributedLockModel.gmt_modified,
            ),
        )

    @with_orm_session
    def try_acquire_lock(
        self,
        *,
        lock_name: str,
        lock_holder: str,
        expire_time: datetime,
    ) -> bool:
        """Atomically try to acquire a lock with a single upsert.

        Issues one atomic upsert keyed on ``uk_lock_name``: a missing row is
        initialized by INSERT, while an existing row is overwritten only when
        it is acquirable (same holder, no ``expire_time``, or expired) and left
        untouched otherwise. A confirming read SELECT then decides whether the
        caller now holds the lock.

        Replacing the former ``UPDATE → SELECT → INSERT`` three-step flow with
        a single upsert removes the concurrent INSERT that produced the
        underlying ``uk_lock_name`` 1062 conflict. OceanBase/MySQL-compatible
        lock wait timeouts (1205) remain normal lock contention for this
        try-acquire API and are still returned as ``False``.
        """
        from sqlalchemy.exc import DatabaseError as SADatabaseError

        from secbaas.community.core.utils.env_utils import get_current_env

        log.info(
            "try_acquire_lock: lock_name=%s, lock_holder=%s",
            lock_name,
            lock_holder,
        )
        now = datetime.now()
        env = get_current_env()

        try:
            self._session.execute(
                self._build_acquire_upsert(
                    lock_name, lock_holder, expire_time, env, now
                )
            )

            row = self._session.execute(
                select(DistributedLockModel).where(
                    DistributedLockModel.lock_name == lock_name
                )
            ).scalar_one_or_none()

            if row is None:
                log.warning(
                    "[distributed-lock:try_acquire_lock] upsert left no row: lock_name=%s",
                    lock_name,
                )
                return False

            if row.lock_holder == lock_holder:
                log.info(
                    "[distributed-lock:try_acquire_lock] acquired: lock_name=%s",
                    lock_name,
                )
                return True

            log.info(
                "[distributed-lock:try_acquire_lock] lock held by %s, not acquired",
                row.lock_holder,
            )
            return False

        except SADatabaseError as exc:
            if _is_lock_wait_timeout(exc):
                self._session.rollback()
                log.warning(
                    "[distributed-lock:try_acquire_lock] lock wait timeout, treated as contention: lock_name=%s",
                    lock_name,
                )
                return False
            raise
