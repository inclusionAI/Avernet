"""List the Coding bots eligible for the user-level work-items snapshot."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from injector import inject

from agentclaw.community.core.repository.protocols.bot import (
    BotRepository,
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

_WORK_ITEM_BOT_PAGE_SIZE = 100
_AICODING_ENGINES = {"aicoding", "applicationcoding"}
_NORMAL_CLAUDE_CODE_TEMPLATES = {"normal", "normalcc"}
_LEGACY_CODING_TEMPLATES = {"personalcoding", "applicationcoding"}


@dataclass(frozen=True)
class WorkItemsBotTarget:
    """A Coding bot that may have a user-visible DevFlow session."""

    bot_id: str
    bot_name: str
    owner_id: str


@runtime_checkable
class WorkItemsBotLister(Protocol):
    """Service API for owner + collaborator Coding-bot enumeration."""

    def list_bot_targets(self, user_id: str) -> list[WorkItemsBotTarget]: ...


def is_coding_session_list_bot(bot: dict[str, Any] | None) -> bool:
    """Mirror the frontend ``aicodingSessionList`` predicate server-side.

    The important distinction from notify is that a bare ``claude_code`` bot is
    not enough: only Coding templates and explicitly aix-capable dynamic
    templates may be probed with ``/api/aicoding/sessions``.
    """

    if not bot:
        return False

    engine_values: list[str] = []
    active_engine = str(bot.get("active_engine") or "").strip().lower()
    if active_engine:
        engine_values.append(active_engine)
    raw_engine_types = bot.get("engine_types")
    if isinstance(raw_engine_types, list):
        engine_values.extend(
            str(value).strip().lower() for value in raw_engine_types if value
        )
    engine_value = engine_values[0] if engine_values else ""

    template_type = str(bot.get("template_type") or "").strip().lower()
    template_config = _template_config(bot)
    migrated = _is_migrated_legacy_coding(template_config, template_type, engine_value)
    capabilities_declared = _template_capabilities_declared(template_config)
    aix_enabled = _aix_capability_enabled(template_config)

    if engine_value in _AICODING_ENGINES:
        return not migrated or aix_enabled

    if engine_value != "claude_code":
        return False
    if not template_type or template_type in _NORMAL_CLAUDE_CODE_TEMPLATES:
        return False
    if template_type in _LEGACY_CODING_TEMPLATES:
        return not migrated or aix_enabled
    # Dynamic Agent Coding templates (architect/generalCC/...) match the frontend:
    # without a template_config, or without a declared capabilities node, they inherit
    # the personal Agent Coding layout; once capabilities are declared, aix is strict.
    return not capabilities_declared or aix_enabled


class RepositoryWorkItemsBotLister:
    """Enumerate every owner/collaborator Coding bot without single-page truncation."""

    @inject
    def __init__(
        self,
        bot_repository: BotRepository,
        collaborator_repository: CollaboratorRepositoryProtocol,
    ) -> None:
        self._bot_repository = bot_repository
        self._collaborator_repository = collaborator_repository

    def list_bot_targets(self, user_id: str) -> list[WorkItemsBotTarget]:
        targets: list[WorkItemsBotTarget] = []
        seen: set[tuple[str, str]] = set()

        bots = self._list_all_owned_bots(user_id)
        for bot in bots:
            target = self._target_from_bot(bot, user_id)
            if target and (target.bot_id, target.owner_id) not in seen:
                targets.append(target)
                seen.add((target.bot_id, target.owner_id))

        collaborators = self._collaborator_repository.list_by_user(
            user_id, get_current_env()
        )
        for record in collaborators:
            bot_id = str(record.bot_id or "")
            owner_id = str(record.owner_id or "")
            if not bot_id or not owner_id or (bot_id, owner_id) in seen:
                continue
            bot = self._bot_repository.get_by_id_and_owner(bot_id, owner_id)
            target = self._target_from_bot(bot, owner_id)
            if target:
                targets.append(target)
                seen.add((bot_id, owner_id))

        targets.sort(key=lambda target: (target.bot_id, target.owner_id))
        if len(targets) > 40:
            logger.info(
                "[work_items_bot_lister] user=%s targets=%s", user_id, len(targets)
            )
        return targets

    def _list_all_owned_bots(self, owner_id: str) -> list[dict[str, Any]]:
        bots: list[dict[str, Any]] = []
        page = 1
        while True:
            total, page_bots = self._bot_repository.list_by_owner(
                owner_id, page=page, page_size=_WORK_ITEM_BOT_PAGE_SIZE
            )
            bots.extend(page_bots)
            if not page_bots or len(bots) >= total:
                break
            page += 1
        return bots

    def _target_from_bot(
        self, bot: dict[str, Any] | None, owner_id: str
    ) -> WorkItemsBotTarget | None:
        if not is_coding_session_list_bot(bot):
            return None
        bot_id = str(bot.get("bot_id") or "")
        if not bot_id:
            return None
        return WorkItemsBotTarget(
            bot_id=bot_id,
            bot_name=str(bot.get("bot_name") or bot_id),
            owner_id=owner_id,
        )


def _template_config(bot: dict[str, Any]) -> dict[str, Any] | None:
    value = bot.get("template_config")
    if value is None:
        ext = bot.get("ext")
        if isinstance(ext, str):
            try:
                value = json.loads(ext).get("template_config")
            except (TypeError, ValueError, AttributeError):
                return None
        else:
            value = ext.get("template_config") if isinstance(ext, dict) else None
    return value if isinstance(value, dict) else None


def _is_migrated_legacy_coding(
    config: dict[str, Any] | None, template_type: str, engine_value: str
) -> bool:
    if config is None:
        return False
    migration = config.get("legacy_template_migration")
    return (
        bool(template_type)
        and template_type in _LEGACY_CODING_TEMPLATES
        and isinstance(migration, dict)
        and migration.get("version") == 1
        and bool(config.get("template_uid"))
        and str(config.get("template_key") or "").strip().lower() == template_type
        and engine_value in _AICODING_ENGINES | {"claude_code"}
    )


def _template_capabilities_declared(config: dict[str, Any] | None) -> bool:
    return config is not None and "capabilities" in config


def _aix_capability_enabled(config: dict[str, Any] | None) -> bool:
    capabilities = config.get("capabilities") if config is not None else None
    return isinstance(capabilities, dict) and capabilities.get("aix") is True
