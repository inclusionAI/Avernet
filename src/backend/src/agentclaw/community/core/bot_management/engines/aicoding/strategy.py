"""Provisioning rules for coding engines.

All knowledge about coding template types, relay default envs and CodeFuse token
provisioning lives here instead of being duplicated in bot/device/template
services.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict

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

logger = get_logger()


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

        template_service.update_template(
            bot_id=ctx.bot_id,
            template_config=candidate,
            template_type=ctx.template_type,
            active_engine=ctx.active_engine,
        )
        logger.info(
            "[aicoding.restart] persisted newer template snapshot: "
            "bot_id=%s old_version=%s new_version=%s",
            ctx.bot_id,
            stored_version,
            incoming_version,
        )

    def refresh_restart_authorization(
        self,
        ctx: BotProvisioningContext,
        bot: Dict[str, Any],
        extra_configs: Dict[str, Any] | None,
        *,
        passport_plugin: Any,
        skill_set_factory: Any,
        template_service: Any,
    ) -> None:
        """Refresh this bot's Passport authorization scope on restart.

        Mirrors the create-time passport scope (``create_flow._apply_passport``)
        so an ``aicoding`` / ``claude_code`` bot keeps the same MCP + CLI grants
        after a restart: recompute the passport MCP codes and the engine default
        CLI items, then push them to Passport via ``update_passport`` as a full
        ``resource_scope`` snapshot (Passport treats each resource list as a
        full replacement). Best-effort: failures are logged by the caller and
        must not block the restart.

        Opt-in: only runs when ``extra_configs['confirmed_template_update']`` is
        truthy — a plain restart must never silently rewrite the Passport
        authorization scope. Anything falsy (missing key, None, false) no-ops.
        """
        if not (
            isinstance(extra_configs, dict)
            and extra_configs.get("confirmed_template_update")
        ):
            logger.info(
                "[aicoding.restart] skip passport refresh: "
                "confirmed_template_update is not set for bot_id=%s",
                ctx.bot_id,
            )
            return
        # Imported lazily: create_flow imports this module's registry, so a
        # module-level import would cycle.
        from agentclaw.community.core.bot_management.create_flow import (
            _get_bot_mcp_codes,
        )
        from agentclaw.community.core.mcp.services._defaults import (
            get_default_cli_items,
        )

        stored_config: Dict[str, Any] = (
            template_service.get_template_config(ctx.bot_id) or {}
        )
        engine_type = ctx.active_engine or self.engine_type
        entity_id = str(bot.get("entity_id") or "")
        entity_type = str(bot.get("entity_type") or "staff")
        owner_id = ctx.owner_id

        mcp_codes = _get_bot_mcp_codes(
            skill_set_factory,
            owner_id,
            ctx.bot_id,
            entity_id,
            entity_type,
            engine_type,
        )
        cli_items = get_default_cli_items(
            engine_type,
            ctx.template_type,
            ext_info={"template_config": stored_config}
            if stored_config
            else None,
        )

        passport_plugin.update_passport(
            bot_id=ctx.bot_id,
            user_id=owner_id,
            bot_name=bot.get("bot_name"),
            bot_desc=bot.get("bot_desc"),
            engine_type=engine_type,
            resource_scope={
                "mcp_codes": mcp_codes,
                "cli_items": cli_items,
            },
        )
        logger.info(
            "[aicoding.restart] refreshed passport authorization scope: "
            "bot_id=%s mcp_codes=%s cli_items=%s",
            ctx.bot_id,
            mcp_codes,
            cli_items,
        )

    def on_bot_created(self, ctx: BotProvisioningContext) -> None:
        # Application-only hooks (DIMA workspace/memory/cron) intentionally stay
        # out of the personalCoding path.  Existing call sites can be migrated
        # here separately without changing token/env semantics.
        return None

    def on_template_updated(
        self, ctx: BotProvisioningContext, *, token_changed: bool
    ) -> None:
        return None
