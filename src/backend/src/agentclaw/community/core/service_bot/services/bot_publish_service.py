"""Bot Publish Service - Bot发布服务业务逻辑层。"""
import threading
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishStatus
from agentclaw.community.core.service_bot.repository.bot_publish_repository import BotPublishRepositoryProtocol
from agentclaw.community.core.service_bot.repository.publish_operation_repository import (
    PublishOperationRepository,
)
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.service_bot.services.publish_draft_restore_mixin import PublishDraftRestoreMixin
from agentclaw.community.core.service_bot.services.publish_exceptions import (
    BotAlreadyServiceTypeError,
    BotNotFoundError,
    BotNotServiceTypeError,
    BotPublishServiceError,
    BotTypeNotSupportedError,
    PublishAlreadyExistsError,
    PublishNotFoundError,
    PublishStatusInvalidError,
)
from agentclaw.community.core.service_bot.services.publish_rollback_mixin import PublishRollbackMixin
from agentclaw.community.core.service_bot.services.arka_image_pin import (
    copy_image_pin_to_ext,
)
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.core.service_bot.types import PublishStage
from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowService
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.core.bot_management.services.bcn_service import BcnService
    from agentclaw.community.core.quality.services.quality_task_service import QualityTaskService

logger = get_logger()


class BotPublishService(PublishDraftRestoreMixin, PublishRollbackMixin):
    """Bot发布服务 - 管理Bot发布生命周期。"""

    def __init__(
        self,
        bot_publish_repo: BotPublishRepositoryProtocol,
        bot_repo: BotRepository,
        publish_flow_service_provider: Callable[[], "PublishFlowService"],
        bot_service: "BotService",
        device_binding_repo: DeviceBindingRepository,
        bcn_service: "BcnService",
        quality_task_service: "QualityTaskService",
        publish_operation_repo: PublishOperationRepository,
        task_queue_service: TaskQueueService,
    ):
        self._repo = bot_publish_repo
        self._bot_repo = bot_repo
        # Lazy provider — breaks the BotPublishService ↔ PublishFlowService cycle.
        self._publish_flow_service_provider = publish_flow_service_provider
        self._bot_service = bot_service
        self._device_binding_repo = device_binding_repo
        self._bcn_service = bcn_service
        self._quality_task_service = quality_task_service
        self._publish_operation_repo = publish_operation_repo
        self._task_queue_service = task_queue_service
        self._env = get_current_env()

    def create_device_binding(
        self,
        entity_id: str,
        entity_type: str,
        device_id: str,
        device_provider: str,
        device_props: Dict[str, Any],
        applied_by: str,
        apply_reason: Optional[str] = None,
        status: str = DeviceBindingStatus.PENDING,
    ) -> int:
        """创建设备绑定记录。

        Args:
            entity_id: 实体ID（用户工号或团队ID）
            entity_type: 实体类型（staff/team/proj）
            device_id: 设备唯一标识
            device_provider: 设备提供商（local/daas/arca）
            device_props: 设备属性字典
            applied_by: 申请人ID
            apply_reason: 申请原因（可选）
            status: 初始状态，默认 PENDING

        Returns:
            新记录的绑定ID
        """
        device_binding_repo = self._device_binding_repo
        binding_id = device_binding_repo.insert_binding(
            entity_id=entity_id,
            entity_type=entity_type,
            device_id=device_id,
            device_provider=device_provider,
            env=self._env,
            device_props=device_props,
            status=status,
            apply_reason=apply_reason,
            applied_by=applied_by,
        )
        logger.info(f"[create_device_binding] binding_id={binding_id}, entity_id={entity_id}, device_id={device_id}, status={status}")
        return binding_id

    def update_device_binding_status(
        self,
        binding_id: int,
        status: str,
    ) -> None:
        """更新设备绑定状态。

        Args:
            binding_id: 绑定记录ID
            status: 新状态（PENDING/ACTIVE/FAILED/RELEASED）
        """
        device_binding_repo = self._device_binding_repo
        device_binding_repo.update_status(binding_id=binding_id, status=status)
        logger.info(f"[update_device_binding_status] binding_id={binding_id}, status={status}")

    def get_device_binding_by_id(
        self,
        binding_id: int,
    ) -> Optional[DeviceBindingRecord]:
        """根据 binding_id 查询设备绑定记录。

        Args:
            binding_id: 绑定记录ID

        Returns:
            DeviceBindingRecord，不存在返回 None
        """
        device_binding_repo = self._device_binding_repo
        return device_binding_repo.get_by_id(binding_id)

    def update_device_binding_with_props(
        self,
        binding_id: int,
        status: str,
        device_props: Dict[str, Any],
    ) -> None:
        """更新设备绑定状态和属性。

        Args:
            binding_id: 绑定记录ID
            status: 新状态（PENDING/ACTIVE/FAILED/RELEASED）
            device_props: 设备属性字典
        """
        device_binding_repo = self._device_binding_repo
        # 先更新状态
        device_binding_repo.update_status_and_alive_at(binding_id=binding_id, status=status)
        # 再更新 device_props（需要通过 reuse_binding 方法）
        device_binding_repo.reuse_binding(
            binding_id=binding_id,
            device_props=device_props,
            apply_reason=None,
            applied_by="system",
            status=status,
        )
        logger.info(f"[update_device_binding_with_props] binding_id={binding_id}, status={status}, device_props updated")

    def create_publish(
        self,
        source_bot_pk: int,
        source_bot_id: str,
        publish_bot_id: str,
        name: str,
        owner_id: str,
        permission_owner: str,
        description: Optional[str] = None,
        owner_name: Optional[str] = None,
        version: Optional[int] = 1,
        last_pub_id: int = 0,
        ext: Optional[Dict[str, Any]] = None,
    ) -> BotPublishRecord:
        """创建发布记录。

        Args:
            source_bot_pk: ac_bots 表主键
            source_bot_id: 源 bot_id
            publish_bot_id: 发布后的 bot_id
            name: bot名称
            owner_id: 工号
            permission_owner: 权限归属
            description: Optional[str] = None,
            owner_name: Optional[str] = None,
            version: Optional[int] = None,
            last_pub_id: int = 0,
            ext: Optional[Dict[str, Any]] = None,

        Returns:
            创建的 BotPublishRecord

        Raises:
            PublishAlreadyExistsError: 如果相同版本已存在
        """
        # 检查是否已存在相同版本
        if version is not None:
            existing = self._repo.get_by_publish_bot_id_and_version(
                publish_bot_id, owner_id, version, self._env
            )
            if existing:
                raise PublishAlreadyExistsError(
                    f"Publish record already exists: {publish_bot_id}, owner={owner_id}, v{version}"
                )

        source_bot = self._bot_repo.get_by_id_and_owner(source_bot_id, owner_id)
        source_bot_ext = (
            source_bot.get("ext") if isinstance(source_bot, dict) else None
        )
        ext = copy_image_pin_to_ext(source_bot_ext, ext)

        record = self._repo.insert({
            "source_bot_pk": source_bot_pk,
            "source_bot_id": source_bot_id,
            "publish_bot_id": publish_bot_id,
            "name": name,
            "description": description,
            "owner_id": owner_id,
            "owner_name": owner_name,
            "status": PublishStatus.DRAFT,
            "version": version,
            "last_pub_id": last_pub_id,
            "env": self._env,
            "ext": ext,
            "permission_owner": permission_owner,
        })
        logger.info(f"[create_publish] id={record.id}, publish_bot_id={publish_bot_id}, owner={owner_id}")
        return record

    def get_publish_by_bot_version(
        self,
        bot_id: str,
        version: int,
        owner_id: str,
    ) -> Optional[BotPublishRecord]:
        """根据 bot_id、version、owner_id 查询发布记录。

        Args:
            bot_id: Bot ID
            version: 版本号
            owner_id: Owner ID

        Returns:
            BotPublishRecord，不存在返回 None
        """
        return self._repo.get_by_publish_bot_id_and_version(
            publish_bot_id=bot_id,
            owner_id=owner_id,
            version=version,
            env=self._env,
        )

    def update_publish_status(
        self,
        publish_id: int,
        target_status: str,
        source_status: str,
    ) -> BotPublishRecord:
        """更新发布记录状态（乐观锁）。

        Args:
            publish_id: 发布记录 ID
            target_status: 目标状态
            source_status: 源状态，只有当前状态等于源状态才能更新成功

        Returns:
            更新后的 BotPublishRecord

        Raises:
            PublishNotFoundError: 发布记录不存在或源状态不匹配
        """
        record = self._repo.update_status(publish_id, target_status, source_status)
        if not record:
            raise PublishNotFoundError(
                f"Publish record not found or source status mismatch: "
                f"publish_id={publish_id}, expected source_status={source_status}"
            )
        logger.info(f"[update_publish_status] id={publish_id}, target_status={target_status}, source_status={source_status}")
        return record

    def update_publish_status_with_ext(
        self,
        publish_id: int,
        target_status: str,
        ext: Dict[str, Any],
        source_status: str,
    ) -> BotPublishRecord:
        """更新发布记录状态和扩展字段（乐观锁）。

        Args:
            publish_id: 发布记录 ID
            target_status: 目标状态
            ext: 扩展字段字典
            source_status: 源状态，只有当前状态等于源状态才能更新成功

        Returns:
            更新后的 BotPublishRecord

        Raises:
            PublishNotFoundError: 发布记录不存在或源状态不匹配
        """
        record = self._repo.update_status_with_ext(publish_id, target_status, ext, source_status)
        if not record:
            raise PublishNotFoundError(
                f"Publish record not found or source status mismatch: "
                f"publish_id={publish_id}, expected source_status={source_status}"
            )
        logger.info(f"[update_publish_status_with_ext] id={publish_id}, target_status={target_status}, source_status={source_status}")
        return record

    def update_publish_ext(
        self,
        publish_id: int,
        ext: Dict[str, Any],
    ) -> BotPublishRecord:
        """仅更新发布记录的扩展字段（不更新状态）。

        使用乐观锁：先读取当前状态，再以该状态作为 source_status 更新。
        如果期间状态被并发修改，更新会失败并抛出异常。

        Args:
            publish_id: 发布记录 ID
            ext: 扩展字段字典

        Returns:
            更新后的 BotPublishRecord

        Raises:
            PublishNotFoundError: 发布记录不存在或状态已被并发修改
        """
        record = self._repo.get_by_id(publish_id)
        if not record:
            raise PublishNotFoundError(f"Publish record not found: {publish_id}")

        current_status = record.status
        updated_record = self._repo.update_status_with_ext(
            publish_id=publish_id,
            target_status=current_status,
            ext=ext,
            source_status=current_status,
        )
        if not updated_record:
            raise PublishNotFoundError(
                f"Publish record not found or status changed concurrently: "
                f"publish_id={publish_id}, expected source_status={current_status}"
            )

        logger.info(f"[update_publish_ext] id={publish_id}, ext updated")
        return updated_record

    def record_draft_artifact(
        self, *, bot_id: str, artifact: Dict[str, Any]
    ) -> bool:
        """Record the delivered artifact onto the DRAFT publish row's
        ``ext.config_artifact`` (build-stage key). No-op (``False``) if no DRAFT
        row. Looks up by ``publish_bot_id`` ONLY — owner filtering would miss org
        bots whose edit-time entity differs from the build owner. Callers wrap."""
        record = self._repo.get_draft_by_publish_bot_id(
            publish_bot_id=bot_id, env=self._env,
        )
        if not record:
            return False
        self.update_publish_ext(
            record.id, {**(record.ext or {}), "config_artifact": artifact}
        )
        return True

    def get_publish_by_id(
        self,
        publish_id: int,
    ) -> Optional[BotPublishRecord]:
        """根据 ID 查询发布记录。

        Args:
            publish_id: 发布记录 ID

        Returns:
            BotPublishRecord，不存在返回 None
        """
        return self._repo.get_by_id(publish_id)

    def get_bot_stage_binding_info(
        self,
        bot_id: str,
        owner_id: str,
        stage: str,
    ) -> Dict[str, Any]:
        """查询 bot 在指定阶段对应的 BaaS/binding 侧信息。"""
        biz_id: Optional[str] = None
        if stage.startswith(f"{PublishStage.EVAL.value}-"):
            _, biz_id = stage.split("-", 1)
            stage = PublishStage.EVAL.value
        elif stage == PublishStage.EVAL.value:
            biz_id = stage

        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: bot_id={bot_id}, owner_id={owner_id}")

        bot_type = bot.get("bot_type", "personal")
        if bot_type == "service" and stage != PublishStage.DRAFT.value:
            return self._get_service_bot_stage_binding_info(
                bot=bot,
                bot_id=bot_id,
                owner_id=owner_id,
                stage=stage,
                task_uuid=biz_id,
            )
        return self._get_personal_bot_binding_info(bot=bot, bot_id=bot_id, owner_id=owner_id)

    def _get_personal_bot_binding_info(
        self,
        *,
        bot: Dict[str, Any],
        bot_id: str,
        owner_id: str,
    ) -> Dict[str, Any]:
        binding_id = bot.get("binding_id")
        if not binding_id:
            raise BotPublishServiceError(
                f"Personal bot missing binding_id: bot_id={bot_id}"
            )
        binding = self.get_device_binding_by_id(binding_id)
        if not binding:
            raise BotPublishServiceError(f"Binding record not found: binding_id={binding_id}")

        device_provider = getattr(binding, "device_provider", "")
        if device_provider == "baas" or device_provider == "teclaw":
            device_id = getattr(binding, "device_id", "")
            if not device_id:
                raise BotPublishServiceError(
                    f"Binding record missing device_id: binding_id={binding_id}"
                )
        else:
            device_props = getattr(binding, "device_props", None) or {}
            device_id = device_props.get("sandbox_id", "")
            if not device_id:
                raise BotPublishServiceError(
                    f"Binding record missing sandbox_id in device_props: binding_id={binding_id}"
                )
        return {
            "bot_id": bot_id,
            "owner_id": owner_id,
            "bot_type": bot.get("bot_type", "personal"),
            "engine_type": bot.get("active_engine", ""),
            "template_type": bot.get("template_type", ""),
            "publish_id": None,
            "publish_status": None,
            "binding_id": binding_id,
            "device_provider": getattr(binding, "device_provider", ""),
            "device_id": device_id,
        }

    def _get_service_publish_record_for_stage(
        self,
        *,
        bot_id: str,
        owner_id: str,
        stage: str,
    ) -> BotPublishRecord:

        if stage == PublishStage.VERIFY.value:
            target_status = PublishStatus.VALIDATING
        elif stage == PublishStage.ONLINE.value:
            target_status = PublishStatus.SUCCESS
        else:
            raise BotPublishServiceError(
                f"Unsupported stage for service bot binding lookup: {stage}"
            )

        publish_record = self._repo.get_latest_by_source_bot_id_and_owner_and_status(
            source_bot_id=bot_id,
            owner_id=owner_id,
            status=target_status,
            env=self._env,
        )
        if not publish_record:
            raise BotPublishServiceError(
                f"No {target_status} publish found for service bot: bot_id={bot_id}, owner_id={owner_id}"
            )
        return publish_record

    def _get_service_bot_stage_binding_info(
        self,
        *,
        bot: Dict[str, Any],
        bot_id: str,
        owner_id: str,
        stage: str,
        task_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        if stage == PublishStage.EVAL.value:
            task_record = self._quality_task_service.get_task_by_uuid(task_uuid)
            if not task_record:
                raise BotPublishServiceError(
                    f"Quality task not found for eval stage: task_uuid={task_uuid}"
                )
            task_ext = task_record.ext or {}
            bot_uuid = task_ext.get("bot_uuid", "")
            if not bot_uuid:
                raise BotPublishServiceError(
                    f"Quality task missing bot_uuid in ext: task_uuid={task_uuid}"
                )
            return {
                "bot_id": bot_id,
                "owner_id": owner_id,
                "bot_type": "service",
                "engine_type": bot.get("active_engine", ""),
                "template_type": bot.get("template_type", ""),
                "publish_id": None,
                "publish_status": None,
                "binding_id": None,
                "device_provider": "baas",
                "device_id": bot_uuid,
            }

        publish_record = self._get_service_publish_record_for_stage(
            bot_id=bot_id,
            owner_id=owner_id,
            stage=stage,
        )
        ext = publish_record.ext or {}
        binding_id = (ext.get("binding") or {}).get(stage)
        if not binding_id:
            raise BotPublishServiceError(
                f"Service bot missing {stage} stage binding: publish_id={publish_record.id}"
            )
        binding = self.get_device_binding_by_id(binding_id)
        if not binding:
            raise BotPublishServiceError(f"Binding record not found: binding_id={binding_id}")
        device_id = getattr(binding, "device_id", "")
        if not device_id:
            raise BotPublishServiceError(
                f"Binding record missing device_id: binding_id={binding_id}"
            )
        return {
            "bot_id": bot_id,
            "owner_id": owner_id,
            "bot_type": "service",
            "engine_type": bot.get("active_engine", ""),
            "template_type": bot.get("template_type", ""),
            "publish_id": publish_record.id,
            "publish_status": publish_record.status,
            "binding_id": binding_id,
            "device_provider": getattr(binding, "device_provider", ""),
            "device_id": device_id,
        }

    def _get_next_version(self, publish_bot_id: str, owner_id: str) -> int:
        """获取下一个版本号（基于最大版本号）。

        查询该 bot 的最新版本记录，返回 version + 1。
        这样可以避免回滚后升级时的版本号冲突。

        Args:
            publish_bot_id: 发布后的 bot_id
            owner_id: 所有者 ID

        Returns:
            下一个版本号
        """
        latest = self._repo.get_by_publish_bot_id(publish_bot_id, owner_id, self._env)
        if latest:
            return (latest.version or 0) + 1
        return 1

    def create_first_publish_for_bot(
        self,
        bot_id: str,
        owner_id: str,
        name: str,
        permission_owner: str = "owner",
        description: Optional[str] = None,
        owner_name: Optional[str] = None,
    ) -> BotPublishRecord:
        """为 bot 创建第一个发布单（发布单已存在时报错）。

        Args:
            bot_id: Bot ID
            owner_id: Owner ID
            name: 发布单名称
            permission_owner: 权限归属
            description: 描述
            owner_name: Owner 花名

        Returns:
            BotPublishRecord

        Raises:
            BotPublishServiceError: BotRepository 未配置
            BotNotFoundError: Bot 不存在
            BotNotServiceTypeError: Bot 不是服务型
            PublishAlreadyExistsError: 发布单已存在
        """
        logger.info(
            f"[BotPublishService.create_first_publish_for_bot] called: bot_id={bot_id}, "
            f"owner_id={owner_id}, permission_owner={permission_owner}"
        )

        # 1. 查询 bot
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: bot_id={bot_id}, owner_id={owner_id}")

        # 2. 检查 bot_type
        bot_type = bot.get("bot_type", "personal")
        if bot_type != "service":
            raise BotNotServiceTypeError(
                f"Only service bot can create publish, got bot_type={bot_type}"
            )

        # 3. 查询是否已存在发布单（已存在则报错）
        existing = self._repo.get_by_publish_bot_id(bot_id, owner_id, self._env)
        if existing:
            raise PublishAlreadyExistsError(
                f"Publish record already exists: bot_id={bot_id}, owner_id={owner_id}"
            )

        # 4. 创建发布单
        return self.create_publish(
            source_bot_pk=bot["id"],
            source_bot_id=bot_id,
            publish_bot_id=bot_id,
            name=name,
            owner_id=owner_id,
            permission_owner=permission_owner,
            description=description,
            owner_name=owner_name,
        )

    def upgrade_publish(
        self,
        publish_id: int,
        owner_id: str,
    ) -> BotPublishRecord:
        """升级发布单，基于成功发布的版本创建新草稿。

        Args:
            publish_id: 原发布单 ID
            owner_id: 操作者 ID（用于权限校验）

        Returns:
            新创建的 BotPublishRecord（状态为 draft）

        Raises:
            PublishNotFoundError: 发布单不存在
            PublishStatusInvalidError: 发布单状态不是 success
            BotPublishServiceError: 权限不足或其他错误

        幂等性保证：
            - 如果步骤 5 成功但步骤 6 失败，重试时会返回已创建的新发布单
            - 通过 last_pub_id 字段关联原发布单，实现幂等查询
        """
        logger.info(
            f"[BotPublishService.upgrade_publish] called: publish_id={publish_id}, owner_id={owner_id}"
        )

        # 1. 查询原发布单
        original = self._repo.get_by_id(publish_id)
        if not original:
            raise PublishNotFoundError(f"Publish record not found: {publish_id}")

        # 2. 状态判断 + 幂等处理
        if original.status == PublishStatus.UPGRADED:
            # 幂等场景1：步骤 5、6 都已完成
            # 查询已创建的新发布单并返回
            existing_new = self._repo.get_by_last_pub_id(publish_id)
            if existing_new:
                logger.info(f"[upgrade_publish] idempotent return: original_id={publish_id} already upgraded to new_id={existing_new.id}")
                return existing_new
            # 异常情况：状态是 upgraded 但找不到新发布单
            raise BotPublishServiceError(
                f"Publish {publish_id} status is upgraded but no new record found"
            )

        if original.status != PublishStatus.SUCCESS:
            # 非 success 状态不允许升级
            raise PublishStatusInvalidError(
                f"Publish status must be 'success', got '{original.status}'"
            )

        # 4. 幂等场景2：状态仍为 success，但步骤 5 可能已完成
        existing_new = self._repo.get_by_last_pub_id(publish_id)
        if existing_new:
            # 步骤 5 已完成，步骤 6 未完成
            # 补偿更新原发布单状态
            # self._repo.update_status(publish_id, PublishStatus.UPGRADED)
            logger.info(f"[upgrade_publish] idempotent return + status fix: original_id={publish_id} -> new_id={existing_new.id}")
            return existing_new

        # 5. 正常流程：创建新发布单
        new_version = self._get_next_version(original.publish_bot_id, original.owner_id)
        # 升级草稿用"当前在跑版本"的 config_artifact 作为起点，与新建 teclaw bot 草稿
        # 期回填 ext.config_artifact 的行为对齐（见 bot_service.create_bot）。只带这
        # 一个 key，不整体继承原 ext：binding/publish 指向上一版本的容器/发布单，
        # 必须由新草稿自己的发布流程重新生成。ARCA 的 ext 没有 config_artifact，
        # 故天然为空（行为不变），无需按 provider 分支。
        prior_artifact = (original.ext or {}).get("config_artifact")
        new_ext = {"config_artifact": prior_artifact} if prior_artifact else None
        new_record = self.create_publish(
            source_bot_pk=original.source_bot_pk,
            source_bot_id=original.source_bot_id,
            publish_bot_id=original.publish_bot_id,
            name=original.name,
            owner_id=original.owner_id,
            permission_owner=original.permission_owner,
            description=original.description,
            owner_name=original.owner_name,
            version=new_version,
            last_pub_id=publish_id,  # 关联原发布单（幂等 key）
            ext=new_ext,  # 仅携带 config_artifact 起点，不整体继承
        )

        logger.info(f"[upgrade_publish] original_id={publish_id} -> new_id={new_record.id}, version={new_version}")
        return new_record

    def can_upgrade_publish(self, publish_id: int) -> bool:
        """检查发布单是否可以升级。

        升级条件：
        1. 发布单状态必须是 success
        2. 根据 last_pub_id 查询升级后的发布单：
           - 不存在记录 → 可以升级
           - 存在且状态为 failed → 可以升级
           - 存在且状态不是 failed → 不能升级

        Args:
            publish_id: 发布单 ID

        Returns:
            是否可以升级
        """
        # 1. 查询发布单
        publish_record = self._repo.get_by_id(publish_id)
        if not publish_record:
            return False

        # 2. 状态必须是 success
        if publish_record.status != PublishStatus.SUCCESS:
            return False

        # 3. 查询以当前发布单为 last_pub_id 的记录
        next_publish = self._repo.get_by_last_pub_id(publish_id)

        # 4. 不存在记录，可以升级
        if not next_publish:
            return True

        # 5. 存在记录，只有 failed 状态可以重新升级
        return next_publish.status == PublishStatus.FAILED

    def can_edit_bot(self, bot_id: str, owner_id: str) -> bool:
        """检查 bot 是否可以编辑。

        编辑条件：
        1. 存在该 bot 的发布单
        2. 发布单状态为草稿（draft）

        Args:
            bot_id: Bot ID
            owner_id: Owner ID

        Returns:
            是否可以编辑
        """
        # 查询该 bot 的发布记录
        publish_record = self._repo.get_by_publish_bot_id(
            publish_bot_id=bot_id,
            owner_id=owner_id,
            env=self._env,
        )
        if not publish_record:
            return False

        # 状态必须是草稿才能编辑
        return publish_record.status == PublishStatus.DRAFT

    def delete_service_bot(self, publish_id: int) -> bool:
        """删除服务 Bot。

        删除条件：
        1. 发布单状态必须是草稿（draft）
        2. 该 bot 没有其他发布成功的发布单

        删除流程：
        1. 查询发布单并检查删除条件
        2. 销毁验证阶段的发布历史（调用 PublishFlowService.destroy_publish_history）
        3. 调用 BotService.delete_bot 删除 bot

        Args:
            publish_id: 发布单 ID

        Returns:
            True 如果删除成功

        Raises:
            BotPublishServiceError: BotService/PublishFlowService 未配置或不满足删除条件
            PublishNotFoundError: 发布单不存在
        """
        # 1. 查询发布单
        publish_record = self._repo.get_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish record not found: {publish_id}")

        # 2. 检查是否可删除
        if not self.can_delete_bot(publish_id):
            raise BotPublishServiceError(
                f"Cannot delete service bot: publish_id={publish_id}, "
                f"status={publish_record.status}, check can_delete_bot conditions"
            )

        bot_id = publish_record.source_bot_id
        owner_id = publish_record.owner_id

        logger.info(f"[delete_service_bot] Starting delete: publish_id={publish_id}, bot_id={bot_id}, owner_id={owner_id}")

        # 3. 销毁验证阶段的发布历史
        # Deps are DI-guaranteed (all required ctor params); resolve the
        # lazy provider (cycle-breaker: BotPublishService <->
        # PublishFlowService) once and reuse for this delete flow.
        publish_flow_service = self._publish_flow_service_provider()
        destroy_result = publish_flow_service.destroy_publish_history(
            publish_id=publish_id,
            stage=PublishStage.VERIFY,
        )
        logger.info(f"[delete_service_bot] Verify stage destroyed: publish_id={publish_id}, result={destroy_result}")

        # 3.1 如果有 last_pub_id，销毁上一个发布单的发布历史
        if publish_record.last_pub_id and publish_record.last_pub_id > 0:
            last_destroy_result = publish_flow_service.destroy_publish_history(
                publish_id=publish_record.last_pub_id,
                stage=PublishStage.VERIFY,
            )
            logger.info(
                f"[delete_service_bot] Last publish destroyed: publish_id={publish_id}, "
                f"last_pub_id={publish_record.last_pub_id}, result={last_destroy_result}"
            )

        # 5. 调用 BotService.delete_bot 删除 bot
        result = self._bot_service.delete_bot(bot_id=bot_id, user_id=owner_id)

        logger.info(
            f"[delete_service_bot] Service bot deleted: publish_id={publish_id}, "
            f"bot_id={bot_id}, owner_id={owner_id}"
        )

        return result

    def can_delete_bot(self, publish_id: int) -> bool:
        """检查发布单是否可以删除。

        删除条件：
        1. 发布单状态必须是草稿（draft）
        2. 该 bot 没有其他发布成功的发布单

        Args:
            publish_id: 发布单 ID

        Returns:
            是否可以删除
        """
        # 1. 查询发布单
        publish_record = self._repo.get_by_id(publish_id)
        if not publish_record:
            return False

        # 2. 状态必须是草稿
        if publish_record.status != PublishStatus.DRAFT:
            return False

        # 3. 查询该 bot 的所有发布记录
        all_publishes = self._repo.list_by_source_bot(
            source_bot_pk=publish_record.source_bot_pk,
            env=self._env,
        )

        # 4. 检查是否有发布成功的记录（排除当前记录）
        for p in all_publishes:
            if p.id != publish_id and p.status == PublishStatus.SUCCESS:
                return False

        return True

    async def offline_publish(
        self,
        publish_id: int,
    ) -> Dict[str, Any]:
        """下线发布单。

        根据发布单状态自动判断阶段并调用 PublishFlowService.destroy_publish_history 销毁发布历史。
        - 状态为 SUCCESS（发布成功）：销毁线上 bot，若无其他非终态发布单则创建新草稿发布单
        - 状态为 VALIDATING（验证中）：使用 VERIFY 阶段，销毁验证环境 bot

        注意：销毁流程会在后台异步执行，此方法不会等待销毁完成。

        Args:
            publish_id: 发布单 ID
        Returns:
            dict: 下线结果，包含:
                - success: 是否成功提交下线请求
                - bot_destroyed: 是否销毁了 bot（异步执行，返回时为 False）
                - new_publish_id: 新创建的草稿发布单 ID（仅 SUCCESS 状态且无其他非终态发布单时）
                - new_publish_version: 新发布单版本号
                - message: 结果消息

        Raises:
            BotPublishServiceError: 状态不支持下线
            PublishNotFoundError: 发布单不存在
        """
        logger.info(f"[BotPublishService.offline_publish] called: publish_id={publish_id}")

        publish_flow_service = self._publish_flow_service_provider()

        # Step 1: 查询发布单
        publish_record = self._repo.get_by_id(publish_id)
        if not publish_record:
            raise PublishNotFoundError(f"Publish order not found: {publish_id}")

        # Step 2: 根据状态判断 stage
        current_status = publish_record.status

        # RELEASED is terminal — the offline already ran. Return an idempotent
        # no-op so a duplicate submit or a durable-task re-run lands cleanly
        # instead of falling through to _resolve_offline_stage and raising.
        # Recovering the narrow flip-then-crash window (record RELEASED but the
        # durable destroy never enqueued) is handled separately, not here.
        if current_status == PublishStatus.RELEASED:
            logger.info(
                f"[offline_publish] Record already RELEASED, no-op: publish_id={publish_id}"
            )
            return {
                "success": True,
                "bot_destroyed": False,
                "new_publish_id": None,
                "new_publish_version": None,
                "message": f"发布单已下线: publish_id={publish_id}",
            }

        stage = self._resolve_offline_stage(current_status, publish_id)

        logger.info(
            f"[offline_publish] Starting offline: publish_id={publish_id}, "
            f"status={current_status}, stage={stage.value}"
        )

        result = {
            "success": True,
            "bot_destroyed": False,
            "new_publish_id": None,
            "new_publish_version": None,
            "message": "",
        }

        # Step 3: 如果是 SUCCESS 状态，检查是否有非终态发布单，有则不创建新发布单，没有则创建新草稿发布单
        if current_status == PublishStatus.SUCCESS:
            self._release_or_redraft_on_offline(publish_record, publish_id, result)

        # Step 4: 如果是 VALIDATING 状态，将发布单置为草稿 (#197 CAS-guarded)
        if current_status == PublishStatus.VALIDATING:
            self._repo.update_status(
                publish_id, PublishStatus.DRAFT, PublishStatus.VALIDATING
            )
            logger.info(
                f"[offline_publish] Publish status updated to draft: publish_id={publish_id}"
            )

        # Step 5: 异步执行销毁流程（仅 ONLINE 阶段，VERIFY 阶段无需销毁）
        if stage == PublishStage.VERIFY.value:
            result["message"] = f"验证环境已下线，发布单已回退到草稿状态: publish_id={publish_id}"

            logger.info(
                f"[offline_publish] Offline request submitted: publish_id={publish_id}, "
                f"stage={stage.value}"
            )
            return result

        # (#197) Enqueue a DURABLE destroy task instead of a fire-and-forget
        # asyncio task, so a pod restart mid-offline does not leak a running
        # online bot (the DB said RELEASED but the bot was never destroyed).
        publish_flow_service.enqueue_offline_destroy(
            publish_id=publish_id, stage=stage, operator="system"
        )
        result["message"] = f"下线请求已提交，销毁流程正在后台执行: stage={stage.value}"

        logger.info(
            f"[offline_publish] Offline request submitted: publish_id={publish_id}, "
            f"stage={stage.value}"
        )

        return result

    def _resolve_offline_stage(self, current_status: str, publish_id: int) -> PublishStage:
        """Map an offline-able publish status to its destroy stage.

        SUCCESS (online) → ONLINE; VALIDATING (verify) → VERIFY; anything else is
        not offline-able and raises."""
        if current_status == PublishStatus.SUCCESS:
            return PublishStage.ONLINE
        if current_status == PublishStatus.VALIDATING:
            return PublishStage.VERIFY
        raise BotPublishServiceError(
            f"发布单状态不支持下线: publish_id={publish_id}, status={current_status}，"
            f"仅支持状态: {PublishStatus.SUCCESS}, {PublishStatus.VALIDATING}"
        )

    def _release_or_redraft_on_offline(
        self,
        publish_record: BotPublishRecord,
        publish_id: int,
        result: Dict[str, Any],
    ) -> None:
        """SUCCESS-branch offline transition: mark the record RELEASED and, when no
        other non-terminal record exists for the bot, open a fresh draft for the
        next cycle. Mutates ``result`` with the new draft's id/version. The bot is
        never destroyed here (that is the durable destroy task's job); both status
        writes are CAS-guarded (SUCCESS→RELEASED) so a lost race is a no-op."""
        bot_id = publish_record.source_bot_id
        owner_id = publish_record.owner_id

        terminal_statuses = {
            PublishStatus.SUCCESS,
            PublishStatus.UPGRADED,
            PublishStatus.RELEASED,
            PublishStatus.FAILED,
        }

        all_publish_records = self._repo.list_by_source_bot(
            publish_record.source_bot_pk, publish_record.env
        )
        non_terminal_records = [
            r for r in all_publish_records
            if r.status not in terminal_statuses and r.id != publish_id
        ]

        if non_terminal_records:
            logger.info(
                f"[offline_publish] Found {len(non_terminal_records)} non-terminal publish record(s), "
                f"skipping bot deletion: bot_id={bot_id}, owner_id={owner_id}"
            )
            self._repo.update_status(
                publish_id, PublishStatus.RELEASED, PublishStatus.SUCCESS
            )
            logger.info(
                f"[offline_publish] Publish status updated to released: publish_id={publish_id}"
            )
            return

        # No other non-terminal record → open a fresh draft for the next cycle.
        logger.info(
            f"[offline_publish] No non-terminal publish record found, "
            f"creating new draft publish: bot_id={bot_id}, owner_id={owner_id}"
        )

        # 计算新版本号（基于最大版本号，避免回滚后冲突）
        new_version = self._get_next_version(
            publish_record.publish_bot_id, publish_record.owner_id
        )

        new_record = self.create_publish(
            source_bot_pk=publish_record.source_bot_pk,
            source_bot_id=publish_record.source_bot_id,
            publish_bot_id=publish_record.publish_bot_id,
            name=publish_record.name,
            owner_id=publish_record.owner_id,
            permission_owner=publish_record.permission_owner,
            description=publish_record.description,
            owner_name=publish_record.owner_name,
            version=new_version,
            last_pub_id=publish_id,
            ext=None,
        )

        result["new_publish_id"] = new_record.id
        result["new_publish_version"] = new_version
        logger.info(
            f"[offline_publish] New draft publish created: "
            f"original_id={publish_id} -> new_id={new_record.id}, version={new_version}"
        )

        self._repo.update_status(
            publish_id, PublishStatus.RELEASED, PublishStatus.SUCCESS
        )
        logger.info(
            f"[offline_publish] Publish status updated to released: publish_id={publish_id}"
        )

    def upgrade_bot_to_service(
        self,
        bot_id: str,
        owner_id: str,
    ) -> Dict[str, Any]:
        """将个人 Bot 升级为服务 Bot。
        Args: bot_id: Bot ID, owner_id: Owner ID
        Returns: Dict[bot, publish_record] - publish_record 已有则为 None
        Raises: BotNotFoundError, BotAlreadyServiceTypeError, BotTypeNotSupportedError
        """
        # 1. 查询 Bot
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if not bot:
            raise BotNotFoundError(f"Bot not found: bot_id={bot_id}, owner_id={owner_id}")

        # 3. 检查当前 bot_type
        bot_type = bot.get("bot_type", "personal")
        if bot_type == "service":
            raise BotAlreadyServiceTypeError(
                f"Bot is already a service bot: bot_id={bot_id}"
            )

        # 4. 检查是否为 aicoding 类型（不支持升级）
        active_engine = bot.get("active_engine", "")
        if active_engine == "aicoding":
            raise BotTypeNotSupportedError(
                f"aicoding type bot cannot be upgraded to service: bot_id={bot_id}"
            )

        # 5. 调用 BCN switch_bot 接口切换绑定
        switch_result = self._bcn_service.switch_bot(teamclaw_bot_uuid=bot_id, owner_workno=owner_id, name=bot.get("bot_name") or bot_id, summary=bot.get("bot_desc") or "")
        logger.info(f"[upgrade_bot_to_service] BCN switch_bot succeeded: bot_id={bot_id}, owner_id={owner_id}, websocket_kicked={switch_result.get('websocket_kicked')}, idempotent_replay={switch_result.get('idempotent_replay')}")

        # 6. 更新 bot_type 为 "service"
        updated_bot = self._bot_repo.update_by_owner(
            bot_id, owner_id, {"bot_type": "service"}
        )
        if not updated_bot:
            raise BotNotFoundError(f"Failed to update bot: bot_id={bot_id}")

        # 7. 异步重启 Bot（不阻塞主流程，无论后续是否有发布记录都要重启）。
        #    重启的作用是把容器按 service 模式重新起（ARCA/coding 容器的 BOT_TYPE /
        #    storage / secret 依赖 bot_type）。teclaw 的草稿容器与 bot_type 完全无关
        #    （producer/composer 不读 bot_type，出站规则也不区分），个人/服务是同一个容器，
        #    因此 teclaw 不需要重启；一旦重启会 destroy 容器 + 重新分配失败，把 bot 打成
        #    无 binding 的坏状态，并丢掉个人阶段的容器内文件（Dima 2026070100117117968）。
        if self._bot_service.is_teclaw_bot(active_engine):
            logger.info(
                f"[upgrade_bot_to_service] teclaw bot, skip restart (container is "
                f"bot_type-agnostic, restart would strand it): bot_id={bot_id}"
            )
        else:
            def _do_restart():
                try:
                    self._bot_service.restart_bot(bot_id=bot_id, user_id=owner_id, nick_name=bot.get("owner_name"))
                except Exception as e:
                    logger.warning(f"[upgrade_bot_to_service] Failed to restart bot: bot_id={bot_id}, owner_id={owner_id}, error={e}")

            threading.Thread(
                target=bind_current_avernet_tenant(_do_restart), daemon=True
            ).start()

        # 8. 检查是否已有发布记录，有则不创建
        existing = self._repo.get_by_publish_bot_id(bot_id, owner_id, self._env)
        if existing:
            logger.info(f"[upgrade_bot_to_service] Bot upgraded (publish exists): bot_id={bot_id}, existing_publish_id={existing.id}")
            return {
                "bot": updated_bot,
                "publish_record": existing,
            }

        # 9. 创建发布记录
        publish_name = bot.get("bot_name") or bot_id
        publish_record = self.create_publish(
            source_bot_pk=bot["id"],
            source_bot_id=bot_id,
            publish_bot_id=bot_id,
            name=publish_name,
            owner_id=owner_id,
            permission_owner="owner",
            description=bot.get("bot_desc"),
            owner_name=bot.get("owner_name"),
        )

        logger.info(f"[upgrade_bot_to_service] Bot upgraded: bot_id={bot_id}, publish_id={publish_record.id}")

        return {
            "bot": updated_bot,
            "publish_record": publish_record,
        }

    def update_bot_type(
        self,
        bot_id: str,
        owner_id: str,
        bot_type: str,
    ) -> Dict[str, Any]:
        """直接更新 Bot 类型（无业务校验）。

        Args:
            bot_id: Bot ID
            owner_id: Owner ID
            bot_type: Bot 类型，有效值为 "personal" 或 "service"

        Returns:
            Dict 包含:
                - bot: 更新后的 Bot 记录

        Raises:
            BotPublishServiceError: BotRepository 未配置
            BotNotFoundError: Bot 不存在或非 owner 操作
        """
        # 1. 验证 bot_type
        valid_types = {"personal", "service"}
        if bot_type not in valid_types:
            raise BotPublishServiceError(
                f"Invalid bot_type: {bot_type}, valid values are: {valid_types}"
            )

        # 2. 更新 bot_type
        updated_bot = self._bot_repo.update_by_owner(
            bot_id, owner_id, {"bot_type": bot_type}
        )
        if not updated_bot:
            raise BotNotFoundError(f"Bot not found: bot_id={bot_id}, owner_id={owner_id}")

        logger.info(
            f"[update_bot_type] Bot type updated: bot_id={bot_id}, "
            f"bot_type={bot_type}, owner_id={owner_id}"
        )

        return {
            "bot": updated_bot,
        }
