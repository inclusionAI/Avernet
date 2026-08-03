"""Tests for BotPublishService.

单元测试 - 不依赖网络、文件系统、数据库，使用 mock。
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, call

from agentclaw.community.core.service_bot.services.bot_publish_service import (
    BotPublishService,
    PublishNotFoundError,
    PublishStatusInvalidError,
    BotPublishServiceError,
    BotNotFoundError,
    BotAlreadyServiceTypeError,
    BotTypeNotSupportedError,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishRecord,
    PublishOperationKind,
    PublishOperationRecord,
    PublishOperationState,
    PublishStatus,
)
from agentclaw.community.core.service_bot.services.template_runtime_engine_type_resolver import (
    BotTypeTemplateRuntimeEngineTypeResolver,
    EmptyTemplateRuntimeEngineTypeResolver,
    PersonalTemplateRuntimeEngineTypeResolver,
)
from agentclaw.community.core.service_bot.types import PublishStage


def _make_service(
    bot_publish_repo,
    *,
    bot_repo=None,
    publish_flow_service_provider=None,
    bot_service=None,
    template_service=None,
    template_runtime_engine_type_resolver=None,
    device_binding_repo=None,
    bcn_service=None,
    quality_task_service=None,
    publish_operation_repo=None,
    task_queue_service=None,
) -> BotPublishService:
    """Build a ``BotPublishService`` with MagicMock fallbacks for unused deps.

    All deps are now required on the prod ctor; tests that only exercise
    paths touching ``bot_publish_repo`` use this helper to keep call
    sites terse.
    """
    operation_repo = publish_operation_repo or MagicMock()
    if publish_operation_repo is None:
        operation_repo.get_latest_by_kind.return_value = None
        operation_repo.max_attempt.return_value = 0

    if template_runtime_engine_type_resolver is None:
        template_runtime_engine_type_resolver = BotTypeTemplateRuntimeEngineTypeResolver(
            resolvers={
                "personal": PersonalTemplateRuntimeEngineTypeResolver(
                    template_service or MagicMock()
                )
            },
            default_resolver=EmptyTemplateRuntimeEngineTypeResolver(),
        )

    return BotPublishService(
        bot_publish_repo=bot_publish_repo,
        bot_repo=bot_repo or MagicMock(),
        publish_flow_service_provider=publish_flow_service_provider or (lambda: MagicMock()),
        bot_service=bot_service or MagicMock(),
        template_runtime_engine_type_resolver=template_runtime_engine_type_resolver,
        device_binding_repo=device_binding_repo or MagicMock(),
        bcn_service=bcn_service or MagicMock(),
        quality_task_service=quality_task_service or MagicMock(),
        publish_operation_repo=operation_repo,
        task_queue_service=task_queue_service or MagicMock(),
    )


def _create_mock_record(
    record_id: int,
    status: str,
    version: int = 1,
    last_pub_id: int = 0,
    owner_id: str = "user_001",
    ext: dict = None,
) -> BotPublishRecord:
    """Helper to create mock BotPublishRecord."""
    return BotPublishRecord(
        id=record_id,
        source_bot_pk=100,
        source_bot_id="bot_001",
        publish_bot_id="bot_001_pub",
        name="Test Bot",
        owner_id=owner_id,
        status=status,
        version=version,
        last_pub_id=last_pub_id,
        env="dev",
        permission_owner="owner",
        ext=ext,
    )


class TestUpgradePublish:
    """upgrade_publish 方法测试。"""

    def test_upgrade_publish_success(self):
        """正常升级：status=success 的发布单可升级。"""
        # Arrange
        mock_repo = Mock()
        original_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
            version=1,
            last_pub_id=0,
            ext={"key": "value"},  # 原始记录有 ext 值
        )
        new_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.DRAFT,
            version=2,
            last_pub_id=1,
        )

        mock_repo.get_by_id.return_value = original_record
        mock_repo.get_by_last_pub_id.return_value = None  # 无幂等记录
        mock_repo.insert.return_value = new_record
        mock_repo.update_status.return_value = original_record
        mock_repo.get_by_publish_bot_id_and_version.return_value = None  # 版本不冲突
        mock_repo.get_by_publish_bot_id.return_value = original_record  # _get_next_version 需要

        service = _make_service(bot_publish_repo=mock_repo)

        # Act
        result = service.upgrade_publish(publish_id=1, owner_id="user_001")

        # Assert
        assert result.id == 2
        assert result.version == 2
        assert result.last_pub_id == 1
        assert result.status == PublishStatus.DRAFT
        # 正常升级流程不调用 update_status，该方法仅在幂等场景中调用
        mock_repo.update_status.assert_not_called()
        # 原 ext 无 config_artifact（ARCA 形态）→ 新草稿 ext 为 None，不整体继承
        insert_call_args = mock_repo.insert.call_args
        assert insert_call_args is not None
        inserted_data = insert_call_args[0][0]
        assert inserted_data["ext"] is None

    def test_upgrade_publish_carries_config_artifact_only(self):
        """teclaw 形态：原 ext 有 config_artifact → 新草稿只带 config_artifact，
        丢弃 binding/publish（指向上一版本容器，须由新草稿自己重新生成）。"""
        mock_repo = Mock()
        artifact = {"schema_version": 4, "mcp": {"servers": []}}
        original_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
            version=1,
            last_pub_id=0,
            ext={
                "config_artifact": artifact,
                "binding": {"verify": 99},  # 指向旧版本，必须丢弃
                "publish": {"verify": "baas-77"},
            },
        )
        new_record = _create_mock_record(
            record_id=2, status=PublishStatus.DRAFT, version=2, last_pub_id=1,
        )
        mock_repo.get_by_id.return_value = original_record
        mock_repo.get_by_last_pub_id.return_value = None
        mock_repo.insert.return_value = new_record
        mock_repo.get_by_publish_bot_id_and_version.return_value = None
        mock_repo.get_by_publish_bot_id.return_value = original_record

        service = _make_service(bot_publish_repo=mock_repo)
        service.upgrade_publish(publish_id=1, owner_id="user_001")

        inserted_ext = mock_repo.insert.call_args[0][0]["ext"]
        assert inserted_ext == {"config_artifact": artifact}
        assert "binding" not in inserted_ext
        assert "publish" not in inserted_ext

    def test_upgrade_publish_not_found(self):
        """发布单不存在时抛出 PublishNotFoundError。"""
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = None
        service = _make_service(bot_publish_repo=mock_repo)

        with pytest.raises(PublishNotFoundError, match="not found"):
            service.upgrade_publish(publish_id=999, owner_id="user_001")

    def test_upgrade_publish_invalid_status(self):
        """状态非 success 时抛出 PublishStatusInvalidError。"""
        mock_repo = Mock()
        original_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,  # 非 success
        )
        mock_repo.get_by_id.return_value = original_record
        mock_repo.get_by_last_pub_id.return_value = None

        service = _make_service(bot_publish_repo=mock_repo)

        with pytest.raises(PublishStatusInvalidError, match="must be 'success'"):
            service.upgrade_publish(publish_id=1, owner_id="user_001")

    # =============== 幂等性测试 ===============

    def test_upgrade_publish_idempotent_when_original_already_upgraded(self):
        """幂等场景1：原发布单状态已是 upgraded，直接返回已创建的新发布单。"""
        mock_repo = Mock()
        original_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.UPGRADED,  # 已升级
        )
        existing_new_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.DRAFT,
            version=2,
            last_pub_id=1,
        )

        mock_repo.get_by_id.return_value = original_record
        mock_repo.get_by_last_pub_id.return_value = existing_new_record

        service = _make_service(bot_publish_repo=mock_repo)
        result = service.upgrade_publish(publish_id=1, owner_id="user_001")

        # Assert: 返回已存在的新发布单，不调用 insert
        assert result.id == 2
        assert result.last_pub_id == 1
        mock_repo.insert.assert_not_called()
        mock_repo.update_status.assert_not_called()

    def test_upgrade_publish_idempotent_when_step5_done_step6_pending(self):
        """幂等场景2：步骤5完成但步骤6未完成，直接返回新发布单（不再补偿更新原状态）。"""
        mock_repo = Mock()
        original_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,  # 步骤6未完成，状态仍为 success
        )
        existing_new_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.DRAFT,
            version=2,
            last_pub_id=1,
        )

        mock_repo.get_by_id.return_value = original_record
        mock_repo.get_by_last_pub_id.return_value = existing_new_record
        mock_repo.update_status.return_value = original_record

        service = _make_service(bot_publish_repo=mock_repo)
        result = service.upgrade_publish(publish_id=1, owner_id="user_001")

        # Assert: 返回已存在的新发布单，不再补偿更新原状态
        assert result.id == 2
        mock_repo.insert.assert_not_called()
        mock_repo.update_status.assert_not_called()

    def test_upgrade_publish_upgraded_but_no_new_record_raises(self):
        """异常场景：原发布单状态是 upgraded 但找不到新发布单。"""
        mock_repo = Mock()
        original_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.UPGRADED,
        )
        mock_repo.get_by_id.return_value = original_record
        mock_repo.get_by_last_pub_id.return_value = None  # 找不到新发布单

        service = _make_service(bot_publish_repo=mock_repo)

        with pytest.raises(BotPublishServiceError, match="no new record found"):
            service.upgrade_publish(publish_id=1, owner_id="user_001")


class TestOfflinePublish:
    """offline_publish 方法测试。"""

    @pytest.mark.asyncio
    async def test_offline_publish_success_with_non_terminal_records(self):
        """SUCCESS 状态有非终态发布单时，不删除 bot，状态更新为 RELEASED。"""
        # Arrange
        mock_repo = Mock()
        mock_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
        )
        # 存在其他非终态发布单
        draft_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.DRAFT,
        )
        mock_repo.get_by_id.return_value = mock_record
        mock_repo.list_by_source_bot.return_value = [mock_record, draft_record]

        mock_publish_flow_service = Mock()
        mock_publish_flow_service.destroy_publish_history.return_value = {
            "success": True,
            "bot_destroyed": True,
            "message": "发布历史销毁完成: publish_id=1, stage=online",
        }

        mock_bot_service = Mock()

        service = _make_service(
            bot_publish_repo=mock_repo,
            publish_flow_service_provider=lambda: mock_publish_flow_service,
            bot_service=mock_bot_service,
        )

        # Act
        result = await service.offline_publish(publish_id=1)

        # Assert
        assert result["success"] is True
        assert result["new_publish_id"] is None
        # 有非终态发布单，不创建新草稿发布单
        mock_bot_service.delete_bot.assert_not_called()
        # 验证状态更新为 RELEASED (#197 CAS-guarded SUCCESS→RELEASED)
        mock_repo.update_status.assert_called_once_with(
            1, PublishStatus.RELEASED, PublishStatus.SUCCESS
        )
        # (#197) 销毁改为入队持久化任务，不再后台直接调用
        mock_publish_flow_service.enqueue_offline_destroy.assert_called_once_with(
            publish_id=1, stage=PublishStage.ONLINE, operator="system"
        )

    @pytest.mark.asyncio
    async def test_offline_publish_released_record_is_noop(self):
        """记录已是 RELEASED（下线已完成）→ 幂等 no-op：不翻转状态、不入队销毁，
        直接返回成功，避免重复提交或 durable 任务重跑时报错。"""
        mock_repo = Mock()
        mock_record = _create_mock_record(record_id=1, status=PublishStatus.RELEASED)
        mock_repo.get_by_id.return_value = mock_record

        mock_publish_flow_service = Mock()
        service = _make_service(
            bot_publish_repo=mock_repo,
            publish_flow_service_provider=lambda: mock_publish_flow_service,
        )

        result = await service.offline_publish(publish_id=1)

        assert result["success"] is True
        # 幂等 no-op：既不翻转状态也不入队销毁。
        mock_repo.update_status.assert_not_called()
        mock_publish_flow_service.enqueue_offline_destroy.assert_not_called()

    @pytest.mark.asyncio
    async def test_offline_publish_success_without_non_terminal_records(self):
        """SUCCESS 状态无非终态发布单时，创建新草稿发布单，状态更新为 RELEASED。"""
        # Arrange
        mock_repo = Mock()
        mock_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
            version=1,
        )
        # 只有终端态发布单（当前发布单和已下线的发布单）
        released_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.RELEASED,
        )
        mock_repo.get_by_id.return_value = mock_record
        mock_repo.list_by_source_bot.return_value = [mock_record, released_record]
        # 模拟版本检查通过（无版本冲突）
        mock_repo.get_by_publish_bot_id_and_version.return_value = None
        # 模拟 _get_next_version 查询最新版本
        mock_repo.get_by_publish_bot_id.return_value = mock_record
        # 模拟创建新发布单
        # 注意：offline_publish 创建草稿时不设置 last_pub_id（默认为 0），
        # 因为这是"下线后创建新草稿"，不是"升级发布"
        new_draft_record = _create_mock_record(
            record_id=3,
            status=PublishStatus.DRAFT,
            version=2,
            last_pub_id=1,  # 下线场景关联原发布单（幂等 key）
        )
        mock_repo.insert.return_value = new_draft_record

        mock_publish_flow_service = Mock()
        mock_publish_flow_service.destroy_publish_history.return_value = {
            "success": True,
            "bot_destroyed": True,
            "message": "发布历史销毁完成: publish_id=1, stage=online",
        }

        mock_bot_service = Mock()

        service = _make_service(
            bot_publish_repo=mock_repo,
            publish_flow_service_provider=lambda: mock_publish_flow_service,
            bot_service=mock_bot_service,
        )

        # Act
        result = await service.offline_publish(publish_id=1)

        # Assert
        assert result["success"] is True
        assert result["new_publish_id"] == 3
        assert result["new_publish_version"] == 2
        # 无非终态发布单，不调用 delete_bot，而是创建新草稿发布单
        mock_bot_service.delete_bot.assert_not_called()
        # 验证版本检查被调用
        mock_repo.get_by_publish_bot_id_and_version.assert_called_once()
        # 验证 insert 被调用创建新记录
        mock_repo.insert.assert_called_once()
        # 验证创建新发布单时关联原发布单（last_pub_id = publish_id）
        insert_call_args = mock_repo.insert.call_args
        assert insert_call_args[0][0]["last_pub_id"] == 1
        # 验证状态更新为 RELEASED (#197 CAS-guarded)
        mock_repo.update_status.assert_called_once_with(
            1, PublishStatus.RELEASED, PublishStatus.SUCCESS
        )
        # (#197) 销毁改为入队持久化任务
        mock_publish_flow_service.enqueue_offline_destroy.assert_called_once_with(
            publish_id=1, stage=PublishStage.ONLINE, operator="system"
        )

    @pytest.mark.asyncio
    async def test_offline_publish_with_validating_status(self):
        """状态为 VALIDATING 时，使用 VERIFY 阶段下线，不调用 delete_bot。"""
        # Arrange
        mock_repo = Mock()
        mock_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.VALIDATING,
        )
        mock_repo.get_by_id.return_value = mock_record

        mock_publish_flow_service = Mock()
        mock_publish_flow_service.destroy_publish_history.return_value = {
            "success": True,
            "bot_destroyed": True,
            "message": "发布历史销毁完成: publish_id=1, stage=verify",
        }

        mock_bot_service = Mock()

        service = _make_service(
            bot_publish_repo=mock_repo,
            publish_flow_service_provider=lambda: mock_publish_flow_service,
            bot_service=mock_bot_service,
        )

        # Act
        result = await service.offline_publish(publish_id=1)

        # Assert
        assert result["success"] is True
        # VALIDATING 状态不调用 delete_bot
        mock_bot_service.delete_bot.assert_not_called()
        # 验证状态更新为 DRAFT (#197 CAS-guarded VALIDATING→DRAFT)
        mock_repo.update_status.assert_called_once_with(
            1, PublishStatus.DRAFT, PublishStatus.VALIDATING
        )
        # VERIFY 阶段不执行销毁流程（也不入队）
        mock_publish_flow_service.enqueue_offline_destroy.assert_not_called()

    @pytest.mark.asyncio
    async def test_offline_publish_not_found(self):
        """发布单不存在时抛出 PublishNotFoundError。"""
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = None

        mock_publish_flow_service = Mock()

        service = _make_service(
            bot_publish_repo=mock_repo,
            publish_flow_service_provider=lambda: mock_publish_flow_service,
        )

        with pytest.raises(PublishNotFoundError, match="not found"):
            await service.offline_publish(publish_id=999)

    @pytest.mark.asyncio
    async def test_offline_publish_invalid_status(self):
        """状态不支持下线时抛出 BotPublishServiceError。"""
        mock_repo = Mock()
        mock_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,  # DRAFT 状态不支持下线
        )
        mock_repo.get_by_id.return_value = mock_record

        mock_publish_flow_service = Mock()

        service = _make_service(
            bot_publish_repo=mock_repo,
            publish_flow_service_provider=lambda: mock_publish_flow_service,
        )

        with pytest.raises(BotPublishServiceError, match="状态不支持下线"):
            await service.offline_publish(publish_id=1)

    @pytest.mark.asyncio
    async def test_offline_publish_create_draft_failure(self):
        """创建草稿发布单失败时抛出 PublishAlreadyExistsError。"""
        from agentclaw.community.core.service_bot.services.bot_publish_service import (
            PublishAlreadyExistsError,
        )

        mock_repo = Mock()
        mock_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
            version=1,
        )
        mock_repo.get_by_id.return_value = mock_record
        # 无非终态发布单，会触发创建新草稿发布单
        mock_repo.list_by_source_bot.return_value = [mock_record]
        # 模拟 _get_next_version 查询最新版本
        mock_repo.get_by_publish_bot_id.return_value = mock_record
        # 模拟版本冲突（已存在相同版本）
        existing_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.DRAFT,
            version=2,
        )
        mock_repo.get_by_publish_bot_id_and_version.return_value = existing_record

        mock_publish_flow_service = Mock()
        mock_bot_service = Mock()

        service = _make_service(
            bot_publish_repo=mock_repo,
            publish_flow_service_provider=lambda: mock_publish_flow_service,
            bot_service=mock_bot_service,
        )

        with pytest.raises(PublishAlreadyExistsError, match="Publish record already exists"):
            await service.offline_publish(publish_id=1)


class TestCanUpgradePublish:
    """can_upgrade_publish 方法测试。"""

    def test_can_upgrade_publish_success_no_next(self):
        """状态为 success 且无升级后发布单，可以升级。"""
        mock_repo = Mock()
        original_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
        )
        mock_repo.get_by_id.return_value = original_record
        mock_repo.get_by_last_pub_id.return_value = None  # 无升级后的记录

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_upgrade_publish(publish_id=1)
        assert result is True

    def test_can_upgrade_publish_success_next_failed(self):
        """状态为 success 且升级后发布单为 failed，可以升级。"""
        mock_repo = Mock()
        original_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
        )
        next_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.FAILED,
            version=2,
            last_pub_id=1,
        )
        mock_repo.get_by_id.return_value = original_record
        mock_repo.get_by_last_pub_id.return_value = next_record

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_upgrade_publish(publish_id=1)
        assert result is True

    def test_can_upgrade_publish_not_found(self):
        """发布单不存在，不能升级。"""
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = None

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_upgrade_publish(publish_id=999)
        assert result is False

    def test_can_upgrade_publish_not_success_status(self):
        """状态不是 success，不能升级。"""
        mock_repo = Mock()
        original_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,
        )
        mock_repo.get_by_id.return_value = original_record

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_upgrade_publish(publish_id=1)
        assert result is False

    def test_can_upgrade_publish_next_not_failed(self):
        """升级后发布单状态不是 failed，不能升级。"""
        mock_repo = Mock()
        original_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
        )
        next_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.DRAFT,  # 非 failed
            version=2,
            last_pub_id=1,
        )
        mock_repo.get_by_id.return_value = original_record
        mock_repo.get_by_last_pub_id.return_value = next_record

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_upgrade_publish(publish_id=1)
        assert result is False


class TestCanDeleteBot:
    """can_delete_bot 方法测试。"""

    def test_can_delete_bot_draft_no_success_publish(self):
        """草稿状态且无成功发布单，可以删除。"""
        mock_repo = Mock()
        draft_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,
        )
        # 其他发布记录（非成功状态）
        other_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.FAILED,
        )
        mock_repo.get_by_id.return_value = draft_record
        mock_repo.list_by_source_bot.return_value = [draft_record, other_record]

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_delete_bot(publish_id=1)
        assert result is True

    def test_can_delete_bot_draft_only_self(self):
        """草稿状态且只有自己，可以删除。"""
        mock_repo = Mock()
        draft_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,
        )
        mock_repo.get_by_id.return_value = draft_record
        mock_repo.list_by_source_bot.return_value = [draft_record]  # 只有自己

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_delete_bot(publish_id=1)
        assert result is True

    def test_can_delete_bot_not_found(self):
        """发布单不存在，不能删除。"""
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = None

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_delete_bot(publish_id=999)
        assert result is False

    def test_can_delete_bot_not_draft(self):
        """状态不是草稿，不能删除。"""
        mock_repo = Mock()
        success_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
        )
        mock_repo.get_by_id.return_value = success_record

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_delete_bot(publish_id=1)
        assert result is False

    def test_can_delete_bot_has_other_success_publish(self):
        """存在其他成功的发布单，不能删除。"""
        mock_repo = Mock()
        draft_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,
        )
        success_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.SUCCESS,
        )
        mock_repo.get_by_id.return_value = draft_record
        mock_repo.list_by_source_bot.return_value = [draft_record, success_record]

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_delete_bot(publish_id=1)
        assert result is False


class TestCanEditBot:
    """can_edit_bot 方法测试。"""

    def test_can_edit_bot_with_draft_publish(self):
        """存在草稿状态的发布单，可以编辑。"""
        mock_repo = Mock()
        draft_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,
        )
        mock_repo.get_by_publish_bot_id.return_value = draft_record

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_edit_bot(bot_id="bot_001", owner_id="user_001")
        assert result is True
        mock_repo.get_by_publish_bot_id.assert_called_once_with(
            publish_bot_id="bot_001",
            owner_id="user_001",
            env="dev",
        )

    def test_can_edit_bot_with_success_publish(self):
        """发布单状态为 success，不能编辑。"""
        mock_repo = Mock()
        success_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
        )
        mock_repo.get_by_publish_bot_id.return_value = success_record

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_edit_bot(bot_id="bot_001", owner_id="user_001")
        assert result is False

    def test_can_edit_bot_with_validating_publish(self):
        """发布单状态为 validating，不能编辑。"""
        mock_repo = Mock()
        validating_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.VALIDATING,
        )
        mock_repo.get_by_publish_bot_id.return_value = validating_record

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_edit_bot(bot_id="bot_001", owner_id="user_001")
        assert result is False

    def test_can_edit_bot_without_publish(self):
        """不存在发布单，不能编辑。"""
        mock_repo = Mock()
        mock_repo.get_by_publish_bot_id.return_value = None

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_edit_bot(bot_id="bot_001", owner_id="user_001")
        assert result is False

    def test_can_edit_bot_with_failed_publish(self):
        """发布单状态为 failed，不能编辑。"""
        mock_repo = Mock()
        failed_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.FAILED,
        )
        mock_repo.get_by_publish_bot_id.return_value = failed_record

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_edit_bot(bot_id="bot_001", owner_id="user_001")
        assert result is False

    def test_can_edit_bot_with_released_publish(self):
        """发布单状态为 released，不能编辑。"""
        mock_repo = Mock()
        released_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.RELEASED,
        )
        mock_repo.get_by_publish_bot_id.return_value = released_record

        service = _make_service(bot_publish_repo=mock_repo)

        result = service.can_edit_bot(bot_id="bot_001", owner_id="user_001")
        assert result is False


class TestUpgradeBotToService:
    """upgrade_bot_to_service 方法测试。"""

    def test_upgrade_bot_to_service_success(self):
        """正常升级：personal bot 升级为 service bot，并创建发布记录。"""
        # Arrange
        mock_repo = Mock()
        mock_bot_repo = Mock()
        mock_bot_service = Mock()
        mock_bcn_service = Mock()
        mock_bcn_service.switch_bot.return_value = {
            "bot_id": "bot_001:user_001",
            "provider_id": "prv_test",
            "token": "test_token",
            "websocket_kicked": True,
            "idempotent_replay": False,
        }

        # Bot 数据
        mock_bot = {
            "id": 100,
            "bot_id": "bot_001",
            "bot_name": "Test Bot",
            "bot_type": "personal",
            "active_engine": "openclaw",
            "owner_id": "user_001",
            "owner_name": "Test User",
            "bot_desc": "Test description",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = mock_bot
        mock_bot_repo.update_by_owner.return_value = {**mock_bot, "bot_type": "service"}
        # openclaw 走重启分支（非 teclaw）。
        mock_bot_service.is_teclaw_bot.return_value = False

        # 无已存在发布记录
        mock_repo.get_by_publish_bot_id.return_value = None

        # 创建的新发布记录
        new_publish_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,
        )
        mock_repo.insert.return_value = new_publish_record
        mock_repo.get_by_publish_bot_id_and_version.return_value = None

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
            bot_service=mock_bot_service,
            bcn_service=mock_bcn_service,
        )

        # Act - 使用 mock threading.Thread 立即执行目标函数
        with pytest.MonkeyPatch.context() as m:
            captured_target = None

            def mock_thread(target, daemon=False):
                nonlocal captured_target
                captured_target = target
                return Mock(start=lambda: captured_target())

            m.setattr("threading.Thread", mock_thread)

            result = service.upgrade_bot_to_service(
                bot_id="bot_001",
                owner_id="user_001",
            )

        # Assert
        assert result["bot"]["bot_type"] == "service"
        assert result["publish_record"] is not None
        mock_bot_repo.update_by_owner.assert_called_once_with(
            "bot_001", "user_001", {"bot_type": "service"}
        )
        mock_repo.insert.assert_called_once()
        # 验证 switch_bot 被调用
        mock_bcn_service.switch_bot.assert_called_once_with(
            teamclaw_bot_uuid="bot_001",
            owner_workno="user_001",
            name="Test Bot",
            summary="Test description",
        )
        # 验证 restart_bot 被异步调用
        mock_bot_service.restart_bot.assert_called_once_with(
            bot_id="bot_001",
            user_id="user_001",
            nick_name="Test User",
        )

    def test_upgrade_bot_to_service_with_existing_publish(self):
        """已有发布记录时，只更新 bot_type，不创建新发布记录，返回已有发布记录，但会异步重启 Bot。"""
        # Arrange
        mock_repo = Mock()
        mock_bot_repo = Mock()
        mock_bot_service = Mock()
        mock_bcn_service = Mock()
        mock_bcn_service.switch_bot.return_value = {
            "bot_id": "bot_001:user_001",
            "provider_id": "prv_test",
            "token": "test_token",
            "websocket_kicked": True,
            "idempotent_replay": False,
        }

        mock_bot = {
            "id": 100,
            "bot_id": "bot_001",
            "bot_name": "Test Bot",
            "bot_type": "personal",
            "active_engine": "openclaw",
            "owner_id": "user_001",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = mock_bot
        mock_bot_repo.update_by_owner.return_value = {**mock_bot, "bot_type": "service"}
        # openclaw 走重启分支（非 teclaw）。
        mock_bot_service.is_teclaw_bot.return_value = False

        # 已存在发布记录
        existing_publish = _create_mock_record(record_id=1, status=PublishStatus.DRAFT)
        mock_repo.get_by_publish_bot_id.return_value = existing_publish

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
            bot_service=mock_bot_service,
            bcn_service=mock_bcn_service,
        )

        # Act
        result = service.upgrade_bot_to_service(
            bot_id="bot_001",
            owner_id="user_001",
        )

        # Assert
        assert result["bot"]["bot_type"] == "service"
        assert result["publish_record"] is not None
        assert result["publish_record"].id == existing_publish.id
        mock_bot_repo.update_by_owner.assert_called_once()
        mock_repo.insert.assert_not_called()
        # 验证 switch_bot 被调用
        mock_bcn_service.switch_bot.assert_called_once()
        # 无论是否有发布记录，都会异步重启 Bot
        mock_bot_service.restart_bot.assert_called_once_with(
            bot_id="bot_001",
            user_id="user_001",
            nick_name=None,
        )

    def test_upgrade_bot_to_service_teclaw_skips_restart(self):
        """teclaw bot 升级为 service 时不重启：容器与 bot_type 无关，重启会 destroy
        容器 + 重新分配失败把 bot 打成无 binding 坏状态并丢数据（Dima 2026070100117117968）。
        仍需切 bot_type、switch_bot、建发布记录。"""
        # Arrange
        mock_repo = Mock()
        mock_bot_repo = Mock()
        mock_bot_service = Mock()
        mock_bot_service.is_teclaw_bot.return_value = True
        mock_bcn_service = Mock()
        mock_bcn_service.switch_bot.return_value = {
            "bot_id": "bot_teclaw:user_001",
            "websocket_kicked": False,
            "idempotent_replay": False,
        }

        mock_bot = {
            "id": 100,
            "bot_id": "bot_teclaw",
            "bot_name": "Teclaw Bot",
            "bot_type": "personal",
            "active_engine": "teclaw",
            "owner_id": "user_001",
            "owner_name": "Test User",
            "bot_desc": "Test description",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = mock_bot
        mock_bot_repo.update_by_owner.return_value = {**mock_bot, "bot_type": "service"}
        mock_repo.get_by_publish_bot_id.return_value = None
        new_publish_record = _create_mock_record(record_id=1, status=PublishStatus.DRAFT)
        mock_repo.insert.return_value = new_publish_record
        mock_repo.get_by_publish_bot_id_and_version.return_value = None

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
            bot_service=mock_bot_service,
            bcn_service=mock_bcn_service,
        )

        # Act — restart 若被调用会经过 threading.Thread；patch 成立即执行以便捕获误调用。
        with pytest.MonkeyPatch.context() as m:
            def mock_thread(target, daemon=False):
                return Mock(start=lambda: target())

            m.setattr("threading.Thread", mock_thread)

            result = service.upgrade_bot_to_service(
                bot_id="bot_teclaw",
                owner_id="user_001",
            )

        # Assert — teclaw 不重启，但其余升级动作照常。
        mock_bot_service.is_teclaw_bot.assert_called_once_with("teclaw")
        mock_bot_service.restart_bot.assert_not_called()
        assert result["bot"]["bot_type"] == "service"
        assert result["publish_record"] is not None
        mock_bcn_service.switch_bot.assert_called_once()
        mock_bot_repo.update_by_owner.assert_called_once_with(
            "bot_teclaw", "user_001", {"bot_type": "service"}
        )
        mock_repo.insert.assert_called_once()

    def test_upgrade_bot_to_service_not_found(self):
        """Bot 不存在时抛出 BotNotFoundError。"""
        mock_repo = Mock()
        mock_bot_repo = Mock()
        mock_bot_repo.get_by_id_and_owner.return_value = None

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
        )

        with pytest.raises(BotNotFoundError, match="Bot not found"):
            service.upgrade_bot_to_service(
                bot_id="bot_001",
                owner_id="user_001",
            )

    def test_upgrade_bot_to_service_already_service(self):
        """Bot 已经是 service 类型时抛出 BotAlreadyServiceTypeError。"""
        mock_repo = Mock()
        mock_bot_repo = Mock()

        mock_bot = {
            "id": 100,
            "bot_id": "bot_001",
            "bot_type": "service",  # 已经是 service
            "active_engine": "openclaw",
            "owner_id": "user_001",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = mock_bot

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
        )

        with pytest.raises(BotAlreadyServiceTypeError, match="already a service bot"):
            service.upgrade_bot_to_service(
                bot_id="bot_001",
                owner_id="user_001",
            )

    def test_upgrade_bot_to_service_aicoding_not_supported(self):
        """aicoding 类型 Bot 不支持升级，抛出 BotTypeNotSupportedError。"""
        mock_repo = Mock()
        mock_bot_repo = Mock()

        mock_bot = {
            "id": 100,
            "bot_id": "bot_001",
            "bot_type": "personal",
            "active_engine": "aicoding",  # aicoding 类型
            "owner_id": "user_001",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = mock_bot

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
        )

        with pytest.raises(BotTypeNotSupportedError, match="aicoding type bot cannot be upgraded"):
            service.upgrade_bot_to_service(
                bot_id="bot_001",
                owner_id="user_001",
            )

    def test_upgrade_bot_to_service_default_bot_type(self):
        """bot_type 未设置（默认 personal）时可以正常升级。"""
        # Arrange
        mock_repo = Mock()
        mock_bot_repo = Mock()
        mock_bcn_service = Mock()
        mock_bcn_service.switch_bot.return_value = {
            "bot_id": "bot_001:user_001",
            "provider_id": "prv_test",
            "token": "test_token",
            "websocket_kicked": True,
            "idempotent_replay": False,
        }

        # Bot 没有 bot_type 字段（默认为 personal）
        mock_bot = {
            "id": 100,
            "bot_id": "bot_001",
            "bot_name": "Test Bot",
            "active_engine": "moltis",
            "owner_id": "user_001",
            "owner_name": "Test User",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = mock_bot
        mock_bot_repo.update_by_owner.return_value = {**mock_bot, "bot_type": "service"}

        mock_repo.get_by_publish_bot_id.return_value = None

        new_publish_record = _create_mock_record(record_id=1, status=PublishStatus.DRAFT)
        mock_repo.insert.return_value = new_publish_record
        mock_repo.get_by_publish_bot_id_and_version.return_value = None

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
            bcn_service=mock_bcn_service,
        )

        # Act
        with pytest.MonkeyPatch.context() as m:
            def mock_thread(target, daemon=False):
                return Mock(start=lambda: None)
            m.setattr("threading.Thread", mock_thread)

            result = service.upgrade_bot_to_service(
                bot_id="bot_001",
                owner_id="user_001",
            )

        # Assert
        assert result["bot"]["bot_type"] == "service"
        assert result["publish_record"] is not None
        mock_bcn_service.switch_bot.assert_called_once()

    def test_upgrade_bot_to_service_restart_bot_exception_logged(self):
        """restart_bot 异常时记录日志，不影响主流程返回。"""
        # Arrange
        mock_repo = Mock()
        mock_bot_repo = Mock()
        mock_bot_service = Mock()
        # openclaw 走重启分支（非 teclaw）。
        mock_bot_service.is_teclaw_bot.return_value = False
        mock_bot_service.restart_bot.side_effect = RuntimeError("restart failed")
        mock_bcn_service = Mock()
        mock_bcn_service.switch_bot.return_value = {
            "bot_id": "bot_001:user_001",
            "provider_id": "prv_test",
            "token": "test_token",
            "websocket_kicked": True,
            "idempotent_replay": False,
        }

        mock_bot = {
            "id": 100,
            "bot_id": "bot_001",
            "bot_name": "Test Bot",
            "bot_type": "personal",
            "active_engine": "openclaw",
            "owner_id": "user_001",
            "owner_name": "Test User",
            "bot_desc": "Test description",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = mock_bot
        mock_bot_repo.update_by_owner.return_value = {**mock_bot, "bot_type": "service"}

        mock_repo.get_by_publish_bot_id.return_value = None

        new_publish_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,
        )
        mock_repo.insert.return_value = new_publish_record
        mock_repo.get_by_publish_bot_id_and_version.return_value = None

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
            bot_service=mock_bot_service,
            bcn_service=mock_bcn_service,
        )

        # Act - 使用 mock threading.Thread 立即执行目标函数
        with pytest.MonkeyPatch.context() as m:
            captured_target = None

            def mock_thread(target, daemon=False):
                nonlocal captured_target
                captured_target = target
                return Mock(start=lambda: captured_target())

            m.setattr("threading.Thread", mock_thread)

            result = service.upgrade_bot_to_service(
                bot_id="bot_001",
                owner_id="user_001",
            )

        # Assert - 异常不影响主流程返回值
        assert result["bot"]["bot_type"] == "service"
        assert result["publish_record"] is not None
        # switch_bot 被调用
        mock_bcn_service.switch_bot.assert_called_once()
        # restart_bot 被调用但抛出异常
        mock_bot_service.restart_bot.assert_called_once_with(
            bot_id="bot_001",
            user_id="user_001",
            nick_name="Test User",
        )

    def test_upgrade_bot_to_service_switch_bot_failure_raises(self):
        """switch_bot 失败时抛出异常，中断流程。"""
        # Arrange
        from agentclaw.community.core.bot_management.services.bcn_service import BcnServiceError

        mock_repo = Mock()
        mock_bot_repo = Mock()
        mock_bot_service = Mock()
        mock_bcn_service = Mock()
        mock_bcn_service.switch_bot.side_effect = BcnServiceError("BCN switch failed")

        mock_bot = {
            "id": 100,
            "bot_id": "bot_001",
            "bot_name": "Test Bot",
            "bot_type": "personal",
            "active_engine": "openclaw",
            "owner_id": "user_001",
            "owner_name": "Test User",
            "bot_desc": "Test description",
        }
        mock_bot_repo.get_by_id_and_owner.return_value = mock_bot

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
            bot_service=mock_bot_service,
            bcn_service=mock_bcn_service,
        )

        # Act & Assert - switch_bot 失败抛出异常
        with pytest.raises(BcnServiceError, match="BCN switch failed"):
            service.upgrade_bot_to_service(
                bot_id="bot_001",
                owner_id="user_001",
            )

        # switch_bot 被调用
        mock_bcn_service.switch_bot.assert_called_once_with(
            teamclaw_bot_uuid="bot_001",
            owner_workno="user_001",
            name="Test Bot",
            summary="Test description",
        )
        # bot_type 未被更新
        mock_bot_repo.update_by_owner.assert_not_called()
        # restart_bot 未被调用
        mock_bot_service.restart_bot.assert_not_called()


class TestUpdateBotType:
    """update_bot_type 方法测试。"""

    def test_update_bot_type_to_service(self):
        """更新 bot_type 为 service。"""
        mock_repo = Mock()
        mock_bot_repo = Mock()

        updated_bot = {
            "id": 100,
            "bot_id": "bot_001",
            "bot_name": "Test Bot",
            "bot_type": "service",
            "owner_id": "user_001",
        }
        mock_bot_repo.update_by_owner.return_value = updated_bot

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
        )

        result = service.update_bot_type(
            bot_id="bot_001",
            owner_id="user_001",
            bot_type="service",
        )

        assert result["bot"]["bot_type"] == "service"
        mock_bot_repo.update_by_owner.assert_called_once_with(
            "bot_001", "user_001", {"bot_type": "service"}
        )

    def test_update_bot_type_to_personal(self):
        """更新 bot_type 为 personal。"""
        mock_repo = Mock()
        mock_bot_repo = Mock()

        updated_bot = {
            "id": 100,
            "bot_id": "bot_001",
            "bot_name": "Test Bot",
            "bot_type": "personal",
            "owner_id": "user_001",
        }
        mock_bot_repo.update_by_owner.return_value = updated_bot

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
        )

        result = service.update_bot_type(
            bot_id="bot_001",
            owner_id="user_001",
            bot_type="personal",
        )

        assert result["bot"]["bot_type"] == "personal"
        mock_bot_repo.update_by_owner.assert_called_once_with(
            "bot_001", "user_001", {"bot_type": "personal"}
        )

    def test_update_bot_type_invalid_type(self):
        """无效的 bot_type 应该报错。"""
        mock_repo = Mock()
        mock_bot_repo = Mock()

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
        )

        with pytest.raises(
            BotPublishServiceError, match="Invalid bot_type"
        ):
            service.update_bot_type(
                bot_id="bot_001",
                owner_id="user_001",
                bot_type="invalid_type",
            )

        # 不应调用 update_by_owner
        mock_bot_repo.update_by_owner.assert_not_called()

    def test_update_bot_type_bot_not_found(self):
        """Bot 不存在应该报错。"""
        mock_repo = Mock()
        mock_bot_repo = Mock()
        mock_bot_repo.update_by_owner.return_value = None

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
        )

        with pytest.raises(BotNotFoundError, match="Bot not found"):
            service.update_bot_type(
                bot_id="bot_001",
                owner_id="user_001",
                bot_type="service",
            )

    def test_update_bot_type_default_bot_allowed(self):
        """允许更新 default bot 的类型。"""
        mock_repo = Mock()
        mock_bot_repo = Mock()

        updated_bot = {
            "id": 100,
            "bot_id": "default",
            "bot_name": "Default Bot",
            "bot_type": "service",
            "owner_id": "user_001",
        }
        mock_bot_repo.update_by_owner.return_value = updated_bot

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=mock_bot_repo,
        )

        result = service.update_bot_type(
            bot_id="default",
            owner_id="user_001",
            bot_type="service",
        )

        assert result["bot"]["bot_type"] == "service"
        assert result["bot"]["bot_id"] == "default"


class TestDeleteServiceBot:
    """delete_service_bot 方法测试。"""

    def test_delete_service_bot_success(self):
        """正常删除：草稿状态且无成功发布单，删除成功。"""
        mock_repo = Mock()
        mock_bot_service = Mock()
        mock_publish_flow_service = Mock()

        draft_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,
        )
        mock_repo.get_by_id.return_value = draft_record
        # 只有自己，无其他成功发布单
        mock_repo.list_by_source_bot.return_value = [draft_record]

        # 模拟销毁发布历史成功
        mock_publish_flow_service.destroy_publish_history.return_value = {
            "success": True,
            "bot_destroyed": True,
            "message": "销毁成功",
        }

        # 模拟 BotService.delete_bot 成功
        mock_bot_service.delete_bot.return_value = True

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_service=mock_bot_service,
            publish_flow_service_provider=lambda: mock_publish_flow_service,
        )

        result = service.delete_service_bot(publish_id=1)

        assert result is True
        # 验证销毁发布历史被调用
        mock_publish_flow_service.destroy_publish_history.assert_called_once_with(
            publish_id=1,
            stage=PublishStage.VERIFY,
        )
        # 验证 BotService.delete_bot 被调用
        mock_bot_service.delete_bot.assert_called_once_with(
            bot_id="bot_001",
            user_id="user_001",
        )

    def test_delete_service_bot_not_found(self):
        """发布单不存在时抛出 PublishNotFoundError。"""
        mock_repo = Mock()
        mock_bot_service = Mock()
        mock_publish_flow_service = Mock()

        mock_repo.get_by_id.return_value = None

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_service=mock_bot_service,
            publish_flow_service_provider=lambda: mock_publish_flow_service,
        )

        with pytest.raises(PublishNotFoundError, match="not found"):
            service.delete_service_bot(publish_id=999)

        # 不应调用销毁和删除方法
        mock_publish_flow_service.destroy_publish_history.assert_not_called()
        mock_bot_service.delete_bot.assert_not_called()

    def test_delete_service_bot_cannot_delete(self):
        """不满足删除条件时抛出 BotPublishServiceError。"""
        mock_repo = Mock()
        mock_bot_service = Mock()
        mock_publish_flow_service = Mock()

        # 成功状态的发布单，不满足删除条件
        success_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
        )
        mock_repo.get_by_id.return_value = success_record

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_service=mock_bot_service,
            publish_flow_service_provider=lambda: mock_publish_flow_service,
        )

        with pytest.raises(BotPublishServiceError, match="Cannot delete service bot"):
            service.delete_service_bot(publish_id=1)

        # 不应调用销毁和删除方法
        mock_publish_flow_service.destroy_publish_history.assert_not_called()
        mock_bot_service.delete_bot.assert_not_called()

    def test_delete_service_bot_destroy_publish_history_failed(self):
        """destroy_publish_history 失败时，抛出异常阻断删除流程。"""
        mock_repo = Mock()
        mock_bot_service = Mock()
        mock_publish_flow_service = Mock()

        draft_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,
        )
        mock_repo.get_by_id.return_value = draft_record
        mock_repo.list_by_source_bot.return_value = [draft_record]

        # 模拟销毁失败
        mock_publish_flow_service.destroy_publish_history.side_effect = Exception("销毁失败")

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_service=mock_bot_service,
            publish_flow_service_provider=lambda: mock_publish_flow_service,
        )

        with pytest.raises(Exception, match="销毁失败"):
            service.delete_service_bot(publish_id=1)

        # BotService.delete_bot 不应被调用
        mock_bot_service.delete_bot.assert_not_called()

    def test_delete_service_bot_has_other_success_publish(self):
        """存在其他成功发布单时，不能删除。"""
        mock_repo = Mock()
        mock_bot_service = Mock()
        mock_publish_flow_service = Mock()

        draft_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,
        )
        success_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.SUCCESS,
        )
        mock_repo.get_by_id.return_value = draft_record
        # 存在其他成功的发布单
        mock_repo.list_by_source_bot.return_value = [draft_record, success_record]

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_service=mock_bot_service,
            publish_flow_service_provider=lambda: mock_publish_flow_service,
        )

        with pytest.raises(BotPublishServiceError, match="Cannot delete service bot"):
            service.delete_service_bot(publish_id=1)

        # 不应调用任何方法
        mock_publish_flow_service.destroy_publish_history.assert_not_called()
        mock_bot_service.delete_bot.assert_not_called()

    def test_delete_service_bot_with_last_pub_id(self):
        """有 last_pub_id 时，销毁当前发布单和上一个发布单的发布历史。"""
        mock_repo = Mock()
        mock_bot_service = Mock()
        mock_publish_flow_service = Mock()

        # 草稿记录有 last_pub_id=100
        draft_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,
            last_pub_id=100,
        )
        mock_repo.get_by_id.return_value = draft_record
        mock_repo.list_by_source_bot.return_value = [draft_record]

        # 模拟销毁发布历史成功
        mock_publish_flow_service.destroy_publish_history.return_value = {
            "success": True,
            "bot_destroyed": True,
            "message": "销毁成功",
        }

        # 模拟 BotService.delete_bot 成功
        mock_bot_service.delete_bot.return_value = True

        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_service=mock_bot_service,
            publish_flow_service_provider=lambda: mock_publish_flow_service,
        )

        result = service.delete_service_bot(publish_id=1)

        assert result is True
        # 验证销毁发布历史被调用两次：当前发布单和上一个发布单
        assert mock_publish_flow_service.destroy_publish_history.call_count == 2
        calls = mock_publish_flow_service.destroy_publish_history.call_args_list
        # 第一次：销毁当前发布单
        assert calls[0] == call(publish_id=1, stage=PublishStage.VERIFY)
        # 第二次：销毁上一个发布单
        assert calls[1] == call(publish_id=100, stage=PublishStage.VERIFY)
        # 验证 BotService.delete_bot 被调用
        mock_bot_service.delete_bot.assert_called_once_with(
            bot_id="bot_001",
            user_id="user_001",
        )


class TestRecordDraftArtifact:
    """record_draft_artifact 方法测试。"""

    def test_records_artifact_onto_draft_ext(self):
        """DRAFT 行：把 config_artifact 合并进 ext，并保留已有 ext 键。"""
        record = _create_mock_record(
            record_id=7, status=PublishStatus.DRAFT, ext={"keep": "me"}
        )
        mock_repo = Mock()
        mock_repo.get_draft_by_publish_bot_id.return_value = record
        mock_repo.get_by_id.return_value = record
        mock_repo.update_status_with_ext.return_value = record
        service = _make_service(bot_publish_repo=mock_repo)

        artifact = {"schema_version": 4, "mcp": {"servers": []}}
        ok = service.record_draft_artifact(bot_id="bot_001_pub", artifact=artifact)

        assert ok is True
        # owner-agnostic lookup (publish_bot_id only — no owner filter, so an
        # org bot's edit-time entity_id can't make it silently miss)
        mock_repo.get_draft_by_publish_bot_id.assert_called_once_with(
            publish_bot_id="bot_001_pub", env=service._env
        )
        # update_publish_ext -> update_status_with_ext with merged ext (other keys kept)
        _, kwargs = mock_repo.update_status_with_ext.call_args
        assert kwargs["publish_id"] == 7
        assert kwargs["ext"] == {"keep": "me", "config_artifact": artifact}

    def test_no_op_when_no_draft_row(self):
        """无 DRAFT 行（personal bot 无发布单，或行已进入 building/online —
        owner-agnostic 查询按 status==DRAFT 过滤）：返回 False，不写 ext。"""
        mock_repo = Mock()
        mock_repo.get_draft_by_publish_bot_id.return_value = None
        service = _make_service(bot_publish_repo=mock_repo)

        ok = service.record_draft_artifact(bot_id="bot_x", artifact={"schema_version": 4})

        assert ok is False
        mock_repo.update_status_with_ext.assert_not_called()


class TestGetNextVersion:
    """_get_next_version 方法测试。"""

    def test_get_next_version_no_existing_record(self):
        """没有已存在的记录时，返回版本号 1。"""
        mock_repo = Mock()
        mock_repo.get_by_publish_bot_id.return_value = None

        service = _make_service(bot_publish_repo=mock_repo)

        version = service._get_next_version("bot_001_pub", "user_001")
        assert version == 1
        mock_repo.get_by_publish_bot_id.assert_called_once_with(
            "bot_001_pub", "user_001", service._env
        )

    def test_get_next_version_with_existing_record(self):
        """有已存在的记录时，返回最大版本号 + 1。"""
        mock_repo = Mock()
        latest_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
            version=3,
        )
        mock_repo.get_by_publish_bot_id.return_value = latest_record

        service = _make_service(bot_publish_repo=mock_repo)

        version = service._get_next_version("bot_001_pub", "user_001")
        assert version == 4

    def test_get_next_version_with_version_none(self):
        """已存在记录的 version 为 None 时，返回 1。"""
        mock_repo = Mock()
        latest_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
            version=None,
        )
        mock_repo.get_by_publish_bot_id.return_value = latest_record

        service = _make_service(bot_publish_repo=mock_repo)

        version = service._get_next_version("bot_001_pub", "user_001")
        assert version == 1

    def test_get_next_version_after_rollback(self):
        """回滚后，版本号基于最大版本号计算，不会冲突。

        场景：v1(SUCCESS) -> v2(SUCCESS) -> v3(SUCCESS)
        回滚 v3 到 v2 后，再升级时应该得到 v4 而不是 v3。
        """
        mock_repo = Mock()
        # 假设 v3 是最新版本
        latest_record = _create_mock_record(
            record_id=3,
            status=PublishStatus.DRAFT,  # 回滚后变为 DRAFT
            version=3,
        )
        mock_repo.get_by_publish_bot_id.return_value = latest_record

        service = _make_service(bot_publish_repo=mock_repo)

        version = service._get_next_version("bot_001_pub", "user_001")
        # 应该返回 4，而不是 3
        assert version == 4


class TestCanRollback:
    """can_rollback 方法测试。"""

    def test_can_rollback_success(self):
        """正常场景：可以回滚。"""
        mock_repo = Mock()
        # 当前版本 v3，状态 SUCCESS
        current_record = _create_mock_record(
            record_id=3,
            status=PublishStatus.SUCCESS,
            version=3,
            last_pub_id=2,
        )
        # 目标版本 v2，状态 UPGRADED，有构建产物
        target_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.UPGRADED,
            version=2,
            ext={"migration_path": "/tmp/build"},
        )

        mock_repo.get_by_id.side_effect = [current_record, target_record]
        mock_repo.get_by_last_pub_id.return_value = None  # 无新版本基于当前版本

        service = _make_service(bot_publish_repo=mock_repo)

        can_rollback, reason = service.can_rollback(3)
        assert can_rollback is True
        assert reason == "可以回滚"

    def test_can_rollback_not_found(self):
        """发布单不存在，不能回滚。"""
        mock_repo = Mock()
        mock_repo.get_by_id.return_value = None

        service = _make_service(bot_publish_repo=mock_repo)

        can_rollback, reason = service.can_rollback(999)
        assert can_rollback is False
        assert "发布单不存在" in reason

    def test_can_rollback_not_success_status(self):
        """状态不是 SUCCESS，不能回滚。"""
        mock_repo = Mock()
        current_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,  # 非 SUCCESS
        )
        mock_repo.get_by_id.return_value = current_record

        service = _make_service(bot_publish_repo=mock_repo)

        can_rollback, reason = service.can_rollback(1)
        assert can_rollback is False
        assert "只有 SUCCESS 状态" in reason

    def test_can_rollback_no_last_pub_id(self):
        """没有上一个版本（last_pub_id 为 0），不能回滚。"""
        mock_repo = Mock()
        current_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.SUCCESS,
            last_pub_id=0,  # 无上一版本
        )
        mock_repo.get_by_id.return_value = current_record

        service = _make_service(bot_publish_repo=mock_repo)

        can_rollback, reason = service.can_rollback(1)
        assert can_rollback is False
        assert "没有可回滚的目标版本" in reason

    def test_can_rollback_with_rollback_restored_from_marker(self):
        """当前版本是通过回滚恢复的（有 rollback_restored_from 标记），不能继续回滚。"""
        mock_repo = Mock()
        current_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.SUCCESS,
            last_pub_id=1,
            ext={"rollback_restored_from": 3},  # 通过回滚恢复
        )
        mock_repo.get_by_id.return_value = current_record

        service = _make_service(bot_publish_repo=mock_repo)

        can_rollback, reason = service.can_rollback(2)
        assert can_rollback is False
        assert "通过回滚恢复的" in reason

    def test_can_rollback_version_chain_extended(self):
        """版本链已延伸（有新版本基于当前版本），不能回滚。"""
        mock_repo = Mock()
        current_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.SUCCESS,
            version=2,
            last_pub_id=1,
        )
        # 有新版本 v3 基于当前版本 v2
        next_record = _create_mock_record(
            record_id=3,
            status=PublishStatus.DRAFT,
            version=3,
            last_pub_id=2,
        )

        mock_repo.get_by_id.return_value = current_record
        mock_repo.get_by_last_pub_id.return_value = next_record

        service = _make_service(bot_publish_repo=mock_repo)

        can_rollback, reason = service.can_rollback(2)
        assert can_rollback is False
        assert "已有新版本" in reason

    def test_can_rollback_target_not_found(self):
        """目标版本不存在，不能回滚。"""
        mock_repo = Mock()
        current_record = _create_mock_record(
            record_id=3,
            status=PublishStatus.SUCCESS,
            last_pub_id=999,  # 不存在
        )
        mock_repo.get_by_id.side_effect = [current_record, None]  # 目标版本不存在
        mock_repo.get_by_last_pub_id.return_value = None

        service = _make_service(bot_publish_repo=mock_repo)

        can_rollback, reason = service.can_rollback(3)
        assert can_rollback is False
        assert "目标版本不存在" in reason

    def test_can_rollback_target_not_upgraded(self):
        """目标版本状态不是 UPGRADED，不能回滚。"""
        mock_repo = Mock()
        current_record = _create_mock_record(
            record_id=3,
            status=PublishStatus.SUCCESS,
            last_pub_id=2,
        )
        target_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.SUCCESS,  # 非 UPGRADED
            ext={"migration_path": "/tmp/build"},
        )

        mock_repo.get_by_id.side_effect = [current_record, target_record]
        mock_repo.get_by_last_pub_id.return_value = None

        service = _make_service(bot_publish_repo=mock_repo)

        can_rollback, reason = service.can_rollback(3)
        assert can_rollback is False
        assert "目标版本状态不支持回滚" in reason

    def test_can_rollback_target_no_build_artifact(self):
        """目标版本没有构建产物，不能回滚。"""
        mock_repo = Mock()
        current_record = _create_mock_record(
            record_id=3,
            status=PublishStatus.SUCCESS,
            last_pub_id=2,
        )
        target_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.UPGRADED,
            ext={},  # 无构建产物
        )

        mock_repo.get_by_id.side_effect = [current_record, target_record]
        mock_repo.get_by_last_pub_id.return_value = None

        service = _make_service(bot_publish_repo=mock_repo)

        can_rollback, reason = service.can_rollback(3)
        assert can_rollback is False
        assert "缺少构建产物" in reason

    def test_can_rollback_target_has_config_artifact(self):
        """目标版本有 config_artifact 也可以回滚。"""
        mock_repo = Mock()
        current_record = _create_mock_record(
            record_id=3,
            status=PublishStatus.SUCCESS,
            last_pub_id=2,
        )
        target_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.UPGRADED,
            ext={"config_artifact": {"schema_version": 1}},  # 有 config_artifact
        )

        mock_repo.get_by_id.side_effect = [current_record, target_record]
        mock_repo.get_by_last_pub_id.return_value = None

        service = _make_service(bot_publish_repo=mock_repo)

        can_rollback, reason = service.can_rollback(3)
        assert can_rollback is True
        assert reason == "可以回滚"


class TestRollbackPublish:
    """rollback_publish 方法测试。"""

    @pytest.mark.asyncio
    async def test_rollback_publish_success(self):
        """正常回滚流程。"""
        mock_repo = Mock()
        # 当前版本 v3，状态 SUCCESS
        current_record = _create_mock_record(
            record_id=3,
            status=PublishStatus.SUCCESS,
            version=3,
            last_pub_id=2,
            ext={},
        )
        # 目标版本 v2，状态 UPGRADED
        target_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.UPGRADED,
            version=2,
            ext={"migration_path": "/tmp/build"},
        )

        # can_rollback 也会调用 get_by_id，所以需要设置 side_effect
        mock_repo.get_by_id.side_effect = [current_record, target_record, current_record, target_record]
        mock_repo.get_by_last_pub_id.return_value = None
        # (#197) 原子翻转：两条 CAS 均命中
        mock_repo.rollback_flip.return_value = (True, True)

        # Mock execute_rollback
        mock_flow_service = MagicMock()
        mock_flow_service.execute_rollback = AsyncMock(return_value=MagicMock(
            status=PublishStatus.ONLINE_PUB,
            message="回滚发布已提交",
        ))
        service = _make_service(
            bot_publish_repo=mock_repo,
            publish_flow_service_provider=lambda: mock_flow_service,
        )

        result = await service.rollback_publish(publish_id=3, operator="user_001", reason="回滚测试")

        # 验证结果
        assert result["rolled_back_publish_id"] == 3
        assert result["rolled_back_status"] == "draft"
        assert result["target_publish_id"] == 2
        assert result["target_version"] == 2
        # target_status 现在由 execute_rollback 返回，表示部署中的状态
        assert result["target_status"] == "online_pub"
        assert result["deploy_status"] == "online_pub"
        assert result["deploy_message"] == "回滚发布已提交"

        # (#197) 验证 rollback_flip 被原子调用一次：current SUCCESS→DRAFT，
        # target UPGRADED→SUCCESS，均在同一事务内。
        assert mock_repo.rollback_flip.call_count == 1
        flip_kwargs = mock_repo.rollback_flip.call_args.kwargs
        assert flip_kwargs["demoted_publish_id"] == 3
        assert flip_kwargs["demoted_from_status"] == PublishStatus.SUCCESS.value
        assert flip_kwargs["demoted_to_status"] == PublishStatus.DRAFT.value
        assert "rollback" in flip_kwargs["demoted_ext"]
        assert flip_kwargs["restored_publish_id"] == 2
        assert flip_kwargs["restored_from_status"] == PublishStatus.UPGRADED.value
        assert flip_kwargs["restored_to_status"] == PublishStatus.SUCCESS.value
        assert flip_kwargs["restored_ext"]["rollback_restored_from"] == 3

        # 验证 execute_rollback 被调用
        mock_flow_service.execute_rollback.assert_awaited_once_with(
            current_publish_id=3,
            target_publish_id=2,
            operator="user_001",
        )

    @pytest.mark.asyncio
    async def test_rollback_publish_cannot_rollback(self):
        """不能回滚时抛出 BotPublishServiceError。"""
        mock_repo = Mock()
        # 当前版本状态不是 SUCCESS
        current_record = _create_mock_record(
            record_id=1,
            status=PublishStatus.DRAFT,
        )
        mock_repo.get_by_id.return_value = current_record

        service = _make_service(bot_publish_repo=mock_repo)

        with pytest.raises(BotPublishServiceError, match="无法回滚"):
            await service.rollback_publish(publish_id=1)

        # 不应该调用 update_status_with_ext
        mock_repo.update_status_with_ext.assert_not_called()

    @pytest.mark.asyncio
    async def test_rollback_publish_current_not_found(self):
        """当前发布单不存在时抛出 PublishNotFoundError（在 can_rollback 之后）。"""
        mock_repo = Mock()
        # can_rollback 通过，但第二次 get_by_id 返回 None
        current_record = _create_mock_record(
            record_id=3,
            status=PublishStatus.SUCCESS,
            last_pub_id=2,
        )
        target_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.UPGRADED,
            ext={"migration_path": "/tmp/build"},
        )

        # can_rollback 调用两次 get_by_id
        mock_repo.get_by_id.side_effect = [current_record, target_record, None]
        mock_repo.get_by_last_pub_id.return_value = None

        service = _make_service(bot_publish_repo=mock_repo)

        with pytest.raises(PublishNotFoundError, match="Publish record not found"):
            await service.rollback_publish(publish_id=3)

    @pytest.mark.asyncio
    async def test_rollback_publish_target_not_found(self):
        """目标发布单不存在时抛出 PublishNotFoundError。"""
        mock_repo = Mock()
        # can_rollback 通过，但 rollback_publish 中的 get_by_id 返回 None
        current_record = _create_mock_record(
            record_id=3,
            status=PublishStatus.SUCCESS,
            last_pub_id=2,
        )
        target_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.UPGRADED,
            ext={"migration_path": "/tmp/build"},
        )

        # can_rollback: get_by_id 返回 current_record 和 target_record
        # rollback_publish: get_by_id 返回 current_record，但 target 返回 None
        mock_repo.get_by_id.side_effect = [current_record, target_record, current_record, None]
        mock_repo.get_by_last_pub_id.return_value = None

        service = _make_service(bot_publish_repo=mock_repo)

        with pytest.raises(PublishNotFoundError, match="Target publish record not found"):
            await service.rollback_publish(publish_id=3)

    @pytest.mark.asyncio
    async def test_rollback_publish_preserves_existing_ext(self):
        """回滚时保留已有的 ext 字段。"""
        mock_repo = Mock()
        # 当前版本已有 ext
        current_record = _create_mock_record(
            record_id=3,
            status=PublishStatus.SUCCESS,
            version=3,
            last_pub_id=2,
            ext={"existing_key": "existing_value"},
        )
        target_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.UPGRADED,
            version=2,
            ext={"migration_path": "/tmp/build", "target_key": "target_value"},
        )

        mock_repo.get_by_id.side_effect = [current_record, target_record, current_record, target_record]
        mock_repo.get_by_last_pub_id.return_value = None
        mock_repo.rollback_flip.return_value = (True, True)

        # Mock execute_rollback
        mock_flow_service = MagicMock()
        mock_flow_service.execute_rollback = AsyncMock(return_value=MagicMock(
            status=PublishStatus.ONLINE_PUB,
            message="回滚发布已提交",
        ))
        service = _make_service(
            bot_publish_repo=mock_repo,
            publish_flow_service_provider=lambda: mock_flow_service,
        )

        result = await service.rollback_publish(publish_id=3)

        assert result is not None

        # (#197) 验证原子翻转保留了两条记录已有的 ext 字段
        flip_kwargs = mock_repo.rollback_flip.call_args.kwargs
        current_ext = flip_kwargs["demoted_ext"]
        assert current_ext["existing_key"] == "existing_value"
        assert "rollback" in current_ext

        target_ext = flip_kwargs["restored_ext"]
        assert target_ext["target_key"] == "target_value"
        assert target_ext["migration_path"] == "/tmp/build"
        assert target_ext["rollback_restored_from"] == 3

    @pytest.mark.asyncio
    async def test_rollback_publish_clears_online_release_refs(self):
        """回滚后当前版本回到 DRAFT，应清除其线上发布/绑定引用。

        否则再次发布时 is_current_online_deployment() 会因残留的 ext.publish.online
        判定为已发布，从而跳过 execute_release_phase（不再执行 upgrade 把共享的线上
        bot/binding 指向本版本）。清除后，重新发布才会真正重新执行 upgrade。
        """
        mock_repo = Mock()
        current_record = _create_mock_record(
            record_id=3,
            status=PublishStatus.SUCCESS,
            version=3,
            last_pub_id=2,
            ext={
                "existing_key": "existing_value",
                # 本版本上线时写入的引用（应被清除）
                "publish": {"online": 9001, "verify": 8001},
                "binding": {"online": 501, "verify": 401},
            },
        )
        target_record = _create_mock_record(
            record_id=2,
            status=PublishStatus.UPGRADED,
            version=2,
            ext={"migration_path": "/tmp/build"},
        )

        mock_repo.get_by_id.side_effect = [current_record, target_record, current_record, target_record]
        mock_repo.get_by_last_pub_id.return_value = None
        mock_repo.rollback_flip.return_value = (True, True)

        mock_flow_service = MagicMock()
        mock_flow_service.execute_rollback = AsyncMock(return_value=MagicMock(
            status=PublishStatus.ONLINE_PUB,
            message="回滚发布已提交",
        ))
        service = _make_service(
            bot_publish_repo=mock_repo,
            publish_flow_service_provider=lambda: mock_flow_service,
        )

        await service.rollback_publish(publish_id=3, operator="user_001")

        first_ext = mock_repo.rollback_flip.call_args.kwargs["demoted_ext"]

        # 线上发布/绑定引用被清除（一并清除，保持一致）
        assert first_ext.get("publish", {}).get("online") is None
        assert first_ext.get("binding", {}).get("online") is None
        # verify 阶段引用及其他字段保留
        assert first_ext["publish"]["verify"] == 8001
        assert first_ext["binding"]["verify"] == 401
        assert first_ext["existing_key"] == "existing_value"
        assert "rollback" in first_ext


class TestGetBotStageBindingInfo:
    def test_personal_bot_success(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "personal",
            "binding_id": 501,
            "active_engine": "openclaw",
            "template_type": "standard",
        }
        device_binding_repo = Mock()
        device_binding_repo.get_by_id.return_value = Mock(
            device_id="IGNORED-FOR-PERSONAL",
            device_provider="arca",
            device_props={"sandbox_id": "BOT-UUID-PERSONAL"},
        )
        template_service = Mock()
        template_service.get_template_config.return_value = {
            "template_runtime_engine_type": " claude_code "
        }
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            template_service=template_service,
            device_binding_repo=device_binding_repo,
        )

        result = service.get_bot_stage_binding_info("bot_001", "user_001", "online")

        assert result == {
            "bot_id": "bot_001",
            "owner_id": "user_001",
            "bot_type": "personal",
            "engine_type": "openclaw",
            "template_type": "standard",
            "template_runtime_engine_type": "claude_code",
            "publish_id": None,
            "publish_status": None,
            "binding_id": 501,
            "device_provider": "arca",
            "device_id": "BOT-UUID-PERSONAL",
        }

    @pytest.mark.parametrize(
        ("template_config", "expected"),
        [
            ({}, ""),
            ({"template_runtime_engine_type": None}, ""),
            ({"template_runtime_engine_type": ""}, ""),
            ({"template_runtime_engine_type": "   "}, ""),
            ({"template_runtime_engine_type": 123}, ""),
            ({"runtime": "aicoding"}, ""),
            (None, ""),
        ],
    )
    def test_personal_bot_empty_or_invalid_template_runtime_engine_type(
        self, template_config, expected
    ):
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "personal",
            "binding_id": 502,
            "active_engine": "claude_code",
            "template_type": "normalCC",
        }
        device_binding_repo = Mock()
        device_binding_repo.get_by_id.return_value = Mock(
            device_id="IGNORED-FOR-PERSONAL",
            device_provider="arca",
            device_props={"sandbox_id": "BOT-UUID-PERSONAL"},
        )
        template_service = Mock()
        template_service.get_template_config.return_value = template_config
        service = _make_service(
            bot_publish_repo=Mock(),
            bot_repo=bot_repo,
            template_service=template_service,
            device_binding_repo=device_binding_repo,
        )

        result = service.get_bot_stage_binding_info(
            "bot_001", "user_001", "online"
        )

        assert result["template_runtime_engine_type"] == expected
        template_service.get_template_config.assert_called_once_with("bot_001")

    def test_service_bot_draft_uses_personal_binding_info(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "service",
            "binding_id": 503,
            "active_engine": "teclaw",
            "template_type": "advanced",
        }
        device_binding_repo = Mock()
        device_binding_repo.get_by_id.return_value = Mock(
            device_id="IGNORED-FOR-DRAFT",
            device_provider="arca",
            device_props={"sandbox_id": "BOT-UUID-DRAFT"},
        )
        template_service = Mock()
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            template_service=template_service,
            device_binding_repo=device_binding_repo,
        )

        result = service.get_bot_stage_binding_info("bot_001", "user_001", PublishStage.DRAFT.value)

        assert result == {
            "bot_id": "bot_001",
            "owner_id": "user_001",
            "bot_type": "service",
            "engine_type": "teclaw",
            "template_type": "advanced",
            "publish_id": None,
            "publish_status": None,
            "binding_id": 503,
            "device_provider": "arca",
            "device_id": "BOT-UUID-DRAFT",
        }
        template_service.get_template_config.assert_not_called()

    def test_service_bot_verify_success(self):
        mock_repo = Mock()
        mock_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = _create_mock_record(
            record_id=12,
            status=PublishStatus.VALIDATING,
            ext={"binding": {"verify": 601}},
        )
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "service",
            "active_engine": "teclaw",
            "template_type": "custom",
        }
        device_binding_repo = Mock()
        device_binding_repo.get_by_id.return_value = Mock(
            device_id="BOT-UUID-VERIFY",
            device_provider="teclaw",
        )
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            device_binding_repo=device_binding_repo,
        )

        result = service.get_bot_stage_binding_info("bot_001", "user_001", "verify")

        assert result == {
            "bot_id": "bot_001",
            "owner_id": "user_001",
            "bot_type": "service",
            "engine_type": "teclaw",
            "template_type": "custom",
            "publish_id": 12,
            "publish_status": PublishStatus.VALIDATING,
            "binding_id": 601,
            "device_provider": "teclaw",
            "device_id": "BOT-UUID-VERIFY",
        }

    def test_service_bot_eval_success(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "service",
            "active_engine": "teclaw",
            "template_type": "eval-type",
        }
        quality_task_service = Mock()
        quality_task_service.get_task_by_uuid.return_value = Mock(
            ext={"bot_uuid": "BOT-UUID-EVAL"}
        )
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            quality_task_service=quality_task_service,
        )

        result = service.get_bot_stage_binding_info("bot_001", "user_001", PublishStage.EVAL.value)

        assert result == {
            "bot_id": "bot_001",
            "owner_id": "user_001",
            "bot_type": "service",
            "engine_type": "teclaw",
            "template_type": "eval-type",
            "publish_id": None,
            "publish_status": None,
            "binding_id": None,
            "device_provider": "baas",
            "device_id": "BOT-UUID-EVAL",
        }
        quality_task_service.get_task_by_uuid.assert_called_once_with("eval")

    def test_service_bot_eval_task_not_found(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "service",
            "active_engine": "teclaw",
        }
        quality_task_service = Mock()
        quality_task_service.get_task_by_uuid.return_value = None
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            quality_task_service=quality_task_service,
        )

        with pytest.raises(BotPublishServiceError, match="Quality task not found for eval stage"):
            service.get_bot_stage_binding_info("bot_001", "user_001", PublishStage.EVAL.value)

    def test_service_bot_eval_task_missing_bot_uuid(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "service",
            "active_engine": "teclaw",
        }
        quality_task_service = Mock()
        quality_task_service.get_task_by_uuid.return_value = Mock(ext={})
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            quality_task_service=quality_task_service,
        )

        with pytest.raises(BotPublishServiceError, match="Quality task missing bot_uuid in ext"):
            service.get_bot_stage_binding_info("bot_001", "user_001", PublishStage.EVAL.value)

    def test_service_bot_online_success(self):
        mock_repo = Mock()
        mock_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = _create_mock_record(
            record_id=13,
            status=PublishStatus.SUCCESS,
            ext={"binding": {"online": 602}},
        )
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "service",
            "active_engine": "teclaw",
            "template_type": "online-template",
        }
        device_binding_repo = Mock()
        device_binding_repo.get_by_id.return_value = Mock(
            device_id="BOT-UUID-ONLINE",
            device_provider="teclaw",
        )
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            device_binding_repo=device_binding_repo,
        )

        result = service.get_bot_stage_binding_info("bot_001", "user_001", "online")

        assert result == {
            "bot_id": "bot_001",
            "owner_id": "user_001",
            "bot_type": "service",
            "engine_type": "teclaw",
            "template_type": "online-template",
            "publish_id": 13,
            "publish_status": PublishStatus.SUCCESS,
            "binding_id": 602,
            "device_provider": "teclaw",
            "device_id": "BOT-UUID-ONLINE",
        }

    def test_bot_not_found(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = None
        service = _make_service(bot_publish_repo=mock_repo, bot_repo=bot_repo)

        with pytest.raises(BotNotFoundError, match="Bot not found: bot_id=bot_001, owner_id=user_001"):
            service.get_bot_stage_binding_info("bot_001", "user_001", "online")

    def test_personal_bot_missing_binding_id(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "personal",
            "binding_id": None,
            "active_engine": "openclaw",
        }
        service = _make_service(bot_publish_repo=mock_repo, bot_repo=bot_repo)

        with pytest.raises(BotPublishServiceError, match="Personal bot missing binding_id"):
            service.get_bot_stage_binding_info("bot_001", "user_001", "online")

    def test_service_bot_verify_publish_not_found(self):
        mock_repo = Mock()
        mock_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = None
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "service",
            "active_engine": "teclaw",
        }
        service = _make_service(bot_publish_repo=mock_repo, bot_repo=bot_repo)

        with pytest.raises(BotPublishServiceError, match="No validating publish found for service bot"):
            service.get_bot_stage_binding_info("bot_001", "user_001", "verify")

    def test_service_bot_online_publish_not_found(self):
        mock_repo = Mock()
        mock_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = None
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "service",
            "active_engine": "teclaw",
        }
        service = _make_service(bot_publish_repo=mock_repo, bot_repo=bot_repo)

        with pytest.raises(BotPublishServiceError, match="No success publish found for service bot"):
            service.get_bot_stage_binding_info("bot_001", "user_001", "online")

    def test_service_bot_missing_stage_binding(self):
        mock_repo = Mock()
        mock_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = _create_mock_record(
            record_id=16,
            status=PublishStatus.SUCCESS,
            ext={},
        )
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "service",
            "active_engine": "teclaw",
        }
        service = _make_service(bot_publish_repo=mock_repo, bot_repo=bot_repo)

        with pytest.raises(BotPublishServiceError, match="Service bot missing online stage binding: publish_id=16"):
            service.get_bot_stage_binding_info("bot_001", "user_001", "online")

    def test_binding_not_found(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "personal",
            "binding_id": 701,
            "active_engine": "openclaw",
        }
        device_binding_repo = Mock()
        device_binding_repo.get_by_id.return_value = None
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            device_binding_repo=device_binding_repo,
        )

        with pytest.raises(BotPublishServiceError, match="Binding record not found: binding_id=701"):
            service.get_bot_stage_binding_info("bot_001", "user_001", "online")

    def test_personal_bot_baas_binding_missing_sandbox_id_uses_binding_device_id(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "personal",
            "binding_id": 702,
            "active_engine": "openclaw",
            "template_type": "baas-template",
        }
        device_binding_repo = Mock()
        device_binding_repo.get_by_id.return_value = Mock(
            device_id="baas-device-id",
            device_provider="baas",
            device_props={},
        )
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            device_binding_repo=device_binding_repo,
        )

        result = service.get_bot_stage_binding_info("bot_001", "user_001", "online")

        assert result["device_provider"] == "baas"
        assert result["device_id"] == "baas-device-id"
        assert result["template_type"] == "baas-template"

    def test_binding_missing_device_id(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "personal",
            "binding_id": 702,
            "active_engine": "openclaw",
        }
        device_binding_repo = Mock()
        device_binding_repo.get_by_id.return_value = Mock(
            device_id="legacy-device-id",
            device_provider="arca",
            device_props={},
        )
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            device_binding_repo=device_binding_repo,
        )

        with pytest.raises(BotPublishServiceError, match="Binding record missing sandbox_id in device_props: binding_id=702"):
            service.get_bot_stage_binding_info("bot_001", "user_001", "online")

    def test_service_bot_eval_prefixed_stage_normalized_then_unsupported(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "service",
            "active_engine": "teclaw",
        }
        quality_task_service = Mock()
        quality_task_service.get_task_by_uuid.return_value = None
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            quality_task_service=quality_task_service,
        )

        with pytest.raises(BotPublishServiceError, match="Quality task not found for eval stage"):
            service.get_bot_stage_binding_info("bot_001", "user_001", "eval-biz-001")

    def test_service_bot_unsupported_stage(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "service",
            "active_engine": "teclaw",
        }
        quality_task_service = Mock()
        quality_task_service.get_task_by_uuid.return_value = None
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            quality_task_service=quality_task_service,
        )

        with pytest.raises(BotPublishServiceError, match="Quality task not found for eval stage"):
            service.get_bot_stage_binding_info("bot_001", "user_001", "eval")

    def test_service_bot_verify_binding_not_found(self):
        mock_repo = Mock()
        mock_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = _create_mock_record(
            record_id=17,
            status=PublishStatus.VALIDATING,
            ext={"binding": {"verify": 801}},
        )
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "service",
            "active_engine": "teclaw",
        }
        device_binding_repo = Mock()
        device_binding_repo.get_by_id.return_value = None
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            device_binding_repo=device_binding_repo,
        )

        with pytest.raises(BotPublishServiceError, match="Binding record not found: binding_id=801"):
            service.get_bot_stage_binding_info("bot_001", "user_001", "verify")

    def test_service_bot_online_binding_missing_device_id(self):
        mock_repo = Mock()
        mock_repo.get_latest_by_source_bot_id_and_owner_and_status.return_value = _create_mock_record(
            record_id=18,
            status=PublishStatus.SUCCESS,
            ext={"binding": {"online": 802}},
        )
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "service",
            "active_engine": "teclaw",
        }
        device_binding_repo = Mock()
        device_binding_repo.get_by_id.return_value = Mock(
            device_id="",
            device_provider="teclaw",
        )
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            device_binding_repo=device_binding_repo,
        )

        with pytest.raises(BotPublishServiceError, match="Binding record missing device_id: binding_id=802"):
            service.get_bot_stage_binding_info("bot_001", "user_001", "online")

    def test_non_service_bot_draft_uses_personal_binding_info(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "weird",
            "binding_id": 902,
            "active_engine": "openclaw",
            "template_type": "weird-template",
        }
        device_binding_repo = Mock()
        device_binding_repo.get_by_id.return_value = Mock(
            device_id="IGNORED-FOR-NON-SERVICE-DRAFT",
            device_provider="arca",
            device_props={"sandbox_id": "BOT-UUID-NON-SERVICE-DRAFT"},
        )
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            device_binding_repo=device_binding_repo,
        )
        result = service.get_bot_stage_binding_info("bot_001", "user_001", PublishStage.DRAFT.value)
        assert result == {
            "bot_id": "bot_001",
            "owner_id": "user_001",
            "bot_type": "weird",
            "engine_type": "openclaw",
            "template_type": "weird-template",
            "publish_id": None,
            "publish_status": None,
            "binding_id": 902,
            "device_provider": "arca",
            "device_id": "BOT-UUID-NON-SERVICE-DRAFT",
        }
    def test_non_service_bot_uses_personal_binding_info(self):
        mock_repo = Mock()
        bot_repo = Mock()
        bot_repo.get_by_id_and_owner.return_value = {
            "bot_id": "bot_001",
            "bot_type": "weird",
            "binding_id": 901,
            "active_engine": "openclaw",
            "template_type": "non-service-template",
        }
        device_binding_repo = Mock()
        device_binding_repo.get_by_id.return_value = Mock(
            device_id="IGNORED-FOR-NON-SERVICE",
            device_provider="arca",
            device_props={"sandbox_id": "BOT-UUID-NON-SERVICE"},
        )
        service = _make_service(
            bot_publish_repo=mock_repo,
            bot_repo=bot_repo,
            device_binding_repo=device_binding_repo,
        )
        result = service.get_bot_stage_binding_info("bot_001", "user_001", "online")
        assert result == {
            "bot_id": "bot_001",
            "owner_id": "user_001",
            "bot_type": "weird",
            "engine_type": "openclaw",
            "template_type": "non-service-template",
            "publish_id": None,
            "publish_status": None,
            "binding_id": 901,
            "device_provider": "arca",
            "device_id": "BOT-UUID-NON-SERVICE",
        }


class TestDraftRestore:
    @staticmethod
    def _stateful_repo(draft: BotPublishRecord, source: BotPublishRecord) -> Mock:
        repo = Mock()
        records = {draft.id: draft, source.id: source}
        repo.get_by_id.side_effect = lambda publish_id: records.get(publish_id)

        def update_status_with_ext(
            publish_id, target_status, ext, source_status
        ):
            record = records.get(publish_id)
            if not record or record.status != source_status:
                return None
            record.ext = ext
            return record

        repo.update_status_with_ext.side_effect = update_status_with_ext
        return repo

    @staticmethod
    def _stateful_operation_repo() -> Mock:
        repo = Mock()
        operations: dict[int, PublishOperationRecord] = {}

        def get_latest(publish_id, operation_kind, stage):
            matches = [
                op
                for op in operations.values()
                if op.publish_id == publish_id
                and op.operation_kind == operation_kind
                and op.stage == stage
            ]
            return max(matches, key=lambda op: op.attempt, default=None)

        def insert(data):
            op_id = len(operations) + 1
            op = PublishOperationRecord(
                id=op_id,
                publish_id=data["publish_id"],
                operation_kind=data["operation_kind"],
                stage=data["stage"],
                attempt=data["attempt"],
                state=PublishOperationState.PENDING.value,
                request_id=data["request_id"],
                bot_uuid=data.get("bot_uuid"),
                params=data.get("params"),
                operator=data["operator"],
                env=data["env"],
                gmt_create=datetime.now(),
                gmt_modified=datetime.now(),
            )
            operations[op_id] = op
            return op

        def update_result(op_id, result):
            op = operations.get(op_id)
            if op:
                op.result = result
            return op

        def fail(op_id, error):
            op = operations.get(op_id)
            if not op or op.state in {
                state.value for state in PublishOperationState.terminal()
            }:
                return None
            op.state = PublishOperationState.FAILED.value
            op.last_error = error
            return op

        def complete_without_workflow(op_id):
            op = operations.get(op_id)
            if not op or op.state != PublishOperationState.PENDING.value:
                return None
            op.state = PublishOperationState.COMPLETED.value
            return op

        def complete(op_id):
            op = operations.get(op_id)
            if not op or op.state != PublishOperationState.ID_RECORDED.value:
                return None
            op.state = PublishOperationState.COMPLETED.value
            return op

        repo.get_latest_by_kind.side_effect = get_latest
        repo.max_attempt.side_effect = lambda publish_id, kind, stage: (
            get_latest(publish_id, kind, stage).attempt
            if get_latest(publish_id, kind, stage)
            else 0
        )
        repo.insert.side_effect = insert
        repo.get_by_id.side_effect = operations.get
        repo.update_result.side_effect = update_result
        repo.fail.side_effect = fail
        repo.complete_without_workflow.side_effect = complete_without_workflow
        repo.complete.side_effect = complete
        repo._operations = operations
        return repo

    def test_first_draft_has_no_restore_action(self):
        repo = Mock()
        repo.get_by_id.return_value = _create_mock_record(
            record_id=1, status=PublishStatus.DRAFT, version=1, last_pub_id=0
        )
        service = _make_service(repo)

        can_restore, reason, source = service.can_restore_draft(1)

        assert can_restore is False
        assert "首次创建" in reason
        assert source is None

    @pytest.mark.parametrize(
        ("draft", "source", "expected_reason"),
        [
            (None, None, "发布单不存在"),
            (
                _create_mock_record(record_id=2, status=PublishStatus.SUCCESS),
                None,
                "只有 DRAFT 状态可以恢复草稿",
            ),
            (
                _create_mock_record(
                    record_id=2,
                    status=PublishStatus.DRAFT,
                    version=2,
                    last_pub_id=1,
                ),
                None,
                "上一版本不存在",
            ),
            (
                _create_mock_record(
                    record_id=2,
                    status=PublishStatus.DRAFT,
                    version=2,
                    last_pub_id=1,
                ),
                _create_mock_record(
                    record_id=1,
                    status=PublishStatus.UPGRADED,
                    ext={"migration_path": "/artifact/v1/openclaw"},
                ),
                "上一版本与当前草稿不属于同一个 Bot 或环境",
            ),
        ],
    )
    def test_restore_target_rejects_invalid_record_chain(
        self, draft, source, expected_reason
    ):
        repo = Mock()
        if draft is None:
            repo.get_by_id.return_value = None
        elif source is None:
            repo.get_by_id.side_effect = [draft, None]
        else:
            source.source_bot_pk = draft.source_bot_pk + 1
            repo.get_by_id.side_effect = [draft, source]
        service = _make_service(repo)

        can_restore, reason, restore_source = service.can_restore_draft(2)

        assert can_restore is False
        assert expected_reason in reason
        assert restore_source is None

    def test_draft_uses_immediately_previous_artifact(self):
        repo = Mock()
        draft = _create_mock_record(
            record_id=2, status=PublishStatus.DRAFT, version=2, last_pub_id=1
        )
        source = _create_mock_record(
            record_id=1,
            status=PublishStatus.UPGRADED,
            version=1,
            ext={"migration_path": "/artifact/v1/openclaw"},
        )
        repo.get_by_id.side_effect = [draft, source]
        service = _make_service(repo)

        can_restore, reason, restore_source = service.can_restore_draft(2)

        assert can_restore is True
        assert reason == "可以恢复草稿"
        assert restore_source == {"source_publish_id": 1, "source_version": 1}

    def test_teclaw_draft_uses_config_artifact_without_migration_path(self):
        repo = Mock()
        draft = _create_mock_record(
            record_id=2, status=PublishStatus.DRAFT, version=2, last_pub_id=1
        )
        source = _create_mock_record(
            record_id=1,
            status=PublishStatus.UPGRADED,
            version=1,
            ext={
                "config_artifact": {
                    "schema_version": 4,
                    "engine_type": "teclaw",
                }
            },
        )
        repo.get_by_id.side_effect = [draft, source]
        bot_service = MagicMock()
        bot_service.get_bot.return_value = {"active_engine": "teclaw"}
        service = _make_service(repo, bot_service=bot_service)

        can_restore, reason, restore_source = service.can_restore_draft(2)

        assert can_restore is True
        assert reason == "可以恢复草稿"
        assert restore_source == {"source_publish_id": 1, "source_version": 1}

    def test_teclaw_provider_detection_is_case_insensitive(self):
        repo = Mock()
        draft = _create_mock_record(
            record_id=2, status=PublishStatus.DRAFT, version=2, last_pub_id=1
        )
        source = _create_mock_record(
            record_id=1,
            status=PublishStatus.UPGRADED,
            version=1,
            ext={"config_artifact": {"engine_type": "teclaw"}},
        )
        repo.get_by_id.side_effect = [draft, source]
        bot_service = MagicMock()
        bot_service.get_bot.return_value = {"active_engine": "TeClaw"}
        service = _make_service(repo, bot_service=bot_service)

        can_restore, reason, _ = service.can_restore_draft(2)

        assert can_restore is True
        assert reason == "可以恢复草稿"

    def test_teclaw_draft_rejects_non_teclaw_config_artifact(self):
        repo = Mock()
        draft = _create_mock_record(
            record_id=2, status=PublishStatus.DRAFT, version=2, last_pub_id=1
        )
        source = _create_mock_record(
            record_id=1,
            status=PublishStatus.UPGRADED,
            version=1,
            ext={"config_artifact": {"engine_type": "openclaw"}},
        )
        repo.get_by_id.side_effect = [draft, source]
        bot_service = MagicMock()
        bot_service.get_bot.return_value = {"active_engine": "teclaw"}
        service = _make_service(repo, bot_service=bot_service)

        can_restore, reason, restore_source = service.can_restore_draft(2)

        assert can_restore is False
        assert reason == "上一版本的 config_artifact 不是 teclaw 构造物"
        assert restore_source is None

    def test_teclaw_draft_rejects_missing_config_artifact(self):
        repo = Mock()
        draft = _create_mock_record(
            record_id=2, status=PublishStatus.DRAFT, version=2, last_pub_id=1
        )
        source = _create_mock_record(
            record_id=1,
            status=PublishStatus.UPGRADED,
            version=1,
            ext={"migration_path": "/arca-only"},
        )
        repo.get_by_id.side_effect = [draft, source]
        bot_service = MagicMock()
        bot_service.get_bot.return_value = {"active_engine": "teclaw"}
        service = _make_service(repo, bot_service=bot_service)

        can_restore, reason, restore_source = service.can_restore_draft(2)

        assert can_restore is False
        assert reason == "上一版本没有可用的 config_artifact 构造物"
        assert restore_source is None

    def test_artifact_without_migration_path_is_not_restoreable(self):
        repo = Mock()
        draft = _create_mock_record(
            record_id=2, status=PublishStatus.DRAFT, version=2, last_pub_id=1
        )
        source = _create_mock_record(
            record_id=1,
            status=PublishStatus.UPGRADED,
            version=1,
            ext={"other_artifact": {"schema_version": 4}},
        )
        repo.get_by_id.side_effect = [draft, source]
        service = _make_service(repo)

        can_restore, reason, restore_source = service.can_restore_draft(2)

        assert can_restore is False
        assert reason == "上一版本没有可用的 migration_path 构造物"
        assert restore_source is None

    @pytest.mark.parametrize(
        ("operation_state", "expected_status", "expected_error"),
        [
            (PublishOperationState.PENDING.value, "restoring", None),
            (PublishOperationState.ID_RECORDED.value, "restoring", None),
            (PublishOperationState.COMPLETED.value, "success", None),
            (PublishOperationState.FAILED.value, "failed", "restore failed"),
            (PublishOperationState.ABANDONED.value, "failed", "superseded"),
        ],
    )
    def test_get_draft_restore_status_maps_ledger_state(
        self, operation_state, expected_status, expected_error
    ):
        draft = _create_mock_record(
            record_id=2, status=PublishStatus.DRAFT, version=2, last_pub_id=1
        )
        source = _create_mock_record(
            record_id=1,
            status=PublishStatus.UPGRADED,
            version=1,
            ext={"migration_path": "/artifact/v1/openclaw"},
        )
        repo = self._stateful_repo(draft, source)
        operation_repo = self._stateful_operation_repo()
        operation = operation_repo.insert(
            {
                "publish_id": 2,
                "operation_kind": PublishOperationKind.DRAFT_RESTORE.value,
                "stage": PublishStage.DRAFT.value,
                "attempt": 1,
                "request_id": "pub_2_draft_restore_draft_a1",
                "operator": "u1",
                "params": {
                    "source_publish_id": 1,
                    "source_version": 1,
                },
                "env": "dev",
            }
        )
        operation.state = operation_state
        operation.baas_publish_id = 8801
        operation.result = {
            "baas_status": "SUCCESS",
            "restore_type": "config_artifact",
            "draft_binding_id": 802,
        }
        operation.last_error = expected_error
        service = _make_service(
            repo, publish_operation_repo=operation_repo
        )

        result = service.get_draft_restore_status(2, operation.id)

        assert result["draft_publish_id"] == 2
        assert result["operation_id"] == operation.id
        assert result["task_id"] == "pub_2_draft_restore_draft_a1"
        assert result["status"] == expected_status
        assert result["operation_state"] == operation_state
        assert result["source_publish_id"] == 1
        assert result["source_version"] == 1
        assert result["baas_publish_id"] == 8801
        assert result["baas_status"] == "SUCCESS"
        assert result["error"] == expected_error
        assert (result["completed_at"] is not None) == (
            operation_state in {
                state.value for state in PublishOperationState.terminal()
            }
        )

    def test_get_draft_restore_status_hides_mismatched_operation(self):
        draft = _create_mock_record(
            record_id=2, status=PublishStatus.DRAFT, version=2, last_pub_id=1
        )
        source = _create_mock_record(
            record_id=1, status=PublishStatus.UPGRADED, version=1
        )
        repo = self._stateful_repo(draft, source)
        operation_repo = self._stateful_operation_repo()
        operation = operation_repo.insert(
            {
                "publish_id": 999,
                "operation_kind": PublishOperationKind.DRAFT_RESTORE.value,
                "stage": PublishStage.DRAFT.value,
                "attempt": 1,
                "request_id": "other-operation",
                "operator": "u2",
                "env": "dev",
            }
        )
        service = _make_service(
            repo, publish_operation_repo=operation_repo
        )

        with pytest.raises(PublishNotFoundError, match="草稿恢复操作不存在"):
            service.get_draft_restore_status(2, operation.id)

    @pytest.mark.parametrize(
        "operation_state",
        [
            PublishOperationState.PENDING.value,
            PublishOperationState.ID_RECORDED.value,
        ],
    )
    def test_get_draft_restore_status_converges_expired_operation(
        self, operation_state
    ):
        draft = _create_mock_record(
            record_id=2, status=PublishStatus.DRAFT, version=2, last_pub_id=1
        )
        source = _create_mock_record(
            record_id=1,
            status=PublishStatus.UPGRADED,
            version=1,
            ext={"migration_path": "/artifact/v1/openclaw"},
        )
        repo = self._stateful_repo(draft, source)
        operation_repo = self._stateful_operation_repo()
        operation = operation_repo.insert(
            {
                "publish_id": 2,
                "operation_kind": PublishOperationKind.DRAFT_RESTORE.value,
                "stage": PublishStage.DRAFT.value,
                "attempt": 1,
                "request_id": "pub_2_draft_restore_draft_a1",
                "operator": "u1",
                "params": {
                    "source_publish_id": 1,
                    "source_version": 1,
                    "deadline_at": (
                        datetime.now() - timedelta(minutes=1)
                    ).isoformat(),
                },
                "env": "dev",
            }
        )
        operation.state = operation_state
        if operation_state == PublishOperationState.ID_RECORDED.value:
            operation.baas_publish_id = 8801
        service = _make_service(repo, publish_operation_repo=operation_repo)

        result = service.get_draft_restore_status(2, operation.id)

        assert result["status"] == "failed"
        assert result["operation_state"] == PublishOperationState.FAILED.value
        assert result["error"] == "恢复草稿超时（默认限制 30 分钟）"
        assert result["completed_at"] is not None
        assert operation_repo.get_by_id(operation.id).state == (
            PublishOperationState.FAILED.value
        )

    def test_can_restore_draft_expires_stale_operation_and_unblocks_retry(self):
        draft = _create_mock_record(
            record_id=2, status=PublishStatus.DRAFT, version=2, last_pub_id=1
        )
        source = _create_mock_record(
            record_id=1,
            status=PublishStatus.UPGRADED,
            version=1,
            ext={"migration_path": "/artifact/v1/openclaw"},
        )
        repo = self._stateful_repo(draft, source)
        operation_repo = self._stateful_operation_repo()
        operation = operation_repo.insert(
            {
                "publish_id": 2,
                "operation_kind": PublishOperationKind.DRAFT_RESTORE.value,
                "stage": PublishStage.DRAFT.value,
                "attempt": 1,
                "request_id": "pub_2_draft_restore_draft_a1",
                "operator": "u1",
                "params": {
                    "source_publish_id": 1,
                    "source_version": 1,
                    "deadline_at": (
                        datetime.now() - timedelta(minutes=1)
                    ).isoformat(),
                },
                "env": "dev",
            }
        )
        bot_service = MagicMock()
        bot_service.get_bot.return_value = {"active_engine": "openclaw"}
        service = _make_service(
            repo,
            bot_service=bot_service,
            publish_operation_repo=operation_repo,
        )

        can_restore, reason, source_info = service.can_restore_draft(2)

        assert can_restore is True
        assert reason == "可以恢复草稿"
        assert source_info == {"source_publish_id": 1, "source_version": 1}
        expired = operation_repo.get_by_id(operation.id)
        assert expired.state == PublishOperationState.FAILED.value
        assert expired.last_error == "恢复草稿超时（默认限制 30 分钟）"

    @pytest.mark.asyncio
    async def test_restore_draft_returns_immediately_and_enqueues_durable_task(self):
        draft = _create_mock_record(
            record_id=2,
            status=PublishStatus.DRAFT,
            version=2,
            last_pub_id=1,
            ext={"existing": True},
        )
        source = _create_mock_record(
            record_id=1,
            status=PublishStatus.UPGRADED,
            version=1,
            ext={"migration_path": "/artifact/v1/openclaw"},
        )
        repo = self._stateful_repo(draft, source)
        operation_repo = self._stateful_operation_repo()
        task_queue = Mock()
        bot_service = Mock()
        bot_service.get_bot.return_value = {"binding_id": 802}
        binding_repo = Mock()
        binding_repo.get_by_id.return_value = SimpleNamespace(
            id=802, device_id="BOT-current-draft"
        )
        service = _make_service(
            repo,
            bot_service=bot_service,
            device_binding_repo=binding_repo,
            publish_operation_repo=operation_repo,
            task_queue_service=task_queue,
        )

        result = await service.restore_draft(2, operator="u1")

        assert result["status"] == "restoring"
        assert result["operation_id"] == 1
        assert result["task_id"].startswith("pub_2_draft_restore_draft_a1")
        assert draft.status == PublishStatus.DRAFT
        assert draft.ext == {"existing": True}
        op = operation_repo.get_by_id(1)
        assert op.state == PublishOperationState.PENDING.value
        assert op.bot_uuid == "BOT-current-draft"
        assert op.params["source_publish_id"] == 1
        assert op.params["source_version"] == 1
        assert datetime.fromisoformat(op.params["deadline_at"]) > datetime.now()
        task_queue.enqueue.assert_called_once_with(
            "service_bot.publish.draft_restore",
            {
                "draft_publish_id": 2,
                "operation_id": 1,
                "operator": "u1",
            },
            deadline_seconds=1860,
        )

    @pytest.mark.asyncio
    async def test_restore_draft_enqueue_failure_marks_operation_failed_and_can_retry(self):
        draft = _create_mock_record(
            record_id=2,
            status=PublishStatus.DRAFT,
            version=2,
            last_pub_id=1,
        )
        source = _create_mock_record(
            record_id=1,
            status=PublishStatus.UPGRADED,
            version=1,
            ext={"migration_path": "/artifact/v1/openclaw"},
        )
        repo = self._stateful_repo(draft, source)
        operation_repo = self._stateful_operation_repo()
        task_queue = Mock()
        task_queue.enqueue.side_effect = RuntimeError("queue unavailable")
        bot_service = Mock()
        bot_service.get_bot.return_value = {"binding_id": 802}
        binding_repo = Mock()
        binding_repo.get_by_id.return_value = SimpleNamespace(
            id=802, device_id="BOT-current-draft"
        )
        service = _make_service(
            repo,
            bot_service=bot_service,
            device_binding_repo=binding_repo,
            publish_operation_repo=operation_repo,
            task_queue_service=task_queue,
        )

        with pytest.raises(BotPublishServiceError, match="恢复任务入队失败"):
            await service.restore_draft(2, operator="u1")

        assert draft.status == PublishStatus.DRAFT
        first_op = operation_repo.get_by_id(1)
        assert first_op.state == PublishOperationState.FAILED.value
        assert first_op.last_error == "持久化恢复任务入队失败: queue unavailable"

        can_restore, reason, restore_source = service.can_restore_draft(2)
        assert can_restore is True
        assert reason == "可以恢复草稿"
        assert restore_source == {"source_publish_id": 1, "source_version": 1}

    @pytest.mark.asyncio
    async def test_restore_draft_rejects_duplicate_while_restoring(self):
        draft = _create_mock_record(
            record_id=2,
            status=PublishStatus.DRAFT,
            version=2,
            last_pub_id=1,
        )
        repo = Mock()
        repo.get_by_id.return_value = draft
        operation_repo = self._stateful_operation_repo()
        operation_repo.insert(
            {
                "publish_id": 2,
                "operation_kind": "draft_restore",
                "stage": "draft",
                "attempt": 1,
                "request_id": "draft_restore_existing",
                "operator": "u1",
                "env": "dev",
            }
        )
        service = _make_service(repo, publish_operation_repo=operation_repo)

        with pytest.raises(BotPublishServiceError, match="正在恢复中"):
            await service.restore_draft(2, operator="u1")

        assert len(operation_repo._operations) == 1
