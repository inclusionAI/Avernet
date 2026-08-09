"""Repository contracts owned by the ``harness`` domain.

Moved here by the ``core/repository`` consolidation. Every member is
``@abstractmethod``: an implementation that omits one fails at construction
naming the missing member, instead of raising ``AttributeError`` at the call
site. Domain imports are ``TYPE_CHECKING``-only — see the module docstring in
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.harness.models import FindingsReport, PatchDefinition, PatchRecord, PatchStatus, PatchTemplate


@runtime_checkable
class HarnessTemplateRepository(Protocol):
    """CRUD for ac_harness_patch_template."""

    @abstractmethod
    def create(self, tpl: PatchTemplate) -> PatchTemplate:
        """Insert and return the created template with id populated."""
        ...

    @abstractmethod
    def get_by_id(self, tpl_id: int) -> PatchTemplate | None:
        """Fetch by primary key."""
        ...

    @abstractmethod
    def get_by_name(self, name: str, env: str) -> PatchTemplate | None:
        """Fetch by unique name+env."""
        ...

    @abstractmethod
    def list(
        self,
        layer: str | None = None,
        status: str | None = None,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[PatchTemplate], int]:
        """Return (items, total_count)."""
        ...

    @abstractmethod
    def update(self, tpl_id: int, **fields) -> PatchTemplate | None:
        """Partial update by id."""
        ...

    @abstractmethod
    def soft_delete(self, tpl_id: int) -> bool:
        """Set status='deprecated'."""
        ...

    @abstractmethod
    def load_all_active(self) -> list[PatchTemplate]:
        """Return all active templates (for in-memory index rebuild)."""
        ...


@runtime_checkable
class HarnessScanRecordRepository(Protocol):
    """Unified repository for ac_harness_scan_record.

    Combines publish-API methods (batch_create, get_latest_dim_records)
    with diagnose-API CRUD methods (create, get_by_id, get_recent, etc.).
    """

    # ── publish-API methods ───────────────────────────────────

    @abstractmethod
    def get_latest_dim_records(
        self,
        bot_id: str,
        entity_id: str,
        bot_publish_id: str | None = None,
        match_null_publish: bool = True,
    ) -> list[dict[str, Any]]:
        """Return the latest completed scan record per scan_dim for a bot.

        When bot_publish_id is None and match_null_publish is True,
        only records with bot_publish_id IS NULL are matched.
        When bot_publish_id is None and match_null_publish is False,
        records with any bot_publish_id are returned.
        Each dict contains raw column values keyed by column name.
        """
        ...

    @abstractmethod
    def list_dim_history(
        self,
        bot_id: str,
        entity_id: str,
        scan_dim: str | None = None,
        bot_publish_id: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return paginated scan history per dimension for a bot.

        Args:
            bot_id: Bot ID
            entity_id: Entity ID
            scan_dim: Optional filter by scan dimension
            bot_publish_id: Optional filter by publish ID
            page: Page number (1-based)
            size: Page size

        Returns:
            Tuple of (items list, total count)
        """
        ...

    @abstractmethod
    def batch_create(
        self,
        bot_id: str,
        entity_id: str,
        bot_publish_id: str | None,
        layer: str,
        trigger_source: str,
        records: list[dict[str, Any]],
    ) -> list[int]:
        """Insert multiple scan records and return generated primary keys.

        Each dict in `records` contains per-dimension fields:
        scan_dim, health_score, score_grade, check_items, findings,
        findings_summary, duration_ms, scan_type, patch_ids, status,
        failed_reason, env.
        """
        ...

    @abstractmethod
    def offline_batch(
        self,
        bot_id: str,
        entity_id: str,
        bot_publish_id: str | None,
        layer: str,
        trigger_source: str,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """离线T+1批量写入，支持upsert和自定义时间。

        唯一键: bot_id + entity_id + scan_type + scan_dim

        records 支持 patches 字段用于同步 ac_harness_patch 表：
        - patches: patch 列表（无id，数据库自增），将 insert 到 ac_harness_patch 并自动更新 scan record.patch_ids
        - 不传 patches 或 patches 为空时，回写 scan record.patch_ids 为 []

        Returns: [{"scan_dim", "scan_type", "action": "inserted"|"updated"|"failed", "id", "reason", "patch_ids", "patches"}]
        - patch_ids: 最终关联的 patch id 列表
        - patches: 各 patch 的 insert 结果明细 [{"id", "source_id", "action", "reason"}]，action 只可能是 inserted/failed
        """
        ...

    # ── diagnose-API methods ──────────────────────────────────

    @abstractmethod
    def create(self, report: FindingsReport) -> int:
        """Insert a scan record from FindingsReport, return the scan_id."""
        ...

    @abstractmethod
    def get_by_id(self, scan_id: int) -> dict | None:
        """Fetch a scan record by primary key."""
        ...

    @abstractmethod
    def get_recent(
        self,
        bot_id: str,
        entity_id: str,
        scan_type: str | None = None,
        layer: str | None = None,
    ) -> dict | None:
        """Fetch the most recent completed scan for a bot."""
        ...

    @abstractmethod
    def list_records(
        self,
        bot_id: str,
        entity_id: str,
        page: int = 1,
        size: int = 20,
        scan_type: str | None = None,
        layer: str | None = None,
        status: str | None = None,
    ) -> tuple[list[dict], int]:
        """Return paginated scan records + total count."""
        ...

    @abstractmethod
    def update_status(
        self,
        scan_id: int,
        status: str,
        failed_reason: str | None = None,
    ) -> None:
        """Update scan record status."""
        ...

    @abstractmethod
    def complete(
        self,
        scan_id: int,
        report: FindingsReport,
    ) -> None:
        """Update an existing scan record with completed FindingsReport data."""
        ...

    @abstractmethod
    def update_findings(
        self,
        scan_id: int,
        findings_json: str,
        findings_summary_json: str,
        check_items_json: str,
        health_score: int,
        score_grade: str,
    ) -> None:
        """Incrementally update findings for a running scan (mid-scan progress)."""
        ...

    @abstractmethod
    def update_patch_ids(
        self,
        scan_id: int,
        patch_ids: list[int],
        findings_with_patch_ids: str | None = None,
    ) -> None:
        """Update patch_ids and enriched findings (with patch_id per check_item) for a scan."""
        ...

    @abstractmethod
    def has_active_scan(
        self,
        bot_id: str,
        entity_id: str,
        within_minutes: int = 5,
        bot_publish_id: str | None = None,
    ) -> bool:
        """Check if there is an active (in-progress) scan created within the last N minutes.

        Only matches scans with status in ('scanning', 'scan_completed', 'patching').
        When bot_publish_id is provided, only match scans with the same publish_id.
        When bot_publish_id is None, match records where bot_publish_id IS NULL.
        """
        ...


@runtime_checkable
class HarnessPatchRecordRepository(Protocol):
    """CRUD for ac_harness_patch_record."""

    @abstractmethod
    def create(self, record: PatchRecord) -> int:
        """Insert and return the created record with id populated."""
        ...

    @abstractmethod
    def get_by_id(self, record_id: int) -> PatchRecord | None:
        """Fetch by primary key."""
        ...

    @abstractmethod
    def list_by_bot(self, bot_id: str, entity_id: str, status: str | None = None) -> list[PatchRecord]:
        """Fetch all patch records for a bot, optionally filtered by status."""
        ...

    @abstractmethod
    def get_by_patch_id(self, patch_id: int) -> PatchRecord | None:
        """Fetch by patch_id (ac_harness_patch.id foreign key)."""
        ...

    @abstractmethod
    def update_status(self, record_id: int, status: PatchStatus, failed_reason: str | None = None) -> None:
        """Update record status."""
        ...


@runtime_checkable
class HarnessPatchRepository(Protocol):
    """CRUD for ac_harness_patch."""

    @abstractmethod
    def create(self, patch: PatchDefinition) -> int:
        """Insert and return the created patch with id populated."""
        ...

    @abstractmethod
    def get_by_id(self, patch_id: int) -> PatchDefinition | None:
        """Fetch by primary key."""
        ...

    @abstractmethod
    def list_by_ids(self, patch_ids: list[int]) -> list[PatchDefinition]:
        """Fetch multiple patches by their IDs (batch query)."""
        ...

    @abstractmethod
    def list_by_record(self, record_id: int) -> list[PatchDefinition]:
        """Fetch all patches linked to a scan/diagnose record_id."""
        ...

    @abstractmethod
    def update_is_applied(self, patch_id: int, is_applied: bool) -> None:
        """Update patch is_applied status."""
        ...
