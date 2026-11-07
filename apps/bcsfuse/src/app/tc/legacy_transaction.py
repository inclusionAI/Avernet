"""One TC legacy mutation commits Worker, Profile and runtime state together."""

from contextlib import contextmanager
from threading import local


class _BorrowedConnection:
    def __init__(self, connection):
        self.connection = connection
        self.failed = False

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def start_transaction(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        self.failed = True

    def close(self):
        pass


class LegacyMutationPool:
    """Borrow the existing SQL pool; scope legacy stores to one transaction/thread."""

    def __init__(self, pool):
        self.pool = pool
        self.current = local()

    def get_connection(self):
        return getattr(self.current, "connection", None) or self.pool.get_connection()

    def close(self):
        pass  # The host owns the underlying pool.

    @contextmanager
    def transaction(self, worker_id=None):
        connection = self.pool.get_connection()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("BEGIN")
                if worker_id is not None:
                    # Serialize index publication with source mutations in other TC
                    # processes. Embedding happens before acquiring this row lock.
                    cursor.execute(
                        "SELECT id FROM bcsfuse_workers WHERE id = %s FOR UPDATE",
                        (worker_id,),
                    )
                    cursor.fetchall()
            finally:
                cursor.close()
            borrowed = _BorrowedConnection(connection)
            self.current.connection = borrowed
            yield
            if borrowed.failed:
                raise RuntimeError("legacy mutation was rolled back")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self.current.connection = None
            connection.close()
