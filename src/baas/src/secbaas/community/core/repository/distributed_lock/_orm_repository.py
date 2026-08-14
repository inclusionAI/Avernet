"""ORM-backed distributed lock repository implementation.

Uses @with_orm_session decorator for SQLAlchemy ORM Session lifecycle.
Corresponds to ZdasDistributedLockRepository (ac_lock_table).
"""

from datetime import datetime

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import DistributedLockModel
from ._protocol import DistributedLockRepository
from ._record import LockRecord

log = get_logger("orm-repository")


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

    @with_orm_session
    def try_acquire_lock(
        self,
        *,
        lock_name: str,
        lock_holder: str,
        expire_time: datetime,
    ) -> bool:
        """Atomically try to acquire a lock in a single session.

        Prefer conditional UPDATE over DELETE + INSERT. Existing lock rows are
        treated as stable state: an expired row, a row with no expire_time, or
        a row already held by the same holder can be acquired by updating its
        holder and expiry. A missing row is initialized by INSERT.

        Concurrent INSERT conflicts and OceanBase/MySQL-compatible lock wait
        timeouts are normal lock contention for this try-acquire API and are
        returned as ``False``.
        """
        from sqlalchemy import func, or_
        from sqlalchemy.exc import DatabaseError as SADatabaseError
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        from secbaas.community.core.utils.env_utils import get_current_env

        log.info(
            "try_acquire_lock: lock_name=%s, lock_holder=%s",
            lock_name,
            lock_holder,
        )
        now = datetime.now()
        env = get_current_env()

        try:
            updated = (
                self._session.query(DistributedLockModel)
                .filter(
                    DistributedLockModel.lock_name == lock_name,
                    or_(
                        DistributedLockModel.lock_holder == lock_holder,
                        DistributedLockModel.expire_time.is_(None),
                        DistributedLockModel.expire_time <= now,
                    ),
                )
                .update(
                    {
                        "lock_holder": lock_holder,
                        "expire_time": expire_time,
                        "gmt_modified": func.now(),
                        "env": env,
                    },
                    synchronize_session=False,
                )
            )

            if int(updated) > 0:
                log.info(
                    "[distributed-lock:try_acquire_lock] acquired by update: lock_name=%s",
                    lock_name,
                )
                return True

            row = (
                self._session.query(DistributedLockModel)
                .filter(DistributedLockModel.lock_name == lock_name)
                .first()
            )
            if row is not None:
                if row.lock_holder == lock_holder:
                    log.info(
                        "[distributed-lock:try_acquire_lock] reentrant already held: lock_name=%s",
                        lock_name,
                    )
                    return True

                log.info(
                    "[distributed-lock:try_acquire_lock] lock held by %s, not acquired",
                    row.lock_holder,
                )
                return False

            new_row = DistributedLockModel(
                lock_name=lock_name,
                lock_holder=lock_holder,
                expire_time=expire_time,
                env=env,
            )
            self._session.add(new_row)
            self._session.flush()

            log.info(
                "[distributed-lock:try_acquire_lock] acquired by insert: lock_name=%s, id=%s",
                lock_name,
                new_row.id,
            )
            return True

        except SAIntegrityError:
            self._session.rollback()
            log.warning(
                "[distributed-lock:try_acquire_lock] concurrent INSERT conflict: lock_name=%s",
                lock_name,
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
