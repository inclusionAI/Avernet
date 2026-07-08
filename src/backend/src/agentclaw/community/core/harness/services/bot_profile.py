"""BotProfile — unified Bot file access with backup and restore.

Wraps IdentityService for file I/O and adds backup/diff capabilities.
Also aggregates activated skills / MCPs from SkillSetService.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TYPE_CHECKING

from agentclaw.community.core.services.identity import IdentityService

if TYPE_CHECKING:
    from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory

from agentclaw.community.core.harness.documents import MarkdownDocument, JsonDocument
from agentclaw.community.core.harness.models import BotFileRef


@dataclass
class BackupSnapshot:
    """In-memory representation of a backup."""

    id: str
    bot_id: str
    app_id: str
    affected_types: list[str]
    files: dict[str, str] = field(default_factory=dict)
    checksum: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)


class BotProfile:
    """All Kit access to Bot config files goes through here.

    - Reads/writes via IdentityService (supports local FS + Arca)
    - Creates/restores backups
    - Generates unified diffs
    - Aggregates activated skills / MCPs from SkillSetService
    """

    def __init__(
        self,
        identity_service: IdentityService,
        path_factory: WorkspacePathFactory,
        skill_set_service_factory: "SkillSetServiceFactory",
    ):
        self._identity = identity_service
        self._skill_set_service_factory = skill_set_service_factory
        self._factory = path_factory

    async def list_files(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        publish_id: str | None = None,
    ) -> list[str]:
        """Return list of identity file types for this bot.

        If publish_id is provided, lists files for the specific publish version
        (currently reserved for future use; returns the same static list).
        """
        from agentclaw.community.core.services.identity import VALID_IDENTITY_FILES
        return list(VALID_IDENTITY_FILES)

    async def read_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        operator_id: str = "",
        publish_id: str | None = None,
    ) -> BotFileRef:
        """Read raw file content via IdentityService and parse into BotFileRef.

        If publish_id is provided, reads files from the specific publish version.
        """
        resp = await self._identity.get_bot_file(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            file_type=file_type,
            operator_id=operator_id,
            publish_id=publish_id,
        )
        content = resp.content or ""
        import logging as _logging
        _logger = _logging.getLogger(__name__)
        _logger.info(
            "[BotProfile] read_file entity_type=%s entity_id=%s bot_id=%s file_type=%s "
            "content_len=%d file_path=%s success=%s",
            entity_type, entity_id, bot_id, file_type,
            len(content), getattr(resp, 'file_path', 'unknown'), resp.success,
        )
        return BotFileRef(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            file_type=file_type,
            content=content,
            checksum=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    async def read_document(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        operator_id: str = "",
        publish_id: str | None = None,
    ) -> MarkdownDocument | JsonDocument:
        """Read and parse the file into a structured Document."""
        ref = await self.read_file(entity_type, entity_id, bot_id, file_type, operator_id, publish_id=publish_id)
        if file_type.endswith(".json"):
            return JsonDocument.parse(ref.content, bot_id=bot_id, file_type=file_type)
        return MarkdownDocument.parse(ref.content, bot_id=bot_id, file_type=file_type)

    async def write_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        document: MarkdownDocument | JsonDocument,
        operator_id: str = "",
        publish_id: str | None = None,
    ) -> BotFileRef:
        """Serialize Document and write back via IdentityService.

        Writes always target the current working draft, regardless of publish_id.
        publish_id is accepted for API consistency but is currently unused.
        """
        raw = document.serialize()
        await self._identity.write_identity_file(
            entity_type, entity_id, bot_id, file_type, raw, entity_id,
        )
        return BotFileRef(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            file_type=file_type,
            content=raw,
            checksum=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )

    # ── Backup / Restore ──────────────────────────────────────

    def create_backup(
        self,
        bot_id: str,
        app_id: str,
        affected_types: list[str],
        files_content: dict[str, str],
    ) -> BackupSnapshot:
        """Create an in-memory backup snapshot.

        In production the caller persists the snapshot via StoragePlugin.
        """
        payload = json.dumps(files_content, sort_keys=True)
        return BackupSnapshot(
            id=f"{bot_id}_{app_id}_{hashlib.sha256(payload.encode()).hexdigest()[:16]}",
            bot_id=bot_id,
            app_id=app_id,
            affected_types=affected_types,
            files=files_content,
            checksum=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )

    def restore_backup(self, snapshot: BackupSnapshot) -> dict[str, str]:
        """Return the file contents from a backup snapshot."""
        return snapshot.files

    def generate_diff(
        self,
        old_ref: BotFileRef,
        new_ref: BotFileRef,
    ) -> str:
        """Unified diff between two file versions."""
        from difflib import unified_diff
        a = old_ref.content.splitlines(keepends=True)
        b = new_ref.content.splitlines(keepends=True)
        return "".join(
            unified_diff(
                a,
                b,
                fromfile=f"{old_ref.file_type} (before)",
                tofile=f"{new_ref.file_type} (after)",
                lineterm="",
            )
        )

    # ── Activated Skills / MCPs (SkillSetService aggregation) ─────────

    def _get_skill_set_service(
        self,
        entity_id: str,
        bot_id: str,
        entity_type: str = "staff",
        engine_type: str | None = None,
    ) -> Any:
        """Lazy-load SkillSetService scoped to this bot via the DI
        factory (SkillSetService.__init__ requires injected repos/plugins
        the bare constructor cannot supply)."""
        return self._skill_set_service_factory.create(
            entity_id=entity_id,
            bot_id=bot_id,
            entity_type=entity_type,
            engine_type=engine_type,
        )

    def get_activated_skills(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str | None = None,
        entity_type: str = "staff",
        engine_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return currently activated skills for this bot.

        Only includes skills from skill-sets where is_active=True.
        Data source: ac_skill_set.is_active + ac_skill_set_skill join table.
        """
        svc = self._get_skill_set_service(entity_id, bot_id, entity_type, engine_type)
        skill_sets = svc.get_all_skill_sets_with_skills(
            user_id=user_id or entity_id,
            bolt_id=bot_id,
        )
        skills: list[dict[str, Any]] = []
        for ss in skill_sets:
            if not ss.get("is_active"):
                continue
            for sk in ss.get("skills", []):
                skills.append({
                    "skill_set_id": ss.get("id"),
                    "skill_set_name": ss.get("name"),
                    **sk,
                })
        return skills

    def get_activated_mcps(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str | None = None,
        entity_type: str = "staff",
        engine_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return currently activated MCPs for this bot.

        Includes MCPs from active skill-sets plus default MCPs (not user-excluded).
        Relies on collect_bot_active_mcps which handles deduplication.
        """
        svc = self._get_skill_set_service(entity_id, bot_id, entity_type, engine_type)
        return svc.collect_bot_active_mcps(
            entity_id=entity_id,
            bot_id=bot_id,
            user_id=user_id or entity_id,
            entity_type=entity_type,
            engine_type=engine_type,
        )
