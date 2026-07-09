"""
单元测试：SkillMemberService

测试技能成员管理服务的业务逻辑。
Service 通过 Repository 注入实现数据库操作，本测试使用 Mock Repository。
"""
import pytest
from unittest.mock import MagicMock

from agentclaw.community.core.skill_center.services.skill_member_service import SkillMemberService


@pytest.fixture
def mock_repo():
    """创建模拟的 SkillMemberRepository"""
    repo = MagicMock()
    return repo


@pytest.fixture
def member_service(mock_repo):
    """创建 SkillMemberService 实例（注入 mock repo）"""
    return SkillMemberService(mock_repo)


class TestGetMembersBySkillUuid:
    """测试 get_members_by_skill_uuid 方法"""

    def test_get_members_success(self, member_service, mock_repo):
        """成功获取成员列表"""
        mock_repo.get_members_by_skill_uuid.return_value = [
            {"id": "1", "skill_uuid": "uuid-100", "user_id": "user123", "role": "admin"},
            {"id": "2", "skill_uuid": "uuid-100", "user_id": "user456", "role": "member"},
        ]

        result = member_service.get_members_by_skill_uuid("uuid-100")

        mock_repo.get_members_by_skill_uuid.assert_called_once_with("uuid-100")
        assert len(result) == 2
        assert result[0]["user_id"] == "user123"
        assert result[1]["user_id"] == "user456"

    def test_get_members_empty(self, member_service, mock_repo):
        """技能无成员时返回空列表"""
        mock_repo.get_members_by_skill_uuid.return_value = []

        result = member_service.get_members_by_skill_uuid("uuid-100")

        assert result == []


class TestGetMember:
    """测试 get_member 方法"""

    def test_get_member_found(self, member_service, mock_repo):
        """成功获取单个成员"""
        mock_repo.get_member.return_value = {
            "id": "1", "skill_uuid": "uuid-100", "user_id": "user123", "role": "admin"
        }

        result = member_service.get_member("uuid-100", "user123")

        mock_repo.get_member.assert_called_once_with("uuid-100", "user123")
        assert result is not None
        assert result["user_id"] == "user123"
        assert result["role"] == "admin"

    def test_get_member_not_found(self, member_service, mock_repo):
        """成员不存在"""
        mock_repo.get_member.return_value = None

        result = member_service.get_member("uuid-100", "nonexistent")

        assert result is None


class TestAddMember:
    """测试 add_member 方法"""

    def test_add_member_success(self, member_service, mock_repo):
        """成功添加成员"""
        mock_repo.add_member.return_value = {
            "id": "1", "skill_uuid": "uuid-100", "user_id": "user123", "role": "admin"
        }

        result = member_service.add_member("uuid-100", "user123", "admin")

        mock_repo.add_member.assert_called_once_with("uuid-100", "user123", "admin")
        assert result["role"] == "admin"

    def test_add_member_already_exists(self, member_service, mock_repo):
        """成员已存在"""
        mock_repo.add_member.side_effect = ValueError("already a member")

        with pytest.raises(ValueError, match="already a member"):
            member_service.add_member("uuid-100", "user123", "admin")

    def test_add_member_invalid_role(self, member_service, mock_repo):
        """角色不合法"""
        mock_repo.add_member.side_effect = ValueError("Invalid role")

        with pytest.raises(ValueError, match="Invalid role"):
            member_service.add_member("uuid-100", "user123", "super_admin")


class TestAddMembersBatch:
    """测试 add_members_batch 方法"""

    def test_batch_add_success(self, member_service, mock_repo):
        """批量添加成员成功"""
        members = [
            {"user_id": "user1", "role": "admin"},
            {"user_id": "user2", "role": "member"}
        ]

        mock_repo.add_member.side_effect = [
            {"id": "1", "skill_uuid": "uuid-100", "user_id": "user1", "role": "admin"},
            {"id": "2", "skill_uuid": "uuid-100", "user_id": "user2", "role": "member"},
        ]

        results = member_service.add_members_batch("uuid-100", members)

        assert len(results["success"]) == 2
        assert len(results["failed"]) == 0
        assert mock_repo.add_member.call_count == 2

    def test_batch_add_partial_failure(self, member_service, mock_repo):
        """批量添加部分失败"""
        members = [
            {"user_id": "user1", "role": "admin"},
            {"user_id": "user2", "role": "invalid_role"},  # 无效角色
            {"user_id": "", "role": "member"}  # 缺少 user_id
        ]

        mock_repo.add_member.side_effect = [
            {"id": "1", "skill_uuid": "uuid-100", "user_id": "user1", "role": "admin"},
            ValueError("Invalid role: invalid_role"),
        ]

        results = member_service.add_members_batch("uuid-100", members)

        assert len(results["success"]) == 1  # user1 成功
        assert len(results["failed"]) == 2  # user2 和空 user_id 失败
        # 验证失败原因
        failed_user_ids = [f.get("user_id") for f in results["failed"]]
        assert "" in failed_user_ids  # 空 user_id 失败
        failed_errors = [f.get("error") for f in results["failed"]]
        assert any("Invalid role" in str(e) for e in failed_errors)

    def test_batch_add_empty_user_id(self, member_service, mock_repo):
        """批量添加时空 user_id 被过滤"""
        members = [
            {"user_id": "", "role": "member"},
        ]

        results = member_service.add_members_batch("uuid-100", members)

        assert len(results["success"]) == 0
        assert len(results["failed"]) == 1
        mock_repo.add_member.assert_not_called()

    def test_batch_add_invalid_role_filtered_by_service(self, member_service, mock_repo):
        """批量添加时无效角色在 service 层被过滤（不调用 repo）"""
        members = [
            {"user_id": "user1", "role": "super_admin"},
        ]

        results = member_service.add_members_batch("uuid-100", members)

        assert len(results["success"]) == 0
        assert len(results["failed"]) == 1
        mock_repo.add_member.assert_not_called()


class TestRemoveMember:
    """测试 remove_member 方法"""

    def test_remove_member_success(self, member_service, mock_repo):
        """成功移除成员"""
        mock_repo.remove_member.return_value = True

        result = member_service.remove_member("uuid-100", "user123")

        mock_repo.remove_member.assert_called_once_with("uuid-100", "user123")
        assert result is True

    def test_remove_member_not_found(self, member_service, mock_repo):
        """成员不存在"""
        mock_repo.remove_member.side_effect = ValueError("Member not found")

        with pytest.raises(ValueError, match="Member not found"):
            member_service.remove_member("uuid-100", "nonexistent")


class TestUpdateMemberRole:
    """测试 update_member_role 方法"""

    def test_update_role_success(self, member_service, mock_repo):
        """成功更新角色"""
        mock_repo.update_member_role.return_value = {
            "id": "1", "skill_uuid": "uuid-100", "user_id": "user123", "role": "admin"
        }

        result = member_service.update_member_role("uuid-100", "user123", "admin")

        mock_repo.update_member_role.assert_called_once_with("uuid-100", "user123", "admin")
        assert result["role"] == "admin"

    def test_update_role_invalid_role(self, member_service, mock_repo):
        """角色不合法"""
        mock_repo.update_member_role.side_effect = ValueError("Invalid role")

        with pytest.raises(ValueError, match="Invalid role"):
            member_service.update_member_role("uuid-100", "user123", "owner")

    def test_update_role_member_not_found(self, member_service, mock_repo):
        """成员不存在"""
        mock_repo.update_member_role.side_effect = ValueError("Member not found")

        with pytest.raises(ValueError, match="Member not found"):
            member_service.update_member_role("uuid-100", "nonexistent", "admin")


class TestIsMember:
    """测试 is_member 方法"""

    def test_is_member_true(self, member_service, mock_repo):
        """用户是成员"""
        mock_repo.is_member.return_value = True

        result = member_service.is_member("uuid-100", "user123")

        mock_repo.is_member.assert_called_once_with("uuid-100", "user123")
        assert result is True

    def test_is_member_false(self, member_service, mock_repo):
        """用户不是成员"""
        mock_repo.is_member.return_value = False

        result = member_service.is_member("uuid-100", "nonexistent")

        assert result is False


class TestGetMemberRole:
    """测试 get_member_role 方法"""

    def test_get_role_admin(self, member_service, mock_repo):
        """获取管理员角色"""
        mock_repo.get_member_role.return_value = "admin"

        result = member_service.get_member_role("uuid-100", "user123")

        mock_repo.get_member_role.assert_called_once_with("uuid-100", "user123")
        assert result == "admin"

    def test_get_role_not_member(self, member_service, mock_repo):
        """用户不是成员"""
        mock_repo.get_member_role.return_value = None

        result = member_service.get_member_role("uuid-100", "nonexistent")

        assert result is None


class TestHasAdminRole:
    """测试 has_admin_role 方法"""

    def test_has_admin_true(self, member_service, mock_repo):
        """用户是管理员"""
        mock_repo.has_admin_role.return_value = True

        result = member_service.has_admin_role("uuid-100", "user123")

        mock_repo.has_admin_role.assert_called_once_with("uuid-100", "user123")
        assert result is True

    def test_has_admin_false_member(self, member_service, mock_repo):
        """用户是普通成员"""
        mock_repo.has_admin_role.return_value = False

        result = member_service.has_admin_role("uuid-100", "user123")

        assert result is False

    def test_has_admin_false_not_member(self, member_service, mock_repo):
        """用户不是成员"""
        mock_repo.has_admin_role.return_value = False

        result = member_service.has_admin_role("uuid-100", "nonexistent")

        assert result is False


class TestGetSkillUuidsByUserId:
    """测试 get_skill_uuids_by_user_id 方法"""

    def test_get_skill_uuids(self, member_service, mock_repo):
        """获取用户参与的技能列表"""
        mock_repo.get_skill_uuids_by_user_id.return_value = ["uuid-1", "uuid-2", "uuid-3"]

        result = member_service.get_skill_uuids_by_user_id("user123")

        mock_repo.get_skill_uuids_by_user_id.assert_called_once_with("user123")
        assert result == ["uuid-1", "uuid-2", "uuid-3"]

    def test_get_skill_uuids_empty(self, member_service, mock_repo):
        """用户未参与任何技能"""
        mock_repo.get_skill_uuids_by_user_id.return_value = []

        result = member_service.get_skill_uuids_by_user_id("nonexistent")

        assert result == []


class TestGetMembersBySkillUuids:
    """测试 get_members_by_skill_uuids 方法"""

    def test_batch_get_members(self, member_service, mock_repo):
        """批量获取多个技能的成员"""
        mock_repo.get_members_by_skill_uuids.return_value = {
            "uuid-1": [{"user_id": "user1", "role": "admin"}],
            "uuid-2": [{"user_id": "user2", "role": "member"}],
        }

        result = member_service.get_members_by_skill_uuids(["uuid-1", "uuid-2"])

        mock_repo.get_members_by_skill_uuids.assert_called_once_with(["uuid-1", "uuid-2"])
        assert "uuid-1" in result
        assert "uuid-2" in result

    def test_batch_get_members_empty(self, member_service, mock_repo):
        """空列表返回空字典"""
        mock_repo.get_members_by_skill_uuids.return_value = {}

        result = member_service.get_members_by_skill_uuids([])

        assert result == {}
