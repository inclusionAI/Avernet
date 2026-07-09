"""Tests for SkillService.get_skill_readme encoding fallback."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.skill_center.services.skill_service import SkillService


def _lenient_skill_repo_sync():
    stub = MagicMock()
    stub.get_scan_target.side_effect = lambda default: default
    return stub


def _make_service(
    skill_repo,
    repo_dir: Path,
    local_dir: Path,
    active_dir: Path,
    device_fs_factory=None,
) -> SkillService:
    return SkillService(
        skill_repo=skill_repo,
        skill_repo_sync=_lenient_skill_repo_sync(),
        category_repo=MagicMock(),
        active_dir=active_dir,
        repo_dir=repo_dir,
        local_dir=local_dir,
        market_cache=MagicMock(),
        device_fs_factory=device_fs_factory or MagicMock(),
        git_sync_service_factory=MagicMock(),
    )


class TestGetSkillReadmeEncoding:
    """get_skill_readme should handle non-UTF-8 encoded files gracefully."""

    @pytest.mark.asyncio
    async def test_local_skill_md_utf8(self, skill_dirs, mock_skill_repo):
        """UTF-8 encoded SKILL.md returns content correctly."""
        content_text = "# 测试技能\n这是一个UTF-8编码的技能。"
        content_bytes = content_text.encode("utf-8")

        mock_skill_repo.get_by_id.return_value = {
            "id": "100",
            "git_path": "local://my-skill",
            "bolt_id": "bot1",
            "user_id": "user1",
        }

        device_fs = AsyncMock()
        device_fs.read_file = AsyncMock(side_effect=lambda path: content_bytes if "SKILL.md" in path else None)
        factory = MagicMock(return_value=device_fs)

        svc = _make_service(mock_skill_repo, skill_dirs["repo_dir"], skill_dirs["local_dir"], skill_dirs["active_dir"], device_fs_factory=factory)
        result = await svc.get_skill_readme("100")
        assert result == content_text

    @pytest.mark.asyncio
    async def test_local_skill_md_gbk(self, skill_dirs, mock_skill_repo):
        """GBK encoded SKILL.md should fallback to gbk decoding instead of raising."""
        content_text = "# 测试技能\n这是一个GBK编码的技能。"
        content_bytes = content_text.encode("gbk")

        mock_skill_repo.get_by_id.return_value = {
            "id": "101",
            "git_path": "local://my-skill",
            "bolt_id": "bot1",
            "user_id": "user1",
        }

        device_fs = AsyncMock()
        device_fs.read_file = AsyncMock(side_effect=lambda path: content_bytes if "SKILL.md" in path else None)
        factory = MagicMock(return_value=device_fs)

        svc = _make_service(mock_skill_repo, skill_dirs["repo_dir"], skill_dirs["local_dir"], skill_dirs["active_dir"], device_fs_factory=factory)
        result = await svc.get_skill_readme("101")
        assert result == content_text

    @pytest.mark.asyncio
    async def test_local_readme_md_gbk(self, skill_dirs, mock_skill_repo):
        """GBK encoded README.md (SKILL.md not found) should also fallback."""
        content_text = "# 自述文件\n这是GBK编码。"
        content_bytes = content_text.encode("gbk")

        mock_skill_repo.get_by_id.return_value = {
            "id": "102",
            "git_path": "local://my-skill",
            "bolt_id": "bot1",
            "user_id": "user1",
        }

        device_fs = AsyncMock()

        async def fake_read(path):
            if "SKILL.md" in path:
                return None
            if "README.md" in path:
                return content_bytes
            return None

        device_fs.read_file = AsyncMock(side_effect=fake_read)
        factory = MagicMock(return_value=device_fs)

        svc = _make_service(mock_skill_repo, skill_dirs["repo_dir"], skill_dirs["local_dir"], skill_dirs["active_dir"], device_fs_factory=factory)
        result = await svc.get_skill_readme("102")
        assert result == content_text

    @pytest.mark.asyncio
    async def test_git_skill_md_gbk(self, skill_dirs, mock_skill_repo):
        """GBK encoded SKILL.md in git:// repo should fallback gracefully."""
        content_text = "# Git技能\n这是GBK编码的git技能。"
        skill_path = skill_dirs["repo_dir"] / "business" / "my-git-skill"
        skill_path.mkdir(parents=True)
        (skill_path / "SKILL.md").write_bytes(content_text.encode("gbk"))

        mock_skill_repo.get_by_id.return_value = {
            "id": "103",
            "git_path": "git://business/my-git-skill",
            "bolt_id": "bot1",
            "user_id": "user1",
        }

        svc = _make_service(mock_skill_repo, skill_dirs["repo_dir"], skill_dirs["local_dir"], skill_dirs["active_dir"])
        result = await svc.get_skill_readme("103")
        assert result == content_text

    @pytest.mark.asyncio
    async def test_git_skill_md_utf8(self, skill_dirs, mock_skill_repo):
        """UTF-8 encoded SKILL.md in git:// repo works as before."""
        content_text = "# Git技能\n这是UTF-8编码。"
        skill_path = skill_dirs["repo_dir"] / "business" / "my-git-skill"
        skill_path.mkdir(parents=True)
        (skill_path / "SKILL.md").write_bytes(content_text.encode("utf-8"))

        mock_skill_repo.get_by_id.return_value = {
            "id": "104",
            "git_path": "git://business/my-git-skill",
            "bolt_id": "bot1",
            "user_id": "user1",
        }

        svc = _make_service(mock_skill_repo, skill_dirs["repo_dir"], skill_dirs["local_dir"], skill_dirs["active_dir"])
        result = await svc.get_skill_readme("104")
        assert result == content_text

    @pytest.mark.asyncio
    async def test_repo_fallback_gbk(self, skill_dirs, mock_skill_repo):
        """_get_readme_from_repo path also handles GBK encoding."""
        content_text = "# 回退技能\n这是GBK编码。"
        skill_path = skill_dirs["repo_dir"] / "my-fallback-skill"
        skill_path.mkdir(parents=True)
        (skill_path / "SKILL.md").write_bytes(content_text.encode("gbk"))

        mock_skill_repo.get_by_id.return_value = None

        svc = _make_service(mock_skill_repo, skill_dirs["repo_dir"], skill_dirs["local_dir"], skill_dirs["active_dir"])
        result = await svc.get_skill_readme("my-fallback-skill")
        assert result == content_text

    @pytest.mark.asyncio
    async def test_local_skill_md_unknown_encoding_uses_replace(self, skill_dirs, mock_skill_repo):
        """Completely garbled bytes should not raise — replaced with placeholders."""
        garbled_bytes = bytes(range(128, 256))

        mock_skill_repo.get_by_id.return_value = {
            "id": "105",
            "git_path": "local://my-skill",
            "bolt_id": "bot1",
            "user_id": "user1",
        }

        device_fs = AsyncMock()
        device_fs.read_file = AsyncMock(side_effect=lambda path: garbled_bytes if "SKILL.md" in path else None)
        factory = MagicMock(return_value=device_fs)

        svc = _make_service(mock_skill_repo, skill_dirs["repo_dir"], skill_dirs["local_dir"], skill_dirs["active_dir"], device_fs_factory=factory)
        result = await svc.get_skill_readme("105")
        assert result is not None
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_local_readme_keeps_caller_bolt_when_db_bolt_missing(self, skill_dirs, mock_skill_repo):
        """DB rows without bolt_id must not erase the route-resolved bot id."""
        mock_skill_repo.get_by_id.return_value = {
            "id": "106",
            "git_path": "local://skills-local/my-skill",
            "bolt_id": None,
            "user_id": "owner1",
        }

        device_fs = AsyncMock()
        device_fs.read_file = AsyncMock(return_value=b"# Caller Bot")
        factory = MagicMock(return_value=device_fs)

        svc = _make_service(
            mock_skill_repo,
            skill_dirs["repo_dir"],
            skill_dirs["local_dir"],
            skill_dirs["active_dir"],
            device_fs_factory=factory,
        )
        result = await svc.get_skill_readme("106", user_id="request-user", bolt_id="route-bot")

        assert result == "# Caller Bot"
        factory.assert_called_once_with("route-bot", "owner1")


class TestPrepareUploadPlanEncoding:
    """_prepare_upload_plan should handle GBK-encoded SKILL.md bytes."""

    def _make_upload_service(self, skill_dirs):
        return _make_service(
            MagicMock(),
            skill_dirs["repo_dir"],
            skill_dirs["local_dir"],
            skill_dirs["active_dir"],
        )

    def test_upload_gbk_skill_md_parses_description(self, skill_dirs):
        """GBK encoded SKILL.md should produce correct description in upload plan."""
        content = "---\nname: infosec-check\ndescription: 检查应用安全合规性\n---\n"
        files = [{"filename": "SKILL.md", "relative_path": "SKILL.md", "content": content.encode("gbk")}]

        svc = self._make_upload_service(skill_dirs)
        skill_name, skill_info, _ = svc._prepare_upload_plan(files)
        assert skill_name == "infosec-check"
        assert skill_info["description"] == "检查应用安全合规性"

    def test_upload_utf8_skill_md_still_works(self, skill_dirs):
        """UTF-8 encoded SKILL.md should still work as before."""
        content = "---\nname: my-skill\ndescription: A test skill\n---\n"
        files = [{"filename": "SKILL.md", "relative_path": "SKILL.md", "content": content.encode("utf-8")}]

        svc = self._make_upload_service(skill_dirs)
        skill_name, skill_info, _ = svc._prepare_upload_plan(files)
        assert skill_name == "my-skill"
        assert skill_info["description"] == "A test skill"


class TestIsIgnoredUploadPath:
    """_is_ignored_upload_path filters macOS/Windows junk and Python bytecode caches."""

    @pytest.mark.parametrize(
        "relative_path",
        [
            ".DS_Store",
            "subdir/.DS_Store",
            "__MACOSX/skill/SKILL.md",
            "__pycache__/index.cpython-313.pyc",
            "pkg/__pycache__/module.cpython-312.pyc",
            "tools/helper.pyo",
            "tools/helper.pyc",
        ],
    )
    def test_ignored_paths_are_filtered(self, relative_path):
        assert SkillService._is_ignored_upload_path(relative_path) is True

    @pytest.mark.parametrize(
        "relative_path",
        [
            "SKILL.md",
            "pkg/helper.py",
            "tools/helper.pyd",  # Windows binary extension — real dependency, must NOT be filtered
            "tools/native.so",
            "scripts/run.sh",
        ],
    )
    def test_real_files_are_kept(self, relative_path):
        assert SkillService._is_ignored_upload_path(relative_path) is False

    def test_pycache_skipped_in_upload_plan(self, skill_dirs):
        """Bytecode caches must be dropped by _prepare_upload_plan before any write_file call."""
        content = "---\nname: my-skill\ndescription: A test skill\n---\n"
        files = [
            {"filename": "SKILL.md", "relative_path": "SKILL.md", "content": content.encode("utf-8")},
            {"filename": "index.cpython-313.pyc",
             "relative_path": "__pycache__/index.cpython-313.pyc",
             "content": b"\x00\x01binary"},
            {"filename": "helper.pyc", "relative_path": "pkg/helper.pyc", "content": b"\x00\x01binary"},
            {"filename": ".DS_Store", "relative_path": ".DS_Store", "content": b"junk"},
            {"filename": "helper.py", "relative_path": "pkg/helper.py", "content": b"print('hi')"},
            {"filename": "pkg/", "relative_path": "pkg/", "content": b""},
        ]

        svc = _make_service(
            MagicMock(),
            skill_dirs["repo_dir"],
            skill_dirs["local_dir"],
            skill_dirs["active_dir"],
        )
        _, _, candidates = svc._prepare_upload_plan(files)
        candidate_paths = {c["relative_path"] for c in candidates}

        assert "__pycache__/index.cpython-313.pyc" not in candidate_paths
        assert "pkg/helper.pyc" not in candidate_paths
        assert ".DS_Store" not in candidate_paths
        assert candidate_paths == {"SKILL.md", "pkg/helper.py"}

