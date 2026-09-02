"""Fakes shared by the managed-files tests (W8)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.bot_config_manifest.repository.managed_files_models import (  # noqa: F401
    BotConfigManagedFileModel,
)
from agentclaw.community.core.repository.implementations.bot.managed_files import (
    BotConfigManagedFilesRepository,
)


class FakeObjectStorage:
    """A dict-backed ``ObjectStoragePlugin``: enough for put/get/delete/list."""

    def __init__(self, *, fail_puts: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.deletes: list[str] = []
        self.fail_puts = fail_puts

    def put_object(self, key: str, content) -> bool:
        if self.fail_puts:
            return False
        self.puts.append(key)
        self.objects[key] = content if isinstance(content, bytes) else content.encode()
        return True

    def get_object(self, key: str) -> Optional[bytes]:
        return self.objects.get(key)

    def delete_object(self, key: str) -> bool:
        self.deletes.append(key)
        return self.objects.pop(key, None) is not None

    def list_objects(self, prefix: str, max_keys: int = 1000) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))[:max_keys]


class _Db:
    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def sqlite_repository() -> BotConfigManagedFilesRepository:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return BotConfigManagedFilesRepository(_Db(engine))
