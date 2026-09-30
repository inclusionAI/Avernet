"""Tests for service bot publish approval router."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.service_bot import router_publish
from agentclaw.community.api.publish_approval import ApprovalResult
from agentclaw.community.core.bot_collaborator.interceptor import InterceptorContext
from agentclaw.community.core.bot_collaborator.interceptor.extractors import PermissionParams
from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishStatus


_USER = AuthenticatedUser(id="u1", staffId="u1", operatorName="u1")
_ANON = AuthenticatedUser(id="anonymous", staffId="anonymous", operatorName="anonymous")


class _MockRequest:
    """Mock FastAPI Request with form data."""
    def __init__(self, form_data: dict):
        self._form_data = form_data

    async def form(self):
        return self._form_data


def _make_record(
    record_id: int = 1,
    owner_id: str = "owner_001",
    source_bot_id: str = "bot_001",
    ext: dict = None,
    status: str = None,
) -> BotPublishRecord:
    """Helper to create mock BotPublishRecord."""
    return BotPublishRecord(
        id=record_id,
        source_bot_pk=100,
        source_bot_id=source_bot_id,
        publish_bot_id=f"{source_bot_id}_pub",
        name="Test Bot",
        owner_id=owner_id,
        status=status or PublishStatus.DRAFT.value,
        version=1,
        last_pub_id=0,
        env="dev",
        permission_owner="owner",
        ext=ext,
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestExtractFromPublishIdForApproval:
    """extract_from_publish_id 测试（用于审批路由）。"""

    async def test_returns_empty_when_publish_id_is_zero(self):
        """publish_id 为 0 时返回空 PermissionParams。"""
        result = await router_publish.extract_from_publish_id(
            "0", InterceptorContext()
        )
        assert result == PermissionParams()

    async def test_returns_empty_when_injector_is_none(self):
        """injector 为 None 时返回空 PermissionParams。"""
        result = await router_publish.extract_from_publish_id(
            "1", InterceptorContext(injector=None)
        )
        assert result == PermissionParams()

    async def test_returns_empty_when_injector_raises(self):
        """injector.get 抛出异常时返回空 PermissionParams。"""
        injector = MagicMock()
        injector.get.side_effect = RuntimeError("no binding")

        result = await router_publish.extract_from_publish_id(
            "1", InterceptorContext(injector=injector)
        )
        assert result == PermissionParams()

    async def test_returns_empty_when_record_not_found(self):
        """记录不存在时返回空 PermissionParams。"""
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = None

        injector = MagicMock()
        injector.get.return_value = publish_service

        result = await router_publish.extract_from_publish_id(
            "1", InterceptorContext(injector=injector)
        )
        assert result == PermissionParams()

    async def test_returns_params_when_record_found(self):
        """记录存在时返回正确的 PermissionParams。"""
        record = _make_record(owner_id="owner_123", source_bot_id="bot_456")
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = record

        injector = MagicMock()
        injector.get.return_value = publish_service

        result = await router_publish.extract_from_publish_id(
            "1", InterceptorContext(injector=injector)
        )
        assert result.bot_id == "bot_456"
        assert result.owner_id == "owner_123"

    async def test_returns_empty_on_exception(self):
        """发生异常时返回空 PermissionParams。"""
        publish_service = MagicMock()
        publish_service.get_publish_by_id.side_effect = ValueError("db error")

        injector = MagicMock()
        injector.get.return_value = publish_service

        result = await router_publish.extract_from_publish_id(
            "1", InterceptorContext(injector=injector)
        )
        assert result == PermissionParams()


@pytest.mark.unit
@pytest.mark.asyncio
class TestPublishApprovalCallback:
    """publish_approval_callback 端点测试。"""

    async def test_invalid_publish_id_returns_error(self):
        """无效的 publish_id 返回错误。"""
        request = _MockRequest({"publish_id": "invalid", "action": "AGREE"})
        service = MagicMock()

        result = await router_publish.publish_approval_callback(request, service)

        assert result["success"] is False
        assert "Invalid publish_id" in result["message"]

    async def test_calls_handle_approval_callback(self):
        """正确调用 handle_approval_callback。"""
        request = _MockRequest({
            "publish_id": "123",
            "action": "online",
            "applicant": "user_001",
            "globalUniqueId": "puid_456",
            "lastOperate": "AGREE",
        })
        service = MagicMock()
        service.handle_approval_callback = AsyncMock(return_value={"success": True})

        result = await router_publish.publish_approval_callback(request, service)

        service.handle_approval_callback.assert_called_once_with(
            publish_id=123,
            action="online",
            applicant="user_001",
            puid="puid_456",
            last_operate="AGREE",
        )
        assert result["success"] is True

    async def test_last_operate_is_uppercased(self):
        """lastOperate 被转换为大写。"""
        request = _MockRequest({
            "publish_id": "123",
            "action": "online",
            "applicant": "user_001",
            "globalUniqueId": "puid_456",
            "lastOperate": "agree",
        })
        service = MagicMock()
        service.handle_approval_callback = AsyncMock(return_value={"success": True})

        await router_publish.publish_approval_callback(request, service)

        call_args = service.handle_approval_callback.call_args
        assert call_args.kwargs["last_operate"] == "AGREE"


@pytest.mark.unit
@pytest.mark.asyncio
class TestCheckAndProcessApproval:
    """check_and_process_approval 端点测试。"""

    async def test_anonymous_user_returns_error(self):
        """匿名用户返回错误。"""
        from agentclaw.community.adapters.http.service_bot.schemas_publish import CheckApprovalRequest

        approval_service = MagicMock()
        publish_service = MagicMock()

        result = await router_publish.check_and_process_approval(
            publish_id=1,
            request=CheckApprovalRequest(action="online"),
            user=_ANON,
            approval_service=approval_service,
            publish_service=publish_service,
        )

        assert result.success is False
        assert result.error_code == 400
        assert "无法获取用户信息" in result.message

    async def test_publish_not_found_returns_404(self):
        """发布单不存在返回 404。"""
        from agentclaw.community.adapters.http.service_bot.schemas_publish import CheckApprovalRequest

        approval_service = MagicMock()
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = None

        result = await router_publish.check_and_process_approval(
            publish_id=999,
            request=CheckApprovalRequest(action="online"),
            user=_USER,
            approval_service=approval_service,
            publish_service=publish_service,
        )

        assert result.success is False
        assert result.error_code == 404
        assert "发布单不存在" in result.message

    async def test_calls_should_approval_for_online_action(self):
        """action=online 时调用 check_and_process_should_approval。"""
        from agentclaw.community.adapters.http.service_bot.schemas_publish import CheckApprovalRequest

        record = _make_record(status=PublishStatus.VALIDATING.value)
        approval_service = MagicMock()
        approval_service.check_and_process_should_approval = AsyncMock(
            return_value=ApprovalResult(
                should_approval=False,
                status="SKIP",
                approval=None,
                message="无需审批",
            )
        )
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = record

        result = await router_publish.check_and_process_approval(
            publish_id=1,
            request=CheckApprovalRequest(action="online"),
            user=_USER,
            approval_service=approval_service,
            publish_service=publish_service,
        )

        approval_service.check_and_process_should_approval.assert_called_once_with(
            publish_record=record,
            operator="u1",
        )
        assert result.success is True
        assert result.data["status"] == "SKIP"

    async def test_calls_offline_approval_for_offline_action(self):
        """action=offline 时调用 check_and_process_offline_approval。"""
        from agentclaw.community.adapters.http.service_bot.schemas_publish import CheckApprovalRequest

        record = _make_record(status=PublishStatus.SUCCESS.value)
        approval_service = MagicMock()
        approval_service.check_and_process_offline_approval = AsyncMock(
            return_value=ApprovalResult(
                should_approval=False,
                status="SKIP",
                approval=None,
                message="无需审批",
            )
        )
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = record

        result = await router_publish.check_and_process_approval(
            publish_id=1,
            request=CheckApprovalRequest(action="offline"),
            user=_USER,
            approval_service=approval_service,
            publish_service=publish_service,
        )

        approval_service.check_and_process_offline_approval.assert_called_once_with(
            publish_record=record,
            operator="u1",
        )
        assert result.success is True

    async def test_returns_processing_status_with_approval_info(self):
        """返回 PROCESSING 状态时包含审批信息。"""
        from agentclaw.community.adapters.http.service_bot.schemas_publish import CheckApprovalRequest

        record = _make_record(status=PublishStatus.VALIDATING.value)
        approval_info = {
            "puid": "puid_123",
            "status": "PROCESSING",
            "approval_url": "https://example.com/approval/123",
        }
        approval_service = MagicMock()
        approval_service.check_and_process_should_approval = AsyncMock(
            return_value=ApprovalResult(
                should_approval=True,
                status="PROCESSING",
                approval=approval_info,
                message="审批中",
            )
        )
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = record

        result = await router_publish.check_and_process_approval(
            publish_id=1,
            request=CheckApprovalRequest(action="online"),
            user=_USER,
            approval_service=approval_service,
            publish_service=publish_service,
        )

        assert result.success is True
        assert result.data["should_approval"] is True
        assert result.data["status"] == "PROCESSING"
        assert result.data["approval"] == approval_info

    async def test_exception_returns_500(self):
        """异常时返回 500 错误。"""
        from agentclaw.community.adapters.http.service_bot.schemas_publish import CheckApprovalRequest

        approval_service = MagicMock()
        approval_service.check_and_process_should_approval = AsyncMock(
            side_effect=RuntimeError("unexpected error")
        )
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_record(status=PublishStatus.VALIDATING.value)

        result = await router_publish.check_and_process_approval(
            publish_id=1,
            request=CheckApprovalRequest(action="online"),
            user=_USER,
            approval_service=approval_service,
            publish_service=publish_service,
        )

        assert result.success is False
        assert result.error_code == 500
        assert "检查审批状态失败" in result.message

    async def test_online_action_with_invalid_status_returns_error(self):
        """online 操作时发布单状态不是 validating，返回 400 错误。"""
        from agentclaw.community.adapters.http.service_bot.schemas_publish import CheckApprovalRequest

        # 状态为 draft，不是 validating
        record = _make_record(status=PublishStatus.DRAFT.value)
        approval_service = MagicMock()
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = record

        result = await router_publish.check_and_process_approval(
            publish_id=1,
            request=CheckApprovalRequest(action="online"),
            user=_USER,
            approval_service=approval_service,
            publish_service=publish_service,
        )

        assert result.success is False
        assert result.error_code == 400
        assert "上线操作要求发布单状态为 validating" in result.message

    async def test_offline_action_with_invalid_status_returns_error(self):
        """offline 操作时发布单状态不是 success，返回 400 错误。"""
        from agentclaw.community.adapters.http.service_bot.schemas_publish import CheckApprovalRequest

        # 状态为 validating，不是 success
        record = _make_record(status=PublishStatus.VALIDATING.value)
        approval_service = MagicMock()
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = record

        result = await router_publish.check_and_process_approval(
            publish_id=1,
            request=CheckApprovalRequest(action="offline"),
            user=_USER,
            approval_service=approval_service,
            publish_service=publish_service,
        )

        assert result.success is False
        assert result.error_code == 400
        assert "下线操作要求发布单状态为 success" in result.message