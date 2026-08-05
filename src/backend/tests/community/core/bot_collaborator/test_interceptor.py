"""Tests for collaborator permission interceptor."""
import pytest
from unittest.mock import MagicMock, patch

from agentclaw.community.core.auth.models import AuthenticatedIdentity
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    InterceptorContext,
    PermissionParams,
    SimplePermissionParamsExtractor,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel


class TestInterceptorContext:
    """InterceptorContext 测试用例。"""

    def test_create_context(self):
        """测试创建上下文。"""
        user = AuthenticatedIdentity(id="1", operatorName="test", staffId="123")
        ctx = InterceptorContext(
            user=user,
            route_kwargs={"request": {"bot_id": "bot_001"}},
        )

        assert ctx.user == user
        assert ctx.route_kwargs == {"request": {"bot_id": "bot_001"}}
        assert ctx.response is None
        assert ctx.metadata == {}

    def test_create_context_with_defaults(self):
        """测试使用默认值创建上下文。"""
        ctx = InterceptorContext(user=None)

        assert ctx.user is None
        assert ctx.route_kwargs == {}
        assert ctx.response is None
        assert ctx.metadata == {}


class TestSimplePermissionParamsExtractor:
    """SimplePermissionParamsExtractor 测试用例。"""

    @pytest.mark.asyncio
    async def test_extract_from_route_kwargs(self):
        """测试从 route_kwargs 提取参数。"""
        extractor = SimplePermissionParamsExtractor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
        )

        class MockRequest:
            bot_id = "bot_001"
            owner_id = "user_001"

        ctx = InterceptorContext(
            user=None,
            route_kwargs={"request": MockRequest()},
        )

        params = await extractor.extract(ctx)
        assert params.bot_id == "bot_001"
        assert params.owner_id == "user_001"

    @pytest.mark.asyncio
    async def test_extract_missing_params(self):
        """测试提取不存在的参数。"""
        extractor = SimplePermissionParamsExtractor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
        )

        ctx = InterceptorContext(user=None, route_kwargs={})

        params = await extractor.extract(ctx)
        assert params.bot_id is None
        assert params.owner_id is None

    @pytest.mark.asyncio
    async def test_extract_from_dict(self):
        """测试从字典提取参数。"""
        extractor = SimplePermissionParamsExtractor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
        )

        ctx = InterceptorContext(
            user=None,
            route_kwargs={"request": {"bot_id": "bot_002", "owner_id": "user_002"}},
        )

        params = await extractor.extract(ctx)
        assert params.bot_id == "bot_002"
        assert params.owner_id == "user_002"


class TestCollaboratorPermissionInterceptor:
    """CollaboratorPermissionInterceptor 测试用例。"""

    def setup_method(self):
        self.user = AuthenticatedIdentity(id="1", operatorName="test", staffId="user_001")

    def test_init_default_required_level(self):
        """测试默认权限级别为 ADMIN。"""
        interceptor = CollaboratorPermissionInterceptor()
        assert interceptor.required_level == PermissionLevel.ADMIN

    def test_init_custom_required_level(self):
        """测试自定义权限级别。"""
        interceptor = CollaboratorPermissionInterceptor(
            required_level=PermissionLevel.OWNER
        )
        assert interceptor.required_level == PermissionLevel.OWNER

    @pytest.mark.asyncio
    async def test_before_no_user_returns_none(self):
        """测试无用户时返回 400。"""
        interceptor = CollaboratorPermissionInterceptor()
        ctx = InterceptorContext(user=None, route_kwargs={})

        result = await interceptor.before(ctx)
        assert result is None
        assert ctx.response is not None
        assert ctx.response.error_code == 400
        assert "无法获取用户信息" in ctx.response.message

    @pytest.mark.asyncio
    async def test_before_owner_bypasses_check(self):
        """测试 owner 直接放行。"""
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
        )

        # user_001 是 owner
        ctx = InterceptorContext(
            user=self.user,
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "user_001"}},
        )

        result = await interceptor.before(ctx)
        assert result is not None
        assert ctx.metadata.get("permission_level") == "OWNER"

    @pytest.mark.asyncio
    async def test_before_no_owner_id_resolves_via_repo(self):
        """测试无 owner_id 时通过 BotRepository 解析归属，而非跳过权限检查。"""
        from agentclaw.community.core.bot_management.repository.protocol import BotRepository

        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
        )

        mock_injector = MagicMock()
        ctx = InterceptorContext(
            user=self.user,
            route_kwargs={"request": {"bot_id": "bot_001"}},  # 无 owner_id
            injector=mock_injector,
        )

        # Mock BotRepository 返回 bot 记录，owner 为 user_001（当前用户）
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"owner_id": "user_001"}
        # 只让 BotRepository 解析到 mock_repo，其余 service 返回 None
        mock_injector.get.side_effect = (
            lambda cls: mock_repo if cls is BotRepository else None
        )

        result = await interceptor.before(ctx)
        # 当前用户即 owner，应直接放行
        assert result is not None
        assert ctx.metadata.get("permission_level") == "OWNER"
        assert ctx.metadata.get("owner_id_resolved") is True

    @pytest.mark.asyncio
    async def test_before_no_owner_id_rejects_non_owner(self):
        """测试无 owner_id 时解析到非当前用户归属 → 403 拒绝。"""
        from agentclaw.community.core.bot_management.repository.protocol import BotRepository

        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
        )

        mock_injector = MagicMock()
        ctx = InterceptorContext(
            user=self.user,  # user_001
            route_kwargs={"request": {"bot_id": "bot_other"}},  # 无 owner_id
            injector=mock_injector,
        )

        # Mock BotRepository 返回 bot 记录，owner 是别人
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"owner_id": "owner_other"}
        # CollaboratorLockService 也需要 mock（否则 get 返回 None）
        mock_injector.get.side_effect = (
            lambda cls: mock_repo if cls is BotRepository else None
        )

        result = await interceptor.before(ctx)
        # 当前用户不是 owner，无协作者 → 403
        assert result is None
        assert ctx.response is not None
        assert ctx.response.error_code == 403
        assert ctx.metadata.get("owner_id_resolved") is True

    @pytest.mark.asyncio
    async def test_before_no_owner_id_skips_when_repo_unavailable(self):
        """测试无 owner_id 且 BotRepository 不可用时回退到 skip（让业务层处理）。"""
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
        )

        ctx = InterceptorContext(
            user=self.user,
            route_kwargs={"request": {"bot_id": "bot_missing"}},  # 无 owner_id
            injector=None,  # injector 不可用
        )

        result = await interceptor.before(ctx)
        # DI 不完整，回退到 skip，让业务层返回 404
        assert result is not None
        assert ctx.metadata.get("permission_skipped") is True

    @pytest.mark.asyncio
    async def test_before_with_extractor_params(self):
        """测试使用 extractor_params 提取参数。"""
        async def mock_extractor(publish_id: str) -> PermissionParams:
            return PermissionParams(bot_id="bot_002", owner_id="user_002")

        interceptor = CollaboratorPermissionInterceptor(
            params_extractor=mock_extractor,
            extractor_params={"publish_id": "$request.publish_id"},
        )

        # user_001 是 owner（user_002）
        ctx = InterceptorContext(
            user=AuthenticatedIdentity(id="2", operatorName="test2", staffId="user_002"),
            route_kwargs={"request": {"publish_id": "123"}},
        )

        result = await interceptor.before(ctx)
        assert result is not None
        assert ctx.metadata.get("permission_level") == "OWNER"

    @pytest.mark.asyncio
    async def test_before_extractor_with_ctx_param(self):
        """测试 extractor 函数包含 ctx 参数。"""
        async def mock_extractor(ctx: InterceptorContext, publish_id: str) -> PermissionParams:
            # 可以访问 ctx.user
            assert ctx.user is not None
            return PermissionParams(bot_id="bot_003", owner_id="user_003")

        interceptor = CollaboratorPermissionInterceptor(
            params_extractor=mock_extractor,
            extractor_params={"publish_id": "$request.publish_id"},
        )

        ctx = InterceptorContext(
            user=AuthenticatedIdentity(id="3", operatorName="test3", staffId="user_003"),
            route_kwargs={"request": {"publish_id": "456"}},
        )

        result = await interceptor.before(ctx)
        assert result is not None

    @pytest.mark.asyncio
    async def test_before_sync_extractor(self):
        """测试同步 extractor 函数。"""
        def sync_extractor(publish_id: str) -> PermissionParams:
            return PermissionParams(bot_id="bot_004", owner_id="user_004")

        interceptor = CollaboratorPermissionInterceptor(
            params_extractor=sync_extractor,
            extractor_params={"publish_id": "$request.publish_id"},
        )

        ctx = InterceptorContext(
            user=AuthenticatedIdentity(id="4", operatorName="test4", staffId="user_004"),
            route_kwargs={"request": {"publish_id": "789"}},
        )

        result = await interceptor.before(ctx)
        assert result is not None
        assert ctx.metadata.get("permission_level") == "OWNER"


class TestPermissionParams:
    """PermissionParams 测试用例。"""

    def test_create_with_defaults(self):
        """测试使用默认值创建。"""
        params = PermissionParams()
        assert params.bot_id is None
        assert params.owner_id is None

    def test_create_with_values(self):
        """测试使用值创建。"""
        params = PermissionParams(bot_id="bot_001", owner_id="user_001")
        assert params.bot_id == "bot_001"
        assert params.owner_id == "user_001"


class TestPersistAuditLog:
    """persist_audit_log 参数测试用例。"""

    def test_persist_audit_log_default_true(self):
        """测试默认开启审计入库。"""
        interceptor = CollaboratorPermissionInterceptor()
        assert interceptor.persist_audit_log is True

    def test_persist_audit_log_explicit_true(self):
        """测试显式开启审计入库。"""
        interceptor = CollaboratorPermissionInterceptor(persist_audit_log=True)
        assert interceptor.persist_audit_log is True

    def test_persist_audit_log_explicit_false(self):
        """测试显式禁用审计入库。"""
        interceptor = CollaboratorPermissionInterceptor(persist_audit_log=False)
        assert interceptor.persist_audit_log is False

    def test_audit_excluded_params_are_normalized(self):
        interceptor = CollaboratorPermissionInterceptor(
            audit_excluded_params={"request"},
        )
        assert interceptor.persist_audit_log is True
        assert interceptor.audit_excluded_params == frozenset({"request"})

    @pytest.mark.asyncio
    async def test_after_skip_when_disabled(self):
        """测试 persist_audit_log=False 时 after 方法直接返回。"""
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
            persist_audit_log=False,
        )

        user = AuthenticatedIdentity(id="1", operatorName="test", staffId="user_001")
        ctx = InterceptorContext(
            user=user,
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "user_001"}},
            metadata={"permission_level": "OWNER"},
        )

        # after 方法应该在 persist_audit_log=False 时直接返回，不执行任何操作
        # 这里验证不会抛出异常
        await interceptor.after(ctx)

        # 验证没有尝试获取 log_repo（因为没有设置 metadata）
        assert ctx.metadata.get("_log_inserted") is None


class TestSkipLockCheck:
    """skip_lock_check 和 persist_audit_log 跳过锁检查测试用例。"""

    def setup_method(self):
        self.user = AuthenticatedIdentity(id="1", operatorName="test", staffId="user_001")
        self.owner = AuthenticatedIdentity(id="2", operatorName="owner", staffId="owner_001")

    @pytest.mark.asyncio
    async def test_skip_lock_check_bypasses_lock(self):
        """测试 skip_lock_check=True 时跳过锁检查。"""
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
            skip_lock_check=True,
        )

        # 模拟有协作者的场景（通过 mock lock_info）
        ctx = InterceptorContext(
            user=self.user,  # user_001 非 owner
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "owner_001"}},
        )

        # Mock lock_service 返回有协作者但无锁
        mock_lock_info = MagicMock()
        mock_lock_info.has_collaborators = True
        mock_lock_info.lock = None  # 无锁

        with patch.object(interceptor, '_get_lock_service') as mock_get_lock:
            mock_lock_service = MagicMock()
            mock_lock_service.get_lock_info.return_value = mock_lock_info
            mock_get_lock.return_value = mock_lock_service

            # Mock collaborator_service 返回有权限
            with patch.object(interceptor, '_get_collaborator_service') as mock_get_collab:
                mock_collab_service = MagicMock()
                mock_collab_service.check_collaborator_permission.return_value = {
                    "has_permission": True,
                    "level": "ADMIN",
                }
                mock_get_collab.return_value = mock_collab_service

                result = await interceptor.before(ctx)

                # 应该放行（跳过锁检查）
                assert result is not None
                assert ctx.response is None
                # 应该调用了协作者权限检查
                mock_collab_service.check_collaborator_permission.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_audit_log_false_bypasses_lock(self):
        """测试 persist_audit_log=False 时也跳过锁检查。"""
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
            persist_audit_log=False,  # 禁用审计日志
        )

        # 模拟有协作者的场景
        ctx = InterceptorContext(
            user=self.user,  # user_001 非 owner
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "owner_001"}},
        )

        # Mock lock_service 返回有协作者但无锁
        mock_lock_info = MagicMock()
        mock_lock_info.has_collaborators = True
        mock_lock_info.lock = None  # 无锁

        with patch.object(interceptor, '_get_lock_service') as mock_get_lock:
            mock_lock_service = MagicMock()
            mock_lock_service.get_lock_info.return_value = mock_lock_info
            mock_get_lock.return_value = mock_lock_service

            # Mock collaborator_service 返回有权限
            with patch.object(interceptor, '_get_collaborator_service') as mock_get_collab:
                mock_collab_service = MagicMock()
                mock_collab_service.check_collaborator_permission.return_value = {
                    "has_permission": True,
                    "level": "ADMIN",
                }
                mock_get_collab.return_value = mock_collab_service

                result = await interceptor.before(ctx)

                # 应该放行（跳过锁检查）
                assert result is not None
                assert ctx.response is None
                # 应该调用了协作者权限检查
                mock_collab_service.check_collaborator_permission.assert_called_once()

    @pytest.mark.asyncio
    async def test_both_flags_false_requires_lock(self):
        """测试两个参数都为 False（默认）时需要持锁才能操作。"""
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
            # skip_lock_check=False (默认)
            # persist_audit_log=True (默认)
        )

        # 模拟有协作者的场景
        ctx = InterceptorContext(
            user=self.user,  # user_001 非 owner
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "owner_001"}},
        )

        # Mock lock_service 返回有协作者但无锁
        mock_lock_info = MagicMock()
        mock_lock_info.has_collaborators = True
        mock_lock_info.lock = None  # 无锁

        with patch.object(interceptor, '_get_lock_service') as mock_get_lock:
            mock_lock_service = MagicMock()
            mock_lock_service.get_lock_info.return_value = mock_lock_info
            mock_get_lock.return_value = mock_lock_service

            result = await interceptor.before(ctx)

            # 应该被拒绝（需要先获取锁）
            assert result is None
            assert ctx.response is not None
            assert ctx.response.error_code == 423
            assert "请先获取编辑锁" in ctx.response.message


class TestExtractOwnerFromRequestBody:
    """测试从 request body 中提取 owner_id 参数。

    这个测试类验证 CollaboratorPermissionInterceptor 在 bot_public API 中的使用模式：
    - bot_id="$bot_id" (从 path 参数提取)
    - owner_id="$req.owner_id" (从 request body 提取，参数名为 req)
    """

    @pytest.mark.asyncio
    async def test_extract_owner_id_from_request_body_dict(self):
        """测试从 request body (dict) 中提取 owner_id。"""
        from agentclaw.community.adapters.http.bot_public.schemas import BotPublicRequest

        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$bot_id",
            owner_id="$req.owner_id",
        )

        # 模拟 request body
        request_body = BotPublicRequest(
            public="1",
            permission_owner="caller",
            friend_approval="0",
            owner_id="u_owner_001",
        )

        user = AuthenticatedIdentity(id="1", operatorName="test", staffId="u_collab")
        ctx = InterceptorContext(
            user=user,
            route_kwargs={
                "bot_id": "bot_001",
                "req": request_body,  # 使用 "req" 作为 key，匹配 FastAPI 路由参数名
            },
        )

        # owner 是 u_owner_001，当前用户是 u_collab
        # 应该检查协作者权限而不是直接放行
        await interceptor.before(ctx)

        # 由于没有 CollaboratorService，owner 不等于 user_id 时会放行
        # 但 metadata 中应该正确记录了 owner_id
        assert ctx.metadata.get("_log_owner_id") == "u_owner_001"
        assert ctx.metadata.get("_log_bot_id") == "bot_001"

    @pytest.mark.asyncio
    async def test_extract_owner_id_none_resolves_via_repo(self):
        """测试 request body 未提供 owner_id 时通过 BotRepository 解析归属。"""
        from agentclaw.community.adapters.http.bot_public.schemas import BotPublicRequest
        from agentclaw.community.core.bot_management.repository.protocol import BotRepository

        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$bot_id",
            owner_id="$req.owner_id",
        )

        # 模拟未提供 owner_id 的 request body
        request_body = BotPublicRequest(
            public="1",
            permission_owner="caller",
            friend_approval="0",
            # owner_id 未提供，默认为 None
        )

        user = AuthenticatedIdentity(id="1", operatorName="test", staffId="u_user")
        mock_injector = MagicMock()
        ctx = InterceptorContext(
            user=user,
            route_kwargs={
                "bot_id": "bot_002",
                "req": request_body,  # 使用 "req" 作为 key
            },
            injector=mock_injector,
        )

        # Mock BotRepository 返回 bot 记录，owner 是别人
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"owner_id": "u_resolved_owner"}
        mock_injector.get.side_effect = (
            lambda cls: mock_repo if cls is BotRepository else None
        )

        result = await interceptor.before(ctx)

        # owner_id 被解析为 bot 的 owner_id，当前用户不是 owner → 403
        assert ctx.metadata.get("owner_id_resolved") is True
        assert ctx.metadata.get("_log_owner_id") == "u_resolved_owner"
        assert result is None
        assert ctx.response is not None
        assert ctx.response.error_code == 403

    @pytest.mark.asyncio
    async def test_owner_id_equals_user_id_bypasses_collab_check(self):
        """测试 owner_id 等于 user_id 时直接放行（owner 权限）。"""
        from agentclaw.community.adapters.http.bot_public.schemas import BotPublicRequest

        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$bot_id",
            owner_id="$req.owner_id",
        )

        # owner_id 与当前用户相同
        request_body = BotPublicRequest(
            public="1",
            permission_owner="caller",
            friend_approval="0",
            owner_id="u_same_user",
        )

        user = AuthenticatedIdentity(id="1", operatorName="test", staffId="u_same_user")
        ctx = InterceptorContext(
            user=user,
            route_kwargs={
                "bot_id": "bot_003",
                "req": request_body,  # 使用 "req" 作为 key
            },
        )

        result = await interceptor.before(ctx)

        # owner 等于 user_id 时应该直接放行
        assert result is not None
        assert ctx.metadata.get("permission_level") == "OWNER"

    @pytest.mark.asyncio
    async def test_interceptor_with_pydantic_model_attribute(self):
        """测试 Pydantic 模型属性访问。"""
        from agentclaw.community.adapters.http.bot_public.schemas import BotPublicRequest

        # 创建 Pydantic 模型实例
        request_body = BotPublicRequest(
            public="1",
            permission_owner="caller",
            friend_approval="0",
            owner_id="u_pydantic_owner",
        )

        # 验证属性访问
        assert request_body.owner_id == "u_pydantic_owner"
        assert request_body.public == "1"

        # 验证表达式解析器可以正确访问
        from agentclaw.community.core.bot_collaborator.interceptor.expression import ExpressionResolver

        resolver = ExpressionResolver()
        route_kwargs = {"bot_id": "bot_test", "req": request_body}

        # 解析 $req.owner_id
        owner_id = resolver.resolve("$req.owner_id", route_kwargs)
        assert owner_id == "u_pydantic_owner"

        # 解析 $bot_id
        bot_id = resolver.resolve("$bot_id", route_kwargs)
        assert bot_id == "bot_test"


class TestResolveOwnerId:
    """_resolve_owner_id 方法测试用例。"""

    def setup_method(self):
        self.user = AuthenticatedIdentity(id="1", operatorName="test", staffId="user_001")

    def test_historical_default_bot_id_short_circuits_to_caller(self):
        """存量 bot_id="default" 保留短路 → 返回当前 user_id。

        单租户内每 owner 都有一条 bot_id="default",repo.get_by_id("default")
        会歧义命中任意 owner 的 default bot,导致串户;旧语义 default=我自己的 bot,
        保留短路避免协作者鉴权用错 owner_id。新 bot 永不为 default,此分支仅命中存量。
        """
        interceptor = CollaboratorPermissionInterceptor()
        ctx = InterceptorContext(
            user=self.user, route_kwargs={}, injector=MagicMock(),
        )

        mock_repo = MagicMock()
        ctx.injector.get.return_value = mock_repo

        owner = interceptor._resolve_owner_id(ctx, "default", "user001")
        assert owner == "user001"
        # default 短路:不走 repo.get_by_id(避免歧义)
        mock_repo.get_by_id.assert_not_called()

    def test_missing_bot_id_returns_current_user(self):
        """bot_id 缺失(None/空) → 返回当前 user_id(语义=我的 bot),保持短路。"""
        interceptor = CollaboratorPermissionInterceptor()
        ctx = InterceptorContext(user=self.user, route_kwargs={})

        assert interceptor._resolve_owner_id(ctx, None, "user_001") == "user_001"
        assert interceptor._resolve_owner_id(ctx, "", "user_001") == "user_001"

    def test_resolves_bot_id_via_repo(self):
        """通过 BotRepository.get_by_id 解析 bot 归属。"""
        interceptor = CollaboratorPermissionInterceptor()
        ctx = InterceptorContext(
            user=self.user, route_kwargs={}, injector=MagicMock(),
        )

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"owner_id": "owner_abc"}
        ctx.injector.get.return_value = mock_repo

        result = interceptor._resolve_owner_id(ctx, "bot_123", "user_001")
        assert result == "owner_abc"
        mock_repo.get_by_id.assert_called_once_with("bot_123")

    def test_returns_none_when_bot_not_found(self):
        """bot_id 有值但 BotRepository 返回 None 时拒绝。"""
        interceptor = CollaboratorPermissionInterceptor()
        ctx = InterceptorContext(
            user=self.user, route_kwargs={}, injector=MagicMock(),
        )

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = None
        ctx.injector.get.return_value = mock_repo

        result = interceptor._resolve_owner_id(ctx, "bot_404", "user_001")
        assert result is None

    def test_returns_none_when_bot_has_no_owner_id(self):
        """bot 记录存在但无 owner_id 时拒绝。"""
        interceptor = CollaboratorPermissionInterceptor()
        ctx = InterceptorContext(
            user=self.user, route_kwargs={}, injector=MagicMock(),
        )

        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = {"bot_id": "bot_123"}  # 无 owner_id
        ctx.injector.get.return_value = mock_repo

        result = interceptor._resolve_owner_id(ctx, "bot_123", "user_001")
        assert result is None

    def test_returns_none_when_no_injector(self):
        """injector 不可用时返回 None（回退到 skip 让业务层处理）。"""
        interceptor = CollaboratorPermissionInterceptor()
        ctx = InterceptorContext(user=self.user, route_kwargs={}, injector=None)

        result = interceptor._resolve_owner_id(ctx, "bot_123", "user_001")
        assert result is None
