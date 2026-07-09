"""Integration-level tests for CleanupService against a real in-memory SQLite DB.

These complement tests/utils/test_cleanup_utils.py (which uses mocks) by
exercising the actual SQL branches of _cleanup_skill_sets_sqlite,
_delete_by_bolt_ids_sqlite, _cleanup_single_user, _bot_row_to_dict, and
the physical-file cleanup path.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from agentclaw.community.utils.cleanup_utils import CleanupResult, CleanupService


class InMemorySqliteDB:
    """DatabasePlugin-compatible wrapper around a single in-memory SQLite engine."""

    def __init__(self):
        self._engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}
        )
        self._factory = sessionmaker(autocommit=False, autoflush=False, bind=self._engine)
        self._init_tables()

    def _init_tables(self):
        # Use the canonical ORM metadata so the schema matches what
        # CleanupService's ORM queries expect (column lists, types).
        # Import the ORM modules to register their classes on Base.metadata.
        from agentclaw.community.core.base import Base
        import agentclaw.community.plugin_api.models  # noqa: F401  ac_bots / ac_resource
        import agentclaw.community.core.models.skill  # noqa: F401  ac_skill* / ac_skill_set_skill
        Base.metadata.create_all(self._engine)

    @contextmanager
    def session(self):
        s = self._factory()
        try:
            yield s
        finally:
            s.close()

    @contextmanager
    def orm_session(self):
        # Mirror SqliteDB.orm_session: commit on clean exit, rollback on error.
        s = self._factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SERVER_ENV", raising=False)
    return InMemorySqliteDB()


def _seed_deleted_bot(db: InMemorySqliteDB, bot_id: str, owner_id: str, env: str):
    from agentclaw.community.plugin_api.models import BotModel, ResourceModel
    from agentclaw.community.core.models.skill import Skill, SkillSet

    with db.orm_session() as s:
        s.add(BotModel(
            bot_id=bot_id, bot_name="name", entity_id="entity1",
            entity_type="staff", creator_id="u", owner_id=owner_id,
            device_id="d", env=env, is_delete=1,
        ))
        s.add(Skill(name=bot_id, bolt_id=bot_id, env=env))
        s.add(SkillSet(name=bot_id, bolt_id=bot_id, env=env))
        s.add(ResourceModel(name=bot_id, resource_type="link", bolt_id=bot_id, env=env))


class TestGetDeletedBots:
    def test_no_users_returns_empty(self, db):
        svc = CleanupService(db=db)
        assert svc.get_deleted_bots_by_users([]) == {}

    def test_returns_deleted_bots(self, db):
        _seed_deleted_bot(db, "bot1", "user1", "dev")
        svc = CleanupService(db=db)
        result = svc.get_deleted_bots_by_users(["user1"], env="dev")
        assert "user1" in result
        assert len(result["user1"]) == 1
        assert result["user1"][0]["bot_id"] == "bot1"

    def test_filters_by_env(self, db):
        _seed_deleted_bot(db, "bot1", "user1", "dev")
        svc = CleanupService(db=db)
        result = svc.get_deleted_bots_by_users(["user1"], env="prod")
        assert result == {}

    def test_users_with_no_deleted_bots_excluded(self, db):
        svc = CleanupService(db=db)
        assert svc.get_deleted_bots_by_users(["nonexistent"]) == {}


class TestCleanupSingleBot:
    def test_dry_run_leaves_data(self, db):
        _seed_deleted_bot(db, "bot1", "user1", "dev")
        svc = CleanupService(db=db)
        result = svc.cleanup_single_bot_data("bot1", "user1", dry_run=True)
        assert result["skills_deleted"] == 1
        assert result["skill_sets_deleted"] == 1
        assert result["resources_deleted"] == 1
        assert result["errors"] == []
        # Verify not actually deleted
        with db.session() as s:
            assert s.execute(text("SELECT COUNT(*) FROM ac_skill")).scalar() == 1

    def test_wet_run_deletes(self, db):
        _seed_deleted_bot(db, "bot1", "user1", "dev")
        svc = CleanupService(db=db)
        result = svc.cleanup_single_bot_data("bot1", "user1", dry_run=False)
        assert result["skills_deleted"] == 1
        with db.session() as s:
            assert s.execute(text("SELECT COUNT(*) FROM ac_skill")).scalar() == 0
            assert s.execute(text("SELECT COUNT(*) FROM ac_skill_set")).scalar() == 0
            assert s.execute(text("SELECT COUNT(*) FROM ac_resource")).scalar() == 0


class TestCleanupUserData:
    def test_no_deleted_bots(self, db):
        svc = CleanupService(db=db)
        results = svc.cleanup_user_data(["user1"], dry_run=True)
        assert len(results) == 1
        assert isinstance(results[0], CleanupResult)
        assert results[0].deleted_bots == []

    def test_with_deleted_bots(self, db):
        _seed_deleted_bot(db, "bot1", "user1", "dev")
        _seed_deleted_bot(db, "bot2", "user1", "dev")
        svc = CleanupService(db=db)
        results = svc.cleanup_user_data(["user1"], dry_run=True, env="dev")
        assert len(results) == 1
        r = results[0]
        assert r.user_id == "user1"
        assert len(r.deleted_bots) == 2
        assert r.skills_deleted == 2
        assert r.skill_sets_deleted == 2
        assert r.resources_deleted == 2

    def test_physical_file_cleanup_nonexistent_dir(self, db, tmp_path: Path):
        _seed_deleted_bot(db, "bot1", "user1", "dev")
        svc = CleanupService(db=db)
        results = svc.cleanup_user_data(
            ["user1"],
            dry_run=False,
            include_files=True,
            env="dev",
            bolt_data_root=tmp_path,
        )
        # Dir doesn't exist, so 0 files deleted (no error)
        assert results[0].files_deleted == 0

    def test_physical_file_cleanup_existing_dir(self, db, tmp_path: Path):
        _seed_deleted_bot(db, "bot1", "user1", "dev")
        bot_dir = tmp_path / "staff_entity1" / "bot1"
        bot_dir.mkdir(parents=True)
        (bot_dir / "data.txt").write_text("x")

        svc = CleanupService(db=db)
        results = svc.cleanup_user_data(
            ["user1"],
            dry_run=False,
            include_files=True,
            env="dev",
            bolt_data_root=tmp_path,
        )
        assert results[0].files_deleted == 1
        assert not bot_dir.exists()

    def test_physical_file_cleanup_dry_run(self, db, tmp_path: Path):
        _seed_deleted_bot(db, "bot1", "user1", "dev")
        bot_dir = tmp_path / "staff_entity1" / "bot1"
        bot_dir.mkdir(parents=True)

        svc = CleanupService(db=db)
        svc.cleanup_user_data(
            ["user1"],
            dry_run=True,
            include_files=True,
            env="dev",
            bolt_data_root=tmp_path,
        )
        # dry_run - not deleted
        assert bot_dir.exists()


class TestResultSerialization:
    def test_cleanup_result_to_dict(self):
        r = CleanupResult(
            user_id="u", dry_run=True,
            deleted_bots=[{"bot_id": "b1"}, {"bot_id": "b2"}],
            skills_deleted=5,
        )
        d = r.to_dict()
        assert d["user_id"] == "u"
        assert d["deleted_bots_count"] == 2
        assert d["deleted_bots"] == ["b1", "b2"]
        assert d["skills_deleted"] == 5
        assert d["dry_run"] is True


class TestLegacyTableDelete:
    def test_tolerant_delete_legacy_missing_table_swallowed(self, db):
        with db.orm_session() as s:
            # Should not raise — table doesn't exist, error swallowed.
            CleanupService._tolerant_delete_legacy(
                s, "ac_nonexistent_table", "id", [1, 2],
            )

    def test_tolerant_delete_legacy_other_error_reraised(self, db):
        class BoomSession:
            def execute(self, *args, **kwargs):
                raise RuntimeError("some other error")

        with pytest.raises(RuntimeError):
            CleanupService._tolerant_delete_legacy(
                BoomSession(), "table", "id", [1],
            )


class TestBotToDict:
    def test_bot_to_dict_with_datetime(self):
        import datetime

        class _Bot:
            id = 1
            bot_id = "b"
            bot_name = "n"
            entity_id = "e"
            entity_type = "t"
            creator_id = "c"
            owner_id = "o"
            device_id = "d"
            env = "env"
            gmt_create = datetime.datetime(2024, 1, 1, 12, 0, 0)
        d = CleanupService._bot_to_dict(_Bot())
        assert d["gmt_create"] == "2024-01-01T12:00:00"
        assert d["bot_id"] == "b"

    def test_bot_to_dict_with_string_date(self):
        class _Bot:
            id = 1
            bot_id = "b"
            bot_name = "n"
            entity_id = "e"
            entity_type = "t"
            creator_id = "c"
            owner_id = "o"
            device_id = "d"
            env = "env"
            gmt_create = "2024-01-01"
        d = CleanupService._bot_to_dict(_Bot())
        assert d["gmt_create"] == "2024-01-01"


class TestEmptyInputs:
    def test_cleanup_skills_empty(self, db):
        svc = CleanupService(db=db)
        assert svc._cleanup_skills([], dry_run=True) == 0

    def test_cleanup_skill_sets_empty(self, db):
        svc = CleanupService(db=db)
        assert svc._cleanup_skill_sets([], dry_run=True) == 0

    def test_cleanup_resources_empty(self, db):
        svc = CleanupService(db=db)
        assert svc._cleanup_resources([], dry_run=True) == 0
