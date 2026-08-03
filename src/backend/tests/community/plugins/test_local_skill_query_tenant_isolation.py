"""#722 repository seam: Local Skill reads use the real Track A tenant guard."""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.models.skill import Skill
from agentclaw.community.plugins.local.sqlite_models import (
    DefaultSkillsetSkillExclusion,
)
from agentclaw.community.plugins.skill_repository import SkillRepository
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class _Database:
    def __init__(self, engine) -> None:
        self._session = sessionmaker(bind=engine)

    @contextmanager
    def orm_session(self):
        session = self._session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def test_exact_local_skill_query_is_tenant_scoped_and_reports_desired_state(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'skills.db'}")
    Skill.__table__.create(engine)
    DefaultSkillsetSkillExclusion.__table__.create(engine)
    repo = SkillRepository(_Database(engine))

    with avernet_tenant_scope("tenant-a"):
        created = repo.create(
            {
                "name": "forecast",
                "description": "Daily Forecast",
                "git_path": "local://forecast",
                "user_id": "owner",
                "bolt_id": "bot",
                "tags": ["weather"],
            }
        )
        repo.create(
            {
                "name": "market",
                "git_path": "git://market",
                "user_id": "owner",
                "bolt_id": "bot",
            }
        )
        total, rows = repo.list_bot_local_skills(
            bot_id="bot",
            user_id="owner",
            page=1,
            page_size=20,
            active=None,
            keyword="FORE",
        )
        assert total == 1
        assert rows[0]["id"] == created["id"]
        assert rows[0]["active"] is True

    with avernet_tenant_scope("tenant-b"):
        total, rows = repo.list_bot_local_skills(
            bot_id="bot",
            user_id="owner",
            page=1,
            page_size=20,
            active=None,
            keyword=None,
        )
        assert total == 0 and rows == []
        assert (
            repo.get_bot_local_skill(
                skill_id=created["id"], bot_id="bot", user_id="owner"
            )
            is None
        )
