"""Mock-friendly tests for utils/cleanup_utils.py.

The DB-touching paths are covered end-to-end by test_cleanup_utils_sqlite.py
against a real in-memory SQLite + the ORM models (same code path as prod
via DatabasePlugin.orm_session). What remains here is the dialect-free
surface area:

- CleanupResult dataclass
- Physical-file cleanup (filesystem only, no DB)
"""

from unittest.mock import MagicMock

from agentclaw.community.utils.cleanup_utils import CleanupResult, CleanupService


# ---------------------------------------------------------------------------
# Tests: CleanupResult
# ---------------------------------------------------------------------------


class TestCleanupResult:
    def test_to_dict_defaults(self):
        r = CleanupResult(user_id="u1")
        d = r.to_dict()
        assert d["user_id"] == "u1"
        assert d["deleted_bots_count"] == 0
        assert d["skills_deleted"] == 0
        assert d["dry_run"] is True

    def test_to_dict_with_data(self):
        r = CleanupResult(
            user_id="u2",
            deleted_bots=[{"bot_id": "b1"}, {"bot_id": "b2"}],
            skills_deleted=5,
            skill_sets_deleted=2,
            resources_deleted=1,
            files_deleted=3,
            errors=["err1"],
            dry_run=False,
        )
        d = r.to_dict()
        assert d["deleted_bots_count"] == 2
        assert d["deleted_bots"] == ["b1", "b2"]
        assert d["skills_deleted"] == 5
        assert d["dry_run"] is False


# ---------------------------------------------------------------------------
# Tests: 物理文件清理
# ---------------------------------------------------------------------------


class TestCleanupPhysicalFiles:
    def test_cleanup_files_dry_run(self, tmp_path):
        bot_dir = tmp_path / "staff_123" / "bot1"
        bot_dir.mkdir(parents=True)
        (bot_dir / "data.json").write_text("{}")

        svc = CleanupService(db=MagicMock())
        count = svc._cleanup_physical_files(
            [{"entity_type": "staff", "entity_id": "123", "bot_id": "bot1"}],
            dry_run=True,
            bolt_data_root=tmp_path,
        )
        assert count == 0
        assert bot_dir.exists()  # dry_run does not delete

    def test_cleanup_files_executes(self, tmp_path):
        bot_dir = tmp_path / "staff_123" / "bot1"
        bot_dir.mkdir(parents=True)
        (bot_dir / "data.json").write_text("{}")

        svc = CleanupService(db=MagicMock())
        count = svc._cleanup_physical_files(
            [{"entity_type": "staff", "entity_id": "123", "bot_id": "bot1"}],
            dry_run=False,
            bolt_data_root=tmp_path,
        )
        assert count == 1
        assert not bot_dir.exists()

    def test_cleanup_files_missing_dir(self, tmp_path):
        svc = CleanupService(db=MagicMock())
        count = svc._cleanup_physical_files(
            [{"entity_type": "staff", "entity_id": "999", "bot_id": "nonexistent"}],
            dry_run=False,
            bolt_data_root=tmp_path,
        )
        assert count == 0

    def test_cleanup_files_skips_empty_ids(self, tmp_path):
        svc = CleanupService(db=MagicMock())
        count = svc._cleanup_physical_files(
            [{"entity_type": "staff", "entity_id": "", "bot_id": ""}],
            dry_run=False,
            bolt_data_root=tmp_path,
        )
        assert count == 0
