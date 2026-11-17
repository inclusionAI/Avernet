"""Tests for PublishApprovalService.

单元测试 - 不依赖网络、文件系统、数据库，使用 mock。
"""
import pytest
from unittest.mock import MagicMock, Mock, AsyncMock

from agentclaw.community.api.publish_approval import ApprovalResult
from agentclaw.community.core.service_bot.services.publish_approval_service import (
    PublishApprovalService,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishStatus,
)


def _make_publish_approval_service(
    publish_service=None,
    publish_flow_service_provider=None,
    process_service=None,
    bot_service=None,
    task_queue_service=None,
) -> PublishApprovalService:
    """Build a PublishApprovalService with MagicMock fallbacks."""
    return PublishApprovalService(
        publish_service=publish_service or MagicMock(),
        publish_flow_service_provider=publish_flow_service_provider or (lambda: MagicMock()),
        process_service=process_service or MagicMock(),
        bot_service=bot_service or MagicMock(),
        task_queue_service=task_queue_service or MagicMock(),
    )


def _make_publish_record(
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


def _make_bot_with_should_approval(should_approval: bool) -> dict:
    """Helper to create bot dict with should_approval config."""
    return {
        "owner_id": "owner_001",
        "ext": {
            "service_bot_config": {
                "should_approval": should_approval,
            }
        }
    }


class TestCheckAndProcessOnlineApproval:
    """check_and_process_should_approval 方法测试。"""

    @pytest.mark.asyncio
    async def test_skip_when_should_approval_disabled(self):
        """should_approval=False 时，跳过审批。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = _make_bot_with_should_approval(False)

        service = _make_publish_approval_service(bot_service=bot_service)
        record = _make_publish_record(owner_id="owner_001")

        result = await service.check_and_process_should_approval(
            publish_record=record,
            operator="collaborator_001",
        )

        assert result.should_approval is False
        assert result.status == "SKIP"
        assert result.message == "无需审批，直接执行上线"

    @pytest.mark.asyncio
    async def test_skip_when_operator_is_owner(self):
        """operator 是 owner 时，跳过审批。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = _make_bot_with_should_approval(True)

        service = _make_publish_approval_service(bot_service=bot_service)
        record = _make_publish_record(owner_id="owner_001")

        result = await service.check_and_process_should_approval(
            publish_record=record,
            operator="owner_001",  # Same as owner_id
        )

        assert result.should_approval is False
        assert result.status == "SKIP"
        assert result.message == "无需审批，操作者为 Bot 拥有者"

    @pytest.mark.asyncio
    async def test_return_existing_when_processing(self):
        """已有 PROCESSING 状态审批时，返回现有审批。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = _make_bot_with_should_approval(True)

        service = _make_publish_approval_service(bot_service=bot_service)
        existing_approval = {
            "puid": "approval_123",
            "status": "PROCESSING",
            "approval_url": "https://example.com/approval/123",
        }
        record = _make_publish_record(
            owner_id="owner_001",
            ext={"approval": existing_approval},
        )

        result = await service.check_and_process_should_approval(
            publish_record=record,
            operator="collaborator_001",
        )

        assert result.should_approval is True
        assert result.status == "PROCESSING"
        assert result.approval == existing_approval
        assert result.message == "审批中，请等待审批结果"

    @pytest.mark.asyncio
    async def test_create_new_approval_for_collaborator(self):
        """非 owner 的协作者需要创建新审批。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = _make_bot_with_should_approval(True)

        process_service = MagicMock()
        process_service.start_approval.return_value = {
            "success": True,
            "puid": "new_approval_456",
            "approval_url": "https://example.com/approval/456",
        }

        publish_service = MagicMock()
        publish_service.update_publish_ext = MagicMock()
        publish_service.get_publish_by_id.return_value = None

        service = _make_publish_approval_service(
            bot_service=bot_service,
            process_service=process_service,
            publish_service=publish_service,
        )
        record = _make_publish_record(owner_id="owner_001")

        result = await service.check_and_process_should_approval(
            publish_record=record,
            operator="collaborator_001",
        )

        assert result.should_approval is True
        assert result.status == "PROCESSING"
        assert result.approval is not None
        assert result.approval["puid"] == "new_approval_456"
        process_service.start_approval.assert_called_once()
        # 验证 content 字段：online action 应为 "{publish_name} 线上发布审批"
        call_args = process_service.start_approval.call_args
        context = call_args.kwargs["context"]
        assert context["content"] == "Test Bot 线上发布审批"


class TestCheckAndProcessOfflineApproval:
    """check_and_process_offline_approval 方法测试。"""

    @pytest.mark.asyncio
    async def test_skip_when_should_approval_disabled(self):
        """should_approval=False 时，跳过审批。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = _make_bot_with_should_approval(False)

        service = _make_publish_approval_service(bot_service=bot_service)
        record = _make_publish_record(owner_id="owner_001")

        result = await service.check_and_process_offline_approval(
            publish_record=record,
            operator="collaborator_001",
        )

        assert result.should_approval is False
        assert result.status == "SKIP"
        assert result.message == "无需审批，直接执行下线"

    @pytest.mark.asyncio
    async def test_skip_when_operator_is_owner(self):
        """operator 是 owner 时，跳过审批。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = _make_bot_with_should_approval(True)

        service = _make_publish_approval_service(bot_service=bot_service)
        record = _make_publish_record(owner_id="owner_001")

        result = await service.check_and_process_offline_approval(
            publish_record=record,
            operator="owner_001",  # Same as owner_id
        )

        assert result.should_approval is False
        assert result.status == "SKIP"
        assert result.message == "无需审批，操作者为 Bot 拥有者"

    @pytest.mark.asyncio
    async def test_return_existing_when_processing(self):
        """已有 PROCESSING 状态审批时，返回现有审批。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = _make_bot_with_should_approval(True)

        service = _make_publish_approval_service(bot_service=bot_service)
        existing_approval = {
            "puid": "approval_123",
            "status": "PROCESSING",
            "approval_url": "https://example.com/approval/123",
        }
        record = _make_publish_record(
            owner_id="owner_001",
            ext={"approval": existing_approval},
        )

        result = await service.check_and_process_offline_approval(
            publish_record=record,
            operator="collaborator_001",
        )

        assert result.should_approval is True
        assert result.status == "PROCESSING"
        assert result.approval == existing_approval
        assert result.message == "审批中，请等待审批结果"

    @pytest.mark.asyncio
    async def test_create_new_approval_for_collaborator(self):
        """非 owner 的协作者需要创建新审批。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = _make_bot_with_should_approval(True)

        process_service = MagicMock()
        process_service.start_approval.return_value = {
            "success": True,
            "puid": "new_approval_789",
            "approval_url": "https://example.com/approval/789",
        }

        publish_service = MagicMock()
        publish_service.update_publish_ext = MagicMock()
        publish_service.get_publish_by_id.return_value = None

        service = _make_publish_approval_service(
            bot_service=bot_service,
            process_service=process_service,
            publish_service=publish_service,
        )
        record = _make_publish_record(owner_id="owner_001")

        result = await service.check_and_process_offline_approval(
            publish_record=record,
            operator="collaborator_001",
        )

        assert result.should_approval is True
        assert result.status == "PROCESSING"
        assert result.approval is not None
        assert result.approval["puid"] == "new_approval_789"
        process_service.start_approval.assert_called_once()
        # 验证 content 字段：offline action 应为 "{publish_name} 下线审批"
        call_args = process_service.start_approval.call_args
        context = call_args.kwargs["context"]
        assert context["content"] == "Test Bot 下线审批"


class TestHandleApprovalCallback:
    """handle_approval_callback 方法测试。"""

    @pytest.mark.asyncio
    async def test_approval_not_found(self):
        """publish_id 不存在时返回失败。"""
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = None

        service = _make_publish_approval_service(publish_service=publish_service)

        result = await service.handle_approval_callback(
            publish_id=999,
            action="online",
            applicant="user_001",
            puid="puid_123",
            last_operate="AGREE",
        )

        assert result["success"] is False
        assert "not found" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_no_approval_in_ext(self):
        """ext 中没有 approval 时返回失败。"""
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(ext={})
        publish_service.update_publish_ext = MagicMock()

        service = _make_publish_approval_service(publish_service=publish_service)

        result = await service.handle_approval_callback(
            publish_id=1,
            action="online",
            applicant="user_001",
            puid="puid_123",
            last_operate="AGREE",
        )

        assert result["success"] is False
        assert "no approval" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_puid_mismatch(self):
        """puid 不匹配时返回失败。"""
        existing_approval = {
            "puid": "correct_puid",
            "status": "PROCESSING",
            "operator_id": "user_001",
        }
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": existing_approval}
        )
        publish_service.update_publish_ext = MagicMock()

        service = _make_publish_approval_service(publish_service=publish_service)

        result = await service.handle_approval_callback(
            publish_id=1,
            action="online",
            applicant="user_001",
            puid="wrong_puid",
            last_operate="AGREE",
        )

        assert result["success"] is False
        assert "puid mismatch" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_unknown_last_operate(self):
        """未知的 last_operate 时返回失败。"""
        existing_approval = {
            "puid": "puid_123",
            "status": "PROCESSING",
            "operator_id": "user_001",
        }
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": existing_approval}
        )
        publish_service.update_publish_ext = MagicMock()

        service = _make_publish_approval_service(publish_service=publish_service)

        result = await service.handle_approval_callback(
            publish_id=1,
            action="online",
            applicant="user_001",
            puid="puid_123",
            last_operate="UNKNOWN",
        )

        assert result["success"] is False
        assert "unknown" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_agree_updates_status(self):
        """AGREE 回调更新审批状态。"""
        existing_approval = {
            "puid": "puid_123",
            "status": "PROCESSING",
            "operator_id": "user_001",
        }
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": existing_approval}
        )
        publish_service.update_publish_ext = MagicMock()

        service = _make_publish_approval_service(publish_service=publish_service)

        result = await service.handle_approval_callback(
            publish_id=1,
            action="online",
            applicant="user_001",
            puid="puid_123",
            last_operate="AGREE",
        )

        assert result["success"] is True
        assert "AGREED" in result["message"]
        # 验证 update_publish_ext 被调用，状态更新为 AGREED
        publish_service.update_publish_ext.assert_called_once()
        call_args = publish_service.update_publish_ext.call_args
        updated_ext = call_args[0][1]
        assert updated_ext["approval"]["status"] == "AGREED"

    @pytest.mark.asyncio
    async def test_disagree_updates_status(self):
        """DISAGREE 回调更新审批状态。"""
        existing_approval = {
            "puid": "puid_123",
            "status": "PROCESSING",
            "operator_id": "user_001",
        }
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": existing_approval}
        )
        publish_service.update_publish_ext = MagicMock()

        service = _make_publish_approval_service(publish_service=publish_service)

        result = await service.handle_approval_callback(
            publish_id=1,
            action="online",
            applicant="user_001",
            puid="puid_123",
            last_operate="DISAGREE",
        )

        assert result["success"] is True
        assert "DISAGREED" in result["message"]

    @pytest.mark.asyncio
    async def test_cancel_updates_status(self):
        """CANCEL 回调更新审批状态。"""
        existing_approval = {
            "puid": "puid_123",
            "status": "PROCESSING",
            "operator_id": "user_001",
        }
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": existing_approval}
        )
        publish_service.update_publish_ext = MagicMock()

        service = _make_publish_approval_service(publish_service=publish_service)

        result = await service.handle_approval_callback(
            publish_id=1,
            action="online",
            applicant="user_001",
            puid="puid_123",
            last_operate="CANCEL",
        )

        assert result["success"] is True
        assert "CANCEL" in result["message"]


class TestCreateNewApproval:
    """_create_new_approval 方法测试。"""

    @pytest.mark.asyncio
    async def test_create_approval_fails(self):
        """创建审批失败时返回 ERROR。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = _make_bot_with_should_approval(True)

        process_service = MagicMock()
        process_service.start_approval.return_value = {
            "success": False,
            "error_msg": "antprocess unavailable",
        }

        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = None

        service = _make_publish_approval_service(
            bot_service=bot_service,
            process_service=process_service,
            publish_service=publish_service,
        )
        record = _make_publish_record(owner_id="owner_001")

        result = await service.check_and_process_should_approval(
            publish_record=record,
            operator="collaborator_001",
        )

        assert result.should_approval is True
        assert result.status == "ERROR"
        assert "创建审批失败" in result.message

    @pytest.mark.asyncio
    async def test_archive_old_approval_on_terminal_state(self):
        """终态审批应被归档后创建新审批。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = _make_bot_with_should_approval(True)

        process_service = MagicMock()
        process_service.start_approval.return_value = {
            "success": True,
            "puid": "new_puid",
            "approval_url": "https://example.com/approval/new",
        }

        old_approval = {
            "puid": "old_puid",
            "status": "DISAGREED",
            "operator_id": "prev_user",
        }
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = None  # After archive, no refresh
        publish_service.update_publish_ext = MagicMock()

        service = _make_publish_approval_service(
            bot_service=bot_service,
            process_service=process_service,
            publish_service=publish_service,
        )
        record = _make_publish_record(
            owner_id="owner_001",
            ext={"approval": old_approval},
        )

        result = await service.check_and_process_should_approval(
            publish_record=record,
            operator="collaborator_001",
        )

        # 验证归档被调用
        assert publish_service.update_publish_ext.called
        process_service.start_approval.assert_called_once()


class TestIsApprovalRequired:
    """_is_approval_required 方法测试。"""

    def test_returns_false_when_bot_not_found(self):
        """Bot 不存在时返回 False。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = None

        service = _make_publish_approval_service(bot_service=bot_service)
        record = _make_publish_record()

        result = service._is_approval_required(record)
        assert result is False

    def test_returns_false_when_should_approval_is_none(self):
        """should_approval 为 None 时返回 False。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = {
            "ext": {
                "service_bot_config": {
                    # should_approval not set
                }
            }
        }

        service = _make_publish_approval_service(bot_service=bot_service)
        record = _make_publish_record()

        result = service._is_approval_required(record)
        assert result is False

    def test_returns_false_when_should_approval_is_false(self):
        """should_approval 为 False 时返回 False。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = _make_bot_with_should_approval(False)

        service = _make_publish_approval_service(bot_service=bot_service)
        record = _make_publish_record()

        result = service._is_approval_required(record)
        assert result is False

    def test_returns_true_when_should_approval_is_true(self):
        """should_approval 为 True 时返回 True。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = _make_bot_with_should_approval(True)

        service = _make_publish_approval_service(bot_service=bot_service)
        record = _make_publish_record()

        result = service._is_approval_required(record)
        assert result is True

    def test_returns_false_when_service_bot_config_missing(self):
        """service_bot_config 不存在时返回 False。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = {"ext": {}}

        service = _make_publish_approval_service(bot_service=bot_service)
        record = _make_publish_record()

        result = service._is_approval_required(record)
        assert result is False


class TestArchiveApproval:
    """_archive_approval 方法测试。"""

    def test_does_nothing_when_no_approval(self):
        """没有审批时不做任何操作。"""
        publish_service = MagicMock()
        publish_service.update_publish_ext = MagicMock()

        service = _make_publish_approval_service(publish_service=publish_service)
        record = _make_publish_record(ext={})

        service._archive_approval(record)

        publish_service.update_publish_ext.assert_not_called()

    def test_archives_approval_to_history(self):
        """审批被归档到历史。"""
        publish_service = MagicMock()
        publish_service.update_publish_ext = MagicMock()

        service = _make_publish_approval_service(publish_service=publish_service)
        old_approval = {"puid": "old_123", "status": "AGREED"}
        record = _make_publish_record(ext={"approval": old_approval})

        service._archive_approval(record)

        # 验证 update_publish_ext 被调用
        publish_service.update_publish_ext.assert_called_once()
        call_args = publish_service.update_publish_ext.call_args
        ext = call_args[0][1]
        assert ext["approval"] is None
        assert ext["approval_history"][0] == old_approval

    def test_removes_old_history_when_exceeds_3(self):
        """历史记录超过 3 条时删除旧记录。"""
        publish_service = MagicMock()
        publish_service.update_publish_ext = MagicMock()

        service = _make_publish_approval_service(publish_service=publish_service)

        # 现有 3 条历史记录
        existing_history = [
            {"puid": "old_1", "status": "AGREED"},
            {"puid": "old_2", "status": "DISAGREED"},
            {"puid": "old_3", "status": "CANCEL"},
        ]
        current_approval = {"puid": "current", "status": "AGREED"}
        record = _make_publish_record(ext={
            "approval": current_approval,
            "approval_history": existing_history,
        })

        service._archive_approval(record)

        call_args = publish_service.update_publish_ext.call_args
        ext = call_args[0][1]
        assert len(ext["approval_history"]) == 3
        # 当前的审批应该在最前面
        assert ext["approval_history"][0] == current_approval
        # 最老的记录被删除
        assert {"puid": "old_3", "status": "CANCEL"} not in ext["approval_history"]


class TestCreateNewApprovalBotNotFound:
    """_create_new_approval Bot 不存在场景测试。"""

    @pytest.mark.asyncio
    async def test_returns_error_when_bot_not_found(self):
        """Bot 不存在时返回 ERROR。"""
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = None  # Bot not found

        service = _make_publish_approval_service(bot_service=bot_service)
        record = _make_publish_record()

        result = await service._create_new_approval(
            publish_record=record,
            action="online",
            operator="user_001",
        )

        assert result.should_approval is True
        assert result.status == "ERROR"
        assert "Bot not found" in result.message


class TestTriggerOnlineRelease:
    """_trigger_online_release 方法测试。"""

    @pytest.mark.asyncio
    async def test_publish_not_found_logs_error(self):
        """发布记录不存在时记录错误。"""
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = None

        service = _make_publish_approval_service(publish_service=publish_service)

        await service._trigger_online_release(publish_id=999, applicant="user_001")

        publish_service.get_publish_by_id.assert_called_once_with(999)

    @pytest.mark.asyncio
    async def test_no_approval_logs_error(self):
        """审批不存在时记录错误。"""
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(ext={})

        service = _make_publish_approval_service(publish_service=publish_service)

        await service._trigger_online_release(publish_id=1, applicant="user_001")

        # 不应该调用 process
        publish_service.get_publish_by_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_status_not_agreed_skips_release(self):
        """状态不是 AGREED 时跳过发布。"""
        approval = {"puid": "puid_123", "status": "PROCESSING"}
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(ext={"approval": approval})

        service = _make_publish_approval_service(publish_service=publish_service)

        await service._trigger_online_release(publish_id=1, applicant="user_001")

        # 不应该调用 offline_publish
        publish_service.offline_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_marks_executed_on_success(self):
        """发布成功后标记为 EXECUTED。"""
        approval = {"puid": "puid_123", "status": "AGREED"}
        updated_record = _make_publish_record(ext={"approval": approval.copy()})

        publish_service = MagicMock()
        publish_service.get_publish_by_id.side_effect = [
            _make_publish_record(ext={"approval": approval}),  # First call
            updated_record,  # Second call for re-fetch
        ]
        publish_service.update_publish_ext = MagicMock()

        publish_flow_service = MagicMock()
        publish_flow_service.process = AsyncMock()
        publish_flow_service.process.return_value = MagicMock(status=PublishStatus.SUCCESS)

        service = _make_publish_approval_service(
            publish_service=publish_service,
            publish_flow_service_provider=lambda: publish_flow_service,
        )

        await service._trigger_online_release(publish_id=1, applicant="user_001")

        # 验证状态更新为 EXECUTED
        publish_service.update_publish_ext.assert_called()
        last_call_args = publish_service.update_publish_ext.call_args
        ext = last_call_args[0][1]
        assert ext["approval"]["status"] == "EXECUTED"


class TestTriggerOffline:
    """_trigger_offline 方法测试。"""

    @pytest.mark.asyncio
    async def test_publish_not_found_logs_error(self):
        """发布记录不存在时记录错误。"""
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = None

        service = _make_publish_approval_service(publish_service=publish_service)

        await service._trigger_offline(publish_id=999, applicant="user_001")

        publish_service.get_publish_by_id.assert_called_once_with(999)

    @pytest.mark.asyncio
    async def test_marks_executed_on_success(self):
        """下线成功后标记为 EXECUTED。"""
        approval = {"puid": "puid_123", "status": "AGREED"}
        updated_record = _make_publish_record(ext={"approval": approval.copy()})

        publish_service = MagicMock()
        publish_service.get_publish_by_id.side_effect = [
            _make_publish_record(ext={"approval": approval}),  # First call
            updated_record,  # Second call for re-fetch
        ]
        publish_service.update_publish_ext = MagicMock()
        publish_service.offline_publish = AsyncMock(return_value={"success": True})

        service = _make_publish_approval_service(publish_service=publish_service)

        await service._trigger_offline(publish_id=1, applicant="user_001")

        # 验证 offline_publish 被调用
        publish_service.offline_publish.assert_called_once_with(publish_id=1)
        # 验证状态更新为 EXECUTED
        publish_service.update_publish_ext.assert_called()
        last_call_args = publish_service.update_publish_ext.call_args
        ext = last_call_args[0][1]
        assert ext["approval"]["status"] == "EXECUTED"


class TestHandleApprovalCallbackTriggers:
    """handle_approval_callback 触发发布/下线测试。"""

    @pytest.mark.asyncio
    async def test_agree_online_enqueues_durable_trigger(self):
        """AGREE + online (#197): callback enqueues the durable trigger, not inline."""
        from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
            APPROVAL_TRIGGER_TASK,
        )

        approval = {"puid": "puid_123", "status": "PROCESSING", "operator_id": "user_001"}

        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": approval}, status=PublishStatus.VALIDATING.value
        )
        publish_service.update_publish_ext = MagicMock()

        task_queue_service = MagicMock()
        service = _make_publish_approval_service(
            publish_service=publish_service,
            task_queue_service=task_queue_service,
        )

        result = await service.handle_approval_callback(
            publish_id=1,
            action="online",
            applicant="user_001",
            puid="puid_123",
            last_operate="AGREE",
        )

        assert result["success"] is True
        task_queue_service.enqueue.assert_called_once()
        call = task_queue_service.enqueue.call_args
        assert call.args[0] == APPROVAL_TRIGGER_TASK
        assert call.args[1] == {"publish_id": 1, "action": "online", "operator": "user_001"}

    @pytest.mark.asyncio
    async def test_agree_offline_enqueues_durable_trigger(self):
        """AGREE + offline (#197): callback enqueues the durable trigger."""
        from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
            APPROVAL_TRIGGER_TASK,
        )

        approval = {"puid": "puid_123", "status": "PROCESSING", "operator_id": "user_001"}

        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": approval}, status=PublishStatus.SUCCESS.value
        )
        publish_service.update_publish_ext = MagicMock()

        task_queue_service = MagicMock()
        service = _make_publish_approval_service(
            publish_service=publish_service,
            task_queue_service=task_queue_service,
        )

        result = await service.handle_approval_callback(
            publish_id=1,
            action="offline",
            applicant="user_001",
            puid="puid_123",
            last_operate="AGREE",
        )

        assert result["success"] is True
        call = task_queue_service.enqueue.call_args
        assert call.args[0] == APPROVAL_TRIGGER_TASK
        assert call.args[1] == {"publish_id": 1, "action": "offline", "operator": "user_001"}

    @pytest.mark.asyncio
    async def test_agree_online_with_invalid_status_skips_trigger(self):
        """AGREE + online 时状态不是 validating，跳过触发。"""
        approval = {"puid": "puid_123", "status": "PROCESSING", "operator_id": "user_001"}

        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": approval},
            status=PublishStatus.DRAFT.value,  # 不是 validating
        )
        publish_service.update_publish_ext = MagicMock()

        publish_flow_service = MagicMock()
        publish_flow_service.process = AsyncMock()

        service = _make_publish_approval_service(
            publish_service=publish_service,
            publish_flow_service_provider=lambda: publish_flow_service,
        )

        result = await service.handle_approval_callback(
            publish_id=1,
            action="online",
            applicant="user_001",
            puid="puid_123",
            last_operate="AGREE",
        )

        assert result["success"] is True
        assert "status is draft" in result["message"]
        publish_flow_service.process.assert_not_called()

    @pytest.mark.asyncio
    async def test_agree_offline_with_invalid_status_skips_trigger(self):
        """AGREE + offline 时状态不是 success，跳过触发。"""
        approval = {"puid": "puid_123", "status": "PROCESSING", "operator_id": "user_001"}

        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": approval},
            status=PublishStatus.VALIDATING.value,  # 不是 success
        )
        publish_service.update_publish_ext = MagicMock()
        publish_service.offline_publish = AsyncMock(return_value={"success": True})

        service = _make_publish_approval_service(publish_service=publish_service)

        result = await service.handle_approval_callback(
            publish_id=1,
            action="offline",
            applicant="user_001",
            puid="puid_123",
            last_operate="AGREE",
        )

        assert result["success"] is True
        assert "status is validating" in result["message"]
        publish_service.offline_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_uses_operator_id_from_approval(self):
        """enqueue 的 operator 应取 approval 的 operator_id 而非 applicant 参数。"""
        approval = {"puid": "puid_123", "status": "PROCESSING", "operator_id": "operator_001"}

        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": approval}, status=PublishStatus.VALIDATING.value
        )
        publish_service.update_publish_ext = MagicMock()

        task_queue_service = MagicMock()
        service = _make_publish_approval_service(
            publish_service=publish_service,
            task_queue_service=task_queue_service,
        )

        result = await service.handle_approval_callback(
            publish_id=1,
            action="online",
            applicant="other_user",  # 与 operator_id 不同
            puid="puid_123",
            last_operate="AGREE",
        )

        assert result["success"] is True
        # 触发任务的 operator 应使用 operator_id 而非 applicant
        assert task_queue_service.enqueue.call_args.args[1]["operator"] == "operator_001"

    @pytest.mark.asyncio
    async def test_falls_back_to_applicant_when_no_operator_id(self):
        """当 approval 中没有 operator_id 时，enqueue 的 operator 取 applicant。"""
        approval = {"puid": "puid_123", "status": "PROCESSING"}  # 没有 operator_id

        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": approval}, status=PublishStatus.VALIDATING.value
        )
        publish_service.update_publish_ext = MagicMock()

        task_queue_service = MagicMock()
        service = _make_publish_approval_service(
            publish_service=publish_service,
            task_queue_service=task_queue_service,
        )

        result = await service.handle_approval_callback(
            publish_id=1,
            action="online",
            applicant="fallback_user",
            puid="puid_123",
            last_operate="AGREE",
        )

        assert result["success"] is True
        assert task_queue_service.enqueue.call_args.args[1]["operator"] == "fallback_user"

    @pytest.mark.asyncio
    async def test_offline_uses_operator_id_from_approval(self):
        """offline enqueue 的 operator 应取 approval 的 operator_id。"""
        approval = {"puid": "puid_123", "status": "PROCESSING", "operator_id": "operator_offline"}

        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": approval}, status=PublishStatus.SUCCESS.value
        )
        publish_service.update_publish_ext = MagicMock()

        task_queue_service = MagicMock()
        service = _make_publish_approval_service(
            publish_service=publish_service,
            task_queue_service=task_queue_service,
        )

        result = await service.handle_approval_callback(
            publish_id=1,
            action="offline",
            applicant="other_user",  # 与 operator_id 不同
            puid="puid_123",
            last_operate="AGREE",
        )

        assert result["success"] is True
        call = task_queue_service.enqueue.call_args
        assert call.args[1]["action"] == "offline"
        assert call.args[1]["operator"] == "operator_offline"

class TestScaleApproval:
    """Scale approval is always gated, persists target_count, and is idempotent."""

    @pytest.mark.asyncio
    async def test_request_scale_approval_persists_counts_and_six_approvers(self):
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = {
            "owner_id": "owner_001",
            "owner_name": "Owner",
        }
        process_service = MagicMock()
        process_service.start_approval.return_value = {
            "success": True,
            "puid": "scale_puid_1",
            "approval_url": "https://approval.example/scale_1",
        }
        publish_service = MagicMock()
        service = _make_publish_approval_service(
            bot_service=bot_service,
            process_service=process_service,
            publish_service=publish_service,
        )
        record = _make_publish_record(ext={})

        result = await service.request_scale_approval(
            record,
            operator="owner_001",
            current_count=3,
            delta=-1,
            target_count=2,
        )

        assert result.should_approval is True
        assert result.status == "PROCESSING"
        assert result.approval["action"] == "scale"
        assert result.approval["current_count"] == 3
        assert result.approval["delta"] == -1
        assert result.approval["target_count"] == 2
        context = process_service.start_approval.call_args.kwargs["context"]
        assert context["publish_owner_audit"] == (
            "w[342011],w[113488],w[330429],w[074488],w[165137],w[382716]"
        )
        assert context["scale_approver_staff_ids"].split(",") == [
            "342011", "113488", "330429", "074488", "165137", "382716",
        ]
        assert context["target_count"] == "2"
        stored = publish_service.update_publish_ext.call_args.args[1]["approval"]
        assert stored["target_count"] == 2

    @pytest.mark.asyncio
    async def test_request_scale_approval_reuses_processing_scale_without_new_workflow(self):
        bot_service = MagicMock()
        bot_service.get_bot_by_address.return_value = {
            "owner_id": "owner_001",
            "owner_name": "Owner",
        }
        process_service = MagicMock()
        existing = {
            "puid": "scale_puid_1",
            "action": "scale",
            "status": "PROCESSING",
            "current_count": 2,
            "delta": 1,
            "target_count": 3,
        }
        service = _make_publish_approval_service(
            bot_service=bot_service,
            process_service=process_service,
        )

        result = await service.request_scale_approval(
            _make_publish_record(ext={"approval": existing}),
            operator="owner_001",
            current_count=2,
            delta=1,
            target_count=3,
        )

        assert result.status == "PROCESSING"
        assert result.approval == existing
        process_service.start_approval.assert_not_called()

    @pytest.mark.asyncio
    async def test_scale_callback_agree_enqueues_once_and_repeat_is_noop(self):
        from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
            APPROVAL_TRIGGER_TASK,
        )

        processing = {
            "puid": "scale_puid_1",
            "action": "scale",
            "status": "PROCESSING",
            "operator_id": "owner_001",
            "current_count": 2,
            "delta": 1,
            "target_count": 3,
        }
        agreed = {**processing, "status": "AGREED"}
        publish_service = MagicMock()
        publish_service.get_publish_by_id.side_effect = [
            _make_publish_record(ext={"approval": processing}, status=PublishStatus.SUCCESS.value),
            _make_publish_record(ext={"approval": agreed}, status=PublishStatus.SUCCESS.value),
            _make_publish_record(
                ext={"approval": {**agreed, "trigger_enqueued_at": "now"}},
                status=PublishStatus.SUCCESS.value,
            ),
        ]
        task_queue_service = MagicMock()
        process_service = MagicMock()
        process_service.query_approval_status.return_value = {
            "success": True, "status": "COMPLETED", "last_operate": "agree",
        }
        service = _make_publish_approval_service(
            publish_service=publish_service,
            task_queue_service=task_queue_service,
            process_service=process_service,
        )

        first = await service.handle_approval_callback(
            publish_id=1, action="scale", applicant="owner_001",
            puid="scale_puid_1", last_operate="AGREE",
        )
        second = await service.handle_approval_callback(
            publish_id=1, action="scale", applicant="owner_001",
            puid="scale_puid_1", last_operate="AGREE",
        )

        assert first["success"] is True
        assert second["success"] is True
        task_queue_service.enqueue.assert_called_once()
        call = task_queue_service.enqueue.call_args
        assert call.args[0] == APPROVAL_TRIGGER_TASK
        assert call.args[1] == {
            "publish_id": 1,
            "action": "scale",
            "operator": "owner_001",
            "puid": "scale_puid_1",
        }
        assert call.kwargs["idempotency_key"] == "approval_trigger:1:scale_puid_1"

    @pytest.mark.asyncio
    async def test_scale_disagree_does_not_enqueue_or_scale(self):
        processing = {
            "puid": "scale_puid_1",
            "action": "scale",
            "status": "PROCESSING",
            "operator_id": "owner_001",
            "target_count": 3,
        }
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": processing}, status=PublishStatus.SUCCESS.value
        )
        task_queue_service = MagicMock()
        process_service = MagicMock()
        process_service.query_approval_status.return_value = {
            "success": True, "status": "COMPLETED", "last_operate": "disagree",
        }
        flow = MagicMock()
        flow.scale_bot = AsyncMock()
        service = _make_publish_approval_service(
            publish_service=publish_service,
            task_queue_service=task_queue_service,
            process_service=process_service,
            publish_flow_service_provider=lambda: flow,
        )

        result = await service.handle_approval_callback(
            publish_id=1, action="scale", applicant="owner_001",
            puid="scale_puid_1", last_operate="DISAGREE",
        )

        assert result["success"] is True
        task_queue_service.enqueue.assert_not_called()
        flow.scale_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_scale_uses_persisted_target_and_marks_executed(self):
        agreed = {
            "puid": "scale_puid_1",
            "action": "scale",
            "status": "AGREED",
            "operator_id": "owner_001",
            "target_count": 3,
        }
        executing = {**agreed, "status": "EXECUTING"}
        publish_service = MagicMock()
        publish_service.get_publish_by_id.side_effect = [
            _make_publish_record(ext={"approval": agreed}, status=PublishStatus.SUCCESS.value),
            _make_publish_record(ext={"approval": executing}, status=PublishStatus.SUCCESS.value),
        ]
        flow = MagicMock()
        flow.scale_bot = AsyncMock(return_value={"success": True, "target_count": 3})
        service = _make_publish_approval_service(
            publish_service=publish_service,
            publish_flow_service_provider=lambda: flow,
        )

        await service._trigger_scale(
            publish_id=1, applicant="owner_001", puid="scale_puid_1"
        )

        flow.scale_bot.assert_awaited_once_with(
            publish_id=1,
            target_count=3,
            operator="owner_001",
            approval_puid="scale_puid_1",
        )
        final_ext = publish_service.update_publish_ext.call_args.args[1]
        assert final_ext["approval"]["status"] == "EXECUTED"

    @pytest.mark.asyncio
    async def test_scale_callback_rejects_forged_agree_when_upstream_disagrees(self):
        processing = {
            "puid": "scale_puid_1",
            "action": "scale",
            "status": "PROCESSING",
            "operator_id": "owner_001",
            "target_count": 3,
        }
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": processing}, status=PublishStatus.SUCCESS.value
        )
        process_service = MagicMock()
        process_service.query_approval_status.return_value = {
            "success": True, "status": "COMPLETED", "last_operate": "disagree",
        }
        task_queue_service = MagicMock()
        service = _make_publish_approval_service(
            publish_service=publish_service,
            process_service=process_service,
            task_queue_service=task_queue_service,
        )

        result = await service.handle_approval_callback(
            publish_id=1, action="scale", applicant="owner_001",
            puid="scale_puid_1", last_operate="AGREE",
        )

        assert result["success"] is False
        task_queue_service.enqueue.assert_not_called()
        publish_service.update_publish_ext.assert_not_called()

    @pytest.mark.asyncio
    async def test_scale_callback_ignores_late_agree_after_disagree(self):
        disagreed = {
            "puid": "scale_puid_1",
            "action": "scale",
            "status": "DISAGREED",
            "operator_id": "owner_001",
            "target_count": 3,
        }
        publish_service = MagicMock()
        publish_service.get_publish_by_id.return_value = _make_publish_record(
            ext={"approval": disagreed}, status=PublishStatus.SUCCESS.value
        )
        process_service = MagicMock()
        process_service.query_approval_status.return_value = {
            "success": True, "status": "COMPLETED", "last_operate": "agree",
        }
        task_queue_service = MagicMock()
        service = _make_publish_approval_service(
            publish_service=publish_service,
            process_service=process_service,
            task_queue_service=task_queue_service,
        )

        result = await service.handle_approval_callback(
            publish_id=1, action="scale", applicant="owner_001",
            puid="scale_puid_1", last_operate="AGREE",
        )

        assert result["success"] is True
        task_queue_service.enqueue.assert_not_called()
        publish_service.update_publish_ext.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_scale_redelivery_does_not_issue_second_scale(self):
        executing = {
            "puid": "scale_puid_1",
            "action": "scale",
            "status": "EXECUTING",
            "operator_id": "owner_001",
            "target_count": 3,
        }
        publish_service = MagicMock()
        publish_service.get_publish_by_id.side_effect = [
            _make_publish_record(
                ext={
                    "approval": executing,
                    "scale": {"publish_id": 99, "approval_puid": "scale_puid_1"},
                },
                status=PublishStatus.SUCCESS.value,
            ),
            _make_publish_record(
                ext={
                    "approval": executing,
                    "scale": {"publish_id": 99, "approval_puid": "scale_puid_1"},
                },
                status=PublishStatus.SUCCESS.value,
            ),
        ]
        flow = MagicMock()
        flow.scale_bot = AsyncMock()
        service = _make_publish_approval_service(
            publish_service=publish_service,
            publish_flow_service_provider=lambda: flow,
        )

        await service._trigger_scale(
            publish_id=1, applicant="owner_001", puid="scale_puid_1"
        )

        flow.scale_bot.assert_not_called()
        final_ext = publish_service.update_publish_ext.call_args.args[1]
        assert final_ext["approval"]["status"] == "EXECUTED"

    @pytest.mark.asyncio
    async def test_request_scale_approval_rejects_different_target_while_processing(self):
        existing = {
            "puid": "scale_puid_1",
            "action": "scale",
            "status": "PROCESSING",
            "current_count": 2,
            "delta": 1,
            "target_count": 3,
        }
        process_service = MagicMock()
        service = _make_publish_approval_service(process_service=process_service)

        result = await service.request_scale_approval(
            _make_publish_record(ext={"approval": existing}),
            operator="owner_001",
            current_count=2,
            delta=2,
            target_count=4,
        )

        assert result.status == "ERROR"
        assert "3" in result.message
        process_service.start_approval.assert_not_called()


    @pytest.mark.asyncio
    async def test_scale_callback_reenqueues_after_enqueue_failure(self):
        import copy

        processing = {
            "puid": "scale_puid_1",
            "action": "scale",
            "status": "PROCESSING",
            "operator_id": "owner_001",
            "current_count": 2,
            "delta": 1,
            "target_count": 3,
        }
        state = {"ext": {"approval": copy.deepcopy(processing)}}
        publish_service = MagicMock()
        publish_service.get_publish_by_id.side_effect = (
            lambda _publish_id: _make_publish_record(
                ext=copy.deepcopy(state["ext"]), status=PublishStatus.SUCCESS.value
            )
        )

        def update_ext(_publish_id, ext, expected_ext=None):
            state["ext"] = copy.deepcopy(ext)

        publish_service.update_publish_ext.side_effect = update_ext
        process_service = MagicMock()
        process_service.query_approval_status.return_value = {
            "success": True, "status": "COMPLETED", "last_operate": "agree",
        }
        task_queue_service = MagicMock()
        task_queue_service.enqueue.side_effect = [RuntimeError("queue down"), None]
        service = _make_publish_approval_service(
            publish_service=publish_service,
            process_service=process_service,
            task_queue_service=task_queue_service,
        )

        with pytest.raises(RuntimeError, match="queue down"):
            await service.handle_approval_callback(
                publish_id=1, action="scale", applicant="owner_001",
                puid="scale_puid_1", last_operate="AGREE",
            )

        assert state["ext"]["approval"]["status"] == "AGREED"
        assert "trigger_enqueued_at" not in state["ext"]["approval"]

        result = await service.handle_approval_callback(
            publish_id=1, action="scale", applicant="owner_001",
            puid="scale_puid_1", last_operate="AGREE",
        )

        assert result["success"] is True
        assert task_queue_service.enqueue.call_count == 2
        assert state["ext"]["approval"]["trigger_enqueued_at"]


    @pytest.mark.asyncio
    async def test_trigger_scale_ignores_stale_marker_from_earlier_approval(self):
        executing = {
            "puid": "scale_puid_2",
            "action": "scale",
            "status": "EXECUTING",
            "operator_id": "owner_001",
            "target_count": 4,
        }
        publish_service = MagicMock()
        publish_service.get_publish_by_id.side_effect = [
            _make_publish_record(
                ext={
                    "approval": executing,
                    "scale": {"publish_id": 99, "approval_puid": "scale_puid_1"},
                },
                status=PublishStatus.SUCCESS.value,
            ),
            _make_publish_record(
                ext={
                    "approval": {**executing, "status": "EXECUTED"},
                    "scale": {"publish_id": 100, "approval_puid": "scale_puid_2"},
                },
                status=PublishStatus.SUCCESS.value,
            ),
        ]
        flow = MagicMock()
        flow.scale_bot = AsyncMock(return_value={"success": True, "baas_publish_id": 100})
        service = _make_publish_approval_service(
            publish_service=publish_service,
            publish_flow_service_provider=lambda: flow,
        )

        await service._trigger_scale(
            publish_id=1, applicant="owner_001", puid="scale_puid_2"
        )

        flow.scale_bot.assert_awaited_once_with(
            publish_id=1,
            target_count=4,
            operator="owner_001",
            approval_puid="scale_puid_2",
        )
