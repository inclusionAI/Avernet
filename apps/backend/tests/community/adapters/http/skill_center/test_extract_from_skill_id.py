"""Tests for extract_from_skill_id and delete_skill interceptor integration."""
from __future__ import annotations

from unittest.mock import MagicMock, AsyncMock
import pytest

from agentclaw.community.core.auth.models import AuthenticatedIdentity
from agentclaw.community.core.bot_collaborator.interceptor import (
    InterceptorContext,
    PermissionParams,
    CollaboratorPermissionInterceptor,
)
from agentclaw.community.adapters.http.skill_center.skills import extract_from_skill_id, delete_skill


@pytest.mark.unit
class TestExtractFromSkillId:
    """extract_from_skill_id 测试用例。"""

    @pytest.mark.asyncio
    async def test_returns_empty_when_skill_id_is_empty(self):
        """skill_id 为空时返回空 PermissionParams。"""
        ctx = MagicMock()
        ctx.injector = MagicMock()

        result = await extract_from_skill_id(skill_id="", ctx=ctx)

        assert result.bot_id is None
        assert result.owner_id is None

    @pytest.mark.asyncio
    async def test_returns_empty_when_skill_id_is_none(self):
        """skill_id 为 None 时返回空 PermissionParams。"""
        ctx = MagicMock()
        ctx.injector = MagicMock()

        result = await extract_from_skill_id(skill_id=None, ctx=ctx)

        assert result.bot_id is None
        assert result.owner_id is None

    @pytest.mark.asyncio
    async def test_returns_empty_when_injector_is_none(self):
        """injector 为 None 时返回空 PermissionParams。"""
        ctx = MagicMock()
        ctx.injector = None

        result = await extract_from_skill_id(skill_id="skill-123", ctx=ctx)

        assert result.bot_id is None
        assert result.owner_id is None

    @pytest.mark.asyncio
    async def test_returns_empty_when_factory_get_fails(self):
        """injector.get 抛出异常时返回空 PermissionParams。"""
        ctx = MagicMock()
        ctx.injector = MagicMock()
        ctx.injector.get.side_effect = Exception("DI error")

        result = await extract_from_skill_id(skill_id="skill-123", ctx=ctx)

        assert result.bot_id is None
        assert result.owner_id is None

    @pytest.mark.asyncio
    async def test_returns_empty_when_skill_not_found(self):
        """skill 不存在时返回空 PermissionParams。"""
        from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol

        ctx = MagicMock()
        ctx.injector = MagicMock()

        mock_factory = MagicMock(spec=SkillServiceFactoryProtocol)
        mock_service = MagicMock()
        mock_service.get_skill.return_value = None
        mock_factory.create.return_value = mock_service
        ctx.injector.get.return_value = mock_factory

        result = await extract_from_skill_id(skill_id="skill-404", ctx=ctx)

        assert result.bot_id is None
        assert result.owner_id is None
        mock_service.get_skill.assert_called_once_with("skill-404")

    @pytest.mark.asyncio
    async def test_returns_params_when_skill_found(self):
        """skill 存在时返回正确 PermissionParams。"""
        from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol

        ctx = MagicMock()
        ctx.injector = MagicMock()

        mock_factory = MagicMock(spec=SkillServiceFactoryProtocol)
        mock_service = MagicMock()
        mock_service.get_skill.return_value = {
            "id": "skill-123",
            "name": "test-skill",
            "bolt_id": "bot-456",
            "user_id": "user-789",
        }
        mock_factory.create.return_value = mock_service
        ctx.injector.get.return_value = mock_factory

        result = await extract_from_skill_id(skill_id="skill-123", ctx=ctx)

        assert result.bot_id == "bot-456"
        assert result.owner_id == "user-789"
        mock_service.get_skill.assert_called_once_with("skill-123")

    @pytest.mark.asyncio
    async def test_handles_missing_bolt_id(self):
        """skill 缺少 bolt_id 时 bot_id 为 None。"""
        from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol

        ctx = MagicMock()
        ctx.injector = MagicMock()

        mock_factory = MagicMock(spec=SkillServiceFactoryProtocol)
        mock_service = MagicMock()
        mock_service.get_skill.return_value = {
            "id": "skill-123",
            "name": "test-skill",
            "user_id": "user-789",
            # bolt_id 缺失
        }
        mock_factory.create.return_value = mock_service
        ctx.injector.get.return_value = mock_factory

        result = await extract_from_skill_id(skill_id="skill-123", ctx=ctx)

        assert result.bot_id is None
        assert result.owner_id == "user-789"

    @pytest.mark.asyncio
    async def test_handles_missing_user_id(self):
        """skill 缺少 user_id 时 owner_id 为 None。"""
        from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol

        ctx = MagicMock()
        ctx.injector = MagicMock()

        mock_factory = MagicMock(spec=SkillServiceFactoryProtocol)
        mock_service = MagicMock()
        mock_service.get_skill.return_value = {
            "id": "skill-123",
            "name": "test-skill",
            "bolt_id": "bot-456",
            # user_id 缺失
        }
        mock_factory.create.return_value = mock_service
        ctx.injector.get.return_value = mock_factory

        result = await extract_from_skill_id(skill_id="skill-123", ctx=ctx)

        assert result.bot_id == "bot-456"
        assert result.owner_id is None

    @pytest.mark.asyncio
    async def test_handles_service_exception(self):
        """service.get_skill 抛出异常时返回空 PermissionParams。"""
        from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol

        ctx = MagicMock()
        ctx.injector = MagicMock()

        mock_factory = MagicMock(spec=SkillServiceFactoryProtocol)
        mock_service = MagicMock()
        mock_service.get_skill.side_effect = Exception("DB error")
        mock_factory.create.return_value = mock_service
        ctx.injector.get.return_value = mock_factory

        result = await extract_from_skill_id(skill_id="skill-123", ctx=ctx)

        assert result.bot_id is None
        assert result.owner_id is None


@pytest.mark.unit
class TestExtractFromSkillIdWithInterceptor:
    """extract_from_skill_id 与 CollaboratorPermissionInterceptor 集成测试。"""

    @pytest.mark.asyncio
    async def test_interceptor_uses_extractor_correctly(self):
        """测试 CollaboratorPermissionInterceptor 使用 extract_from_skill_id 提取参数。"""
        from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
        from agentclaw.community.core.bot_collaborator.interceptor import CollaboratorPermissionInterceptor

        # 准备 mock
        ctx = MagicMock()
        ctx.injector = MagicMock()

        mock_factory = MagicMock(spec=SkillServiceFactoryProtocol)
        mock_service = MagicMock()
        mock_service.get_skill.return_value = {
            "id": "skill-123",
            "name": "test-skill",
            "bolt_id": "bot-456",
            "user_id": "owner-789",
        }
        mock_factory.create.return_value = mock_service
        ctx.injector.get.return_value = mock_factory

        user = AuthenticatedIdentity(id="1", operatorName="test", staffId="owner-789")
        ctx.user = user
        ctx.route_kwargs = {"skill_id": "skill-123"}
        ctx.response = None
        ctx.metadata = {}

        # 创建拦截器
        interceptor = CollaboratorPermissionInterceptor(
            params_extractor=extract_from_skill_id,
            extractor_params={"skill_id": "$skill_id"},
        )

        # 执行提取
        params = await interceptor._extract_params(ctx)

        assert params.bot_id == "bot-456"
        assert params.owner_id == "owner-789"

    @pytest.mark.asyncio
    async def test_owner_can_pass_permission_check(self):
        """Owner 可以通过权限检查。"""
        from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
        from agentclaw.community.core.bot_collaborator.interceptor import CollaboratorPermissionInterceptor

        ctx = MagicMock()
        ctx.injector = MagicMock()

        mock_factory = MagicMock(spec=SkillServiceFactoryProtocol)
        mock_service = MagicMock()
        mock_service.get_skill.return_value = {
            "id": "skill-123",
            "name": "test-skill",
            "bolt_id": "bot-456",
            "user_id": "owner-789",
        }
        mock_factory.create.return_value = mock_service
        ctx.injector.get.return_value = mock_factory

        # owner 是当前用户
        user = AuthenticatedIdentity(id="1", operatorName="test", staffId="owner-789")
        ctx.user = user
        ctx.route_kwargs = {"skill_id": "skill-123"}
        ctx.response = None
        ctx.metadata = {}

        interceptor = CollaboratorPermissionInterceptor(
            params_extractor=extract_from_skill_id,
            extractor_params={"skill_id": "$skill_id"},
        )

        result = await interceptor.before(ctx)

        # owner 应该直接放行
        assert result is not None
        assert ctx.metadata.get("permission_level") == "OWNER"

    @pytest.mark.asyncio
    async def test_non_owner_without_collaborators_rejected(self):
        """非 owner 且无协作者时返回 403。"""
        from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
        from agentclaw.community.core.bot_collaborator.interceptor import CollaboratorPermissionInterceptor

        ctx = MagicMock()
        ctx.injector = MagicMock()

        mock_factory = MagicMock(spec=SkillServiceFactoryProtocol)
        mock_service = MagicMock()
        mock_service.get_skill.return_value = {
            "id": "skill-123",
            "name": "test-skill",
            "bolt_id": "bot-456",
            "user_id": "owner-789",
        }
        mock_factory.create.return_value = mock_service
        ctx.injector.get.return_value = mock_factory

        # 非登 owner 用户
        user = AuthenticatedIdentity(id="1", operatorName="test", staffId="other-user")
        ctx.user = user
        ctx.route_kwargs = {"skill_id": "skill-123"}
        ctx.response = None
        ctx.metadata = {}

        interceptor = CollaboratorPermissionInterceptor(
            params_extractor=extract_from_skill_id,
            extractor_params={"skill_id": "$skill_id"},
        )

        result = await interceptor.before(ctx)

        # 非 owner 且无协作者应该被拒绝
        assert result is None
        assert ctx.response is not None
        assert ctx.response.error_code == 403

    @pytest.mark.asyncio
    async def test_skill_not_found_skips_permission_check(self):
        """skill 未找到时跳过权限检查（返回空 PermissionParams）。"""
        from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
        from agentclaw.community.core.bot_collaborator.interceptor import CollaboratorPermissionInterceptor

        ctx = MagicMock()
        ctx.injector = MagicMock()

        mock_factory = MagicMock(spec=SkillServiceFactoryProtocol)
        mock_service = MagicMock()
        mock_service.get_skill.return_value = None  # skill 未找到
        mock_factory.create.return_value = mock_service
        ctx.injector.get.return_value = mock_factory

        user = AuthenticatedIdentity(id="1", operatorName="test", staffId="user-123")
        ctx.user = user
        ctx.route_kwargs = {"skill_id": "skill-404"}
        ctx.response = None
        ctx.metadata = {}

        interceptor = CollaboratorPermissionInterceptor(
            params_extractor=extract_from_skill_id,
            extractor_params={"skill_id": "$skill_id"},
        )

        result = await interceptor.before(ctx)

        # owner_id 为 None 时通过 _resolve_owner_id 解析归属；
        # bot_id 为 "skill-404" 且无 injector 可用 → 回退到当前用户 ID → owner 放行
        assert result is not None
        assert ctx.metadata.get("owner_id_resolved") is True


@pytest.mark.unit
class TestDeleteSkillRouteInterceptor:
    """delete_skill 路由拦截器装饰器测试。"""

    def test_delete_skill_has_interceptor_decorator(self):
        """验证 delete_skill 路由已添加拦截器装饰器。"""
        # 检查 delete_skill 是否被 with_interceptors 装饰
        # with_interceptors 会在 __wrapped__ 属性中保存原始函数
        assert hasattr(delete_skill, '__wrapped__'), \
            "delete_skill 应该被 with_interceptors 装饰"

    def test_delete_skill_has_user_id_parameter(self):
        """验证 delete_skill 路由添加了 user_id 参数。"""
        import inspect
        # __wrapped__ 保存了被装饰的原始函数
        wrapped_func = getattr(delete_skill, '__wrapped__', delete_skill)
        sig = inspect.signature(wrapped_func)
        params = sig.parameters

        assert 'user_id' in params, "delete_skill 应该有 user_id 参数"
        user_id_param = params['user_id']
        # 检查参数类型注解包含 Optional 或 None
        param_str = str(user_id_param)
        assert 'str' in param_str, f"user_id 参数应该是 str 类型，实际为: {param_str}"