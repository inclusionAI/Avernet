"""Publish service assembly; workflow responsibilities live in focused mixins."""

from __future__ import annotations

from secbaas.community.api.bot_manage import BotManageService
from secbaas.community.api.device_manage import DeviceService
from secbaas.community.api.publish_manage import PublishService
from secbaas.community.api.template_manage import DeviceTemplateManageService
from secbaas.community.core.repository.bot import BotRepository
from secbaas.community.core.repository.bot_device_rel import BotDeviceRelRepository
from secbaas.community.core.repository.bot_session import BotSessionRepository
from secbaas.community.core.repository.device import DeviceRepository
from secbaas.community.core.repository.publish import PublishRepository
from secbaas.community.core.repository.publish_batch import PublishBatchRepository
from secbaas.community.core.repository.publish_record import PublishRecordRepository
from secbaas.community.core.service.paas import is_paas_mock_mode
from secbaas.community.core.utils.env_utils import get_current_env

from ._publish_approval import PublishApprovalMixin
from ._publish_common import BatchConfig as BatchConfig
from ._publish_common import PublishLockService as PublishLockService
from ._publish_common import (
    _extra_config_to_publish_config as _extra_config_to_publish_config,
)
from ._publish_common import _NoOpLockService as _NoOpLockService
from ._publish_completion import PublishCompletionMixin
from ._publish_create import PublishCreateMixin
from ._publish_create_update import PublishCreateUpdateMixin
from ._publish_destroy_stop import PublishDestroyStopMixin
from ._publish_execute import PublishExecuteMixin
from ._publish_plan import PublishPlanMixin
from ._publish_queries import PublishQueriesMixin
from ._publish_restart_scale import PublishRestartScaleMixin
from ._publish_retry_orchestrator import PublishAttemptOrchestrator


class DefaultPublishService(
    PublishAttemptOrchestrator,
    PublishCreateMixin,
    PublishPlanMixin,
    PublishQueriesMixin,
    PublishApprovalMixin,
    PublishExecuteMixin,
    PublishCreateUpdateMixin,
    PublishRestartScaleMixin,
    PublishDestroyStopMixin,
    PublishCompletionMixin,
    PublishService,
):
    """Bot publish workflow orchestration service.

    State Machine Overview:
        Publish documents progress through states: PENDING → ACTIVE → APPROVING → SUCCESS
        Terminal states: REJECTED, FAILED, SUCCESS, REVOKED
        See PublishStatus enum for full transition table.

    Pipeline Stages by PublishType (Per D-01, D-01a):
        - CREATE/UPDATE: PREPUB → GRAY → PROD_FIRST_BATCH → PROD_OTHER_BATCH → SUCCESS
        - RESTART: PROD_FIRST_BATCH → PROD_OTHER_BATCH → SUCCESS
        - SCALE_UP/SCALE_DOWN/DESTROY: Direct execution (single batch, no stage gates)

    Key Features:
        - Stage gates with approval (pause_for_approval)
        - Auto-compact for small device counts
        - Batch execution with cooldown periods
        - Concurrent publish prevention per bot
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        device_repo: DeviceRepository,
        rel_repo: BotDeviceRelRepository,
        session_repo: BotSessionRepository,
        publish_repo: PublishRepository,
        batch_repo: PublishBatchRepository,
        publish_record_repo: PublishRecordRepository,
        template_service: DeviceTemplateManageService,
        bot_service: BotManageService,
        device_service: DeviceService,
        lock_service: PublishLockService | None = None,
    ) -> None:
        """Initialize with injected dependencies."""
        self._lock_service: PublishLockService = lock_service or _NoOpLockService()
        self._bot_repo = bot_repo
        self._device_repo = device_repo
        self._rel_repo = rel_repo
        self._session_repo = session_repo
        self._publish_repo = publish_repo
        self._publish_batch_repo = batch_repo
        self._publish_record_repo = publish_record_repo
        self._template_service = template_service
        self._bot_service = bot_service
        self._device_service = device_service

    TRANSITIONS: dict[tuple[str, str], str] = {
        # Initial approval
        ("PENDING", "approve"): "ACTIVE",
        ("PENDING", "reject"): "REJECTED",
        # Stage execution completion
        ("ACTIVE", "stage_complete"): "APPROVING",
        ("ACTIVE", "all_complete"): "SUCCESS",
        ("ACTIVE", "fail"): "FAILED",
        # Stage gate approval
        ("APPROVING", "approve"): "ACTIVE",
        ("APPROVING", "reject"): "REJECTED",
        ("APPROVING", "revoke"): "REVOKED",
    }

    def _can_transition(self, current_status: str, action: str) -> bool:
        """Check if state transition is valid."""
        return (current_status, action) in self.TRANSITIONS

    def _get_next_status(self, current_status: str, action: str) -> str | None:
        """Get next status after transition."""
        return self.TRANSITIONS.get((current_status, action))

    @staticmethod
    def _current_env() -> str:
        return get_current_env()

    @staticmethod
    def _is_paas_mock_mode() -> bool:
        return is_paas_mock_mode()
