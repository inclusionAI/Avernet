"""PatchEngine — plan → preview → apply → verify → rollback lifecycle.

Phase 1: L1 file-level patch operations only.
Supports two apply modes:
  1. Traditional operations (rewrite_section / insert_after / delete_section)
  2. LLM-generated patches (llm_fix) — directly writes preview_diff to file

This is the default L1 implementation of PatchEngineProtocol.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agentclaw.community.core.harness.documents import MarkdownDocument
from agentclaw.community.core.harness.models import (
    PatchDefinition,
    PatchOperation,
    PatchRecord,
    PatchStatus,
    PatchTarget,
)
from agentclaw.community.core.harness.services.bot_profile import BotProfile
from agentclaw.community.core.harness.services.content_scanner import ContentScanner
from agentclaw.community.core.repository.protocols.harness import HarnessPatchRecordRepository

if TYPE_CHECKING:
    from agentclaw.community.core.services.identity import IdentityService

logger = logging.getLogger(__name__)


class PatchEngineError(Exception):
    """Patch engine failure."""

    def __init__(self, stage: str, message: str):
        self.stage = stage
        self.message = message
        super().__init__(f"[{stage}] {message}")


class PatchEngine:
    """Execute patch plans with preview, apply, verify, and rollback.

    Default L1 implementation of PatchEngineProtocol.
    """

    def __init__(
        self,
        bot_profile: BotProfile,
        scanner: ContentScanner,
        db: object,
        patch_record_repo: HarnessPatchRecordRepository,
        identity_service: IdentityService,
    ):
        self._bot_profile = bot_profile
        self._scanner = scanner
        self._db = db
        self._patch_record_repo = patch_record_repo
        self._identity = identity_service

    async def plan(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        patch: PatchDefinition,
        operator_id: str = "",
    ) -> PatchRecord:
        """Validate patch and create a PatchRecord in ``planned`` state."""
        record = PatchRecord(
            bot_id=bot_id,
            entity_id=entity_id,
            patch_id=patch.id or 0,
            layer=patch.layer,
            target=PatchTarget(files=[patch.name], sections=[]),
            status=PatchStatus.PLANNED,
        )
        return record

    async def preview(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        operations: list[PatchOperation],
        file_type: str | None = None,
        operator_id: str = "",
    ) -> tuple[str, list[tuple[str, str, str, str]]]:
        """Generate unified diff preview without writing.

        Simplified for Phase 1: only 'update_md' op is implemented.
        Other ops are reserved for future extension.

        Args:
            file_type: Target file type. If None, inferred from operations[0].target

        Returns:
            Tuple of (resolved_file_type, list of (op_type, target, diff_text, content))
        """
        if not operations:
            raise PatchEngineError("preview", "No operations provided")

        # Infer file_type from first operation if not provided
        resolved_file_type = file_type or operations[0].target

        results: list[tuple[str, str, str, str]] = []

        for op in operations:
            if op.op in ("update_md", "update_file"):
                detail = op.detail or {}
                src_content = detail.get("src_content", "")
                dst_content = detail.get("dst_content", "")

                # Simplified preview: show before/after content directly
                diff_text = f"--- BEFORE ({resolved_file_type}) ---\n{src_content}\n\n--- AFTER ({resolved_file_type}) ---\n{dst_content}"

                results.append((op.op, op.target, diff_text, dst_content))
            else:
                # Reserved extension point: other ops not implemented in Phase 1
                raise PatchEngineError(
                    "preview",
                    f"Operation '{op.op}' not supported in preview (only 'update_md'/'update_file' is implemented)"
                )

        return resolved_file_type, results

    async def apply(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        record: PatchRecord,
        operations: list[PatchOperation],
        operator_id: str = "",
        publish_id: str | None = None,
    ) -> PatchRecord:
        """Write files, create backup, and transition to ``applied``.

        Supports both traditional ops and llm_fix (direct preview_diff write).
        Raises PatchEngineError on failure.
        """
        record.status = PatchStatus.APPLYING

        # 1. Snapshot current files for backup
        files_before: dict[str, str] = {}
        affected_types: list[str] = []
        for op in operations:
            if op.op == "update_file":
                content = await self._read_file_content(
                    op, entity_type, entity_id, bot_id, operator_id, publish_id,
                )
                files_before[op.target] = content
            else:
                ref = await self._bot_profile.read_file(
                    entity_type, entity_id, bot_id, op.target, operator_id,
                    publish_id=publish_id,
                )
                files_before[op.target] = ref.content
            affected_types.append(op.target)

        backup = self._bot_profile.create_backup(
            bot_id=bot_id,
            app_id=str(record.id or "unknown"),
            affected_types=affected_types,
            files_content=files_before,
        )
        record.backup_checksum = backup.checksum
        record.backup_file_types = affected_types
        record.backup_content = json.dumps(files_before) if not record.backup_content else record.backup_content

        # 2. Apply each operation
        for op in operations:
            if op.op == "update_md":
                dst_content = op.detail.get("dst_content") if op.detail else None
                if dst_content:
                    doc = MarkdownDocument.parse(dst_content, bot_id=bot_id, file_type=op.target)
                    await self._bot_profile.write_file(
                        entity_type, entity_id, bot_id, op.target, doc, operator_id,
                        publish_id=publish_id,
                    )
            elif op.op == "update_file":
                dst_content = op.detail.get("dst_content") if op.detail else None
                if dst_content:
                    await self._write_file_content(
                        op, dst_content, entity_type, entity_id, bot_id, operator_id, publish_id,
                    )
            else:
                # TODO: 预留其他操作类型的口子（如 rewrite_section, insert_after 等）
                raise PatchEngineError("apply", f"Unsupported operation: {op.op}")

        record.status = PatchStatus.APPLIED
        record.applied_by = operator_id
        from datetime import datetime
        record.applied_at = datetime.utcnow()

        # Persist record status to DB
        if record.id:
            try:
                self._patch_record_repo.update_status(record.id, PatchStatus.APPLIED)
                logger.info("[PatchEngine] Updated record %s status to APPLIED", record.id)
            except Exception as e:
                logger.warning("[PatchEngine] Failed to update record status: %s", e)

        return record

    async def verify(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        record: PatchRecord,
        operator_id: str = "",
    ) -> PatchRecord:
        """Re-scan files after apply and transition to ``verified`` or ``failed``.
        """
        report = await self._scanner.scan(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            operator_id=operator_id,
            file_types=record.backup_file_types,
        )
        record.health_score = report.health_score
        if report.status == "completed":
            record.status = PatchStatus.VERIFIED
            from datetime import datetime
            record.verified_at = datetime.utcnow()
        else:
            record.status = PatchStatus.FAILED
            record.failed_reason = report.failed_reason or "Verification failed"

        # Persist record status to DB
        if record.id:
            try:
                self._patch_record_repo.update_status(
                    record.id, record.status, record.failed_reason
                )
            except Exception:
                pass
        return record

    async def rollback(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        record: PatchRecord,
        operator_id: str = "",
        publish_id: str | None = None,
    ) -> PatchRecord:
        """Restore files from backup and transition to ``rolled_back``.
        """
        record.status = PatchStatus.ROLLED_BACK
        from datetime import datetime
        record.rolled_back_at = datetime.utcnow()
        return record

    async def rollback_by_patch(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        patch: PatchDefinition,
        operations: list[PatchOperation] | None = None,
        file_type: str | None = None,
        operator_id: str = "",
        publish_id: str | None = None,
    ) -> tuple[bool, str]:
        """Smart rollback based on patch operations.

        For update_md: check current content == dst_content, then restore src_content.
        If current content differs from dst_content, rollback is rejected.

        Returns: (success, message)
        """
        if not patch.content:
            return False, "Patch has no content"

        if operations is None:
            # Parse operations from patch.content
            try:
                ops_data = json.loads(patch.content)
                operations = [PatchOperation(**op) for op in ops_data]
            except (json.JSONDecodeError, TypeError) as e:
                return False, f"Failed to parse patch operations: {e}"

        if not operations:
            return False, "No operations found in patch"

        for op in operations:
            if op.op == "update_md":
                detail = op.detail or {}
                src_content = detail.get("src_content", "")
                dst_content = detail.get("dst_content", "")

                if not src_content and not dst_content:
                    return False, f"Operation missing src_content/dst_content"

                # Read current file content
                try:
                    ref = await self._bot_profile.read_file(
                        entity_type, entity_id, bot_id, op.target, operator_id,
                        publish_id=publish_id,
                    )
                    current_content = ref.content
                except Exception as e:
                    return False, f"Failed to read current file: {e}"

                # Check if current content matches dst_content
                if current_content.strip() != dst_content.strip():
                    return False, (
                        f"File content has changed since patch was applied. "
                        f"Cannot rollback safely."
                    )

                # Restore src_content
                doc = MarkdownDocument.parse(src_content, bot_id=bot_id, file_type=op.target)
                await self._bot_profile.write_file(
                    entity_type, entity_id, bot_id, op.target, doc, operator_id,
                    publish_id=publish_id,
                )
            elif op.op == "update_file":
                detail = op.detail or {}
                src_content = detail.get("src_content", "")
                dst_content = detail.get("dst_content", "")

                if not src_content and not dst_content:
                    return False, f"Operation missing src_content/dst_content"

                # Read current file content
                try:
                    current_content = await self._read_file_content(
                        op, entity_type, entity_id, bot_id, operator_id, publish_id,
                    )
                except Exception as e:
                    return False, f"Failed to read current file: {e}"

                # Check if current content matches dst_content
                if current_content.strip() != dst_content.strip():
                    return False, (
                        f"File content has changed since patch was applied. "
                        f"Cannot rollback safely."
                    )

                # Restore src_content
                await self._write_file_content(
                    op, src_content, entity_type, entity_id, bot_id, operator_id, publish_id,
                )
            else:
                # Reserved for other operation types
                return False, f"Operation '{op.op}' not supported for rollback"

        return True, "Rollback successful"

    async def _read_file_content(
        self,
        op: PatchOperation,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        operator_id: str = "",
        publish_id: str | None = None,
    ) -> str:
        """Read file content, routing update_file via IdentityService."""
        if op.op == "update_file":
            return await self._identity.read_file(
                Path(op.target), bot_id=bot_id, owner_id=entity_id,
            )
        ref = await self._bot_profile.read_file(
            entity_type, entity_id, bot_id, op.target, operator_id,
            publish_id=publish_id,
        )
        return ref.content

    async def _write_file_content(
        self,
        op: PatchOperation,
        content: str,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        operator_id: str = "",
        publish_id: str | None = None,
    ) -> None:
        """Write file content, routing update_file via IdentityService."""
        if op.op == "update_file":
            await self._identity.write_file(
                Path(op.target), content, bot_id=bot_id, owner_id=entity_id,
            )
            return
        doc = MarkdownDocument.parse(content, bot_id=bot_id, file_type=op.target)
        await self._bot_profile.write_file(
            entity_type, entity_id, bot_id, op.target, doc, operator_id,
            publish_id=publish_id,
        )
