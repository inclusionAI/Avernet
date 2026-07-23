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

        SELECT → if absent/expired → DELETE expired → INSERT.
        If INSERT hits unique constraint, return False (concurrent race).
        """
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        from secbaas.community.core.utils.env_utils import get_current_env

        log.info(
            "try_acquire_lock: lock_name=%s, lock_holder=%s",
            lock_name,
            lock_holder,
        )
        now = datetime.now()

        row = (
            self._session.query(DistributedLockModel)
            .filter(DistributedLockModel.lock_name == lock_name)
            .first()
        )

        if row is not None and row.lock_holder == lock_holder:
            row.expire_time = expire_time
            self._session.flush()
            log.info(
                "[distributed-lock:try_acquire_lock] reentrant renew: lock_name=%s",
                lock_name,
            )
            return True

        if row is not None and row.expire_time is not None and row.expire_time > now:
            log.info(
                "[distributed-lock:try_acquire_lock] lock held by %s, not acquired",
                row.lock_holder,
            )
            return False

        if row is not None:
            self._session.delete(row)
            self._session.flush()

        new_row = DistributedLockModel(
            lock_name=lock_name,
            lock_holder=lock_holder,
            expire_time=expire_time,
            env=get_current_env(),
        )
        self._session.add(new_row)
        try:
            self._session.flush()
        except SAIntegrityError:
            self._session.rollback()
            log.warning(
                "[distributed-lock:try_acquire_lock] concurrent INSERT conflict: lock_name=%s",
                lock_name,
            )
            return False

        log.info(
            "[distributed-lock:try_acquire_lock] acquired: lock_name=%s, id=%s",
            lock_name,
            new_row.id,
        )
        return True
