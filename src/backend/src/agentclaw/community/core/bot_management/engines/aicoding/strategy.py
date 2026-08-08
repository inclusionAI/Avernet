"""Provisioning rules for coding engines.

All knowledge about coding template types, relay default envs and CodeFuse token
provisioning lives here instead of being duplicated in bot/device/template
services.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from agentclaw.community.core.bot_management.capabilities import (
    is_template_factory_config,
)

from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.utils import secret_utils

from ..provisioning import BotProvisioningContext, EngineProvisioningStrategy


# Legacy coding template types.  This is only used for old call sites that
# identify coding bots by template_type (applicationCoding/personalCoding).
# Template-factory templates (normalCC/architect/user-created templates) must be
# detected from active_engine + template_config snapshot, not by extending this
# set with template keys.
CODING_TEMPLATE_TYPES = frozenset({"applicationCoding", "personalCoding"})
AICODING_ENGINE_TYPE = "aicoding"
CLAUDE_CODE_ENGINE_TYPE = "claude_code"
NORMAL_CC_TEMPLATE_TYPE = "normalcc"
TEMPLATE_CONFIG_CONSUMING_ENGINES = frozenset(
    {AICODING_ENGINE_TYPE, CLAUDE_CODE_ENGINE_TYPE}
)
LEGACY_BOT_TYPE_ENV_MAP = {
    "personalCoding": "personal",
    "applicationCoding": "application",
}
_THETA_KEY_PATH = ("bot_template_config", "ext_config", "thetaKey")
_ENCRYPTED_VALUE_PREFIX = "enc:v1:"


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


class AicodingProvisioningStrategy(EngineProvisioningStrategy):
    """Provisioning strategy shared by ``aicoding`` and ``claude_code`` engines."""

    def __init__(self, engine_type: str) -> None:
        self._engine_type = engine_type

    @property
    def engine_type(self) -> str:
        return self._engine_type

    @staticmethod
    def normalize_engine_type(
        engine_type: str | None, *, default: str = "openclaw"
    ) -> str:
        return (engine_type or default).strip().lower().replace("-", "_")

    @staticmethod
    def normalize_template_type(template_type: str | None) -> str:
        return (template_type or "").strip().lower()

    @staticmethod
    def is_coding_template(template_type: str | None) -> bool:
        return template_type in CODING_TEMPLATE_TYPES

    @classmethod
    def should_use_aicoding_baas_bucket(
        cls,
        *,
        active_engine: str | None,
        template_type: str | None,
    ) -> bool:
        """Whether this context should select the aicoding BaaS bucket.

        BaaS bucket routing is an image/runtime selection policy: all explicit
        claude_code template-factory types except normalCC reuse the aicoding
        BaaS template bucket. It only depends on active_engine + template_type;
        caller/create routing may only have template_type available.
        """
        if (
            cls.normalize_engine_type(active_engine, default="")
            != CLAUDE_CODE_ENGINE_TYPE
        ):
            return False
        normalized_template_type = cls.normalize_template_type(template_type)
        return bool(
            normalized_template_type
            and normalized_template_type != NORMAL_CC_TEMPLATE_TYPE
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
        return normalized_engine in TEMPLATE_CONFIG_CONSUMING_ENGINES and has_template_identity

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
        # template types do not require backend enum/map changes.
        if template_type:
            envs["BOT_TYPE"] = LEGACY_BOT_TYPE_ENV_MAP.get(
                template_type, template_type
            )

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
                if isinstance(repo, str) and repo_key not in legacy_repo_keys and repo.strip():
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
            return None

        stored = self._get_template_value(ctx.template_config, _THETA_KEY_PATH)
        if (
            secret_resolver is None
            or not theta_master_key_secret
            or not isinstance(stored, str)
            or not stored.startswith(_ENCRYPTED_VALUE_PREFIX)
        ):
            return None
        ciphertext = stored[len(_ENCRYPTED_VALUE_PREFIX):]
        if not ciphertext:
            return None

        try:
            secret = secret_resolver.get_secret(theta_master_key_secret)
            master_key = getattr(secret, "secret_value", secret) if secret else None
            if not master_key:
                return None
            api_key = secret_utils.symmetric_decrypt(ciphertext, str(master_key))
        except Exception:
            return None
        if not isinstance(api_key, str) or not api_key:
            return None
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

    def on_bot_created(self, ctx: BotProvisioningContext) -> None:
        # Application-only hooks (DIMA workspace/memory/cron) intentionally stay
        # out of the personalCoding path.  Existing call sites can be migrated
        # here separately without changing token/env semantics.
        return None

    def on_template_updated(
        self, ctx: BotProvisioningContext, *, token_changed: bool
    ) -> None:
        return None
