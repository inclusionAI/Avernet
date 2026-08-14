"""Tests for agentclaw.community.core.skill_center.utils.skill_metadata_writer."""
import json
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_writer(tmp_path, user_id=None, skill_set_repo=None, skill_repo=None):
    """Construct a SkillSetMetadataWriter with skills_dir pointing to tmp_path."""
    from unittest.mock import MagicMock
    from agentclaw.community.core.skill_center.utils.skill_metadata_writer import SkillSetMetadataWriter

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    writer = SkillSetMetadataWriter(
        skill_set_repo=skill_set_repo or MagicMock(),
        skill_repo=skill_repo or MagicMock(),
        skills_dir=skills_dir,
        user_id=user_id,
    )
    return writer


# ---------------------------------------------------------------------------
# SkillSetMetadataWriter.__init__ paths
# ---------------------------------------------------------------------------

class TestSkillSetMetadataWriterInit:
    def test_explicit_skills_dir_used_directly(self, tmp_path):
        from agentclaw.community.core.skill_center.utils.skill_metadata_writer import SkillSetMetadataWriter

        skills_dir = tmp_path / "custom_skills"
        skills_dir.mkdir()
        writer = SkillSetMetadataWriter(skill_set_repo=MagicMock(), skill_repo=MagicMock(), skills_dir=skills_dir)

        assert writer.skills_dir == skills_dir
        assert writer.METADATA_FILE == skills_dir / "skill_sets.json"

    def test_default_path_when_no_args(self, tmp_path):
        """When no skills_dir provided, falls back to module SKILLS_DIR."""
        from agentclaw.community.core.skill_center.utils import skill_metadata_writer

        original = skill_metadata_writer.SKILLS_DIR
        try:
            skill_metadata_writer.SKILLS_DIR = tmp_path / "default_skills"
            (tmp_path / "default_skills").mkdir()
            writer = skill_metadata_writer.SkillSetMetadataWriter(skill_set_repo=MagicMock(), skill_repo=MagicMock())
            assert writer.skills_dir == tmp_path / "default_skills"
        finally:
            skill_metadata_writer.SKILLS_DIR = original


# ---------------------------------------------------------------------------
# _get_active_skill_set_ids
# ---------------------------------------------------------------------------

class TestGetActiveSkillSetIds:
    def test_returns_active_ids_from_db(self, tmp_path):
        writer = _make_writer(tmp_path)
        writer.skill_set_repo = MagicMock()
        writer.skill_set_repo.list_all.return_value = [
            {"id": "1", "is_active": 1},
            {"id": "2", "is_active": 0},
            {"id": "3", "is_active": 1},
        ]
        ids = writer._get_active_skill_set_ids()
        assert ids == {"1", "3"}

    def test_fallback_to_file_when_db_empty(self, tmp_path):
        writer = _make_writer(tmp_path)
        writer.skill_set_repo = MagicMock()
        writer.skill_set_repo.list_all.return_value = []  # no active sets

        current_set_file = writer.skills_dir / ".current_skill_set"
        current_set_file.write_text(json.dumps({"skill_set_id": "fallback-42"}))

        ids = writer._get_active_skill_set_ids()
        assert ids == {"fallback-42"}

    def test_fallback_to_empty_when_no_file(self, tmp_path):
        writer = _make_writer(tmp_path)
        writer.skill_set_repo = MagicMock()
        writer.skill_set_repo.list_all.return_value = []

        ids = writer._get_active_skill_set_ids()
        assert ids == set()

    def test_db_exception_falls_back_to_file(self, tmp_path):
        writer = _make_writer(tmp_path)
        writer.skill_set_repo = MagicMock()
        writer.skill_set_repo.list_all.side_effect = Exception("db down")

        current_set_file = writer.skills_dir / ".current_skill_set"
        current_set_file.write_text(json.dumps({"skill_set_id": "db-fallback"}))

        ids = writer._get_active_skill_set_ids()
        assert ids == {"db-fallback"}

    def test_file_exception_returns_empty(self, tmp_path):
        writer = _make_writer(tmp_path)
        writer.skill_set_repo = MagicMock()
        writer.skill_set_repo.list_all.return_value = []

        # Write invalid JSON to force file read exception
        current_set_file = writer.skills_dir / ".current_skill_set"
        current_set_file.write_bytes(b"\xff\xfe bad bytes")

        ids = writer._get_active_skill_set_ids()
        assert ids == set()


# ---------------------------------------------------------------------------
# _get_skill_dir_name
# ---------------------------------------------------------------------------

class TestGetSkillDirName:
    def test_git_path(self, tmp_path):
        writer = _make_writer(tmp_path)
        assert writer._get_skill_dir_name("git://category/sub/my-skill") == "my-skill"

    def test_local_path(self, tmp_path):
        writer = _make_writer(tmp_path)
        assert writer._get_skill_dir_name("local://my-local-skill") == "my-local-skill"

    def test_pool_local_path_uses_basename(self, tmp_path):
        writer = _make_writer(tmp_path)
        assert (
            writer._get_skill_dir_name(
                "local:///home/admin/.openclaw/workspace/"
                "skills-pool/skills-local/my-local-skill"
            )
            == "my-local-skill"
        )

    def test_bare_path_fallback(self, tmp_path):
        writer = _make_writer(tmp_path)
        assert writer._get_skill_dir_name("some/path/skill-name") == "skill-name"

    def test_none_returns_empty(self, tmp_path):
        writer = _make_writer(tmp_path)
        assert writer._get_skill_dir_name(None) == ""

    def test_empty_string_returns_empty(self, tmp_path):
        writer = _make_writer(tmp_path)
        assert writer._get_skill_dir_name("") == ""


# ---------------------------------------------------------------------------
# _get_skill_absolute_path
# ---------------------------------------------------------------------------

class TestGetSkillAbsolutePath:
    def test_git_path_joined_with_repo_dir(self, tmp_path):
        writer = _make_writer(tmp_path)
        result = writer._get_skill_absolute_path("git://cat/sub/skill-a")
        expected = str(tmp_path / "skills-repo" / "cat" / "sub" / "skill-a")
        assert result == expected

    def test_local_path_joined_with_local_dir(self, tmp_path):
        writer = _make_writer(tmp_path)
        result = writer._get_skill_absolute_path("local://my-local")
        expected = str(tmp_path / "skills-local" / "my-local")
        assert result == expected

    def test_pool_local_absolute_path_is_not_prefixed_again(self, tmp_path):
        writer = _make_writer(tmp_path)
        locator_path = (
            "/home/admin/.openclaw/workspace/"
            "skills-pool/skills-local/my-local"
        )
        assert writer._get_skill_absolute_path(f"local://{locator_path}") == (
            locator_path
        )

    def test_bare_path_falls_back_to_repo_dir(self, tmp_path):
        writer = _make_writer(tmp_path)
        result = writer._get_skill_absolute_path("bare/relative")
        assert result == str(tmp_path / "skills-repo" / "bare" / "relative")

    def test_none_returns_empty(self, tmp_path):
        writer = _make_writer(tmp_path)
        assert writer._get_skill_absolute_path(None) == ""

    def test_symlink_repo_dir_is_resolved(self, tmp_path):
        real_repo = tmp_path / "real_repo"
        real_repo.mkdir()
        # writer.skills_dir is tmp_path/skills; skills-repo is its sibling
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(exist_ok=True)
        symlink_target = tmp_path / "skills-repo"
        symlink_target.mkdir()

        from agentclaw.community.core.skill_center.utils.skill_metadata_writer import SkillSetMetadataWriter

        writer = SkillSetMetadataWriter(skill_set_repo=MagicMock(), skill_repo=MagicMock(), skills_dir=skills_dir)
        result = writer._get_skill_absolute_path("git://cat/skill-b")
        # Should not raise
        assert "skill-b" in result


# ---------------------------------------------------------------------------
# write_metadata
# ---------------------------------------------------------------------------

class TestWriteMetadata:
    def _mock_repo(self, skill_sets, skills_per_set=None):
        repo = MagicMock()
        repo.list_all.return_value = skill_sets
        if skills_per_set is not None:
            repo.get_skills_in_set.side_effect = lambda ss_id: skills_per_set.get(ss_id, [])
        else:
            repo.get_skills_in_set.return_value = []
        return repo

    def test_creates_metadata_file(self, tmp_path):
        writer = _make_writer(tmp_path)
        writer.skill_set_repo = self._mock_repo(
            [{"id": "1", "name": "set-a", "is_active": 1, "description": "desc-a"}]
        )
        writer.skill_set_repo.get_skills_in_set.return_value = []
        writer.skill_set_repo.list_all.return_value = [
            {"id": "1", "name": "set-a", "is_active": 1, "description": "desc-a"}
        ]

        writer.write_metadata()

        assert writer.METADATA_FILE.exists()
        data = json.loads(writer.METADATA_FILE.read_text())
        assert "skill_sets" in data
        assert len(data["skill_sets"]) == 1
        assert data["skill_sets"][0]["name"] == "set-a"
        assert data["skill_sets"][0]["is_current"] is True

    def test_metadata_file_contains_skills(self, tmp_path):
        writer = _make_writer(tmp_path)
        writer.skill_set_repo = MagicMock()
        writer.skill_set_repo.list_all.return_value = [
            {"id": "10", "name": "set-b", "is_active": 0}
        ]
        writer.skill_set_repo.get_skills_in_set.return_value = [
            {"name": "skill-x", "description": "sx", "git_path": "git://cat/skill-x"},
            {"name": "skill-y", "description": "sy", "git_path": "local://skill-y"},
        ]

        writer.write_metadata()

        data = json.loads(writer.METADATA_FILE.read_text())
        skills = data["skill_sets"][0]["skills"]
        assert len(skills) == 2
        assert skills[0]["name"] == "skill-x"
        assert skills[0]["skill"] == "skill-x"
        assert "skill-x" in skills[0]["path"]
        assert skills[1]["skill"] == "skill-y"

    def test_metadata_is_current_flag(self, tmp_path):
        writer = _make_writer(tmp_path)
        writer.skill_set_repo = MagicMock()
        writer.skill_set_repo.list_all.return_value = [
            {"id": "5", "name": "active-set", "is_active": 1},
            {"id": "6", "name": "inactive-set", "is_active": 0},
        ]
        writer.skill_set_repo.get_skills_in_set.return_value = []

        writer.write_metadata()

        data = json.loads(writer.METADATA_FILE.read_text())
        by_name = {ss["name"]: ss for ss in data["skill_sets"]}
        assert by_name["active-set"]["is_current"] is True
        assert by_name["inactive-set"]["is_current"] is False

    def test_write_metadata_does_not_raise_on_exception(self, tmp_path):
        """write_metadata silently swallows exceptions."""
        writer = _make_writer(tmp_path)
        writer.skill_set_repo = MagicMock()
        writer.skill_set_repo.list_all.side_effect = RuntimeError("boom")

        # Should not raise
        writer.write_metadata()

    def test_atomic_write_uses_tmp_then_rename(self, tmp_path):
        """Verify no .tmp file is left after successful write."""
        writer = _make_writer(tmp_path)
        writer.skill_set_repo = MagicMock()
        writer.skill_set_repo.list_all.return_value = []
        writer.skill_set_repo.get_skills_in_set.return_value = []

        writer.write_metadata()

        tmp_file = writer.METADATA_FILE.with_suffix(".tmp")
        assert not tmp_file.exists()
        assert writer.METADATA_FILE.exists()

    def test_write_metadata_with_user_id_override(self, tmp_path):
        writer = _make_writer(tmp_path, user_id="user1")
        writer.skill_set_repo = MagicMock()
        writer.skill_set_repo.list_all.return_value = []
        writer.skill_set_repo.get_skills_in_set.return_value = []

        # Pass a different user_id at call time
        writer.write_metadata(user_id="user2")

        # list_all should be called with user_id="user2"
        writer.skill_set_repo.list_all.assert_called_with(user_id="user2", bolt_id=None)


# ---------------------------------------------------------------------------
# get_metadata_writer factory
# ---------------------------------------------------------------------------

class TestGetMetadataWriter:
    def test_returns_skill_set_metadata_writer(self, tmp_path):
        from agentclaw.community.core.skill_center.utils.skill_metadata_writer import (
            SkillSetMetadataWriter,
            get_metadata_writer,
        )

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        writer = get_metadata_writer(skill_set_repo=MagicMock(), skill_repo=MagicMock(), skills_dir=skills_dir)
        assert isinstance(writer, SkillSetMetadataWriter)
        assert writer.skills_dir == skills_dir

    def test_passes_user_id(self, tmp_path):
        from agentclaw.community.core.skill_center.utils.skill_metadata_writer import get_metadata_writer

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        writer = get_metadata_writer(
            skill_set_repo=MagicMock(),
            skill_repo=MagicMock(),
            skills_dir=skills_dir,
            user_id="dave",
        )

        assert writer.user_id == "dave"
