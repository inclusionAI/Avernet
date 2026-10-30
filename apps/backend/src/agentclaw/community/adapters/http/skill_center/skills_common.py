"""Shared compatibility helpers for the Legacy skills endpoints."""

import io
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
)
from agentclaw.community.adapters.http.skill_center.legacy_bot_address import (
    legacy_path_params,
)
from agentclaw.community.adapters.http.skill_center.schemas import (
    UploadSkillResponse,
)
from agentclaw.community.api.runtime_layout_probe_service import (
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeStatus,
)
from agentclaw.community.api.skill_parameter_service_factory import (
    SkillParameterServiceFactoryProtocol,
)
from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
from agentclaw.community.api.skill_set_management_service import (
    SkillSetManagementServiceProtocol,
)
from agentclaw.community.core.access.admin_scopes import skill_admin
from agentclaw.community.core.bot_collaborator.interceptor import (
    CollaboratorPermissionInterceptor,
    InterceptedResponse,
    InterceptorContext,
)
from agentclaw.community.core.bot_collaborator.interceptor.extractors import (
    PermissionParams,
)
from agentclaw.community.core.config_compose.teclaw_paths import (
    to_local_skill_engine_path,
)
from agentclaw.community.core.devices.services.device_context import (
    BotNotFoundError,
    DeviceNotBoundError,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillInvalidPackageError,
    LocalSkillRuntimeUnavailableError,
    LocalSkillTooLargeError,
)
from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    recover_legacy_skill_set_scope,
)
from agentclaw.community.core.skill_center.skill_metadata import (
    SkillManifestError,
    SkillManifestErrorCode,
)
from agentclaw.community.core.skill_center.upload_error_codes import (
    SkillUploadErrorCode,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
)
from agentclaw.community.plugin_api.sandbox_runtime import (
    SandboxRuntimeUnavailableError,
)
from fastapi import (
    APIRouter,
    HTTPException,
)

DEFAULT_ENGINE_TYPE = "openclaw"

_UPLOAD_SIGN_URL_EXPIRES = 7200

logger = get_logger()

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _legacy_runtime_projection(
    item: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    """Keep the BFF wire stable and only surface non-converged diagnostics."""

    projection = item.get("runtime_projection")
    if not isinstance(projection, dict):
        return "", None
    status = projection.get("status")
    if status == "PENDING":
        return "能力集状态已保存，运行环境暂未完成同步", projection
    if status == "DEGRADED":
        return "能力集状态已保存，但部分 Skill 未完成运行时收敛", projection
    return "", None


BOT_RUNTIME_UNAVAILABLE_MESSAGE = "当前 Bot 的运行环境暂不可用，请重新启动 Bot 后重试。"

_RUNTIME_UPLOAD_EXCEPTION_TYPES = (
    BotNotFoundError,
    DeviceNotBoundError,
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterTimeoutError,
    SandboxRuntimeUnavailableError,
    LocalSkillRuntimeUnavailableError,
    ConnectionError,
    TimeoutError,
)

_RUNTIME_UPLOAD_ERROR_PATTERNS = (
    re.compile(r"\b502\s+bad gateway\b"),
    re.compile(r"\b503\s+service unavailable\b"),
    re.compile(r"\b504\s+gateway timeout\b"),
    re.compile(
        r"(?:bad gateway|service unavailable|gateway timeout|connection refused|"
        r"connection reset|connecterror|readtimeout|requesterror|proxypass|"
        r"agentclawproxy|sandbox id is required|404 not found)"
    ),
    re.compile(r"\btimeout\b"),
    re.compile(r"(?:request|read|connect|operation|gateway).{0,24}timed? ?out"),
)

_MANIFEST_UPLOAD_ERROR_CODES = {
    SkillManifestErrorCode.MISSING_FRONTMATTER: SkillUploadErrorCode.MANIFEST_INVALID,
    SkillManifestErrorCode.INVALID_FRONTMATTER: SkillUploadErrorCode.MANIFEST_INVALID,
    SkillManifestErrorCode.INVALID_ENCODING: SkillUploadErrorCode.MANIFEST_ENCODING_INVALID,
    SkillManifestErrorCode.INVALID_PATH: SkillUploadErrorCode.PATH_INVALID,
    SkillManifestErrorCode.MISSING_NAME: SkillUploadErrorCode.NAME_MISSING,
    SkillManifestErrorCode.MISSING_DESCRIPTION: SkillUploadErrorCode.DESCRIPTION_MISSING,
    SkillManifestErrorCode.INVALID_NAME_TYPE: SkillUploadErrorCode.MANIFEST_FIELD_TYPE_INVALID,
    SkillManifestErrorCode.INVALID_DESCRIPTION_TYPE: SkillUploadErrorCode.MANIFEST_FIELD_TYPE_INVALID,
    SkillManifestErrorCode.EMPTY_NAME: SkillUploadErrorCode.NAME_EMPTY,
    SkillManifestErrorCode.EMPTY_DESCRIPTION: SkillUploadErrorCode.DESCRIPTION_EMPTY,
    SkillManifestErrorCode.NAME_TOO_LONG: SkillUploadErrorCode.NAME_TOO_LONG,
    SkillManifestErrorCode.DESCRIPTION_TOO_LONG: SkillUploadErrorCode.DESCRIPTION_TOO_LONG,
    SkillManifestErrorCode.NAME_DIRECTORY_MISMATCH: SkillUploadErrorCode.ROOT_NAME_MISMATCH,
    SkillManifestErrorCode.INVALID_CONFIG: SkillUploadErrorCode.MANIFEST_INVALID,
}

_PACKAGE_UPLOAD_ERROR_CODES = {
    "invalid_zip": SkillUploadErrorCode.ZIP_INVALID,
    "missing_skill_file": SkillUploadErrorCode.MANIFEST_MISSING,
    "multiple_skill_files": SkillUploadErrorCode.MANIFEST_MULTIPLE,
    "unsafe_file_path": SkillUploadErrorCode.PATH_INVALID,
    "duplicate_file_path": SkillUploadErrorCode.PATH_INVALID,
    "invalid_wrapper": SkillUploadErrorCode.FILE_OUTSIDE_ROOT,
    "invalid_encoding": SkillUploadErrorCode.MANIFEST_ENCODING_INVALID,
    "invalid_metadata": SkillUploadErrorCode.MANIFEST_INVALID,
    "wrapper_name_mismatch": SkillUploadErrorCode.ROOT_NAME_MISMATCH,
}

_PACKAGE_UPLOAD_MESSAGES = {
    "invalid_zip": "File is not a valid ZIP archive.",
    "missing_skill_file": "SKILL.md is required.",
    "multiple_skill_files": "Only one skill can be uploaded at a time.",
}

_FILESYSTEM_LAYOUT_ENGINES = frozenset(
    {"openclaw", "claude_code", "aicoding", "hermes"}
)


def _desktop_active_root_from_probe(
    probe: RuntimeLayoutProbeResult,
    *,
    legacy_fallback: Path,
) -> Path:
    """Use the Engine-owned active root, with one old-image fallback."""
    if probe.status is RuntimeLayoutProbeStatus.READY:
        resolved_layout = probe.evidence.get("resolved_layout")
        active_root = (
            resolved_layout.get("active_root")
            if isinstance(resolved_layout, dict)
            else None
        )
        if (
            not isinstance(active_root, str)
            or not active_root
            or not Path(active_root).is_absolute()
            or ".." in Path(active_root).parts
        ):
            raise RuntimeError("runtime layout probe omitted a valid active root")
        return Path(active_root)

    if (
        probe.status is RuntimeLayoutProbeStatus.NOT_CAPABLE
        and probe.evidence.get("reason") == "runtime_layout_probe_endpoint_absent"
    ):
        return legacy_fallback

    raise RuntimeError(
        "runtime layout probe did not resolve an authoritative active root: "
        f"status={probe.status.value} reason={probe.evidence.get('reason')}"
    )


def _upload_error_chain(error: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return tuple(chain)


def _resolve_underlying_manifest_error(
    error: BaseException,
) -> "SkillManifestError | None":
    """Return the first ``SkillManifestError`` in the error cause chain.

    The legacy upload path wraps a validator-level ``SkillManifestError`` twice
    before it reaches the HTTP adapter:

    ``SkillManifestError`` (precise code/message)
        -> ``SkillPackageInvalidError("invalid_metadata") from exc``
        -> ``LocalSkillInvalidPackageError("invalid_metadata") from exc``

    The most informative value lives on the original ``SkillManifestError``.
    Walk ``_upload_error_chain`` so the public classifier and response builder
    can reuse the precise ``SkillManifestErrorCode`` and validator message
    instead of collapsing every wrapped manifest failure to the generic
    ``invalid_metadata`` reason string.
    """
    if not isinstance(error, BaseException):
        return None
    for candidate in _upload_error_chain(error):
        if isinstance(candidate, SkillManifestError):
            return candidate
    return None


def _contains_zip_upload_error(error: BaseException | str) -> bool:
    candidates: tuple[BaseException | str, ...] = (
        _upload_error_chain(error) if isinstance(error, BaseException) else (error,)
    )
    for candidate in candidates:
        if isinstance(candidate, (zipfile.BadZipFile, zipfile.LargeZipFile)):
            return True
        message = str(candidate).lower()
        if any(
            marker in message
            for marker in (
                "badzipfile",
                "bad crc-32",
                "not a zip file",
                "central directory",
            )
        ):
            return True
    return False


def _classify_upload_validation_error(
    error: BaseException | str,
) -> SkillUploadErrorCode | None:
    """Classify known legacy validation failures before transport errors."""

    if isinstance(error, LocalSkillTooLargeError):
        return SkillUploadErrorCode.PACKAGE_TOO_LARGE
    if isinstance(error, LocalSkillInvalidPackageError):
        # ``LocalSkillInvalidPackageError`` wraps the validator-level cause
        # chain; a nested ``SkillManifestError`` carries the precise
        # ``SkillManifestErrorCode`` that already maps to a dedicated public
        # code. Look it up first so callers keep the actionable code (e.g.
        # ``NAME_MISSING`` instead of generic ``MANIFEST_INVALID``); fall back
        # to the reason-string table for cases without a manifest cause
        # (``unsafe_file_path``, ``invalid_zip``, the markdown-only fallback
        # producing ``metadata={}`` with no ``SkillManifestError``, ...).
        manifest_error = _resolve_underlying_manifest_error(error)
        if manifest_error is not None:
            try:
                manifest_code = SkillManifestErrorCode(manifest_error.code)
            except (TypeError, ValueError):
                manifest_code = None
            if manifest_code is not None:
                return _MANIFEST_UPLOAD_ERROR_CODES.get(manifest_code)
        return _PACKAGE_UPLOAD_ERROR_CODES.get(
            str(error), SkillUploadErrorCode.MANIFEST_INVALID
        )
    if _contains_zip_upload_error(error):
        return SkillUploadErrorCode.ZIP_INVALID

    manifest_code = getattr(error, "code", None)
    if isinstance(error, SkillManifestError) or manifest_code is not None:
        try:
            manifest_code = SkillManifestErrorCode(manifest_code)
        except (TypeError, ValueError):
            manifest_code = None
    if manifest_code is not None:
        return _MANIFEST_UPLOAD_ERROR_CODES.get(manifest_code)

    message = str(error)
    if message == "No files uploaded":
        return SkillUploadErrorCode.NO_FILES
    if message == "SKILL.md is required.":
        return SkillUploadErrorCode.MANIFEST_MISSING
    if message.startswith("Only one skill can be uploaded"):
        return SkillUploadErrorCode.MANIFEST_MULTIPLE
    if message == "Upload contains files outside the skill root directory.":
        return SkillUploadErrorCode.FILE_OUTSIDE_ROOT
    if message.startswith("Invalid upload path:"):
        return SkillUploadErrorCode.PATH_INVALID
    if message == "SKILL.md must contain required field: name.":
        return SkillUploadErrorCode.NAME_MISSING
    if message == "SKILL.md field 'name' cannot be empty.":
        return SkillUploadErrorCode.NAME_EMPTY
    if message == "SKILL.md must contain required field: description.":
        return SkillUploadErrorCode.DESCRIPTION_MISSING
    if message == "SKILL.md must be encoded as UTF-8 or GBK.":
        return SkillUploadErrorCode.MANIFEST_ENCODING_INVALID
    if message == "SKILL.md must contain valid frontmatter or legacy metadata.":
        return SkillUploadErrorCode.MANIFEST_INVALID
    if message.startswith("SKILL.md field ") and "must be a string" in message:
        return SkillUploadErrorCode.MANIFEST_FIELD_TYPE_INVALID
    if message.startswith("SKILL.md field 'description' cannot be empty"):
        return SkillUploadErrorCode.DESCRIPTION_EMPTY
    if message.startswith("SKILL.md field 'name' cannot exceed"):
        return SkillUploadErrorCode.NAME_TOO_LONG
    if message.startswith("SKILL.md field 'description' cannot exceed"):
        return SkillUploadErrorCode.DESCRIPTION_TOO_LONG
    if message.startswith("Skill folder name must match"):
        return SkillUploadErrorCode.ROOT_NAME_MISMATCH
    if message.startswith("Skill name ") and " is invalid" in message:
        return SkillUploadErrorCode.NAME_INVALID
    if message == "Skill name cannot contain underscore '_'":
        return SkillUploadErrorCode.NAME_INVALID
    if message.startswith("Skill name ") and " is reserved" in message:
        return SkillUploadErrorCode.NAME_RESERVED
    return None


def _is_runtime_upload_error(error: BaseException | str) -> bool:
    """Classify transport failures at the legacy HTTP adapter boundary."""

    if isinstance(error, BaseException):
        for current in _upload_error_chain(error):
            if isinstance(current, (zipfile.BadZipFile, zipfile.LargeZipFile)):
                return False
            if isinstance(current, DeviceAdapterHTTPStatusError):
                return 500 <= current.status_code < 600
            if isinstance(current, _RUNTIME_UPLOAD_EXCEPTION_TYPES):
                return True
            if any(
                pattern.search(str(current).lower())
                for pattern in _RUNTIME_UPLOAD_ERROR_PATTERNS
            ):
                return True
        return False
    return any(
        pattern.search(error.lower()) for pattern in _RUNTIME_UPLOAD_ERROR_PATTERNS
    )


def _normalize_upload_error_message(
    error_msg: str,
    *,
    error: BaseException | None = None,
) -> str:
    if _is_runtime_upload_error(error if error is not None else error_msg):
        return BOT_RUNTIME_UNAVAILABLE_MESSAGE
    return error_msg


def _build_upload_error_response(
    error: BaseException,
    *,
    include_upload_prefix: bool = False,
) -> UploadSkillResponse:
    """Build the backward-compatible failed-upload envelope once."""

    if isinstance(error, LocalSkillInvalidPackageError):
        # Surface the underlying ``SkillManifestError`` message when the
        # validator-level cause chain survives wrapping (frontmatter present
        # but missing/invalid fields). For cases without a manifest cause
        # (e.g. markdown-only SKILL.md producing ``metadata={}``), keep the
        # existing ``public_message`` fallback so callers still get the
        # documented "must define a valid name and description" hint.
        manifest_error = _resolve_underlying_manifest_error(error)
        if manifest_error is not None and str(manifest_error):
            raw_message = str(manifest_error)
        else:
            raw_message = _PACKAGE_UPLOAD_MESSAGES.get(
                str(error), error.public_message
            )
    elif isinstance(error, LocalSkillTooLargeError):
        raw_message = "Skill package is too large."
    else:
        raw_message = str(error)
    message = f"Upload failed: {raw_message}" if include_upload_prefix else raw_message
    validation_error_code = _classify_upload_validation_error(error)
    runtime_unavailable = validation_error_code is None and _is_runtime_upload_error(
        error
    )
    normalized_message = (
        message
        if validation_error_code is not None
        else _normalize_upload_error_message(message, error=error)
    )
    error_code = (
        SkillUploadErrorCode.RUNTIME_UNAVAILABLE
        if runtime_unavailable
        else validation_error_code or SkillUploadErrorCode.UPLOAD_FAILED
    )
    return UploadSkillResponse(
        success=False,
        error_code=error_code,
        message=normalized_message,
    )


def _build_uploaded_zip(uploaded_files: list[dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    paths = [f.get("relative_path") or f.get("filename") for f in uploaded_files]
    common = os.path.commonpath(paths) if len(paths) > 1 else ""
    if common and not any(p == common for p in paths):
        prefix_len = len(common.rstrip("/")) + 1
    else:
        prefix_len = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_info in uploaded_files:
            relative_path = file_info.get("relative_path") or file_info.get("filename")
            arcname = relative_path[prefix_len:] if prefix_len else relative_path
            zf.writestr(arcname, file_info.get("content", b""))
    return buf.getvalue()




def parse_tags(skill_tags) -> list[str]:
    """Parse tags from database (JSON string) to list."""
    if not skill_tags:
        return []
    if isinstance(skill_tags, list):
        return skill_tags
    try:
        return json.loads(skill_tags)
    except (json.JSONDecodeError, TypeError):
        return []


def _get_path_params(
    ctx: RequestContext,
    entity_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    bot_id: Optional[str] = None,
    engine_type: Optional[str] = None,
    *,
    bot_repo: BotRepository,
) -> tuple:
    """Resolve legacy locators to stable resource paths; actor stays in ctx."""
    return legacy_path_params(
        ctx, entity_id, entity_type, bot_id, engine_type, bot_repo=bot_repo
    )


def _get_skill_set_path_params(
    ctx: RequestContext,
    *,
    set_id: str,
    entity_id: str | None,
    entity_type: str | None,
    bot_id: str | None,
    engine_type: str | None,
    bot_repo: BotRepository,
    control_plane: SkillSetManagementServiceProtocol,
) -> tuple:
    """Recover the deprecated SkillSet Bot address before path normalization."""
    entity_id, bot_id = recover_legacy_skill_set_scope(
        set_id=set_id,
        actor_id=ctx.user_id,
        owner_id_hint=entity_id,
        bot_id_hint=bot_id,
        control_plane=control_plane,
    )
    return _get_path_params(
        ctx,
        entity_id,
        entity_type,
        bot_id,
        engine_type,
        bot_repo=bot_repo,
    )


def _resolve_teclaw_local_skill(resolver, bot_id: str, owner_id: str):
    """``(is_teclaw, local_skill_path_adapter)`` for a bot's local-skill device-fs.

    teclaw owns its local-skill files (engine-owned model): the DB records the
    minimal ``local://skills-local/<name>`` and the device-fs paths are expanded to
    the workspace namespace. Resolving the provider is best-effort — any failure
    (no binding yet, resolver error) falls back to the unchanged non-teclaw
    behavior ``(False, None)`` so arca/baas/local paths are untouched.
    """
    try:
        if resolver.resolve_for_bot(bot_id, owner_id).provider == "teclaw":
            return True, to_local_skill_engine_path
    except Exception as e:
        logger.info(f"[skills] provider resolve skipped ({e}); treating as non-teclaw")
    return False, None


def _get_skill_by_id_or_link_name(service, skill_id: str, *bolt_ids: str | None):
    """Look up a skill by numeric ID or link_name across likely bot scopes.

    README requests may arrive without the real bot id in the request context.
    Numeric IDs are globally unique, while link_name lookup is bot-scoped; try
    the effective/request bot candidates first and then a global lookup.
    """
    if skill_id.isdigit():
        skill = service.get_skill(skill_id)
        if skill:
            return skill

    seen: set[str | None] = set()
    for bolt_id in (*bolt_ids, None):
        if bolt_id in seen:
            continue
        seen.add(bolt_id)
        skill = service.get_skill_by_link_name(skill_id, bolt_id=bolt_id)
        if skill:
            return skill
    return None


_EXTRA_FIELDS = [
    "is_public",
    "git_path",
    "is_builtin",
    "gmt_created",
    "gmt_modified",
    "risk_tags",
    "mcp_dependencies",
    "user_id",
    "env",
    "bolt_id",
    "category",
    "skill_uuid",
]


def _merge_skill_fields(sc_skills: list[dict], name_skill_map: dict[str, dict]) -> None:
    """将 ac_skill 的额外字段 merge 进 SC 返回的技能数据。"""
    for skill in sc_skills:
        if not isinstance(skill, dict):
            continue
        local = name_skill_map.get(skill.get("name", ""))
        if not local:
            continue
        skill["id"] = local.get("id")
        for field in _EXTRA_FIELDS:
            if field in local:
                skill[field] = local[field]


async def _extract_skill_mutation_permission(
    skill_id: str,
    ctx: InterceptorContext,
) -> PermissionParams:
    """Resolve the persisted Bot identity used for local Skill mutations."""
    if not skill_id or ctx.injector is None:
        return PermissionParams()
    try:
        service = ctx.injector.get(SkillServiceFactoryProtocol).create()
        skill = service.get_skill(skill_id)
    except Exception:
        return PermissionParams()
    if not skill:
        return PermissionParams()
    return PermissionParams(
        bot_id=skill.get("bolt_id"),
        owner_id=skill.get("user_id"),
    )


class _FailClosedSkillMutationPermissionInterceptor(
    CollaboratorPermissionInterceptor,
):
    """Never turn a collaborator permission lookup failure into mutation access."""

    def __init__(self, *args, authorization_state_key: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._authorization_state_key = authorization_state_key

    async def before(
        self,
        ctx: InterceptorContext,
    ) -> InterceptorContext | None:
        actor_id = ctx.user.staffId if ctx.user else None
        # Preserve the pre-existing global Skill admin capability; it is not a
        # Bot-collaborator grant and therefore must not depend on that service.
        if actor_id and actor_id in skill_admin():
            ctx.metadata["permission_level"] = "SKILL_ADMIN"
            return ctx

        result = await super().before(ctx)
        if result is None:
            return None
        actor_id = ctx.metadata.get("_log_user_id")
        owner_id = ctx.metadata.get("_log_owner_id")
        if actor_id != owner_id or ctx.metadata.get("permission_level") == "OWNER":
            if not ctx.metadata.get("permission_level"):
                ctx.response = InterceptedResponse(
                    success=False,
                    message="协作者权限服务暂不可用",
                    error_code=503,
                )
                return None
            if ctx.request is not None:
                setattr(ctx.request.state, self._authorization_state_key, True)
            for value in ctx.route_kwargs.values():
                if isinstance(value, RequestContext):
                    value.metadata[self._authorization_state_key] = True
        return result


async def extract_from_skill_id(skill_id: str, ctx) -> PermissionParams:
    """从 skill_id 查询 bot_id 和 owner_id.

    用于 CollaboratorPermissionInterceptor 提取权限参数。

    Args:
        skill_id: 技能 ID（由拦截器通过表达式注入）
        ctx: 拦截器上下文，ctx.injector 用于解析服务
    """
    if not skill_id:
        return PermissionParams()

    if ctx.injector is None:
        return PermissionParams()

    try:
        factory = ctx.injector.get(SkillServiceFactoryProtocol)
        service = factory.create()
    except Exception:
        return PermissionParams()

    try:
        skill = service.get_skill(skill_id)
        if not skill:
            return PermissionParams()

        return PermissionParams(
            bot_id=skill.get("bolt_id"),  # Skill.bolt_id 即 bot_id
            owner_id=skill.get("user_id"),  # Skill.user_id 即 owner_id
        )
    except Exception:
        return PermissionParams()


class _SkillParameterPermissionInterceptor(CollaboratorPermissionInterceptor):
    """Fail closed for owner-backed parameter access when auth is unavailable."""

    async def before(
        self,
        ctx: InterceptorContext,
    ) -> InterceptorContext | None:
        result = await super().before(ctx)
        if result is None:
            return None
        actor_id = ctx.metadata.get("_log_user_id")
        owner_id = ctx.metadata.get("_log_owner_id")
        if actor_id != owner_id and not ctx.metadata.get("permission_level"):
            ctx.response = InterceptedResponse(
                success=False,
                message="协作者权限服务暂不可用",
                error_code=503,
            )
            return None
        return result


def _resolve_parameter_bot(
    *,
    skill: dict[str, Any],
    requested_bot_id: str,
    requested_entity_id: str,
    bot_repo: BotRepository,
) -> dict[str, Any]:
    """Resolve the trusted Bot identity that owns a Skill parameter file.

    ``SkillParameterServiceFactory`` uses its ``user_id`` argument to locate the
    Bot's active device binding.  That identity must therefore be the Bot owner,
    not the authenticated actor (who may be an ADMIN collaborator).

    The requested Bot ID is used only as a lookup key; ownership always comes
    from the Bot row. Historical ``Skill.user_id`` values may contain a
    collaborator, so they must never participate in device-owner resolution.
    Bot-private Skills are scoped by ``bolt_id``. Shared market Skills are
    identified by their trusted source scheme (``git://`` or ``center://``)
    and remain usable across Bots regardless of historical ``bolt_id`` values.
    """

    bot = bot_repo.get_by_id_and_entity(requested_bot_id, requested_entity_id)
    if not isinstance(bot, dict):
        raise HTTPException(status_code=404, detail="Bot not found")
    bot_owner_id = str(bot.get("owner_id") or "")
    if not bot_owner_id:
        raise HTTPException(
            status_code=409,
            detail="Bot ownership metadata is incomplete",
        )

    skill_bot_id = str(skill.get("bolt_id") or "")
    git_path = str(skill.get("git_path") or "")
    is_shared_market_skill = git_path.startswith(("git://", "center://"))
    if is_shared_market_skill:
        return bot

    if not skill_bot_id:
        raise HTTPException(
            status_code=409,
            detail="Skill Bot metadata is incomplete",
        )
    if requested_bot_id != skill_bot_id:
        raise HTTPException(
            status_code=409,
            detail="Skill does not belong to the requested Bot",
        )
    return bot


async def _extract_parameter_permission(
    *,
    bot_id: str,
    entity_id: str,
    ctx,
):
    """Resolve parameter authorization from the exact Bot identity scope."""

    from agentclaw.community.core.bot_collaborator.interceptor.extractors import (
        PermissionParams,
    )

    bot_repo = ctx.injector.get(BotRepository) if ctx.injector is not None else None
    bot = (
        bot_repo.get_by_id_and_entity(bot_id, entity_id)
        if bot_repo is not None
        else None
    )
    owner_id = str(bot.get("owner_id") or "") if isinstance(bot, dict) else ""
    # A non-empty sentinel prevents the generic interceptor from falling back
    # to its bot_id-only legacy lookup. The route will return the precise 404.
    return PermissionParams(
        bot_id=bot_id,
        owner_id=owner_id or "__parameter_bot_not_found__",
    )


async def _create_skill_parameter_service(
    *,
    parameter_service_factory: SkillParameterServiceFactoryProtocol,
    bot_id: str,
    owner_id: str,
):
    """Create the device-backed parameter service with a structured no-binding error."""

    try:
        return await parameter_service_factory.create(
            bot_id=bot_id,
            user_id=owner_id,
        )
    except DeviceNotBoundError as exc:
        raise HTTPException(
            status_code=409,
            detail="Bot has no active device",
        ) from exc
