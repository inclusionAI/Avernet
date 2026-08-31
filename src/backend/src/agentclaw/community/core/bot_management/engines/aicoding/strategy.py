"""Provisioning rules for coding engines.

All knowledge about coding template types, relay default envs and CodeFuse token
provisioning lives here instead of being duplicated in bot/device/template
services.
"""

from __future__ import annotations

import json
from copy import deepcopy
import threading
import uuid
from typing import Any, Dict, TYPE_CHECKING

from agentclaw.community.core.bot_management.capabilities import (
    is_template_factory_config,
)
from agentclaw.community.core.bot_management.errors import (
    BotCombinationUnsupportedError,
    BotTemplateInvalidError,
)
from agentclaw.community.core.workspace.runtime_identity import (
    claude_code_uses_aicoding_runtime,
)

from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.utils import secret_utils
from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant
from agentclaw.community.log import get_logger

from ..provisioning import (
    BotCreateTemplateValidationMode,
    BotProvisioningContext,
    EngineProvisioningStrategy,
    PreparedBotCreate,
    to_internal_template_config,
)


# Legacy coding template types.  This is only used for old call sites that
# identify coding bots by template_type (applicationCoding/personalCoding).
# Template-factory templates (normalCC/architect/user-created templates) must be
# detected from active_engine + template_config snapshot, not by extending this
# set with template keys.
CODING_TEMPLATE_TYPES = frozenset({"applicationCoding", "personalCoding"})
AICODING_ENGINE_TYPE = "aicoding"
CLAUDE_CODE_ENGINE_TYPE = "claude_code"
TEMPLATE_CONFIG_CONSUMING_ENGINES = frozenset(
    {AICODING_ENGINE_TYPE, CLAUDE_CODE_ENGINE_TYPE}
)
LEGACY_BOT_TYPE_ENV_MAP = {
    "personalCoding": "personal",
    "applicationCoding": "application",
}
_THETA_KEY_PATH = ("bot_template_config", "ext_config", "thetaKey")
_ENCRYPTED_VALUE_PREFIX = "enc:v1:"
_AICODING_RESTART_MARKER_KEY = "_aicoding_restart"
_AICODING_RESTART_RESYNC_KEY = "resync_authorization"
logger = get_logger()




if TYPE_CHECKING:
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        BotRuntimeProjectorProtocol,
    )


def _validate_application_coding_config(
    value: Dict[str, Any] | None,
) -> Dict[str, Any] | None:
    """Validate the stable outer contract while preserving extensions.

    Input must already be detached by ``to_internal_template_config``. An empty
    config is rejected; unknown keys survive as engine-owned extensions.
    """
    if value is None:
        return None
    if not value:
        raise BotTemplateInvalidError(
            "applicationCoding template_config must not be empty"
        )
    expected_types: dict[str, type | tuple[type, ...]] = {
        "devflow_workflow": (str, dict),
        "yuque_kb_repos": list,
        "code_repos": list,
        "bot_template_config": dict,
        "token": str,
    }
    for key, expected in expected_types.items():
        if key not in value:
            continue
        field_value = value[key]
        if not isinstance(field_value, expected):
            raise BotTemplateInvalidError(
                f"applicationCoding template_config.{key} has invalid type"
            )
        if key == "token" and not field_value.strip():
            raise BotTemplateInvalidError(
                "applicationCoding template_config.token cannot be empty"
            )
    return value


class AicodingBaasEngineBucketResolver:
    """BaaS bucket resolver contributed by the aicoding engine module."""

    def resolve_baas_engine_bucket(
        self,
        *,
        normalized_engine_type: str,
        template_type: str | None,
    ) -> str | None:
        return AicodingProvisioningStrategy.resolve_baas_engine_bucket(
            active_engine=normalized_engine_type,
            template_type=template_type,
        )

    def resolve_default_capabilities_engine_bucket(
        self,
        *,
        normalized_engine_type: str,
        template_type: str | None,
    ) -> str | None:
        return self.resolve_baas_engine_bucket(
            normalized_engine_type=normalized_engine_type,
            template_type=template_type,
        )


class AicodingProvisioningStrategy(EngineProvisioningStrategy):
    """Provisioning strategy shared by ``aicoding`` and ``claude_code`` engines."""

    def __init__(self, engine_type: str) -> None:
        self._engine_type = engine_type

    @property
    def engine_type(self) -> str:
        return self._engine_type

    def prepare_create(
        self,
        *,
        engine_type: str,
        engine_properties: Dict[str, Any],
        bot_type: str,
        deployment_mode: str,
        space_kind: str,
        template_validation_mode: BotCreateTemplateValidationMode = (
            BotCreateTemplateValidationMode.LEGACY
        ),
    ) -> PreparedBotCreate:
        """Parse and validate application-coding create input (single owner).

        The one implementation behind both input shapes: the public
        ``engine_properties.template`` contract and the legacy
        ``template_type="applicationCoding"`` normalized by the create flow.
        Combination gates keep their historical order, error types and messages
        so the HTTP mappings answer identically; server-managed-field rejection
        follows the caller's validation mode.
        """
        if not engine_properties:
            return PreparedBotCreate()

        # Envelope integrity for keys this engine owns. The public schema's
        # ``extra="forbid"`` cannot guard direct Core-level spec construction,
        # so unknown keys fail here instead of being silently ignored.
        unknown_keys = set(engine_properties) - {"template"}
        if unknown_keys:
            raise BotTemplateInvalidError(
                f"unsupported engine_properties fields: {sorted(unknown_keys)}"
            )
        if "template" not in engine_properties:
            raise BotTemplateInvalidError("engine_properties.template is required")

        # Historical combination gates, in their historical order. The gate set
        # and messages are mirrored (production-dead) in
        # ``bot_inventory/policies/combo_policy.py``
        # ``assert_application_coding_create`` — keep the two in sync, or
        # single-source them once bot_management may depend on bot_inventory.
        if deployment_mode != "cloud":
            raise BotCombinationUnsupportedError("application coding is cloud-only")
        if engine_type != CLAUDE_CODE_ENGINE_TYPE:
            # The strategy class is registered for both engine types, but
            # application-coding creation stays claude_code-only.
            raise BotCombinationUnsupportedError(
                f"application coding does not support engine: {engine_type}"
            )
        if bot_type != "personal":
            raise BotCombinationUnsupportedError(
                "application coding bot must be personal"
            )
        if space_kind != "personal":
            raise BotCombinationUnsupportedError(
                "application coding is personal-space only"
            )

        template = engine_properties["template"]
        if template is None:
            # Core-only legacy compatibility shape: the key's presence is the
            # application-coding intent, ``None`` the intentionally-omitted
            # config. The public schema requires a non-empty dict, so callers
            # cannot express this through HTTP.
            return PreparedBotCreate(
                template_type="applicationCoding",
                template_config=None,
                requires_workspace_hosting=True,
            )
        if not template:
            # Byte-identical to the historical ladder: only genuinely empty
            # payloads are rejected. Truthy non-dict values (legacy internal
            # callers forwarding raw JSON ``template_config``) keep the
            # historical pass-through — the expected-type checks and
            # reserved-field scan below treat them exactly as before.
            raise BotTemplateInvalidError(
                "applicationCoding template_config must not be empty"
            )
        sanitized = to_internal_template_config(
            template,
            reject_server_managed_fields=(
                template_validation_mode is BotCreateTemplateValidationMode.PUBLIC
            ),
        )
        return PreparedBotCreate(
            template_type="applicationCoding",
            template_config=_validate_application_coding_config(sanitized),
            requires_workspace_hosting=True,
        )

    def resolve_bot_engine(self, bot: dict[str, Any]) -> str | None:
        active_engine = bot.get("active_engine")
        active_engine = active_engine if isinstance(active_engine, str) else None
        if self.should_use_aicoding_runtime_engine(
            active_engine=active_engine,
            template_type=bot.get("template_type"),
        ):
            return AICODING_ENGINE_TYPE
        return active_engine

    @staticmethod
    def normalize_engine_type(
        engine_type: str | None, *, default: str = "openclaw"
    ) -> str:
        return (engine_type or default).strip().lower().replace("-", "_")

    @staticmethod
    def is_coding_template(template_type: str | None) -> bool:
        return template_type in CODING_TEMPLATE_TYPES

    @classmethod
    def should_use_aicoding_runtime_engine(
        cls,
        *,
        active_engine: str | None,
        template_type: str | None,
    ) -> bool:
        """Whether this bot should use the aicoding runtime engine."""
        return claude_code_uses_aicoding_runtime(
            active_engine=active_engine,
            template_type=template_type,
        )

    @classmethod
    def should_use_aicoding_baas_bucket(
        cls,
        *,
        active_engine: str | None,
        template_type: str | None,
    ) -> bool:
        """Whether this context should select the aicoding BaaS bucket."""
        return cls.should_use_aicoding_runtime_engine(
            active_engine=active_engine,
            template_type=template_type,
        )

    @classmethod
    def resolve_baas_engine_bucket(
        cls,
        *,
        active_engine: str | None,
        template_type: str | None,
    ) -> str | None:
        """Return the aicoding BaaS bucket override, if this engine owns it."""
        if cls.should_use_aicoding_baas_bucket(
            active_engine=active_engine,
            template_type=template_type,
        ):
            return AICODING_ENGINE_TYPE
        return None

    has_template_factory_config = staticmethod(is_template_factory_config)

    @classmethod
    def consumes_template_config(
        cls,
        template_type: str | None,
        *,
        active_engine: str | None = None,
        template_config: dict[str, Any] | None = None,
    ) -> bool:
        # Keep historical/built-in template types working even where legacy call
        # sites only know template_type.  For non-legacy template types, the
        # active engine must be a template-config-consuming engine and the saved
        # snapshot must carry full template identity (template_key and template_uid).
        # This prevents arbitrary plain dicts from being treated as governed template
        # config while still allowing normalCC / architect / user-created / future
        # template types to consume their resolved snapshot without backend enum
        # updates.
        if template_type in CODING_TEMPLATE_TYPES:
            return True
        has_template_identity = isinstance(template_config, dict) and bool(
            template_config.get("template_key") and template_config.get("template_uid")
        )
        normalized_engine = cls.normalize_engine_type(active_engine, default="")
        return (
            normalized_engine in TEMPLATE_CONFIG_CONSUMING_ENGINES
            and has_template_identity
        )

    def build_extra_envs(self, ctx: BotProvisioningContext) -> Dict[str, str] | None:
        template_type = ctx.template_type
        template_config = ctx.template_config or {}
        if not self.consumes_template_config(
            template_type,
            active_engine=ctx.active_engine,
            template_config=template_config,
        ):
            return None
        envs: Dict[str, str] = {}

        # Historical template types expose stable legacy bot-type values, while
        # template-factory templates expose their template_type verbatim so new
        # template types do not require backend enum/map changes. Service bots
        # already convey their service identity through the startup command, so
        # skip this legacy template-kind env for that domain.
        if template_type and ctx.bot_type != "service":
            envs["BOT_TYPE"] = LEGACY_BOT_TYPE_ENV_MAP.get(template_type, template_type)

        devflow_workflow = template_config.get("devflow_workflow", "")
        if isinstance(devflow_workflow, dict):
            aix_devflow_info = devflow_workflow.get("path", "")
        elif isinstance(devflow_workflow, str):
            aix_devflow_info = devflow_workflow
        else:
            aix_devflow_info = ""
        if aix_devflow_info:
            envs["AIX_DEVFLOW_INFO"] = aix_devflow_info

        legacy_repo_keys = ("backend_repo", "frontend_repo", "lib_repo")
        repo_keys = list(legacy_repo_keys)
        if is_template_factory_config(template_config):
            repo_keys.extend(["repos", "init_repos", "application_repo_urls"])

        repo_list: list[str] = []
        for repo_key in repo_keys:
            repos = template_config.get(repo_key)
            if not isinstance(repos, list):
                continue
            for repo in repos:
                if (
                    isinstance(repo, str)
                    and repo_key not in legacy_repo_keys
                    and repo.strip()
                ):
                    repo_list.append(repo.strip())
                elif isinstance(repo, dict):
                    for url_key in ("repo_url", "url", "git_url", "ssh_url"):
                        repo_url = repo.get(url_key)
                        if isinstance(repo_url, str) and repo_url.strip():
                            repo_list.append(repo_url.strip())
                            break
        if repo_list:
            envs["GIT_ADDRESSES"] = json.dumps(repo_list, ensure_ascii=False)

        # Bug-fix semantics: both applicationCoding and personalCoding may set
        # relay default model/runtime.  Template-factory normalCC/architect bots
        # reuse the same runtime configuration consumption via template_config.
        model = template_config.get("model")
        if isinstance(model, str) and model.strip():
            envs["RELAY_DEFAULT_MODEL"] = model.strip()
        runtime = template_config.get("runtime")
        if isinstance(runtime, str) and runtime.strip():
            envs["RELAY_DEFAULT_RUNTIME"] = runtime.strip()

        return envs or None

    @staticmethod
    def _get_template_value(
        template_config: dict[str, Any] | None,
        path: tuple[str, ...],
    ) -> Any | None:
        value: Any = template_config
        for key in path:
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value

    def build_extra_properties(
        self,
        ctx: BotProvisioningContext,
        *,
        secret_resolver: SecretResolver | None = None,
        theta_master_key_secret: str = "",
    ) -> dict[str, Any] | None:
        """Resolve AICoding-owned template fields into a generic runtime envelope.

        ``theta_master_key_secret`` is the deployment-configured secret-registry
        name (neutral default empty): without it (community / singlebox /
        unconfigured env) the hook is a no-op so downstream keeps the legacy
        egress-rule fallback.
        """
        if (
            ctx.active_engine not in TEMPLATE_CONFIG_CONSUMING_ENGINES
            and ctx.template_type not in CODING_TEMPLATE_TYPES
        ):
            logger.info(
                "[AicodingProvisioningStrategy.build_extra_properties] skipped: "
                "bot_id=%s, active_engine=%s, template_type=%s, "
                "reason=unsupported_engine_or_template",
                ctx.bot_id,
                ctx.active_engine,
                ctx.template_type,
            )
            return None

        stored = self._get_template_value(ctx.template_config, _THETA_KEY_PATH)
        has_theta_key = isinstance(stored, str) and bool(stored)
        has_encrypted_theta_key = isinstance(stored, str) and stored.startswith(
            _ENCRYPTED_VALUE_PREFIX
        )
        logger.info(
            "[AicodingProvisioningStrategy.build_extra_properties] input: "
            "bot_id=%s, active_engine=%s, template_type=%s, "
            "has_template_config=%s, has_theta_key=%s, "
            "has_encrypted_theta_key=%s, has_secret_resolver=%s, "
            "has_theta_master_key_secret=%s",
            ctx.bot_id,
            ctx.active_engine,
            ctx.template_type,
            isinstance(ctx.template_config, dict),
            has_theta_key,
            has_encrypted_theta_key,
            secret_resolver is not None,
            bool(theta_master_key_secret),
        )
        if secret_resolver is None:
            logger.warning(
                "[AicodingProvisioningStrategy.build_extra_properties] fallback: "
                "bot_id=%s, reason=secret_resolver_missing",
                ctx.bot_id,
            )
            return None
        if not theta_master_key_secret:
            logger.warning(
                "[AicodingProvisioningStrategy.build_extra_properties] fallback: "
                "bot_id=%s, reason=theta_master_key_secret_name_missing",
                ctx.bot_id,
            )
            return None
        if not has_encrypted_theta_key:
            logger.warning(
                "[AicodingProvisioningStrategy.build_extra_properties] fallback: "
                "bot_id=%s, reason=encrypted_theta_key_missing_or_invalid",
                ctx.bot_id,
            )
            return None

        ciphertext = stored[len(_ENCRYPTED_VALUE_PREFIX) :]
        if not ciphertext:
            logger.warning(
                "[AicodingProvisioningStrategy.build_extra_properties] fallback: "
                "bot_id=%s, reason=theta_ciphertext_empty",
                ctx.bot_id,
            )
            return None

        try:
            secret = secret_resolver.get_secret(theta_master_key_secret)
            master_key = getattr(secret, "secret_value", secret) if secret else None
            if not master_key:
                logger.warning(
                    "[AicodingProvisioningStrategy.build_extra_properties] fallback: "
                    "bot_id=%s, reason=theta_master_secret_empty",
                    ctx.bot_id,
                )
                return None
            api_key = secret_utils.symmetric_decrypt(ciphertext, str(master_key))
        except Exception as exc:
            logger.warning(
                "[AicodingProvisioningStrategy.build_extra_properties] fallback: "
                "bot_id=%s, reason=theta_key_decrypt_failed, error_type=%s",
                ctx.bot_id,
                type(exc).__name__,
            )
            return None
        if not isinstance(api_key, str) or not api_key:
            logger.warning(
                "[AicodingProvisioningStrategy.build_extra_properties] fallback: "
                "bot_id=%s, reason=decrypted_theta_key_empty",
                ctx.bot_id,
            )
            return None

        logger.info(
            "[AicodingProvisioningStrategy.build_extra_properties] resolved: "
            "bot_id=%s, custom_outbound_key_resolved=True",
            ctx.bot_id,
        )
        return {"outbound_api_key": api_key}

    def should_encrypt_template_token(self, ctx: BotProvisioningContext) -> bool:
        return self.consumes_template_config(
            ctx.template_type,
            active_engine=ctx.active_engine,
            template_config=ctx.template_config,
        )

    def extract_runtime_token(self, ctx: BotProvisioningContext) -> str | None:
        if not self.consumes_template_config(
            ctx.template_type,
            active_engine=ctx.active_engine,
            template_config=ctx.template_config,
        ):
            return None
        token = (ctx.template_config or {}).get("token")
        return token if isinstance(token, str) and token else None

    def uses_adapter_chat_session_lifecycle(self, ctx: BotProvisioningContext) -> bool:
        # AICoding / claude_code chat sessions are created lazily by the relay,
        # so ExpertChat must not call Adapter /api/sessions create/check/delete.
        return False

    def build_local_chat_session_key(
        self, ctx: BotProvisioningContext, *, user_id: str
    ) -> str:
        """Build the relay-owned local chat session key for coding engines.

        Contract:
        ``src/backend/specs/2026-08-10-expert-chat-service-bot-session-keys/spec.md``.
        ``claude_code`` keeps the normalCC legacy form while ``aicoding`` uses
        the service-bot form parsed by teamclaw-aicoding-relay.
        """
        if self._engine_type == CLAUDE_CODE_ENGINE_TYPE:
            return f"session:{uuid.uuid4()}:user:{user_id}"
        return f"user:{user_id}:session:{uuid.uuid4()}:agent:{ctx.bot_id}"

    @staticmethod
    def _template_version_id(config: Any) -> int | None:
        """Return a comparable template version id from a saved snapshot."""
        if not isinstance(config, dict):
            return None
        value = config.get("template_version_id")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _has_restart_resync_marker(template_config: Any) -> bool:
        if not isinstance(template_config, dict):
            return False
        marker = template_config.get(_AICODING_RESTART_MARKER_KEY)
        return isinstance(marker, dict) and bool(
            marker.get(_AICODING_RESTART_RESYNC_KEY)
        )

    @staticmethod
    def _with_restart_resync_marker(
        template_config: dict[str, Any], *, template_version_id: int | None
    ) -> dict[str, Any]:
        updated = deepcopy(template_config)
        marker = updated.get(_AICODING_RESTART_MARKER_KEY)
        if not isinstance(marker, dict):
            marker = {}
        marker[_AICODING_RESTART_RESYNC_KEY] = True
        if template_version_id is not None:
            marker["template_version_id"] = template_version_id
        marker.setdefault("source", "restart_template_update")
        updated[_AICODING_RESTART_MARKER_KEY] = marker
        return updated

    @staticmethod
    def _clear_restart_resync_marker(
        ctx: BotProvisioningContext, *, template_service: Any
    ) -> None:
        if template_service is None:
            return
        current = template_service.get_template_config(ctx.bot_id)
        if (
            not isinstance(current, dict)
            or not AicodingProvisioningStrategy._has_restart_resync_marker(current)
        ):
            return
        updated = deepcopy(current)
        updated.pop(_AICODING_RESTART_MARKER_KEY, None)
        template_service.update_template(
            bot_id=ctx.bot_id,
            template_config=updated,
            template_type=ctx.template_type,
            active_engine=ctx.active_engine,
        )
        logger.info(
            "[aicoding.restart] cleared persisted restart resync marker: bot_id=%s",
            ctx.bot_id,
        )

    def apply_restart_extra_configs(
        self,
        ctx: BotProvisioningContext,
        extra_configs: dict[str, Any] | None,
        *,
        template_service: Any,
    ) -> None:
        """Apply AICoding/Claude Code values from generic restart extras."""
        active_engine = self.normalize_engine_type(ctx.active_engine, default="")
        if active_engine not in TEMPLATE_CONFIG_CONSUMING_ENGINES:
            return
        if not isinstance(extra_configs, dict):
            return
        candidate = extra_configs.get("template_config")
        if not isinstance(candidate, dict):
            return

        stored_config = template_service.get_template_config(ctx.bot_id) or {}
        incoming_version = self._template_version_id(candidate)
        stored_version = self._template_version_id(stored_config)
        if incoming_version is None or incoming_version < 0:
            return
        if stored_version is not None and incoming_version <= stored_version:
            return

        persisted_config = candidate
        if extra_configs.get("confirmed_template_update"):
            persisted_config = self._with_restart_resync_marker(
                candidate, template_version_id=incoming_version
            )

        template_service.update_template(
            bot_id=ctx.bot_id,
            template_config=persisted_config,
            template_type=ctx.template_type,
            active_engine=ctx.active_engine,
        )
        logger.info(
            "[aicoding.restart] persisted newer template snapshot: "
            "bot_id=%s old_version=%s new_version=%s resync_marker=%s",
            ctx.bot_id,
            stored_version,
            incoming_version,
            persisted_config is not candidate,
        )

    def refresh_restart_authorization(
        self,
        ctx: BotProvisioningContext,
        bot: Dict[str, Any],
        extra_configs: Dict[str, Any] | None,
        *,
        mcp_sync: Any = None,
        skill_set_factory: Any = None,
        runtime_reconciler: "BotRuntimeProjectorProtocol | None" = None,
        template_service: Any = None,
    ) -> bool:
        """Refresh AICoding restart authorization and runtime skill symlinks.

        This is intentionally AICoding-owned: only a confirmed template update
        opts in. The refresh is fire-and-forget and best-effort so restart is
        never blocked.
        """
        should_resync = (
            isinstance(extra_configs, dict)
            and bool(extra_configs.get("confirmed_template_update"))
        ) or self._has_restart_resync_marker(ctx.template_config)
        if not should_resync:
            logger.info(
                "[aicoding.restart] skip authorization/runtime resync: "
                "confirmed_template_update is not set for bot_id=%s",
                ctx.bot_id,
            )
            return False

        effective_entity_id = str(bot.get("entity_id") or ctx.owner_id or "")
        effective_entity_type = str(bot.get("entity_type") or "staff")
        effective_engine = ctx.active_engine or self.engine_type

        def _run() -> None:
            import asyncio

            refresh_succeeded = True
            skill_set_service = None
            logger.info(
                "[aicoding.restart] begin restart resync: bot_id=%s, engine_type=%s, entity_id=%s, entity_type=%s",
                ctx.bot_id, effective_engine, effective_entity_id, effective_entity_type,
            )
            if skill_set_factory is not None:
                try:
                    skill_set_service = skill_set_factory.create(
                        user_id=effective_entity_id,
                        entity_id=effective_entity_id,
                        bot_id=ctx.bot_id,
                        entity_type=effective_entity_type,
                        engine_type=effective_engine,
                    )
                    logger.info(
                        "[aicoding.restart] skill set service created for restart resync: bot_id=%s, engine_type=%s",
                        ctx.bot_id, effective_engine,
                    )
                except Exception as skill_error:
                    refresh_succeeded = False
                    logger.error(
                        "[aicoding.restart] skill set service create error: "
                        "bot_id=%s, engine_type=%s, error=%s; continue with remaining restart resync steps",
                        ctx.bot_id, effective_engine, skill_error,
                        exc_info=True,
                    )

            if runtime_reconciler is not None:
                try:
                    from agentclaw.community.core.skill_center.runtime_projection_contract import (
                        ProjectionScope,
                    )

                    async def _do_mcp_projection() -> None:
                        await runtime_reconciler.project_mcp_and_cli(
                            bot_id=ctx.bot_id,
                            owner_id=effective_entity_id,
                            scope=ProjectionScope(mcp=True, claim_all_mcp=True),
                        )

                    asyncio.run(_do_mcp_projection())
                    logger.info(
                        "[aicoding.restart] MCP projection resync succeeded: "
                        "bot_id=%s, engine_type=%s",
                        ctx.bot_id, effective_engine,
                    )
                except Exception as mcp_error:
                    refresh_succeeded = False
                    logger.error(
                        "[aicoding.restart] MCP projection resync failed: "
                        "bot_id=%s, engine_type=%s, error=%s; continue with skill sync",
                        ctx.bot_id, effective_engine, mcp_error,
                        exc_info=True,
                    )
            else:
                refresh_succeeded = False
                logger.error(
                    "[aicoding.restart] MCP projection resync skipped: "
                    "bot_id=%s, engine_type=%s, error=%s; continue with skill sync",
                    ctx.bot_id, effective_engine,
                    "runtime reconciler unavailable",
                )

            if skill_set_service is not None:
                try:
                    logger.info(
                        "[aicoding.restart] start skill symlink resync after MCP stage: bot_id=%s, engine_type=%s",
                        ctx.bot_id, effective_engine,
                    )
                    # ``project_skills`` is async so that both halves of the
                    # capability boundary are awaited the same way; this is a
                    # worker thread with no running loop, so it bridges the
                    # same way ``_do_mcp_projection`` above does.
                    skill_synced = bool(asyncio.run(skill_set_service.project_skills()))
                    if skill_synced:
                        logger.info(
                            "[aicoding.restart] skill symlink sync succeeded: "
                            "bot_id=%s, engine_type=%s",
                            ctx.bot_id, effective_engine,
                        )

                    if not skill_synced:
                        refresh_succeeded = False
                        logger.error(
                            "[aicoding.restart] skill symlink sync failed: "
                            "bot_id=%s, engine_type=%s; continue without clearing restart marker",
                            ctx.bot_id, effective_engine,
                        )
                except Exception as skill_error:
                    refresh_succeeded = False
                    logger.error(
                        "[aicoding.restart] skill symlink sync error: "
                        "bot_id=%s, engine_type=%s, error=%s; continue without clearing restart marker",
                        ctx.bot_id, effective_engine, skill_error,
                        exc_info=True,
                    )

            if refresh_succeeded and should_resync and template_service is not None:
                try:
                    self._clear_restart_resync_marker(
                        ctx, template_service=template_service
                    )
                except Exception as clear_error:
                    logger.warning(
                        "[aicoding.restart] clear persisted restart resync marker failed; "
                        "bot_id=%s, engine_type=%s, error=%s",
                        ctx.bot_id, effective_engine, clear_error,
                        exc_info=True,
                    )

        threading.Thread(
            target=bind_current_avernet_tenant(_run), daemon=True
        ).start()
        return True

    def on_bot_created(self, ctx: BotProvisioningContext) -> None:
        # Application-only hooks (DIMA workspace/memory/cron) intentionally stay
        # out of the personalCoding path.  Existing call sites can be migrated
        # here separately without changing token/env semantics.
        return None

    def on_template_updated(
        self, ctx: BotProvisioningContext, *, token_changed: bool
    ) -> None:
        return None
