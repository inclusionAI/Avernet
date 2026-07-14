"""Repair control-plane Passport state for an existing default bot."""

from __future__ import annotations

from collections.abc import Mapping
from threading import Lock
from typing import Any

from agentclaw.community.core.bot_management.errors import (
    DefaultBotPassportRepairError,
)
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.mcp.services._defaults import get_default_cli_items
from agentclaw.community.core.mcp.services.passport_scope import (
    filter_passport_mcp_codes,
)
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin


class DefaultBotPassportRepairService:
    """Repair Passport and owner authorization without touching bot runtime."""

    def __init__(
        self,
        *,
        repository: BotRepository,
        passport_plugin: PassportPlugin,
        auth_relationship_plugin: AuthRelationshipPlugin,
        skill_set_factory: SkillSetServiceFactory,
    ) -> None:
        self._repository = repository
        self._passport_plugin = passport_plugin
        self._auth_relationship_plugin = auth_relationship_plugin
        self._skill_set_factory = skill_set_factory
        self._target_locks_guard = Lock()
        self._target_locks: dict[tuple[str, str, str], Any] = {}

    def repair(
        self,
        *,
        target_user_id: str,
        target_env: str,
        operator_user_id: str,
        operator_name: str,
    ) -> dict[str, Any]:
        if target_env not in {"pre", "prod"}:
            raise DefaultBotPassportRepairError(
                "target_env must be pre or prod", error_code=400
            )
        lock_key = (target_env, target_user_id, "default")
        with self._target_locks_guard:
            target_lock = self._target_locks.get(lock_key)
            if target_lock is None:
                target_lock = Lock()
                self._target_locks[lock_key] = target_lock
        with target_lock:
            return self._repair_target(
                target_user_id=target_user_id,
                target_env=target_env,
                operator_user_id=operator_user_id,
                operator_name=operator_name,
            )

    def _repair_target(
        self,
        *,
        target_user_id: str,
        target_env: str,
        operator_user_id: str,
        operator_name: str,
    ) -> dict[str, Any]:
        bots = self._repository.get_live_by_id_owner_and_env(
            bot_id="default",
            owner_id=target_user_id,
            env=target_env,
        )
        if not bots:
            raise DefaultBotPassportRepairError(
                "default bot not found in target environment", error_code=404
            )
        if len(bots) != 1:
            raise DefaultBotPassportRepairError(
                "multiple live default bots found in target environment",
                error_code=409,
            )
        bot = bots[0]

        try:
            passport = self._passport_plugin.query_agent_passport(
                bot_id="default",
                owner_workno=target_user_id,
            )
            auth_status = self._passport_plugin.query_auth_status(
                bot_id="default",
                owner_workno=target_user_id,
            )
            token = self._passport_plugin.query_token(
                bot_id="default",
                owner_workno=target_user_id,
            )
        except Exception as exc:
            raise DefaultBotPassportRepairError(
                "Passport query failed", error_code=5400
            ) from exc

        source = "existing"
        if not self._passport_complete(passport, auth_status, token):
            try:
                skill_set_service = self._skill_set_factory.create(
                    user_id=target_user_id,
                    entity_id=bot.get("entity_id") or target_user_id,
                    bot_id="default",
                    entity_type=bot.get("entity_type") or "staff",
                    engine_type=bot.get("active_engine"),
                )
                mcp_codes = skill_set_service.get_bot_mcp_codes_for_env(
                    entity_id=bot.get("entity_id") or target_user_id,
                    bot_id="default",
                    user_id=target_user_id,
                    entity_type=bot.get("entity_type") or "staff",
                    engine_type=bot.get("active_engine"),
                    target_env=target_env,
                )
                mcp_codes = filter_passport_mcp_codes(mcp_codes)
                apply_result = self._passport_plugin.apply_first_agent_passport(
                    bot_id="default",
                    owner_workno=target_user_id,
                    mcp_codes=mcp_codes,
                    cli_items=get_default_cli_items(
                        bot.get("active_engine"), bot.get("template_type")
                    ),
                    bot_name=bot.get("bot_name"),
                    bot_desc=bot.get("bot_desc"),
                    engine_type=bot.get("active_engine"),
                    access_mode="RESTRICTED",
                    workspace_path="/home/admin/.openclaw",
                )
            except Exception as exc:
                raise DefaultBotPassportRepairError(
                    "Passport first apply failed", error_code=5400
                ) from exc
            if not isinstance(apply_result, Mapping) or not apply_result.get("token"):
                raise DefaultBotPassportRepairError(
                    "apply_first_agent_passport returned no token", error_code=5401
                )

            try:
                passport = self._passport_plugin.query_agent_passport(
                    bot_id="default",
                    owner_workno=target_user_id,
                )
                auth_status = self._passport_plugin.query_auth_status(
                    bot_id="default",
                    owner_workno=target_user_id,
                )
                token = self._passport_plugin.query_token(
                    bot_id="default",
                    owner_workno=target_user_id,
                )
            except Exception as exc:
                raise DefaultBotPassportRepairError(
                    "Passport verification query failed", error_code=5400
                ) from exc
            applied_agent_code = apply_result.get("agent_code")
            queried_agent_code = (
                passport.get("agent_code") if isinstance(passport, Mapping) else None
            )
            if applied_agent_code and applied_agent_code != queried_agent_code:
                raise DefaultBotPassportRepairError(
                    "Passport agent_code verification failed", error_code=5400
                )
            source = "applied"

        if not self._passport_complete(passport, auth_status, token):
            raise DefaultBotPassportRepairError(
                "Passport verification failed", error_code=5400
            )

        assert isinstance(passport, Mapping)
        agent_code = str(passport["agent_code"])
        ext = dict(bot.get("ext") or {})
        ext_passport = dict(ext.get("passport") or {})
        database_changed = ext_passport.get("agent_code") != agent_code
        ext_passport["agent_code"] = agent_code
        ext["passport"] = ext_passport
        try:
            if database_changed:
                self._repository.update_ext_by_id_owner_and_env(
                    bot_id="default",
                    owner_id=target_user_id,
                    env=target_env,
                    ext=ext,
                )
            persisted_matches = self._repository.get_live_by_id_owner_and_env(
                bot_id="default",
                owner_id=target_user_id,
                env=target_env,
            )
            if len(persisted_matches) != 1:
                raise RuntimeError("database read-back did not return one row")
            persisted = persisted_matches[0]
        except Exception as exc:
            raise DefaultBotPassportRepairError(
                "Bot Passport agent_code persistence failed", error_code=500
            ) from exc
        persisted_agent_code = ((persisted.get("ext") or {}).get("passport") or {}).get(
            "agent_code"
        )
        if persisted_agent_code != agent_code:
            raise DefaultBotPassportRepairError(
                "Bot Passport agent_code persistence verification failed",
                error_code=500,
            )

        try:
            relationships = self._auth_relationship_plugin.query_relationships_for_env(
                target_env=target_env,
                agent_code=agent_code,
                work_no=target_user_id,
            )
            created = False
            if not self._has_owner_relationship(
                relationships, work_no=target_user_id, agent_code=agent_code
            ):
                self._auth_relationship_plugin.create_relationship_for_env(
                    target_env=target_env,
                    work_no=target_user_id,
                    agent_code=agent_code,
                    description="Bot owner default authorization",
                    operator_work_no=operator_user_id,
                    operator_name=operator_name,
                )
                relationships = (
                    self._auth_relationship_plugin.query_relationships_for_env(
                        target_env=target_env,
                        agent_code=agent_code,
                        work_no=target_user_id,
                    )
                )
                created = True
        except Exception as exc:
            raise DefaultBotPassportRepairError(
                "Owner authorization relationship operation failed",
                error_code=5402,
            ) from exc
        relationship = self._find_owner_relationship(
            relationships, work_no=target_user_id, agent_code=agent_code
        )
        if relationship is None:
            raise DefaultBotPassportRepairError(
                "Owner authorization relationship verification failed",
                error_code=5402,
            )

        return {
            "target_user_id": target_user_id,
            "bot_id": "default",
            "action": (
                "repaired"
                if source == "applied" or created or database_changed
                else "verified"
            ),
            "target_env": target_env,
            "passport": {
                "status": auth_status["status"],
                "agent_code": agent_code,
                "credential_id": passport["credential_id"],
                "token_present": bool(token),
                "source": source,
            },
            "owner_relationship": {
                "verified": True,
                "created": created,
                "auth_id": relationship.get("auth_id", relationship.get("authId")),
            },
            "database": {"ext_agent_code_verified": True},
            "runtime": {
                "restart_required": True,
                "restart_environment": target_env,
            },
        }

    @staticmethod
    def _passport_complete(
        passport: object, auth_status: object, token: object
    ) -> bool:
        return bool(
            isinstance(passport, Mapping)
            and passport.get("agent_code")
            and passport.get("credential_id")
            and isinstance(auth_status, Mapping)
            and auth_status.get("status") == "ISSUED"
            and token
        )

    @classmethod
    def _has_owner_relationship(
        cls,
        relationships: object,
        *,
        work_no: str,
        agent_code: str,
    ) -> bool:
        return (
            cls._find_owner_relationship(
                relationships, work_no=work_no, agent_code=agent_code
            )
            is not None
        )

    @staticmethod
    def _find_owner_relationship(
        relationships: object,
        *,
        work_no: str,
        agent_code: str,
    ) -> Mapping[str, Any] | None:
        if not isinstance(relationships, list):
            return None
        for relationship in relationships:
            if not isinstance(relationship, Mapping):
                continue
            actual_work_no = relationship.get("work_no", relationship.get("workNo"))
            actual_agent_code = relationship.get(
                "agent_code", relationship.get("agentCode")
            )
            if str(actual_work_no) == work_no and actual_agent_code == agent_code:
                return relationship
        return None
