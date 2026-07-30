"""Tests for CollaboratorPermissionInterceptor coding-app logic.

Covers two changes from the session-lock feature branch:
1. ``_is_coding_app()`` — detects coding applications by active_engine + template_type.
2. The ``skip_lock`` branch in ``before()`` — coding apps with collaborators skip
   bot-level lock enforcement (session-level locks are used instead), but
   collaborator permission is still checked.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from agentclaw.community.core.auth.models import AuthenticatedIdentity
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    InterceptorContext,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel


# ============================================================================
# _is_coding_app unit tests
# ============================================================================


class TestIsCodingApp:
    """Tests for CollaboratorPermissionInterceptor._is_coding_app."""

    def setup_method(self):
        self.interceptor = CollaboratorPermissionInterceptor()

    def test_coding_app_return_true(self):
        """coding 应用 (claude_code + applicationCoding) -> True."""
        ctx = InterceptorContext(user=None, route_kwargs={})
        mock_injector = MagicMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot.return_value = {
            "active_engine": "claude_code",
            "template_type": "applicationCoding",
        }
        mock_injector.get.return_value = mock_bot_service
        ctx.injector = mock_injector

        result = self.interceptor._is_coding_app(ctx, "bot_123", "owner_001")
        assert result is True
        mock_bot_service.get_bot.assert_called_once_with("bot_123", "owner_001")

    def test_member_management_flag_return_true(self):
        """advanced_config.member_management=true -> True even when not coding app."""
        ctx = InterceptorContext(user=None, route_kwargs={})
        mock_injector = MagicMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot.return_value = {
            "active_engine": "openclaw",
            "template_type": "chat",
            "template_config": {
                "bot_template_config": {"advanced_config": {"member_management": True}}
            },
        }
        mock_injector.get.return_value = mock_bot_service
        ctx.injector = mock_injector

        result = self.interceptor._is_coding_app(ctx, "bot_123", "owner_001")
        assert result is True

    def test_member_management_flag_requires_boolean_true(self):
        """member_management 字符串 true 不放行。"""
        ctx = InterceptorContext(user=None, route_kwargs={})
        mock_injector = MagicMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot.return_value = {
            "active_engine": "openclaw",
            "template_type": "chat",
            "template_config": {
                "bot_template_config": {"advanced_config": {"member_management": "true"}}
            },
        }
        mock_injector.get.return_value = mock_bot_service
        ctx.injector = mock_injector

        result = self.interceptor._is_coding_app(ctx, "bot_123", "owner_001")
        assert result is False

    def test_service_bot_return_false(self):
        """Service Bot (not claude_code) -> False."""
        ctx = InterceptorContext(user=None, route_kwargs={})
        mock_injector = MagicMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot.return_value = {
            "active_engine": "openai",
            "template_type": "service",
        }
        mock_injector.get.return_value = mock_bot_service
        ctx.injector = mock_injector

        result = self.interceptor._is_coding_app(ctx, "bot_123", "owner_001")
        assert result is False

    def test_partial_coding_only_active_engine_return_false(self):
        """仅 active_engine 命中、template_type 不符 -> False."""
        ctx = InterceptorContext(user=None, route_kwargs={})
        mock_injector = MagicMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot.return_value = {
            "active_engine": "claude_code",
            "template_type": "chat",  # not applicationCoding
        }
        mock_injector.get.return_value = mock_bot_service
        ctx.injector = mock_injector

        result = self.interceptor._is_coding_app(ctx, "bot_123", "owner_001")
        assert result is False

    def test_partial_coding_only_template_type_return_false(self):
        """仅 template_type 命中、active_engine 不符 -> False."""
        ctx = InterceptorContext(user=None, route_kwargs={})
        mock_injector = MagicMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot.return_value = {
            "active_engine": "openai",
            "template_type": "applicationCoding",
        }
        mock_injector.get.return_value = mock_bot_service
        ctx.injector = mock_injector

        result = self.interceptor._is_coding_app(ctx, "bot_123", "owner_001")
        assert result is False

    def test_bot_not_found_return_false(self):
        """Bot 不存在 (get_bot 返回 None) -> False."""
        ctx = InterceptorContext(user=None, route_kwargs={})
        mock_injector = MagicMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot.return_value = None
        mock_injector.get.return_value = mock_bot_service
        ctx.injector = mock_injector

        result = self.interceptor._is_coding_app(ctx, "bot_123", "owner_001")
        assert result is False

    def test_none_bot_id_return_false(self):
        """bot_id 为 None -> False (不查 bot service)."""
        ctx = InterceptorContext(user=None, route_kwargs={})
        mock_injector = MagicMock()
        ctx.injector = mock_injector

        result = self.interceptor._is_coding_app(ctx, None, "owner_001")
        assert result is False
        mock_injector.get.assert_not_called()

    def test_none_owner_id_return_false(self):
        """owner_id 为 None -> False (不查 bot service)."""
        ctx = InterceptorContext(user=None, route_kwargs={})
        mock_injector = MagicMock()
        ctx.injector = mock_injector

        result = self.interceptor._is_coding_app(ctx, "bot_123", None)
        assert result is False
        mock_injector.get.assert_not_called()

    def test_none_injector_return_false(self):
        """injector 为 None -> False (不查 bot service)."""
        ctx = InterceptorContext(user=None, route_kwargs={})
        ctx.injector = None

        result = self.interceptor._is_coding_app(ctx, "bot_123", "owner_001")
        assert result is False

    def test_bot_service_exception_return_false(self):
        """get_bot 抛异常 -> 保守返回 False."""
        ctx = InterceptorContext(user=None, route_kwargs={})
        mock_injector = MagicMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot.side_effect = RuntimeError("db error")
        mock_injector.get.return_value = mock_bot_service
        ctx.injector = mock_injector

        result = self.interceptor._is_coding_app(ctx, "bot_123", "owner_001")
        assert result is False

    def test_injector_get_exception_return_false(self):
        """injector.get 抛异常 (Protocol 未注册) -> 保守返回 False."""
        ctx = InterceptorContext(user=None, route_kwargs={})
        mock_injector = MagicMock()
        mock_injector.get.side_effect = Exception("no provider")
        ctx.injector = mock_injector

        result = self.interceptor._is_coding_app(ctx, "bot_123", "owner_001")
        assert result is False


# ============================================================================
# Interceptor before() — coding app skip_lock integration tests
# ============================================================================


class TestCodingAppSkipLock:
    """Tests that coding apps skip bot-level lock but still check collaborator permission.

    When has_collaborators is True and the bot is a coding app:
    - Bot-level lock check is skipped (session-level locks used instead)
    - Collaborator permission is still checked
    - Non-collaborators are denied
    """

    def setup_method(self):
        self.user = AuthenticatedIdentity(id="1", operatorName="test", staffId="user_001")
        self.owner = AuthenticatedIdentity(id="2", operatorName="owner", staffId="owner_001")

    @pytest.mark.asyncio
    async def test_coding_app_skips_bot_level_lock(self):
        """coding 应用有协作者但无 bot 级锁 -> 跳过锁检查，检查权限后放行。"""
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
            # skip_lock_check=False (default)
            # persist_audit_log=True (default)
        )

        ctx = InterceptorContext(
            user=self.user,  # user_001, not the owner
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "owner_001"}},
        )

        # Mock lock_service: has collaborators but no bot-level lock
        mock_lock_info = MagicMock()
        mock_lock_info.has_collaborators = True
        mock_lock_info.lock = None  # No lock held

        with patch.object(interceptor, '_get_lock_service') as mock_get_lock:
            mock_lock_service = MagicMock()
            mock_lock_service.get_lock_info.return_value = mock_lock_info
            mock_get_lock.return_value = mock_lock_service

            # Mock _is_coding_app to return True
            with patch.object(interceptor, '_is_coding_app', return_value=True):
                # Mock collaborator_service: user has permission
                with patch.object(interceptor, '_get_collaborator_service') as mock_get_collab:
                    mock_collab_service = MagicMock()
                    mock_collab_service.check_collaborator_permission.return_value = {
                        "has_permission": True,
                        "level": "ADMIN",
                    }
                    mock_get_collab.return_value = mock_collab_service

                    result = await interceptor.before(ctx)

                    # Should pass: coding app skips bot-level lock
                    assert result is not None
                    assert ctx.response is None
                    # Collaborator permission should have been checked
                    mock_collab_service.check_collaborator_permission.assert_called_once()

    @pytest.mark.asyncio
    async def test_coding_app_denies_without_permission(self):
        """coding 应用跳过锁检查，但协作者权限不足仍拒绝。"""
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
        )

        ctx = InterceptorContext(
            user=self.user,  # user_001, not the owner
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "owner_001"}},
        )

        mock_lock_info = MagicMock()
        mock_lock_info.has_collaborators = True
        mock_lock_info.lock = None

        with patch.object(interceptor, '_get_lock_service') as mock_get_lock:
            mock_lock_service = MagicMock()
            mock_lock_service.get_lock_info.return_value = mock_lock_info
            mock_get_lock.return_value = mock_lock_service

            with patch.object(interceptor, '_is_coding_app', return_value=True):
                with patch.object(interceptor, '_get_collaborator_service') as mock_get_collab:
                    mock_collab_service = MagicMock()
                    mock_collab_service.check_collaborator_permission.return_value = {
                        "has_permission": False,
                        "level": "NONE",
                    }
                    mock_get_collab.return_value = mock_collab_service

                    result = await interceptor.before(ctx)

                    # Should deny: permission insufficient
                    assert result is None
                    assert ctx.response is not None
                    assert ctx.response.error_code == 403

    @pytest.mark.asyncio
    async def test_non_coding_app_requires_lock(self):
        """非 coding 应用 (Service Bot) 有协作者但无 bot 级锁 -> 拒绝（需持锁）。"""
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
        )

        ctx = InterceptorContext(
            user=self.user,  # user_001, not the owner
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "owner_001"}},
        )

        mock_lock_info = MagicMock()
        mock_lock_info.has_collaborators = True
        mock_lock_info.lock = None  # No bot-level lock

        with patch.object(interceptor, '_get_lock_service') as mock_get_lock:
            mock_lock_service = MagicMock()
            mock_lock_service.get_lock_info.return_value = mock_lock_info
            mock_get_lock.return_value = mock_lock_service

            # _is_coding_app returns False → bot-level lock is enforced
            with patch.object(interceptor, '_is_coding_app', return_value=False):
                result = await interceptor.before(ctx)

                # Should deny: no lock held
                assert result is None
                assert ctx.response is not None
                assert ctx.response.error_code == 423
                assert "编辑锁" in ctx.response.message

    @pytest.mark.asyncio
    async def test_coding_app_owner_bypasses_all(self):
        """coding 应用 owner 直接放行（不经锁/权限检查）。

        有协作者时 owner 在 skip_lock 分支中放行：因 user_id == owner_id，
        不进入 check_collaborator_permission，直接 return ctx。
        注意：有协作者的 owner 分支不设 permission_level="OWNER"（该 metadata
        仅在 has_collaborators=False 分支设置），只验证放行不拒绝。
        """
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
        )

        # Use owner as the user
        ctx = InterceptorContext(
            user=self.owner,  # owner_001 == owner_id
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "owner_001"}},
        )

        mock_lock_info = MagicMock()
        mock_lock_info.has_collaborators = True
        mock_lock_info.lock = None  # No bot-level lock

        with patch.object(interceptor, '_get_lock_service') as mock_get_lock:
            mock_lock_service = MagicMock()
            mock_lock_service.get_lock_info.return_value = mock_lock_info
            mock_get_lock.return_value = mock_lock_service

            with patch.object(interceptor, '_is_coding_app', return_value=True):
                # Owner should NOT be rejected even without bot-level lock
                result = await interceptor.before(ctx)

                # Owner passes through (no 423, no 403)
                assert result is not None
                assert ctx.response is None

    @pytest.mark.asyncio
    async def test_coding_app_not_called_when_skip_lock_already_true(self):
        """skip_lock_check=True 时不再调用 _is_coding_app（避免多余 bot 查询）。"""
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
            skip_lock_check=True,  # Already skip lock
        )

        ctx = InterceptorContext(
            user=self.user,  # not owner
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "owner_001"}},
        )

        mock_lock_info = MagicMock()
        mock_lock_info.has_collaborators = True
        mock_lock_info.lock = None

        with patch.object(interceptor, '_get_lock_service') as mock_get_lock:
            mock_lock_service = MagicMock()
            mock_lock_service.get_lock_info.return_value = mock_lock_info
            mock_get_lock.return_value = mock_lock_service

            with patch.object(interceptor, '_is_coding_app') as mock_is_coding:
                with patch.object(interceptor, '_get_collaborator_service') as mock_get_collab:
                    mock_collab_service = MagicMock()
                    mock_collab_service.check_collaborator_permission.return_value = {
                        "has_permission": True,
                        "level": "ADMIN",
                    }
                    mock_get_collab.return_value = mock_collab_service

                    result = await interceptor.before(ctx)

                    assert result is not None
                    # _is_coding_app should NOT be called (skip_lock already True)
                    mock_is_coding.assert_not_called()

    @pytest.mark.asyncio
    async def test_coding_app_not_called_when_persist_audit_log_false(self):
        """persist_audit_log=False 时不再调用 _is_coding_app（skip_lock 已由 audit log 旁路）。"""
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
            persist_audit_log=False,  # Disables audit log, also skips lock
        )

        ctx = InterceptorContext(
            user=self.user,  # not owner
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "owner_001"}},
        )

        mock_lock_info = MagicMock()
        mock_lock_info.has_collaborators = True
        mock_lock_info.lock = None

        with patch.object(interceptor, '_get_lock_service') as mock_get_lock:
            mock_lock_service = MagicMock()
            mock_lock_service.get_lock_info.return_value = mock_lock_info
            mock_get_lock.return_value = mock_lock_service

            with patch.object(interceptor, '_is_coding_app') as mock_is_coding:
                with patch.object(interceptor, '_get_collaborator_service') as mock_get_collab:
                    mock_collab_service = MagicMock()
                    mock_collab_service.check_collaborator_permission.return_value = {
                        "has_permission": True,
                        "level": "ADMIN",
                    }
                    mock_get_collab.return_value = mock_collab_service

                    result = await interceptor.before(ctx)

                    assert result is not None
                    # _is_coding_app should NOT be called
                    mock_is_coding.assert_not_called()

    @pytest.mark.asyncio
    async def test_coding_app_with_bot_service_exception_fallback(self):
        """_is_coding_app 内部 bot_service 异常时保守返回 False，
        回退到 Service Bot 逻辑（需持锁）。

        不用 patch.object _is_coding_app 的 side_effect（会直接抛出
        绕过函数内的 try/except），而是让 injector.get 返回一个
        get_bot 会抛异常的 mock bot_service，使 _is_coding_app
        走异常分支返回 False。
        """
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
        )

        ctx = InterceptorContext(
            user=self.user,  # not owner
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "owner_001"}},
        )

        # Set up injector so _is_coding_app's internal try/except catches the error
        mock_injector = MagicMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot.side_effect = RuntimeError("db error")
        mock_injector.get.return_value = mock_bot_service
        ctx.injector = mock_injector

        mock_lock_info = MagicMock()
        mock_lock_info.has_collaborators = True
        mock_lock_info.lock = None  # No lock held

        with patch.object(interceptor, '_get_lock_service') as mock_get_lock:
            mock_lock_service = MagicMock()
            mock_lock_service.get_lock_info.return_value = mock_lock_info
            mock_get_lock.return_value = mock_lock_service

            result = await interceptor.before(ctx)

            # _is_coding_app returns False (exception caught), falls to Service Bot logic
            # Service Bot with no lock -> 423
            assert result is None
            assert ctx.response is not None
            assert ctx.response.error_code == 423
            assert "编辑锁" in ctx.response.message


class TestCodingAppRegression:
    """Regression tests: ensure non-coding app behavior is unchanged."""

    def setup_method(self):
        self.user = AuthenticatedIdentity(id="1", operatorName="test", staffId="user_001")
        self.owner = AuthenticatedIdentity(id="2", operatorName="owner", staffId="owner_001")

    @pytest.mark.asyncio
    async def test_service_bot_with_held_lock_passes(self):
        """Service Bot 有锁且持锁者是自己 -> 放行（原有行为不变）。"""
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
        )

        ctx = InterceptorContext(
            user=self.user,  # not owner
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "owner_001"}},
        )

        mock_lock = MagicMock()
        mock_lock.holder_user_id = "user_001"  # Held by current user

        mock_lock_info = MagicMock()
        mock_lock_info.has_collaborators = True
        mock_lock_info.lock = mock_lock
        mock_lock_info.holder_name = "Test User"

        with patch.object(interceptor, '_get_lock_service') as mock_get_lock:
            mock_lock_service = MagicMock()
            mock_lock_service.get_lock_info.return_value = mock_lock_info
            mock_get_lock.return_value = mock_lock_service

            # _is_coding_app returns False (Service Bot)
            with patch.object(interceptor, '_is_coding_app', return_value=False):
                with patch.object(interceptor, '_get_collaborator_service') as mock_get_collab:
                    mock_collab_service = MagicMock()
                    mock_collab_service.check_collaborator_permission.return_value = {
                        "has_permission": True,
                        "level": "ADMIN",
                    }
                    mock_get_collab.return_value = mock_collab_service

                    result = await interceptor.before(ctx)

                    # Should pass: user holds the lock and has permission
                    assert result is not None
                    assert ctx.response is None

    @pytest.mark.asyncio
    async def test_service_bot_no_collaborators_owner_bypass(self):
        """Service Bot 无协作者、owner 请求 -> 直接放行（原有行为不变）。"""
        interceptor = CollaboratorPermissionInterceptor(
            bot_id="$request.bot_id",
            owner_id="$request.owner_id",
        )

        ctx = InterceptorContext(
            user=self.owner,  # owner
            route_kwargs={"request": {"bot_id": "bot_001", "owner_id": "owner_001"}},
        )

        mock_lock_info = MagicMock()
        mock_lock_info.has_collaborators = False

        with patch.object(interceptor, '_get_lock_service') as mock_get_lock:
            mock_lock_service = MagicMock()
            mock_lock_service.get_lock_info.return_value = mock_lock_info
            mock_get_lock.return_value = mock_lock_service

            result = await interceptor.before(ctx)

            # Owner should bypass: no collaborator check, no lock check
            assert result is not None
            assert ctx.metadata.get("permission_level") == "OWNER"