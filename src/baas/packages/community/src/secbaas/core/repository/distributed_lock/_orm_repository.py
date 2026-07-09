"""ORM-backed distributed lock repository implementation.

Uses @with_orm_session decorator for SQLAlchemy ORM Session lifecycle.
Corresponds to ZdasDistributedLockRepository (ac_lock_table).
"""

from datetime import datetime

from sqlalchemy import text

from secbaas.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.logger import get_logger

from ._orm_model import DistributedLockModel
from ._protocol import DistributedLockRepository
from ._record import LockRecord

log = get_logger("orm-repository")

# MySQL / OceanBase — INSERT ... ON DUPLICATE KEY UPDATE
# Uses :current_time from Python for clock consistency.
# Update condition: expired OR same holder (renewal) — avoids unnecessary
# writes when the lock is held by a different, non-expired holder.
_UPSERT_SQL_MYSQL = text(
    """
    INSERT INTO ac_lock_table (gmt_create, gmt_modified, lock_name, lock_holder, expire_time, env)
    VALUES (:current_time, :current_time, :lock_name, :lock_holder, :expire_time, :env)
    ON DUPLICATE KEY UPDATE
      gmt_modified = IF(expire_time IS NULL OR expire_time < :current_time OR lock_holder = VALUES(lock_holder), :current_time, gmt_modified),
      lock_holder  = IF(expire_time IS NULL OR expire_time < :current_time OR lock_holder = VALUES(lock_holder), VALUES(lock_holder), lock_holder),
      expire_time  = IF(expire_time IS NULL OR expire_time < :current_time OR lock_holder = VALUES(lock_holder), VALUES(expire_time), expire_time),
      env          = IF(expire_time IS NULL OR expire_time < :current_time OR lock_holder = VALUES(lock_holder), VALUES(env), env)
    """
)

# SQLite — INSERT ... ON CONFLICT DO UPDATE
# Uses :current_time from Python for clock consistency.
_UPSERT_SQL_SQLITE = text(
    """
    INSERT INTO ac_lock_table (gmt_create, gmt_modified, lock_name, lock_holder, expire_time, env)
    VALUES (:current_time, :current_time, :lock_name, :lock_holder, :expire_time, :env)
    ON CONFLICT(lock_name) DO UPDATE SET
      gmt_modified = CASE WHEN expire_time IS NULL OR expire_time < :current_time OR lock_holder = excluded.lock_holder
                          THEN :current_time ELSE gmt_modified END,
      lock_holder  = CASE WHEN expire_time IS NULL OR expire_time < :current_time OR lock_holder = excluded.lock_holder
                          THEN excluded.lock_holder ELSE lock_holder END,
      expire_time  = CASE WHEN expire_time IS NULL OR expire_time < :current_time OR lock_holder = excluded.lock_holder
                          THEN excluded.expire_time ELSE expire_time END,
      env          = CASE WHEN expire_time IS NULL OR expire_time < :current_time OR lock_holder = excluded.lock_holder
                          THEN excluded.env ELSE env END
    """
)

_UPSERT_RESULT_SQL = text(
    """
    SELECT lock_holder, expire_time FROM ac_lock_table WHERE lock_name = :lock_name
    """
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
    def try_acquire(
        self,
        *,
        lock_name: str,
        lock_holder: str,
        expire_time: datetime,
        env: str | None = None,
        current_time: datetime | None = None,
    ) -> bool:
        """Atomic try-acquire via INSERT ... ON DUPLICATE KEY UPDATE.

        Returns True if the lock was acquired (new insert, expired-lock
        takeover, or same-holder renewal), False if the lock is held
        by someone else.
        """
        if current_time is None:
            current_time = datetime.now()
        log.info("try_acquire: lock_name=%s, lock_holder=%s", lock_name, lock_holder)
        dialect = self._session.bind.dialect.name
        upsert_sql = _UPSERT_SQL_SQLITE if dialect == "sqlite" else _UPSERT_SQL_MYSQL
        self._session.execute(
            upsert_sql,
            {
                "current_time": current_time,
                "lock_name": lock_name,
                "lock_holder": lock_holder,
                "expire_time": expire_time,
                "env": env,
            },
        )
        self._session.flush()

        row = self._session.execute(
            _UPSERT_RESULT_SQL, {"lock_name": lock_name}
        ).fetchone()

        acquired = row is not None and row.lock_holder == lock_holder
        log.info(
            "[distributed-lock:try_acquire] lock_name=%s, acquired=%s",
            lock_name,
            acquired,
        )
        return acquired

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
