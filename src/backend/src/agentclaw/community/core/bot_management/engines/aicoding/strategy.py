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

from ..provisioning import BotProvisioningContext, EngineProvisioningStrategy


# Legacy coding template types.  This is only used for old call sites that
# identify coding bots by template_type (applicationCoding/personalCoding).
# Template-factory templates (normalCC/architect/user-created templates) must be
# detected from active_engine + template_config snapshot, not by extending this
# set with template keys.
CODING_TEMPLATE_TYPES = frozenset({"applicationCoding", "personalCoding"})
TEMPLATE_CONFIG_CONSUMING_ENGINES = frozenset({"aicoding", "claude_code"})
BOT_TYPE_ENV_MAP = {
    "personalCoding": "personal",
    "applicationCoding": "application",
}


class AicodingProvisioningStrategy(EngineProvisioningStrategy):
    """Provisioning strategy shared by ``aicoding`` and ``claude_code`` engines."""

    def __init__(self, engine_type: str) -> None:
        self._engine_type = engine_type

    @property
    def engine_type(self) -> str:
        return self._engine_type

    @staticmethod
    def is_coding_template(template_type: str | None) -> bool:
        return template_type in CODING_TEMPLATE_TYPES

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
        # sites only know template_type.  For user-created AC templates, do not
        # require backend enum updates: as long as the bot is a coding engine and
        # carries a template-factory config snapshot, consume it.
        if template_type in CODING_TEMPLATE_TYPES:
            return True
        return (
            active_engine in TEMPLATE_CONFIG_CONSUMING_ENGINES
            and cls.has_template_factory_config(template_config)
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

        bot_type = BOT_TYPE_ENV_MAP.get(template_type or "")
        if bot_type:
            envs["BOT_TYPE"] = bot_type

        devflow_workflow = template_config.get("devflow_workflow", "")
        if isinstance(devflow_workflow, dict):
            aix_devflow_info = devflow_workflow.get("path", "")
        elif isinstance(devflow_workflow, str):
            aix_devflow_info = devflow_workflow
        else:
            aix_devflow_info = ""
        if aix_devflow_info:
            envs["AIX_DEVFLOW_INFO"] = aix_devflow_info

        legacy_repo_keys = {"backend_repo", "frontend_repo", "lib_repo"}
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
