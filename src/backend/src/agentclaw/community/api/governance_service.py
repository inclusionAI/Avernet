"""Service API Protocols for economy/governance endpoints.

These Protocols decouple the HTTP adapter layer from concrete core service
classes, following the project's adapter→api→core layering rule (Rule 14).
Routers inject ``Injected(XProtocol)`` instead of importing service classes
from ``core/`` directly.

**Split (核心业务层化改造, 2026-07-12):**
三个被 core 内部 service 消费的 Protocol(Admin/Whitelist/Lifecycle)的**定义**
移到 ``core/economy/governance/services/service_protocols.py`` —— core 自家抽象,
service↔service 不跨层依赖 ``api/``。本文件 **re-export** 同名 Protocol,供
adapters/http router 注入(router 在 api 外圈,import core Protocol 合法)。
其余 4 个 Protocol(Feedback/Bot/WhitelistRepo/RecordProcess)的消费者都在
api 外圈,定义保留在本文件。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from agentclaw.community.core.economy.governance.services.service_protocols import (
    GovernanceAdminServiceProtocol,
    GovernanceAuditReadServiceProtocol,
    GovernanceLifecycleServiceProtocol,
    GovernanceWhitelistServiceProtocol,
    GovernanceWorkflowServiceProtocol,
    NotifyLifecycleServiceProtocol,
)


if TYPE_CHECKING:
    from agentclaw.community.core.economy.governance.domain.record import GovernanceRecord


@runtime_checkable
class GovernanceFeedbackServiceProtocol(Protocol):
    """Protocol for user-facing governance feedback interactions.

    list_pending / list_history / get_notification 已删除:无真实用户主动调用,
    治理反馈真入口是 card-callback(经 ``resolve``)。仅保留 ``resolve``。
    """

    def resolve(
        self,
        notification_id: str,
        response: str,
        user_id: str,
        remark: str | None,
        source: str,
        repair_deadline: Any | None = None,
        feedback_payload: dict | None = None,
    ) -> Any:
        ...


@runtime_checkable
class GovernanceBotServiceProtocol(Protocol):
    """Protocol for governance cron tick orchestrator (§7.3)."""

    def process_cron_tick(
        self,
        *,
        dry_run: bool | None = None,
    ) -> Any:
        ...


@runtime_checkable
class GovernanceWhitelistProtocol(Protocol):
    """Protocol for governance whitelist operations (single-point add + list_by_owner).

    Decouples the public router from the concrete
    ``GovernanceWhitelistRepository`` in ``core/``, following Rule 14.
    """

    def add(
        self,
        *,
        bot_id: str,
        owner_id: str,
        created_by: str,
        whitelist_type: str = "governance",
        source: str = "manual",
        reason: str = "",
        expires_at: Any | None = None,
    ) -> Any:
        ...

    def list_by_owner(
        self,
        owner_id: str,
        *,
        whitelist_type: str = "governance",
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        ...

    def is_whitelisted(
        self,
        bot_id: str,
        owner_id: str,
        *,
        whitelist_type: str = "governance",
    ) -> bool:
        ...

    def count_by_type(
        self,
        *,
        whitelist_type: str = "governance",
    ) -> int:
        ...


@runtime_checkable
class GovernanceRecordProcessProtocol(Protocol):
    """Protocol for offline-batch record processing (§7.2).

    ``process_record()`` is an internal implementation detail used by
    ``process_offline_batch()``; it is not exposed via this Protocol.
    """

    def process_offline_batch(
        self,
        records: list[GovernanceRecord],
        *,
        batch_id: str,
        dt_version: str,
        total_count: int,
        dry_run: bool = False,
    ) -> Any:
        ...
