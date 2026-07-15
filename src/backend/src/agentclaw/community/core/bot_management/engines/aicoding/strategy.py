"""Provisioning rules for coding engines.

All knowledge about coding template types, relay default envs and CodeFuse token
provisioning lives here instead of being duplicated in bot/device/template
services.
"""
from __future__ import annotations

import json
from typing import Dict

from ..provisioning import BotProvisioningContext


CODING_TEMPLATE_TYPES = frozenset({"applicationCoding", "personalCoding"})
BOT_TYPE_ENV_MAP = {
    "personalCoding": "personal",
    "applicationCoding": "application",
}


class AicodingProvisioningStrategy:
    """Provisioning strategy shared by ``aicoding`` and ``claude_code`` engines."""

    def __init__(self, engine_type: str) -> None:
        self._engine_type = engine_type

    @property
    def engine_type(self) -> str:
        return self._engine_type

    @staticmethod
    def is_coding_template(template_type: str | None) -> bool:
        return template_type in CODING_TEMPLATE_TYPES

    def build_extra_envs(self, ctx: BotProvisioningContext) -> Dict[str, str] | None:
        template_type = ctx.template_type
        if not self.is_coding_template(template_type):
            return None

        template_config = ctx.template_config or {}
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

        repo_list: list[str] = []
        for repo_key in ("backend_repo", "frontend_repo", "lib_repo"):
            for repo in template_config.get(repo_key, []) or []:
                if isinstance(repo, dict):
                    repo_url = repo.get("repo_url")
                    if repo_url:
                        repo_list.append(repo_url)
        if repo_list:
            envs["GIT_ADDRESSES"] = json.dumps(repo_list, ensure_ascii=False)

        # Bug-fix semantics: both applicationCoding and personalCoding may set
        # relay default model/runtime.  Non-coding templates are rejected above.
        model = template_config.get("model")
        if isinstance(model, str) and model.strip():
            envs["RELAY_DEFAULT_MODEL"] = model.strip()
        runtime = template_config.get("runtime")
        if isinstance(runtime, str) and runtime.strip():
            envs["RELAY_DEFAULT_RUNTIME"] = runtime.strip()

        return envs or None

    def should_encrypt_template_token(self, ctx: BotProvisioningContext) -> bool:
        return self.is_coding_template(ctx.template_type)

    def extract_runtime_token(self, ctx: BotProvisioningContext) -> str | None:
        if not self.is_coding_template(ctx.template_type):
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
