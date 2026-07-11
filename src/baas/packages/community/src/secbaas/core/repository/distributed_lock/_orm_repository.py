"""ORM-backed distributed lock repository implementation.

Uses @with_orm_session decorator for SQLAlchemy ORM Session lifecycle.
Corresponds to ZdasDistributedLockRepository (ac_lock_table).
"""

from datetime import datetime

from secbaas.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.logger import get_logger

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
    def get_by_lock_name_for_update(self, lock_name: str) -> LockRecord | None:
        """Get lock record with FOR UPDATE for distributed locking."""
        log.info("get_by_lock_name_for_update: lock_name=%s", lock_name)
        row = (
            self._session.query(DistributedLockModel)
            .filter(DistributedLockModel.lock_name == lock_name)
            .with_for_update()
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[distributed-lock:get_by_lock_name_for_update] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def insert_lock(
        self,
        *,
        lock_name: str,
        lock_holder: str,
        expire_time: datetime,
    ) -> int:
        log.info("insert_lock: lock_name=%s, lock_holder=%s", lock_name, lock_holder)
        """Insert a new lock record."""
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        from secbaas.core.utils.env_utils import get_current_env

        row = DistributedLockModel(
            lock_name=lock_name,
            lock_holder=lock_holder,
            expire_time=expire_time,
            env=get_current_env(),
        )
        self._session.add(row)
        try:
            self._session.flush()
        except SAIntegrityError:
            self._session.rollback()
            log.warning(
                "[distributed-lock:insert_lock] key violation (lock already exists): lock_name=%s",
                lock_name,
            )
            return 0
        result = int(row.id)
        log.info("[distributed-lock:insert_lock] result: id=%s", result)
        return result

    @with_orm_session
    def update_lock_holder(
        self,
        *,
        lock_name: str,
        lock_holder: str,
        expire_time: datetime,
    ) -> int:
        log.info(
            "update_lock_holder: lock_name=%s, lock_holder=%s", lock_name, lock_holder
        )
        """Update lock holder and expire time."""
        from sqlalchemy import func

        result = (
            self._session.query(DistributedLockModel)
            .filter(DistributedLockModel.lock_name == lock_name)
            .update(
                {
                    "lock_holder": lock_holder,
                    "expire_time": expire_time,
                    "gmt_modified": func.now(),
                },
                synchronize_session=False,
            )
        )
        result = int(result)
        log.info("[distributed-lock:update_lock_holder] result: %s rows", result)
        return result

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
    def delete_expired_locks(self, current_time: datetime) -> int:
        log.info("delete_expired_locks: current_time=%s", current_time)
        """Delete all expired lock records."""
        result = (
            self._session.query(DistributedLockModel)
            .filter(DistributedLockModel.expire_time < current_time)
            .delete(synchronize_session=False)
        )
        result = int(result)
        log.info("[distributed-lock:delete_expired_locks] result: %s rows", result)
        return result
