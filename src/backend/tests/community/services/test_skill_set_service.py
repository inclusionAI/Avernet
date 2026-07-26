"""Unit tests for SkillSetService symlink mapping and sync logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGetSymlinkMappings:
    """Tests for get_symlink_mappings method."""

    @pytest.fixture
    def mock_skill_set_repo(self):
        repo = MagicMock()
        return repo

    @pytest.fixture
    def mock_skill_repo(self):
        repo = MagicMock()
        return repo

    @pytest.fixture
    def skill_set_service(self, mock_skill_set_repo, mock_skill_repo, tmp_path):
        from agentclaw.community.core.skill_center.services.skill_set_service import (
            SkillSetService,
        )

        service = SkillSetService(
            skill_repo=mock_skill_repo,
            skill_set_repo=mock_skill_set_repo,
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            skills_dir=tmp_path / "skills",
            repo_dir=tmp_path / "skills-repo",
            local_dir=tmp_path / "skills-local",
            bot_repo=MagicMock(),
            path_factory=MagicMock(),
        )
        return service

    def _skills_dir(self, service):
        return service._skills_dir

    def _repo_dir(self, service):
        return service._repo_dir

    def _local_dir(self, service):
        return service._local_dir

    def test_local_path_with_absolute_path(
        self, skill_set_service, mock_skill_set_repo, mock_skill_repo
    ):
        """测试 local:// 绝对路径格式 - 使用新接口的绝对路径软链"""
        mock_skill_set_repo.get_all_active_skill_sets.return_value = [
            {"id": "1", "name": "Test Skill Set", "is_default": False}
        ]
        mock_skill_set_repo.get_skills_in_set.return_value = [
            {
                "id": "1",
                "name": "cct-zbb-instraction-query",
                "git_path": "local:///aidesktop/aidesktop_pre/bolt_data/staff_100015/default/openclaw/skills/skills-local/cct-zbb-instraction-query",
            }
        ]
        symlinks = skill_set_service.get_symlink_mappings(
            user_id="100015", bolt_id="default"
        )
        assert len(symlinks) == 1
        assert symlinks[0].source.endswith("/skills-local/cct-zbb-instraction-query")
        assert symlinks[0].target.endswith("/skills/cct-zbb-instraction-query")

    def test_pool_locator_is_used_as_mapping_source_without_legacy_rewrite(
        self, skill_set_service, mock_skill_set_repo
    ):
        pool_source = (
            "/home/admin/.openclaw/workspace/skills-pool/skills-local/handmade"
        )
        mock_skill_set_repo.get_all_active_skill_sets.return_value = [
            {"id": "1", "name": "Pool", "is_default": False}
        ]
        mock_skill_set_repo.get_skills_in_set.return_value = [
            {
                "id": "1",
                "name": "handmade",
                "git_path": f"local://{pool_source}",
            }
        ]

        mappings = skill_set_service.get_symlink_mappings(
            user_id="100015",
            bolt_id="default",
        )

        assert mappings[0].source == pool_source
        assert mappings[0].target.endswith("/openclaw/workspace/skills/handmade")

    def test_claude_pool_locators_drive_restart_mappings(
        self, skill_set_service, mock_skill_set_repo
    ):
        pool_local = (
            "/home/admin/.claude_code/workspace/skills-pool/skills-local/handmade"
        )
        skill_set_service.engine_type = "claude_code"
        skill_set_service.entity_id = "100015"
        skill_set_service._pool_layout_paths = lambda *_: (
            "/home/admin/.claude/skills",
            "/home/admin/.claude_code/workspace/skills-pool/skills-local",
            "/home/admin/.claude_code/workspace/skills-pool/skills-repo",
        )
        mock_skill_set_repo.get_all_active_skill_sets.return_value = [
            {"id": "1", "name": "Claude Pool", "is_default": False}
        ]
        mock_skill_set_repo.get_skills_in_set.return_value = [
            {
                "id": "1",
                "name": "handmade",
                "git_path": f"local://{pool_local}",
            },
            {
                "id": "2",
                "name": "repo-skill",
                "git_path": "git://business/repo-skill",
            },
        ]

        mappings = skill_set_service.get_symlink_mappings(
            user_id="100015",
            bolt_id="default",
        )

        assert [(mapping.source, mapping.target) for mapping in mappings] == [
            (
                pool_local,
                "/home/admin/.claude/skills/handmade",
            ),
            (
                "/home/admin/.claude_code/workspace/skills-pool/skills-repo/business/repo-skill",
                "/home/admin/.claude/skills/repo-skill",
            ),
        ]

    def test_aicoding_pool_locators_drive_restart_mappings(
        self, skill_set_service, mock_skill_set_repo
    ):
        pool_local = (
            "/home/admin/.aicoding/workspace/skills-pool/skills-local/handmade"
        )
        skill_set_service.engine_type = "aicoding"
        skill_set_service.entity_id = "100015"
        skill_set_service._pool_layout_paths = lambda *_: (
            "/home/admin/.claude/skills",
            "/home/admin/.aicoding/workspace/skills-pool/skills-local",
            "/home/admin/.aicoding/workspace/skills-pool/skills-repo",
        )
        mock_skill_set_repo.get_all_active_skill_sets.return_value = [
            {"id": "1", "name": "AICoding Pool", "is_default": False}
        ]
        mock_skill_set_repo.get_skills_in_set.return_value = [
            {
                "id": "1",
                "name": "handmade",
                "git_path": f"local://{pool_local}",
            },
            {
                "id": "2",
                "name": "repo-skill",
                "git_path": "git://business/repo-skill",
            },
        ]

        mappings = skill_set_service.get_symlink_mappings(
            user_id="100015",
            bolt_id="default",
        )

        assert [(mapping.source, mapping.target) for mapping in mappings] == [
            (
                pool_local,
                "/home/admin/.claude/skills/handmade",
            ),
            (
                "/home/admin/.aicoding/workspace/skills-pool/skills-repo/business/repo-skill",
                "/home/admin/.claude/skills/repo-skill",
            ),
        ]

    def test_hermes_pool_locators_drive_restart_mappings(
        self, skill_set_service, mock_skill_set_repo
    ):
        pool_local = (
            "/home/admin/.hermes/workspace/skills-pool/skills-local/handmade"
        )
        skill_set_service.engine_type = "hermes"
        skill_set_service.entity_id = "100015"
        skill_set_service._pool_layout_paths = lambda *_: (
            "/home/admin/.hermes/skills",
            "/home/admin/.hermes/workspace/skills-pool/skills-local",
            "/home/admin/.hermes/workspace/skills-pool/skills-repo",
        )
        mock_skill_set_repo.get_all_active_skill_sets.return_value = [
            {"id": "1", "name": "Hermes Pool", "is_default": False}
        ]
        mock_skill_set_repo.get_skills_in_set.return_value = [
            {
                "id": "1",
                "name": "handmade",
                "git_path": f"local://{pool_local}",
            },
            {
                "id": "2",
                "name": "repo-skill",
                "git_path": "git://business/repo-skill",
            },
        ]

        mappings = skill_set_service.get_symlink_mappings(
            user_id="100015",
            bolt_id="default",
        )

        assert [(mapping.source, mapping.target) for mapping in mappings] == [
            (
                pool_local,
                "/home/admin/.hermes/skills/handmade",
            ),
            (
                "/home/admin/.hermes/workspace/skills-pool/skills-repo/business/repo-skill",
                "/home/admin/.hermes/skills/repo-skill",
            ),
        ]

    def test_pool_active_repo_only_skill_uses_canonical_pool_source(
        self, skill_set_service, mock_skill_set_repo
    ):
        skill_set_service.entity_id = "100015"
        skill_set_service.engine_type = "openclaw"
        skill_set_service._pool_layout_paths = lambda *_: (
            "/home/admin/.openclaw/workspace/skills",
            "/home/admin/.openclaw/workspace/skills-pool/skills-local",
            "/home/admin/.openclaw/workspace/skills-pool/skills-repo",
        )
        mock_skill_set_repo.get_all_active_skill_sets.return_value = [
            {"id": "1", "name": "Pool", "is_default": False}
        ]
        mock_skill_set_repo.get_skills_in_set.return_value = [
            {
                "id": "2",
                "name": "repo-skill",
                "git_path": "git://business/repo-skill",
            }
        ]

        with patch(
            "agentclaw.community.utils.env_utils.is_local_mode",
            return_value=False,
        ):
            mappings = skill_set_service.get_symlink_mappings(
                user_id="100015",
                bolt_id="default",
            )

        assert mappings[0].source == (
            "/home/admin/.openclaw/workspace/skills-pool/"
            "skills-repo/business/repo-skill"
        )
        assert mappings[0].target == (
            "/home/admin/.openclaw/workspace/skills/repo-skill"
        )

    def test_local_path_with_relative_name(
        self, skill_set_service, mock_skill_set_repo, mock_skill_repo
    ):
        """测试 local:// 相对名称格式 - 使用新接口的绝对路径软链"""
        mock_skill_set_repo.get_all_active_skill_sets.return_value = [
            {"id": "1", "name": "Test Skill Set", "is_default": False}
        ]
        mock_skill_set_repo.get_skills_in_set.return_value = [
            {"id": "1", "name": "my-local-skill", "git_path": "local://my-local-skill"}
        ]
        symlinks = skill_set_service.get_symlink_mappings(
            user_id="100015", bolt_id="default"
        )
        assert len(symlinks) == 1
        assert symlinks[0].source.endswith("/skills-local/my-local-skill")
        assert symlinks[0].target.endswith("/skills/my-local-skill")

    def test_git_path_standard_format(
        self, skill_set_service, mock_skill_set_repo, mock_skill_repo
    ):
        """测试 git:// 标准格式 - 使用新接口的绝对路径软链"""
        mock_skill_set_repo.get_all_active_skill_sets.return_value = [
            {"id": "1", "name": "Test Skill Set", "is_default": False}
        ]
        mock_skill_set_repo.get_skills_in_set.return_value = [
            {
                "id": "1",
                "name": "query-user-trade-record-list",
                "git_path": "git://business/riskinsight/query-user-trade-record-list",
            }
        ]
        symlinks = skill_set_service.get_symlink_mappings(
            user_id="100015", bolt_id="default"
        )
        assert len(symlinks) == 1
        assert symlinks[0].source.endswith(
            "/skills-repo/business/riskinsight/query-user-trade-record-list"
        )
        assert symlinks[0].target.endswith("/skills/query-user-trade-record-list")

    def test_local_path_with_trailing_slash(
        self, skill_set_service, mock_skill_set_repo, mock_skill_repo
    ):
        """测试 local:// 带尾部斜杠 - 使用新接口的绝对路径软链"""
        mock_skill_set_repo.get_all_active_skill_sets.return_value = [
            {"id": "1", "name": "Test Skill Set", "is_default": False}
        ]
        mock_skill_set_repo.get_skills_in_set.return_value = [
            {
                "id": "1",
                "name": "skill-name",
                "git_path": "local:///aidesktop/path/skills-local/skill-name/",
            }
        ]
        symlinks = skill_set_service.get_symlink_mappings(
            user_id="100015", bolt_id="default"
        )
        assert len(symlinks) == 1
        assert symlinks[0].source.endswith("/skills-local/skill-name")
        assert symlinks[0].target.endswith("/skills/skill-name")

    def test_no_active_skill_sets(self, skill_set_service, mock_skill_set_repo):
        """测试没有激活的技能集"""
        mock_skill_set_repo.get_all_active_skill_sets.return_value = []
        symlinks = skill_set_service.get_symlink_mappings(
            user_id="100015", bolt_id="default"
        )
        assert len(symlinks) == 0


class TestAddRemoveSkillSync:
    """Tests for add_skills_to_set and remove_skill_from_set sync behavior."""

    @pytest.fixture
    def mock_skill_set_repo(self):
        repo = MagicMock()
        return repo

    @pytest.fixture
    def mock_skill_repo(self):
        repo = MagicMock()
        return repo

    @pytest.fixture
    def skill_set_service(self, mock_skill_set_repo, mock_skill_repo, tmp_path):
        from agentclaw.community.core.skill_center.services.skill_set_service import (
            SkillSetService,
        )
        from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
        from agentclaw.community.plugins.local.skill_repo_sync import (
            LocalSkillRepoSyncPlugin,
        )

        service = SkillSetService(
            skill_repo=mock_skill_repo,
            skill_set_repo=mock_skill_set_repo,
            mcp_center=MagicMock(),
            mcp_config_service=MagicMock(),
            skill_service=MagicMock(),
            skills_dir=tmp_path / "skills",
            repo_dir=tmp_path / "skills-repo",
            local_dir=tmp_path / "skills-local",
            entity_id="staff_100015",
            bot_id="test_bot",
            bot_repo=MagicMock(),
            path_factory=WorkspacePathFactory(
                skill_repo_sync=LocalSkillRepoSyncPlugin()
            ),
        )
        return service

    @pytest.mark.asyncio
    async def test_add_skills_to_active_set_triggers_sync(
        self, skill_set_service, mock_skill_set_repo, mock_skill_repo
    ):
        """测试：在激活的技能集中添加技能会触发软链同步到设备"""
        # Arrange: 设置技能集为激活状态（is_active=True）
        mock_skill_set_repo.get_by_id.return_value = {
            "id": "1",
            "name": "TestSet",
            "is_default": False,
            "is_active": True,
        }
        mock_skill_set_repo.get_skills_in_set.return_value = []

        # Mock 技能（bolt_id 必须与 service.bot_id 一致，否则触发跨 Bot 校验）
        mock_skill_repo.get_by_id.return_value = {
            "id": "123",
            "name": "test-skill",
            "git_path": "git://business/test/skill",
            "bolt_id": "test_bot",
        }

        with patch.object(
            skill_set_service, "_sync_symlinks_to_device_if_needed"
        ) as mock_sync:
            with patch.object(
                skill_set_service.skill_service,
                "activate_skill",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch(
                    "agentclaw.community.core.skill_center.services.skill_set_service.SkillSetMetadataWriter"
                ):
                    # Act
                    result = await skill_set_service.add_skills_to_set(
                        "1", ["123"], user_id="100015"
                    )

        # Assert: 同步方法被调用
        mock_sync.assert_called_once()
        assert len(result["success"]) == 1

    @pytest.mark.asyncio
    async def test_add_skills_to_inactive_set_no_sync(
        self, skill_set_service, mock_skill_set_repo, mock_skill_repo
    ):
        """测试：在非激活的技能集中添加技能不会触发同步"""
        # Arrange: 设置技能集为非激活状态（is_active=False）
        mock_skill_set_repo.get_by_id.return_value = {
            "id": "1",
            "name": "TestSet",
            "is_default": False,
            "is_active": False,
        }
        mock_skill_set_repo.get_skills_in_set.return_value = []

        mock_skill_repo.get_by_id.return_value = {
            "id": "123",
            "name": "test-skill",
            "bolt_id": "test_bot",
        }

        with patch.object(
            skill_set_service, "_sync_symlinks_to_device_if_needed"
        ) as mock_sync:
            with patch(
                "agentclaw.community.core.skill_center.services.skill_set_service.SkillSetMetadataWriter"
            ):
                # Act
                await skill_set_service.add_skills_to_set(
                    "1", ["123"], user_id="100015"
                )

        # Assert: 同步方法没有被调用
        mock_sync.assert_not_called()

    @pytest.mark.asyncio
    async def test_remove_skill_from_active_set_triggers_sync(
        self, skill_set_service, mock_skill_set_repo, mock_skill_repo
    ):
        """测试：在激活的技能集中移除技能会触发软链同步（包括空列表情况）"""
        # Arrange
        mock_skill_set_repo.get_by_id.return_value = {
            "id": "1",
            "name": "TestSet",
            "is_default": False,
        }
        # get_all_active_skill_sets 返回包含当前 skill_set_id 的列表（表示激活状态）
        mock_skill_set_repo.get_all_active_skill_sets.return_value = [{"id": "1"}]

        mock_skill_repo.get_by_id.return_value = {
            "id": "123",
            "name": "test-skill",
            "git_path": "git://business/test/skill",
        }
        mock_skill_set_repo.remove_skill_from_set.return_value = True

        with patch.object(
            skill_set_service, "_sync_symlinks_to_device_if_needed"
        ) as mock_sync:
            with patch.object(
                skill_set_service.skill_service,
                "deactivate_skill",
                new_callable=AsyncMock,
                return_value=True,
            ):
                with patch.object(
                    skill_set_service.skill_service,
                    "get_link_name",
                    return_value="business_test_skill",
                ):
                    with patch(
                        "agentclaw.community.core.skill_center.services.skill_set_service.SkillSetMetadataWriter"
                    ):
                        # Act
                        result = await skill_set_service.remove_skill_from_set(
                            "1", "123", user_id="100015"
                        )

        # Assert: 即使移除后技能集为空，也会触发同步（清空设备软链）
        mock_sync.assert_called_once()
        assert result is True

    def test_sync_symlinks_with_device_proxy(
        self, skill_set_service, mock_skill_set_repo
    ):
        """测试：_sync_symlinks_to_device_if_needed 通过 DeviceSyncPlugin 同步软链"""
        mock_device_sync = MagicMock()
        mock_device_sync.sync_symlinks.return_value = {"success": True, "message": "ok"}

        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = mock_device_sync
        skill_set_service._device_sync_dispatcher = dispatcher
        skill_set_service._resolver = MagicMock()

        with patch.object(
            skill_set_service, "get_symlink_mappings", return_value=[]
        ) as mock_get_mappings:
            # Act
            result = skill_set_service._sync_symlinks_to_device_if_needed(
                user_id="100015"
            )

        # Assert: 即使软链列表为空也会调用同步（关键修复点）
        assert result is True
        mock_get_mappings.assert_called_once_with(user_id="100015", bolt_id="test_bot")
        mock_device_sync.sync_symlinks.assert_called_once_with([])

    def test_sync_skips_when_sync_returns_failure(self, skill_set_service):
        """测试：当同步失败时返回 False"""
        mock_device_sync = MagicMock()
        mock_device_sync.sync_symlinks.return_value = {
            "success": False,
            "message": "error",
        }

        dispatcher = MagicMock()
        dispatcher.dispatch.return_value = mock_device_sync
        skill_set_service._device_sync_dispatcher = dispatcher
        skill_set_service._resolver = MagicMock()

        with patch.object(skill_set_service, "get_symlink_mappings", return_value=[]):
            result = skill_set_service._sync_symlinks_to_device_if_needed(
                user_id="100015"
            )

        assert result is False

    def test_sync_skips_when_exception_raised(self, skill_set_service):
        """测试：当同步抛出异常时返回 False"""
        dispatcher = MagicMock()
        dispatcher.dispatch.side_effect = Exception("connection refused")
        skill_set_service._device_sync_dispatcher = dispatcher
        skill_set_service._resolver = MagicMock()

        with patch.object(skill_set_service, "get_symlink_mappings", return_value=[]):
            result = skill_set_service._sync_symlinks_to_device_if_needed(
                user_id="100015"
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_add_skill_already_in_other_set_rejected(
        self, skill_set_service, mock_skill_set_repo, mock_skill_repo
    ):
        """测试：同一 bot 下，一个 skill 不能同时属于多个 skill set"""
        # Target set we're adding to
        mock_skill_set_repo.get_by_id.return_value = {
            "id": "2",
            "name": "Set2",
            "is_default": False,
            "is_active": False,
        }
        # Skill not in target set, but in another set
        mock_skill_set_repo.get_skills_in_set.side_effect = lambda sid: (
            [{"id": "999", "name": "shared-skill"}] if sid == "1" else []
        )

        mock_skill_repo.get_by_id.return_value = {
            "id": "999",
            "name": "shared-skill",
            "bolt_id": "test_bot",
        }

        # list_skill_sets returns two sets: set '1' has the skill, set '2' is target
        with patch.object(
            skill_set_service,
            "list_skill_sets",
            return_value=[
                {"id": "1", "name": "Set1"},
                {"id": "2", "name": "Set2"},
            ],
        ):
            with patch(
                "agentclaw.community.core.skill_center.services.skill_set_service.SkillSetMetadataWriter"
            ):
                result = await skill_set_service.add_skills_to_set(
                    "2", ["999"], user_id="100015"
                )

        # Should be rejected
        assert len(result["failed"]) == 1
        assert "already exists in another skill set" in result["failed"][0]["error"]
        # add_skill_to_set should NOT have been called
        mock_skill_set_repo.add_skill_to_set.assert_not_called()
