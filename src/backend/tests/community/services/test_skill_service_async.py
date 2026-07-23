"""Unit tests for SkillParser.parse_content and async SkillService methods."""
import pytest
from unittest.mock import MagicMock, AsyncMock


class TestSkillParserParseContent:
    """Tests for SkillParser.parse_content method."""

    def test_parse_basic_frontmatter(self):
        """Test parsing basic YAML frontmatter."""
        from agentclaw.community.core.skill_center.services.skill_parser import SkillParser

        content = "---\nname: my-skill\ndescription: A test skill\n---\n# My Skill"
        result = SkillParser.parse_content(content)
        assert result["name"] == "my-skill"
        assert result["description"] == "A test skill"

    def test_parse_empty_content(self):
        """Test parsing empty content returns None."""
        from agentclaw.community.core.skill_center.services.skill_parser import SkillParser

        assert SkillParser.parse_content("") is None
        assert SkillParser.parse_content(None) is None


class TestSkillServiceAsyncRouting:
    """Tests for async SkillService file I/O via DeviceFileSystemPlugin."""

    def _service(self, tmp_path, mock_device_fs=None, mock_repo=None):
        from agentclaw.community.core.skill_center.services.skill_service import SkillService

        if mock_device_fs is None:
            mock_device_fs = AsyncMock()
            mock_device_fs.write_file = AsyncMock()
            mock_device_fs.delete_tree = AsyncMock()

        if mock_repo is None:
            mock_repo = MagicMock()
            mock_repo.list_skills.return_value = []
            mock_repo.get_bot_local_by_name.return_value = None
            mock_repo.create.side_effect = lambda data: {"id": "1", **data}
            mock_repo.get_by_name_global.return_value = None

        service = SkillService(
            skill_repo=mock_repo,
            skill_repo_sync=MagicMock(),
            market_cache=MagicMock(),
            category_repo=MagicMock(),
            active_dir=tmp_path / "skills",
            repo_dir=tmp_path / "skills-repo",
            local_dir=tmp_path / "skills-local",
            device_fs_factory=lambda bolt_id, user_id: mock_device_fs,
            git_sync_service_factory=MagicMock(),
        )
        return service, mock_device_fs, mock_repo

    @pytest.mark.asyncio
    async def test_upload_writes_via_device_filesystem(self, tmp_path):
        """Test upload_skill writes files via DeviceFileSystemPlugin."""
        service, mock_device_fs, _ = self._service(tmp_path)

        files = [{
            "filename": "SKILL.md",
            "content": b"---\nname: test-skill\ndescription: test\n---\n# Test",
            "relative_path": "SKILL.md",
        }]
        await service.upload_skill(files, user_id="user1", bolt_id="bot1")

        # Verify DeviceFileSystemPlugin was used for file operations
        mock_device_fs.delete_tree.assert_called_once()
        mock_device_fs.write_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_reupload_uses_existing_pool_locator_without_duplicate(
        self, tmp_path
    ):
        pool_path = (
            "/home/admin/.openclaw/workspace/"
            "skills-pool/skills-local/test-skill"
        )
        existing = {
            "id": "41",
            "name": "test-skill",
            "user_id": "user1",
            "git_path": f"local://{pool_path}",
        }
        repo = MagicMock()
        repo.get_bot_local_by_name.return_value = existing
        repo.update.return_value = existing
        service, device_fs, _ = self._service(tmp_path, mock_repo=repo)

        result = await service.upload_skill(
            [
                {
                    "filename": "SKILL.md",
                    "content": (
                        b"---\nname: test-skill\ndescription: test\n"
                        b"---\n# Test"
                    ),
                    "relative_path": "SKILL.md",
                }
            ],
            user_id="user1",
            bolt_id="bot1",
        )

        assert result == existing
        device_fs.delete_tree.assert_awaited_once_with(pool_path)
        device_fs.write_file.assert_awaited_once_with(
            f"{pool_path}/SKILL.md",
            b"---\nname: test-skill\ndescription: test\n---\n# Test",
        )
        repo.update.assert_called_once()
        repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_does_not_overwrite_global_local_skill(
        self, tmp_path
    ):
        repo = MagicMock()
        repo.get_bot_local_by_name.return_value = None
        repo.create.side_effect = lambda data: {"id": "52", **data}
        service, _, _ = self._service(tmp_path, mock_repo=repo)

        result = await service.upload_skill(
            [
                {
                    "filename": "SKILL.md",
                    "content": (
                        b"---\nname: shared-name\ndescription: bot copy\n"
                        b"---\n# Bot copy"
                    ),
                    "relative_path": "SKILL.md",
                }
            ],
            user_id="user1",
            bolt_id="bot1",
        )

        assert result["id"] == "52"
        repo.get_bot_local_by_name.assert_called_once_with(
            bot_id="bot1",
            name="shared-name",
            user_id="user1",
        )
        repo.update.assert_not_called()
        repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_wraps_device_write_failure(self, tmp_path):
        """Arca/Bolt write errors surface as upload processing errors."""
        service, mock_device_fs, _ = self._service(tmp_path)
        mock_device_fs.write_file.side_effect = Exception("404 Not Found")

        files = [{
            "filename": "SKILL.md",
            "content": b"---\nname: test-skill\ndescription: test\n---\n# Test",
            "relative_path": "SKILL.md",
        }]

        with pytest.raises(
            ValueError,
            match="Upload processing error: 404 Not Found",
        ):
            await service.upload_skill(files, user_id="user1", bolt_id="bot1")

        assert mock_device_fs.delete_tree.call_count == 2
        mock_device_fs.write_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_nested_skill_root_uses_skill_md_name_and_strips_root(self, tmp_path):
        service, mock_device_fs, mock_repo = self._service(tmp_path)

        files = [
            {
                "filename": "SKILL.md",
                "content": b"---\nname: real-skill-name\ndescription: test skill\n---\n# Test",
                "relative_path": "manysomany/real-skill-name/SKILL.md",
            },
            {
                "filename": "icon.png",
                "content": b"png",
                "relative_path": "manysomany/real-skill-name/assets/icon.png",
            },
            {
                "filename": ".DS_Store",
                "content": b"ignored",
                "relative_path": "manysomany/real-skill-name/.DS_Store",
            },
        ]

        result = await service.upload_skill(files, user_id="user1", bolt_id="bot1")

        assert result["name"] == "real-skill-name"
        assert result["git_path"].startswith("local:///")
        assert result["git_path"].endswith("/skills-local/real-skill-name")
        written_paths = [call.args[0] for call in mock_device_fs.write_file.call_args_list]
        assert str(tmp_path / "skills-local" / "real-skill-name" / "SKILL.md") in written_paths
        assert str(tmp_path / "skills-local" / "real-skill-name" / "assets" / "icon.png") in written_paths
        assert not any(".DS_Store" in path for path in written_paths)
        mock_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_rejects_folder_name_mismatch(self, tmp_path):
        service, mock_device_fs, _ = self._service(tmp_path)

        files = [
            {
                "filename": "SKILL.md",
                "content": b"---\nname: real-skill-name\ndescription: test skill\n---\n# Test",
                "relative_path": "manysomany/folder-name/SKILL.md",
            },
        ]

        with pytest.raises(ValueError, match="Skill folder name must match"):
            await service.upload_skill(files, user_id="user1", bolt_id="bot1")
        mock_device_fs.write_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_rejects_multiple_skill_md_files(self, tmp_path):
        service, mock_device_fs, _ = self._service(tmp_path)

        files = [
            {
                "filename": "SKILL.md",
                "content": b"---\nname: a\ndescription: a\n---",
                "relative_path": "root/a/SKILL.md",
            },
            {
                "filename": "SKILL.md",
                "content": b"---\nname: b\ndescription: b\n---",
                "relative_path": "root/b/SKILL.md",
            },
        ]

        with pytest.raises(ValueError, match="Only one skill can be uploaded"):
            await service.upload_skill(files, user_id="user1", bolt_id="bot1")
        mock_device_fs.write_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_rejects_files_outside_skill_root(self, tmp_path):
        service, mock_device_fs, _ = self._service(tmp_path)

        files = [
            {
                "filename": "SKILL.md",
                "content": b"---\nname: a\ndescription: a\n---",
                "relative_path": "root/a/SKILL.md",
            },
            {"filename": "note.txt", "content": b"x", "relative_path": "root/note.txt"},
        ]

        with pytest.raises(ValueError, match="outside the skill root"):
            await service.upload_skill(files, user_id="user1", bolt_id="bot1")
        mock_device_fs.write_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_requires_skill_md_name_and_description(self, tmp_path):
        service, mock_device_fs, _ = self._service(tmp_path)

        missing_description = [
            {"filename": "SKILL.md", "content": b"---\nname: a\n---", "relative_path": "root/a/SKILL.md"},
        ]
        with pytest.raises(ValueError, match="description"):
            await service.upload_skill(missing_description, user_id="user1", bolt_id="bot1")

        empty_name = [
            {"filename": "SKILL.md", "content": b"---\nname:\ndescription: a\n---", "relative_path": "root/a/SKILL.md"},
        ]
        with pytest.raises(ValueError, match="cannot be empty"):
            await service.upload_skill(empty_name, user_id="user1", bolt_id="bot1")

        mock_device_fs.write_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_removes_via_device_filesystem(self, tmp_path):
        """Test delete_skill removes files via DeviceFileSystemPlugin."""
        from agentclaw.community.core.skill_center.services.skill_service import SkillService

        mock_device_fs = AsyncMock()
        mock_device_fs.delete_tree.return_value = True

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {
            "id": "1", "name": "test", "git_path": "local:///path/test",
            "bolt_id": "bot1", "user_id": "user1"
        }
        mock_repo.delete.return_value = True

        service = SkillService(
            skill_repo=mock_repo,
            skill_repo_sync=MagicMock(),
            market_cache=MagicMock(),
            category_repo=MagicMock(),
            active_dir=tmp_path / "skills",
            repo_dir=tmp_path / "skills-repo",
            local_dir=tmp_path / "skills-local",
            device_fs_factory=lambda bolt_id, user_id: mock_device_fs,
            git_sync_service_factory=MagicMock(),
        )

        await service.delete_skill("1", user_id="user1")

        # Verify DeviceFileSystemPlugin was used to delete files (Phase 4: step 1 active-link + step 2 physical)
        assert mock_device_fs.delete_tree.call_count == 2
        called_paths = [c.args[0] for c in mock_device_fs.delete_tree.call_args_list]
        # step 1: active link under active_dir
        assert any("test" in p and "skills" in p for p in called_paths)
        # step 2: physical file
        assert any(p == "/path/test" for p in called_paths)

    @pytest.mark.asyncio
    async def test_readme_reads_via_device_filesystem(self, tmp_path):
        """Test get_skill_readme reads via DeviceFileSystemPlugin."""
        from agentclaw.community.core.skill_center.services.skill_service import SkillService

        mock_device_fs = AsyncMock()
        mock_device_fs.read_file.return_value = b"# Test Skill"

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {
            "id": "1", "git_path": "local:///path/test",
            "bolt_id": "bot1", "user_id": "user1"
        }

        service = SkillService(
            skill_repo=mock_repo,
            skill_repo_sync=MagicMock(),
            market_cache=MagicMock(),
            category_repo=MagicMock(),
            active_dir=tmp_path / "skills",
            repo_dir=tmp_path / "skills-repo",
            local_dir=tmp_path / "skills-local",
            device_fs_factory=lambda bolt_id, user_id: mock_device_fs,
            git_sync_service_factory=MagicMock(),
        )

        result = await service.get_skill_readme("1", user_id="user1")
        assert result == "# Test Skill"
        mock_device_fs.read_file.assert_called()
