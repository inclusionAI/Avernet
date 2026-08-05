"""Provision a bot for a target user with a callback-ready control-plane identity.

The bot id is globally unique (allocated via ``generate_bot_id``); the legacy
``"default"`` id convention is retired. When a target user already owns at least
one bot, the service repairs/restarts that existing bot in place — preserving its
assigned id — rather than allocating a new one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import TYPE_CHECKING, Any

from agentclaw.community.core.bot_management.errors import CreateBotForOthersError
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.mcp.services._defaults import get_default_cli_items
from agentclaw.community.core.mcp.services.passport_scope import (
    filter_passport_mcp_codes,
)
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.core.bot_management.services.bot_service import (
    BotOperationNotAllowedError,
    generate_bot_id,
)
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT,
    get_current_avernet_tenant,
)

if TYPE_CHECKING:
    from agentclaw.community.core.bot_management.services.bot_service import BotService


_RESTART_WAIT = timedelta(minutes=30)


@dataclass(frozen=True)
class _PassportReadiness:
    agent_code: str
    source: str


class _TargetLockEntry:
    """One keyed operation lock and the number of callers using or waiting on it."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.users = 0


class CreateBotForOthersService:
    """Ensure Passport and owner authorization before create/restart actions.

    Looks up an existing bot for the target user by owner (not by a literal
    ``"default"`` id). When one exists, it is repaired/restarted in place and its
    assigned bot id is preserved. When none exists, a new bot is created with a
    globally-unique id from ``generate_bot_id``.
    """

    def __init__(
        self,
        *,
        repository: BotRepository,
        bot_service: BotService,
        passport_plugin: PassportPlugin,
        auth_relationship_plugin: AuthRelationshipPlugin,
        skill_set_factory: SkillSetServiceFactory,
    ) -> None:
        self._repository = repository
        self._bot_service = bot_service
        self._passport_plugin = passport_plugin
        self._auth_relationship_plugin = auth_relationship_plugin
        self._skill_set_factory = skill_set_factory
        self._target_locks_guard = Lock()
        self._target_locks: dict[str, _TargetLockEntry] = {}

    def execute(
        self,
        *,
        target_user_id: str,
        target_nick_name: str,
        bot_type: str | None,
        operator_user_id: str,
        operator_name: str,
        cookie: str,
    ) -> dict[str, Any]:
        """Create or repair one target user's default bot serially."""
        # This service writes ``bot_id="default"`` directly rather than going
        # through ``generate_bot_id``, so it is the one path that can still mint
        # a colliding id. ``generate_bot_id`` confines "default" to the default
        # tenant precisely because the Passport principal and BCN record derived
        # from it carry no tenant field; creating one here for another tenant
        # would reintroduce that collision. Fail closed instead: a non-default
        # tenant has no "default" bots by construction, so the operation has no
        # meaning there.
        tenant = get_current_avernet_tenant()
        if tenant != DEFAULT_AVERNET_TENANT:
            raise CreateBotForOthersError(
                "create-for-others is only available in the default tenant",
                error_code=400,
            )
        with self._target_locks_guard:
            lock_entry = self._target_locks.get(target_user_id)
            if lock_entry is None:
                lock_entry = _TargetLockEntry()
                self._target_locks[target_user_id] = lock_entry
            lock_entry.users += 1
        try:
            with lock_entry.lock:
                return self._execute_target(
                    target_user_id=target_user_id,
                    target_nick_name=target_nick_name,
                    bot_type=bot_type,
                    operator_user_id=operator_user_id,
                    operator_name=operator_name,
                    cookie=cookie,
                )
        finally:
            with self._target_locks_guard:
                lock_entry.users -= 1
                if lock_entry.users == 0:
                    self._target_locks.pop(target_user_id, None)

    def _execute_target(
        self,
        *,
        target_user_id: str,
        target_nick_name: str,
        bot_type: str | None,
        operator_user_id: str,
        operator_name: str,
        cookie: str,
    ) -> dict[str, Any]:
        existing_bot = self._find_existing_bot_for_owner(target_user_id)
        if existing_bot is None:
            bot_id = generate_bot_id(target_user_id, self._repository)
            return self._create_bot(
                bot_id=bot_id,
                target_user_id=target_user_id,
                target_nick_name=target_nick_name,
                bot_type=bot_type,
                operator_user_id=operator_user_id,
                operator_name=operator_name,
                cookie=cookie,
            )
        return self._repair_or_restart_default_bot(
            bot=existing_bot,
            target_user_id=target_user_id,
            target_nick_name=target_nick_name,
            operator_user_id=operator_user_id,
            operator_name=operator_name,
        )

    def _find_existing_bot_for_owner(
        self, target_user_id: str
    ) -> Mapping[str, Any] | None:
        """Return one live bot for the owner if any exist (owner-based lookup)."""
        # NOTE: repairs the owner's first listed bot; multi-bot owner selection
        # ordering is tracked as a follow-up.
        _count, items = self._repository.list_by_owner(target_user_id, 1, 1)
        if not items:
            return None
        return items[0]

    def _create_bot(
        self,
        *,
        bot_id: str,
        target_user_id: str,
        target_nick_name: str,
        bot_type: str | None,
        operator_user_id: str,
        operator_name: str,
        cookie: str,
    ) -> dict[str, Any]:
        self._bot_service.check_create_bot_preflight(user_id=target_user_id)
        readiness = self._ensure_passport(
            bot_id=bot_id,
            target_user_id=target_user_id,
            bot_name=target_nick_name,
            bot_desc=None,
            entity_id=target_user_id,
            entity_type="staff",
            engine_type=DEFAULT_ENGINE_TYPE,
            template_type=None,
            existing_ext=None,
            force_apply=True,
        )
        owner_relationship = self._ensure_owner_relationship(
            target_user_id=target_user_id,
            agent_code=readiness.agent_code,
            operator_user_id=operator_user_id,
            operator_name=operator_name,
        )
        result = self._bot_service.create_bot(
            user_id=target_user_id,
            nick_name=target_nick_name,
            bot_name=target_nick_name,
            entity_id=target_user_id,
            entity_type="staff",
            engine_type=DEFAULT_ENGINE_TYPE,
            ext={"passport": {"agent_code": readiness.agent_code}},
            bot_id=bot_id,
            bot_type=bot_type,
            cookie=cookie,
        )
        return {
            "target_user_id": target_user_id,
            "bot_id": bot_id,
            "action": "created",
            "bot": result,
            "passport": self._passport_result(readiness),
            "owner_relationship": owner_relationship,
            "database": {"ext_agent_code_verified": True},
            "runtime": {"restart_required": False},
        }

    def _repair_or_restart_default_bot(
        self,
        *,
        bot: Mapping[str, Any],
        target_user_id: str,
        target_nick_name: str,
        operator_user_id: str,
        operator_name: str,
    ) -> dict[str, Any]:
        bot_id = str(bot.get("bot_id") or "")
        if not bot_id:
            raise CreateBotForOthersError(
                "existing bot record has no bot_id; cannot repair",
                error_code=500,
            )
        engine_type = str(bot.get("active_engine") or DEFAULT_ENGINE_TYPE)
        existing_ext = self._mapping_copy(bot.get("ext"))
        readiness = self._ensure_passport(
            bot_id=bot_id,
            target_user_id=target_user_id,
            bot_name=self._optional_string(bot.get("bot_name")),
            bot_desc=self._optional_string(bot.get("bot_desc")),
            entity_id=str(bot.get("entity_id") or target_user_id),
            entity_type=str(bot.get("entity_type") or "staff"),
            engine_type=engine_type,
            template_type=self._optional_string(bot.get("template_type")),
            existing_ext=existing_ext,
            force_apply=False,
        )
        database_changed = self._persist_agent_code(
            bot_id=bot_id,
            bot=bot,
            target_user_id=target_user_id,
            agent_code=readiness.agent_code,
        )
        owner_relationship = self._ensure_owner_relationship(
            target_user_id=target_user_id,
            agent_code=readiness.agent_code,
            operator_user_id=operator_user_id,
            operator_name=operator_name,
        )
        identity_repaired = readiness.source == "applied" or database_changed
        bot_status = str(bot.get("status") or "UNKNOWN")
        base_result = {
            "target_user_id": target_user_id,
            "bot_id": bot_id,
            "status": bot_status,
            "passport": self._passport_result(readiness),
            "owner_relationship": owner_relationship,
            "database": {
                "ext_agent_code_verified": True,
                "changed": database_changed,
            },
        }

        if bot_status == "ACTIVE":
            return {
                **base_result,
                "action": "repaired" if identity_repaired else "skipped",
                "runtime": {"restart_required": identity_repaired},
            }

        wait = self._restart_wait(bot.get("gmt_modified"))
        if wait is not None:
            minutes_since_modified, minutes_remaining = wait
            return {
                **base_result,
                "action": "skipped_wait",
                "minutes_since_modified": minutes_since_modified,
                "minutes_remaining": minutes_remaining,
                "runtime": {"restart_required": True},
            }

        try:
            result = self._bot_service.restart_bot(
                bot_id=bot_id,
                user_id=target_user_id,
                nick_name=target_nick_name,
            )
        except BotOperationNotAllowedError as exc:
            raise CreateBotForOthersError(str(exc), error_code=400) from exc
        return {
            **base_result,
            "action": "restarted",
            "bot": result,
            "runtime": {"restart_required": False},
        }

    def _ensure_passport(
        self,
        *,
        bot_id: str,
        target_user_id: str,
        bot_name: str | None,
        bot_desc: str | None,
        entity_id: str,
        entity_type: str,
        engine_type: str,
        template_type: str | None,
        existing_ext: Mapping[str, Any] | None,
        force_apply: bool,
    ) -> _PassportReadiness:
        stored_agent_code = self._stored_agent_code(existing_ext)
        if not force_apply:
            passport, auth_status, token = self._query_passport(
                bot_id=bot_id, target_user_id=target_user_id
            )
            agent_code = self._agent_code(passport) or stored_agent_code
            if self._passport_complete(
                agent_code=agent_code,
                auth_status=auth_status,
                token=token,
            ):
                assert agent_code is not None
                return _PassportReadiness(agent_code=agent_code, source="existing")

        try:
            skill_set_service = self._skill_set_factory.create(
                user_id=target_user_id,
                entity_id=entity_id,
                bot_id=bot_id,
                entity_type=entity_type,
                engine_type=engine_type,
            )
            mcp_codes = skill_set_service.get_bot_mcp_codes(
                entity_id=entity_id,
                bot_id=bot_id,
                user_id=target_user_id,
                entity_type=entity_type,
            )
            apply_result = self._passport_plugin.apply_first_agent_passport(
                bot_id=bot_id,
                owner_workno=target_user_id,
                mcp_codes=filter_passport_mcp_codes(mcp_codes),
                cli_items=get_default_cli_items(engine_type, template_type),
                bot_name=bot_name,
                bot_desc=bot_desc,
                engine_type=engine_type,
                access_mode="RESTRICTED",
                workspace_path="/home/admin/.openclaw",
            )
        except Exception as exc:
            raise CreateBotForOthersError(
                "Passport first apply failed", error_code=5400
            ) from exc

        if not isinstance(apply_result, Mapping) or not self._non_empty_string(
            apply_result.get("token")
        ):
            raise CreateBotForOthersError(
                "apply_first_agent_passport returned no token", error_code=5401
            )
        applied_agent_code = self._non_empty_string(apply_result.get("agent_code"))
        if applied_agent_code is None:
            raise CreateBotForOthersError(
                "apply_first_agent_passport returned no agent_code",
                error_code=5400,
            )

        passport, auth_status, token = self._query_passport(
            bot_id=bot_id, target_user_id=target_user_id
        )
        queried_agent_code = self._agent_code(passport)
        if queried_agent_code and queried_agent_code != applied_agent_code:
            raise CreateBotForOthersError(
                "Passport agent_code verification failed", error_code=5400
            )
        agent_code = queried_agent_code or applied_agent_code
        if not self._passport_complete(
            agent_code=agent_code,
            auth_status=auth_status,
            token=token,
        ):
            raise CreateBotForOthersError(
                "Passport verification failed", error_code=5400
            )
        return _PassportReadiness(agent_code=agent_code, source="applied")

    def _query_passport(
        self, *, bot_id: str, target_user_id: str
    ) -> tuple[object, object, object]:
        try:
            passport = self._passport_plugin.query_agent_passport(
                bot_id=bot_id,
                owner_workno=target_user_id,
            )
            auth_status = self._passport_plugin.query_auth_status(
                bot_id=bot_id,
                owner_workno=target_user_id,
            )
            token = self._passport_plugin.query_token(
                bot_id=bot_id,
                owner_workno=target_user_id,
            )
            return passport, auth_status, token
        except Exception as exc:
            raise CreateBotForOthersError(
                "Passport verification query failed", error_code=5400
            ) from exc

    def _persist_agent_code(
        self,
        *,
        bot_id: str,
        bot: Mapping[str, Any],
        target_user_id: str,
        agent_code: str,
    ) -> bool:
        ext = self._mapping_copy(bot.get("ext"))
        ext_passport = self._mapping_copy(ext.get("passport"))
        if self._non_empty_string(ext_passport.get("agent_code")) == agent_code:
            return False
        ext_passport["agent_code"] = agent_code
        ext["passport"] = ext_passport
        try:
            persisted = self._repository.update_by_owner(
                bot_id=bot_id,
                owner_id=target_user_id,
                update_data={"ext": ext},
            )
        except Exception as exc:
            raise CreateBotForOthersError(
                "Bot Passport agent_code persistence failed", error_code=500
            ) from exc
        persisted_ext = (
            self._mapping_copy(persisted.get("ext"))
            if isinstance(persisted, Mapping)
            else {}
        )
        if self._stored_agent_code(persisted_ext) != agent_code:
            raise CreateBotForOthersError(
                "Bot Passport agent_code persistence verification failed",
                error_code=500,
            )
        return True

    def _ensure_owner_relationship(
        self,
        *,
        target_user_id: str,
        agent_code: str,
        operator_user_id: str,
        operator_name: str,
    ) -> dict[str, Any]:
        try:
            relationships = self._auth_relationship_plugin.query_relationships(
                agent_code=agent_code,
                work_no=target_user_id,
            )
            relationship = self._find_owner_relationship(
                relationships,
                work_no=target_user_id,
                agent_code=agent_code,
            )
            if relationship is not None:
                return {
                    "verified": True,
                    "created": False,
                    "auth_id": relationship.get(
                        "auth_id", relationship.get("authId")
                    ),
                }
            created = self._auth_relationship_plugin.create_relationship(
                work_no=target_user_id,
                agent_code=agent_code,
                description="Bot owner default authorization",
                operator_work_no=operator_user_id,
                operator_name=operator_name,
            )
        except Exception as exc:
            raise CreateBotForOthersError(
                "Owner authorization relationship operation failed",
                error_code=5402,
            ) from exc
        if not isinstance(created, Mapping) or not created:
            raise CreateBotForOthersError(
                "Owner authorization relationship creation failed",
                error_code=5402,
            )
        return {
            "verified": True,
            "created": True,
            "auth_id": created.get("auth_id", created.get("authId")),
        }

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

    @classmethod
    def _passport_complete(
        cls,
        *,
        agent_code: str | None,
        auth_status: object,
        token: object,
    ) -> bool:
        return bool(
            agent_code
            and isinstance(auth_status, Mapping)
            and auth_status.get("status") == "ISSUED"
            and cls._non_empty_string(token)
        )

    @classmethod
    def _agent_code(cls, passport: object) -> str | None:
        if not isinstance(passport, Mapping):
            return None
        return cls._non_empty_string(passport.get("agent_code"))

    @classmethod
    def _stored_agent_code(
        cls, ext: Mapping[str, Any] | None
    ) -> str | None:
        if not isinstance(ext, Mapping):
            return None
        passport = ext.get("passport")
        if not isinstance(passport, Mapping):
            return None
        return cls._non_empty_string(passport.get("agent_code"))

    @staticmethod
    def _passport_result(readiness: _PassportReadiness) -> dict[str, Any]:
        return {
            "status": "ISSUED",
            "agent_code": readiness.agent_code,
            "token_present": True,
            "source": readiness.source,
        }

    @staticmethod
    def _restart_wait(value: object) -> tuple[int, int] | None:
        modified = value
        if isinstance(modified, str):
            try:
                modified = datetime.fromisoformat(modified.replace("Z", "+00:00"))
            except ValueError:
                return None
        if not isinstance(modified, datetime):
            return None
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - modified
        if elapsed >= _RESTART_WAIT:
            return None
        seconds = max(0, int(elapsed.total_seconds()))
        remaining_seconds = max(
            0, int((_RESTART_WAIT - elapsed).total_seconds())
        )
        return seconds // 60, remaining_seconds // 60

    @staticmethod
    def _mapping_copy(value: object) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except (TypeError, ValueError):
                return {}
            if isinstance(decoded, Mapping):
                return dict(decoded)
        return {}

    @staticmethod
    def _non_empty_string(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) else None
