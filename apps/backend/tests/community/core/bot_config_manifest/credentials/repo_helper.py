"""In-memory SQLite fixture helpers for credential tests (shared shape)."""
from __future__ import annotations

from contextlib import contextmanager


class InMemorySqliteDB:
    """https://pytest fixtures/… same shape as the startup-script repo tests."""

    def __init__(self, engine):
        self._engine = engine
        from sqlalchemy.orm import sessionmaker

        self._session_factory = sessionmaker(bind=self._engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
