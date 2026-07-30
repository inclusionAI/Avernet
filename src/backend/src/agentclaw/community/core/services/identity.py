"""
Identity service — business logic for entity and bot identity file management.

Handles validation, path resolution, file I/O (local + Arca), and AGENTS.md sync.

This module owns the canonical schemas + constants for identity files;
``api/identity/router.py``, ``core/harness/services/bot_profile.py`` and
tests all import them from here so there is one place to change a field.
"""

from pathlib import Path

import httpx
from injector import inject
from pydantic import BaseModel, Field

from agentclaw.community.core.errors import InternalError
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.engine_resolver import (
    resolve_engine_for_bot,
)
from agentclaw.community.core.config_compose.teclaw_paths import IDENTITY_NS
from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError,
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.service_bot.repository.bot_publish_repository import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.core.devices.services import device_info as device_info_lookup
from agentclaw.community.di.modules.skill_center_module import (
    DeviceFilesystemDispatcher,
)
from agentclaw.community.log import get_logger

logger = get_logger()


# HTML comment markers for reference files section in AGENTS.md
REFERENCE_SECTION_START_MARKER = "<!-- REFERENCE_FILES_SECTION_START -->"
REFERENCE_SECTION_END_MARKER = "<!-- REFERENCE_FILES_SECTION_END -->"


# ==================== Constants ====================

# Valid identity file names.
VALID_IDENTITY_FILES = {
    "RULES.md",
    "OKR.md",
    "SAFETY.md",
    "SOUL.md",
    "OUTPUT.md",
    "MEMORY.md",
    "IDENTITY.md",
    "AGENTS.md",
    "USER.md",
    "TOOLS.md",
    "HEARTBEAT.md",
    "BOOTSTRAP.md",
    "KNOWLEDGE.md",
    "CLAUDE.md",
    "GREETING.md",
    "README.md",
}

# claude_code engine 支持的 identity 文件（仅 CLAUDE.md）。
CLAUDE_CODE_IDENTITY_FILES = {"CLAUDE.md"}

# Reference files that need to be synced to AGENTS.md for openclaw visibility.
REFERENCE_FILES = {"RULES.md", "OKR.md", "SAFETY.md", "OUTPUT.md"}

# AGENTS.md file name.
AGENTS_MD_FILE = "AGENTS.md"

# Valid entity types.
VALID_ENTITY_TYPES = {"staff", "proj", "team"}


# ==================== Domain Errors ====================


class InvalidIdentityEntityTypeError(ValueError):
    """Raised when an ``entity_type`` is not in :data:`VALID_ENTITY_TYPES`."""


class InvalidIdentityFileTypeError(ValueError):
    """Raised when a ``file_type`` is not in :data:`VALID_IDENTITY_FILES`."""


# ==================== Request Models ====================


class IdentityFileContent(BaseModel):
    """Identity file content model."""

    content: str = Field(..., description="File content (Markdown format)")


# ==================== Entity-Level Response Models ====================


class IdentityFileResponse(BaseModel):
    """Identity file read response."""

    success: bool
    file_type: str = Field(..., description="File type (e.g. RULES.md, SOUL.md)")
    entity_type: str = Field(..., description="Entity type (staff, proj, team)")
    entity_id: str = Field(..., description="Entity ID")
    content: str = Field(..., description="File content")
    file_path: str = Field(..., description="Absolute file path")


class IdentityFileUpdateResponse(BaseModel):
    """Identity file update response."""

    success: bool
    message: str
    file_type: str
    entity_type: str
    entity_id: str
    file_path: str


class IdentityFileListItem(BaseModel):
    """Identity file list item."""

    file_type: str
    exists: bool
    file_path: str


class IdentityFileListResponse(BaseModel):
    """Identity file list response."""

    success: bool
    entity_type: str
    entity_id: str
    files: list[IdentityFileListItem]


# ==================== Bot-Level Response Models ====================


class BotIdentityFileResponse(BaseModel):
    """Bot identity file read response."""

    success: bool
    file_type: str = Field(..., description="File type (e.g. RULES.md, SOUL.md)")
    entity_type: str = Field(..., description="Entity type (staff, proj, team)")
    entity_id: str = Field(..., description="Entity ID")
    bot_id: str = Field(..., description="Bot ID")
    content: str = Field(..., description="File content")
    file_path: str = Field(..., description="Absolute file path")


class BotIdentityFileUpdateResponse(BaseModel):
    """Bot identity file update response."""

    success: bool
    message: str
    file_type: str
    entity_type: str
    entity_id: str
    bot_id: str
    file_path: str


class IdentityService:
    """Business service for identity file management."""

    @inject
    def __init__(
        self,
        path_factory: WorkspacePathFactory,
        publish_repo: BotPublishRepositoryProtocol,
        bot_repo: BotRepository,
        resolver: DeviceContextResolver,
        device_fs_dispatcher: DeviceFilesystemDispatcher,
    ):
        self.path_factory = path_factory
        self._publish_repo = publish_repo
        self._resolver = resolver
        self._device_fs_dispatcher = device_fs_dispatcher
        self._bot_repo = bot_repo

    # ==================== Validation ====================

    @staticmethod
    def validate_entity_type(entity_type: str) -> None:
        if entity_type not in VALID_ENTITY_TYPES:
            raise InvalidIdentityEntityTypeError(
                f"Invalid entity_type: {entity_type}. Must be one of: {VALID_ENTITY_TYPES}"
            )

    @staticmethod
    def validate_file_type(file_type: str) -> None:
        if file_type not in VALID_IDENTITY_FILES:
            raise InvalidIdentityFileTypeError(
                f"Invalid file_type: {file_type}. Must be one of: {VALID_IDENTITY_FILES}"
            )

    # ==================== Path Resolution ====================

    def get_entity_file_path(
        self,
        entity_type: str,
        entity_id: str,
        file_type: str,
        engine_type: str = "openclaw",
    ) -> Path:
        self.validate_entity_type(entity_type)
        self.validate_file_type(file_type)
        entity_identity_dir = self.path_factory.get_entity_identity_dir(
            entity_id, entity_type, engine_type
        )
        return entity_identity_dir / file_type

    def get_bot_file_path(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        engine_type: str = DEFAULT_ENGINE_TYPE,
    ) -> Path:
        self.validate_entity_type(entity_type)
        self.validate_file_type(file_type)
        bot_work_dir = self.path_factory.get_bot_engine_dir(
            entity_id, bot_id, engine_type, entity_type
        )
        if engine_type == "openclaw":
            bot_work_dir = bot_work_dir / "workspace"
        return bot_work_dir / file_type

    # ==================== File I/O (provider-blind) ====================
    #
    # The service is provider-agnostic AND container-view-agnostic: it addresses
    # every file by the logical ``identity/<file_type>`` namespace and lets the
    # dispatcher (the factory) build the per-provider mapper from the coordinates.
    # No provider/engine path branching — and no local-OSS fallback — lives here:
    # a bot with no resolvable device context is a bug and surfaces as the
    # resolver's error (fail early, never silently touch a dead local path).

    def _identity_device_fs(
        self,
        *,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        owner_id: str,
        engine_type: str,
    ):
        """Resolve the bot's device context (raises if unbound) and build its
        identity-addressing DeviceFileSystem via the dispatcher."""
        ctx = self._resolver.resolve_for_bot(bot_id, owner_id)
        return self._device_fs_dispatcher.dispatch_addressed(
            ctx,
            namespace=IDENTITY_NS,
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            engine_type=engine_type,
        )

    async def _device_read(
        self,
        *,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        owner_id: str,
        engine_type: str,
    ) -> str:
        device_fs = self._identity_device_fs(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
        )
        # 容器里没写过这个 identity 文件时,baas/arca 的 device_fs.read_file 会抛
        # 404(plugin 故意不吞,见 BaasDeviceFileSystem.read_file 注释)。identity
        # 域的契约是"缺省即空内容",由 service 层在这里收口,避免 router 吃 500、
        # 前端打不开编辑器。非 404 的 HTTPStatusError(代理 401/沙箱 5xx 等)仍透出。
        try:
            content_bytes = await device_fs.read_file(f"{IDENTITY_NS}/{file_type}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return ""
            raise
        return content_bytes.decode("utf-8") if content_bytes else ""

    async def _device_write(
        self,
        *,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        content: str,
        owner_id: str,
        engine_type: str,
    ) -> None:
        device_fs = self._identity_device_fs(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            owner_id=owner_id,
            engine_type=engine_type,
        )
        await device_fs.write_file(
            f"{IDENTITY_NS}/{file_type}", content.encode("utf-8")
        )

    async def read_identity_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
    ) -> str:
        """Read a bot-level **identity** file (provider-blind, coordinate-based).

        ``engine_type`` is resolved per-bot when not given. The high-level identity
        methods + ``bot_profile`` use this; it addresses the file as
        ``identity/<file_type>`` and lets the factory compose the device address.
        """
        eng = resolve_engine_for_bot(
            bot_id, entity_id, override=engine_type, bot_repo=self._bot_repo
        )
        return await self._device_read(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            file_type=file_type,
            owner_id=owner_id,
            engine_type=eng,
        )

    async def write_identity_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        content: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
    ) -> None:
        """Write a bot-level **identity** file (provider-blind, coordinate-based)."""
        eng = resolve_engine_for_bot(
            bot_id, entity_id, override=engine_type, bot_repo=self._bot_repo
        )
        await self._device_write(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            file_type=file_type,
            content=content,
            owner_id=owner_id,
            engine_type=eng,
        )

    # ── generic device-or-local file I/O (by absolute path) ──────────────
    # Used by patch_engine ``update_file`` for arbitrary workspace/skill files,
    # where the caller already holds a resolved absolute path (NOT an identity
    # file_type). Kept path-based + arca/local exactly as before; teclaw/baas skill
    # addressing is owned by skill_center, out of scope for identity consolidation.

    async def read_file(
        self, file_path: Path, bot_id: str | None = None, owner_id: str | None = None
    ) -> str:
        """Generic file read by absolute path (arca device or local FS)."""
        try:
            if bot_id and owner_id:
                device_provider, sandbox_id = device_info_lookup.get_device_info(
                    bot_id, owner_id, self._bot_repo
                )
                if device_provider == "arca" and sandbox_id:
                    ctx = self._resolver.resolve_for_bot(bot_id, owner_id)
                    device_fs = self._device_fs_dispatcher.dispatch(ctx)
                    content_bytes = await device_fs.read_file(str(file_path))
                    return content_bytes.decode("utf-8") if content_bytes else ""
            if file_path.exists():
                return file_path.read_text(encoding="utf-8")
            return ""
        except Exception as e:
            logger.error(f"[IdentityService.read_file] Error reading {file_path}: {e}")
            raise InternalError(f"Failed to read file: {str(e)}") from e

    async def write_file(
        self,
        file_path: Path,
        content: str,
        bot_id: str | None = None,
        owner_id: str | None = None,
    ) -> None:
        """Generic file write by absolute path (arca device or local FS)."""
        try:
            if bot_id and owner_id:
                device_provider, sandbox_id = device_info_lookup.get_device_info(
                    bot_id, owner_id, self._bot_repo
                )
                if device_provider == "arca" and sandbox_id:
                    ctx = self._resolver.resolve_for_bot(bot_id, owner_id)
                    device_fs = self._device_fs_dispatcher.dispatch(ctx)
                    await device_fs.write_file(str(file_path), content.encode("utf-8"))
                    return
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
        except Exception as e:
            logger.error(f"[IdentityService.write_file] Error writing {file_path}: {e}")
            raise InternalError(f"Failed to write file: {str(e)}") from e

    # ==================== AGENTS.md Sync ====================

    @staticmethod
    def _generate_reference_section() -> str:
        content_lines = [
            "",
            REFERENCE_SECTION_START_MARKER,
            "",
            "## Additional Reference Files",
            "",
            "The following files are available in the workspace. Use the `read` tool to access them when needed:",
            "",
            "- `~/.openclaw/workspace/OKR.md` - 目标与关键结果",
            "- `~/.openclaw/workspace/SAFETY.md` - 安全行为准则",
            "- `~/.openclaw/workspace/OUTPUT.md` - 输出格式规范",
            "- `~/.openclaw/workspace/RULES.md` - 工作规则约定",
            "",
            REFERENCE_SECTION_END_MARKER,
            "",
        ]
        return "\n".join(content_lines)

    @staticmethod
    def _remove_reference_section(content: str) -> str:
        start_pos = content.find(REFERENCE_SECTION_START_MARKER)
        if start_pos == -1:
            legacy_marker = "## Additional Reference Files"
            legacy_pos = content.find(legacy_marker)
            if legacy_pos == -1:
                return content
            return content[:legacy_pos].rstrip()

        end_pos = content.find(REFERENCE_SECTION_END_MARKER, start_pos)
        if end_pos == -1:
            logger.warning(
                "[_remove_reference_section] Found start marker but no end marker, skipping"
            )
            return content

        end_pos += len(REFERENCE_SECTION_END_MARKER)
        while end_pos < len(content) and content[end_pos] == "\n":
            end_pos += 1

        return content[:start_pos] + content[end_pos:]

    async def sync_agents_md(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        engine_type: str = DEFAULT_ENGINE_TYPE,
        *,
        owner_id: str | None = None,
    ) -> None:
        """Sync AGENTS.md with reference files section using HTML comment markers.

        Provider-blind: reads/writes AGENTS.md through the same device path as any
        identity file (openclaw-only; gated by the caller).
        """
        owner = owner_id or entity_id
        try:
            existing_content = await self.read_identity_file(
                entity_type,
                entity_id,
                bot_id,
                AGENTS_MD_FILE,
                owner,
                engine_type=engine_type,
            )
            base_content = (
                self._remove_reference_section(existing_content)
                if existing_content
                else "# AGENTS.md\n"
            )
            new_content = base_content.rstrip() + self._generate_reference_section()
            await self.write_identity_file(
                entity_type,
                entity_id,
                bot_id,
                AGENTS_MD_FILE,
                new_content,
                owner,
                engine_type=engine_type,
            )
            logger.info(
                "[sync_agents_md] Updated AGENTS.md for %s/%s bot=%s",
                entity_type,
                entity_id,
                bot_id,
            )
        except Exception as e:
            logger.error(f"[sync_agents_md] Error syncing AGENTS.md: {e}")

    # ==================== High-Level Business Methods ====================

    async def get_entity_file(
        self,
        entity_type: str,
        entity_id: str,
        file_type: str,
        operator_id: str,
    ) -> IdentityFileResponse:
        # Entity-level files belong to the ``default`` bot; openclaw-centric layout.
        self.validate_entity_type(entity_type)
        self.validate_file_type(file_type)
        content = await self._device_read(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id="default",
            file_type=file_type,
            owner_id=operator_id,
            engine_type=DEFAULT_ENGINE_TYPE,
        )
        return IdentityFileResponse(
            success=True,
            file_type=file_type,
            entity_type=entity_type,
            entity_id=entity_id,
            content=content,
            file_path=f"{IDENTITY_NS}/{file_type}",
        )

    async def update_entity_file(
        self,
        entity_type: str,
        entity_id: str,
        file_type: str,
        content: str,
        operator_id: str,
    ) -> IdentityFileUpdateResponse:
        self.validate_entity_type(entity_type)
        self.validate_file_type(file_type)
        await self._device_write(
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id="default",
            file_type=file_type,
            content=content,
            owner_id=entity_id,
            engine_type=DEFAULT_ENGINE_TYPE,
        )
        return IdentityFileUpdateResponse(
            success=True,
            message=f"{entity_type}/{entity_id} {file_type} updated successfully",
            file_type=file_type,
            entity_type=entity_type,
            entity_id=entity_id,
            file_path=f"{IDENTITY_NS}/{file_type}",
        )

    async def list_entity_files(
        self,
        entity_type: str,
        entity_id: str,
    ) -> IdentityFileListResponse:
        self.validate_entity_type(entity_type)
        # Report logic-view paths (identity/<file>); ``exists`` is a best-effort local
        # check (unchanged from before — accurate for unbound/dev, not device bots).
        files = []
        for ft in VALID_IDENTITY_FILES:
            local_path = self.get_entity_file_path(entity_type, entity_id, ft)
            files.append(
                IdentityFileListItem(
                    file_type=ft,
                    exists=local_path.exists(),
                    file_path=f"{IDENTITY_NS}/{ft}",
                )
            )
        return IdentityFileListResponse(
            success=True,
            entity_type=entity_type,
            entity_id=entity_id,
            files=files,
        )

    async def get_bot_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        operator_id: str,
        publish_id: str | None = None,
        engine_type: str | None = None,
    ) -> BotIdentityFileResponse:
        self.validate_entity_type(entity_type)
        self.validate_file_type(file_type)
        eng = resolve_engine_for_bot(
            bot_id, entity_id, override=engine_type, bot_repo=self._bot_repo
        )
        file_path = f"{IDENTITY_NS}/{file_type}"

        # publish_id present → read from the published *stage* binding (online→verify),
        # not the bot's draft binding. Provider-blind: resolve_for_binding → dispatch.
        # Authoritative for published bots — does NOT fall through to the draft read.
        if publish_id:
            content = await self._read_from_publish_device(
                publish_id=publish_id,
                entity_type=entity_type,
                entity_id=entity_id,
                bot_id=bot_id,
                file_type=file_type,
                operator_id=operator_id,
                engine_type=eng,
            )
            return BotIdentityFileResponse(
                success=True,
                file_type=file_type,
                entity_type=entity_type,
                entity_id=entity_id,
                bot_id=bot_id,
                content=content or "",
                file_path=str(file_path),
            )

        # 默认读取方式（provider-blind；unbound 时回退本地 OSS）
        # 使用 entity_id 作为 owner_id（与 write 一致，对齐 router 行为）
        owner_id = entity_id if entity_id else operator_id
        content = await self.read_identity_file(
            entity_type,
            entity_id,
            bot_id,
            file_type,
            owner_id,
            engine_type=eng,
        )
        return BotIdentityFileResponse(
            success=True,
            file_type=file_type,
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            content=content,
            file_path=str(file_path),
        )

    async def list_bot_files(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        owner_id: str,
        *,
        engine_type: str | None = None,
    ) -> list[tuple[str, bool]]:
        """Probe each whitelisted identity file_type's presence for a bot.

        Returns ``(file_type, exists)`` pairs for every entry in
        :data:`VALID_IDENTITY_FILES`; ``exists`` is ``True`` when the read
        returned non-empty content. Provider-blind: ``read_identity_file``
        addresses each file as ``identity/<file_type>`` and turns a device
        404 into an empty string (absent → ``exists=False``).
        """
        self.validate_entity_type(entity_type)
        results: list[tuple[str, bool]] = []
        for ft in VALID_IDENTITY_FILES:
            content = await self.read_identity_file(
                entity_type,
                entity_id,
                bot_id,
                ft,
                owner_id,
                engine_type=engine_type,
            )
            results.append((ft, bool(content)))
        return results

    async def _read_from_publish_device(
        self,
        *,
        publish_id: str,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        operator_id: str,
        engine_type: str,
    ) -> str | None:
        """Read an identity file from a publish record's stage device binding.

        Resolves the stage ``bind_id`` (``ext.binding`` online→verify) and reads
        provider-blind via ``resolve_for_binding → dispatch_addressed →
        device_fs.read_file("identity/<file>")`` — covering arca / baas / teclaw
        uniformly (replaces the old ``ARCA_`` target string-sniff). Returns ``None``
        when the publish record / stage binding can't be resolved.
        """
        from agentclaw.community.core.service_bot.repository.models import (
            select_stage_bind_id,
        )

        try:
            record = self._publish_repo.get_by_id(int(publish_id))
            if not record:
                logger.warning("[IdentityService] publish_id=%s not found", publish_id)
                return None
            ext = record.ext or {}
            binding_info = ext.get("binding", {})
            # Select by `record.status`; the final guard then treats a missing/0
            # bind_id as "no stage binding" (binding PKs are ≥1, so 0 is never a
            # real binding).
            bind_id = select_stage_bind_id(binding_info, record.status)
            if not bind_id:
                logger.warning(
                    "[IdentityService] publish_id=%s stage bind_id missing", publish_id
                )
                return None
            try:
                ctx = self._resolver.resolve_for_binding(
                    int(bind_id), operator_id, bot_id=bot_id
                )
            except (DeviceNotBoundError, UnknownProviderError, ConnInfoBuildError) as e:
                logger.warning(
                    "[IdentityService] publish_id=%s bind_id=%s resolve failed: %s",
                    publish_id,
                    bind_id,
                    e,
                )
                return None
            device_fs = self._device_fs_dispatcher.dispatch_addressed(
                ctx,
                namespace=IDENTITY_NS,
                entity_type=entity_type,
                entity_id=entity_id,
                bot_id=bot_id,
                engine_type=engine_type,
            )
            content_bytes = await device_fs.read_file(f"{IDENTITY_NS}/{file_type}")
            return content_bytes.decode("utf-8") if content_bytes else ""
        except Exception as e:
            logger.error(
                "[IdentityService] publish read failed for publish_id=%s: %s",
                publish_id,
                e,
            )
            return None

    async def update_bot_file(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        file_type: str,
        content: str,
        operator_id: str,
        engine_type: str | None = None,
    ) -> BotIdentityFileUpdateResponse:
        self.validate_entity_type(entity_type)
        self.validate_file_type(file_type)
        eng = resolve_engine_for_bot(
            bot_id, entity_id, override=engine_type, bot_repo=self._bot_repo
        )
        await self.write_identity_file(
            entity_type,
            entity_id,
            bot_id,
            file_type,
            content,
            entity_id,
            engine_type=eng,
        )
        # AGENTS.md sync only applies to openclaw reference files (router parity).
        if file_type in REFERENCE_FILES and eng == "openclaw":
            await self.sync_agents_md(
                entity_type, entity_id, bot_id, eng, owner_id=entity_id
            )
        return BotIdentityFileUpdateResponse(
            success=True,
            message=f"{entity_type}/{entity_id}/bot/{bot_id} {file_type} updated successfully",
            file_type=file_type,
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            file_path=f"{IDENTITY_NS}/{file_type}",
        )
