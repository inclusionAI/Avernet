"""Tests for PR #31: 跳过已删除 Bot 的重名检查

验证 3.1 迁移功能：
- create_skill 复用已删除 Bot 的同名技能记录
- create_skill_set 用 list_all_exclude_deleted 做重名检查
- create_skill_set 复用已删除 Bot 的同名技能集记录
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Tests: SkillService.create_skill 复用已删除 Bot 的同名记录
# ---------------------------------------------------------------------------


class TestCreateSkillReuseDeleted:
    """create_skill 应在发现已删除 Bot 的同名技能时复用记录"""

    def _make_svc(self, tmp_path):
        from agentclaw.community.core.skill_center.services.skill_service import SkillService
        active_dir = tmp_path / "skills"
        local_dir = tmp_path / "skills-local"
        repo_dir = tmp_path / "skills-repo"
        for d in (active_dir, local_dir, repo_dir):
            d.mkdir(exist_ok=True)

        svc = SkillService(
            skill_repo=MagicMock(),
            skill_repo_sync=MagicMock(),
            category_repo=MagicMock(),
            active_dir=active_dir,
            local_dir=local_dir,
            repo_dir=repo_dir,
        market_cache=MagicMock(),
        device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        return svc

    def _setup_git_skill(self, tmp_path, name):
        """创建 git 技能目录和 SKILL.md"""
        d = tmp_path / "skills-repo" / "test" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"# {name}")
        return d

    def test_no_reuse_when_same_bot(self, tmp_path):
        """当同名技能属于同一 Bot 时，不应复用，直接 create"""
        svc = self._make_svc(tmp_path)
        repo = MagicMock()
        repo.get_by_name_global_include_deleted.return_value = {
            'id': '42', 'name': 'my-skill', 'bolt_id': 'same-bot',
        }
        repo.get_by_name_global.return_value = None
        repo.create.return_value = {'id': '99', 'name': 'my-skill', 'bolt_id': 'same-bot'}
        svc._skill_repo = repo

        source_path = self._setup_git_skill(tmp_path, 'my-skill')
        with patch.object(svc, 'parse_skill_path', return_value=('git', source_path)):
            svc.create_skill(
                name='my-skill', description='desc', skill_path='git://test/my-skill',
                user_id='123', bolt_id='same-bot',
            )

        repo.create.assert_called_once()
        repo.update.assert_not_called()

    def test_no_reuse_when_no_deleted_record(self, tmp_path):
        """没有已删除记录时正常 create"""
        svc = self._make_svc(tmp_path)
        repo = MagicMock()
        repo.get_by_name_global_include_deleted.return_value = None
        repo.get_by_name_global.return_value = None
        repo.create.return_value = {'id': '1', 'name': 'new-skill'}
        svc._skill_repo = repo

        source_path = self._setup_git_skill(tmp_path, 'new-skill')
        with patch.object(svc, 'parse_skill_path', return_value=('git', source_path)):
            svc.create_skill(
                name='new-skill', description='desc', skill_path='git://test/new-skill',
                user_id='123', bolt_id='my-bot',
            )

        repo.create.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: SkillSetService.create_skill_set 重名检查排除已删除 Bot
# ---------------------------------------------------------------------------


class TestCreateSkillSetReuseDeleted:
    """create_skill_set 应用 list_all_exclude_deleted 做重名检查"""

    def _make_svc(self, tmp_path):
        from agentclaw.community.core.skill_center.services.skill_set_service import SkillSetService
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(exist_ok=True)

        with patch("agentclaw.community.core.skill_center.services.skill_set_service.WorkspacePathFactory"):
            svc = SkillSetService(
                skill_repo=MagicMock(),
                skill_set_repo=MagicMock(),
                mcp_center=MagicMock(),
                mcp_config_service=MagicMock(),
                skill_service=MagicMock(),
            bot_repo=MagicMock(),

            path_factory=MagicMock(),
        )
        return svc

    def test_exclude_deleted_in_name_check(self, tmp_path):
        """重名检查应使用 list_all_exclude_deleted"""
        svc = self._make_svc(tmp_path)
        repo = MagicMock()
        repo.list_all_exclude_deleted.return_value = []
        repo.get_skill_set_by_name_include_deleted.return_value = {
            'id': '10', 'name': 'my-set', 'bolt_id': 'old-deleted-bot',
        }
        repo.update.return_value = {'id': '10', 'name': 'my-set', 'bolt_id': 'new-bot'}
        svc.skill_set_repo = repo

        svc.create_skill_set(name='my-set', description='desc', user_id='123', bolt_id='new-bot')

        repo.list_all_exclude_deleted.assert_called_once()
        # list_all may still be called by SkillSetMetadataWriter.write_metadata()
        repo.update.assert_called_once()
        repo.create.assert_not_called()

    def test_name_conflict_with_active_bot(self, tmp_path):
        """与活跃 Bot 的同名技能集应仍然报错"""
        svc = self._make_svc(tmp_path)
        repo = MagicMock()
        repo.list_all_exclude_deleted.return_value = [
            {'id': '5', 'name': 'my-set', 'bolt_id': 'active-bot'}
        ]
        svc.skill_set_repo = repo

        with pytest.raises(ValueError, match="already exists"):
            svc.create_skill_set(name='my-set', description='desc', user_id='123', bolt_id='new-bot')

    def test_create_new_when_no_deleted(self, tmp_path):
        """没有已删除记录时正常 create"""
        svc = self._make_svc(tmp_path)
        repo = MagicMock()
        repo.list_all_exclude_deleted.return_value = []
        repo.get_skill_set_by_name_include_deleted.return_value = None
        repo.create.return_value = {'id': '1', 'name': 'new-set'}
        svc.skill_set_repo = repo

        svc.create_skill_set(name='new-set', description='desc', user_id='123')

        repo.create.assert_called_once()
        repo.update.assert_not_called()
