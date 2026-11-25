"""Bot overview provider for AICoding / Claude Code Bots.

Fills :class:`AICodingEnginePayload` from the Bot record's coding-template
fields. ``template_config`` is resolved the way every AICoding capability
reads it (see ``member_management_capability``): the Bot record's top level
first, then ``bot.ext.template_config``, then the snapshot row in the
template repository (``ac_templates.ext``). A lookup that fails degrades to
"no template reported" — the overview is an unauthenticated read surface and
must answer, not error. It also fills the neutral ``default_model``; skills,
MCP servers and session activity are platform-owned and filled by
``BotOverviewService``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.bot_management.capabilities import (
    is_template_factory_config,
)
from agentclaw.community.core.bot_management.services.aicoding.dima_workspace_capability import (
    has_dima_workspace_enabled,
)
from agentclaw.community.core.bot_management.utils import (
    LEGACY_YUQUE_KNOWLEDGE_KEYS,
    TEMPLATE_FACTORY_YUQUE_KNOWLEDGE_KEYS,
    extract_code_repo_urls,
    extract_workflow_name,
)
from agentclaw.community.core.repository.protocols.bot import TemplateRepository
from agentclaw.community.log import get_logger

from ..models import EngineBotOverview
from ..payloads import AICodingEnginePayload, AICodingTemplateFactoryIdentity
from ..provider import BotOverviewQuery

logger = get_logger()

_YUQUE_URL_KEYS = ("url", "wiki_url", "repo_wiki_url", "space_url", "link")


def _text(value: Any) -> str | None:
    """A non-empty stripped string, or ``None`` when there is none."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _item_url(item: Any) -> str:
    """A URL-like value from a direct string or an object item."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, Mapping):
        for key in _YUQUE_URL_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _yuque_source_urls(template_config: dict[str, Any]) -> list[str]:
    """Yuque knowledge-base URLs from the snapshot; team tokens never leave.

    Inlined here — instead of extending ``bot_management.utils`` — so this
    provider stays inside the bot_overview boundary. Same rules as the pair
    extractor in ``bot_management.utils``: legacy ``yuque_kb_repos`` takes
    object items only; template-factory alias keys also take direct URL
    strings. Only URLs are collected, never the per-repo team token.
    """
    keys: tuple[str, ...] = LEGACY_YUQUE_KNOWLEDGE_KEYS
    if is_template_factory_config(template_config):
        keys = keys + TEMPLATE_FACTORY_YUQUE_KNOWLEDGE_KEYS
    urls: list[str] = []
    for key in keys:
        value = template_config.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str):
                if key in LEGACY_YUQUE_KNOWLEDGE_KEYS:
                    continue
                if not item.strip().startswith(("http://", "https://")):
                    continue
            url = _item_url(item)
            if url:
                urls.append(url)
    return urls


def _resolve_template_config(
    *,
    bot_id: str,
    bot: Mapping[str, Any],
    template_repo: TemplateRepository,
) -> dict[str, Any] | None:
    """The Bot's ``template_config`` snapshot, wherever it is stored.

    The same three places every AICoding capability reads, in order: the Bot
    record's top level (enriched records), ``bot.ext.template_config``
    (historical nested form), then the snapshot row in the template
    repository (``ac_templates.ext``). A repository failure degrades to
    ``None`` so the overview answers instead of erroring.
    """
    top_level = bot.get("template_config")
    if isinstance(top_level, Mapping):
        return dict(top_level)
    ext = bot.get("ext")
    if isinstance(ext, Mapping):
        nested = ext.get("template_config")
        if isinstance(nested, Mapping):
            return dict(nested)
    try:
        template = template_repo.get_by_bot_id(bot_id)
    except Exception:
        logger.warning(
            "[bot_overview.aicoding] template lookup failed bot_id=%s; "
            "reporting no template fields",
            bot_id,
        )
        return None
    stored = template.get("ext") if isinstance(template, Mapping) else None
    return dict(stored) if isinstance(stored, Mapping) else None


def _template_factory(
    template_config: Mapping[str, Any] | None,
) -> AICodingTemplateFactoryIdentity | None:
    """Factory identity when the config is a template-factory snapshot.

    A snapshot is identified by ``template_key`` + ``template_uid`` both being
    present; hand-written coding configs carry neither (and a lone
    ``template_uid`` there is caller smuggling the provisioning rejects), so
    anything short of the pair reports no factory identity.
    """
    if not isinstance(template_config, Mapping):
        return None
    template_key = _text(template_config.get("template_key"))
    template_uid = _text(template_config.get("template_uid"))
    if not template_key or not template_uid:
        return None
    return AICodingTemplateFactoryIdentity(
        template_key=template_key,
        template_uid=template_uid,
        template_version=_text(template_config.get("template_version")),
    )


class AICodingBotOverviewProvider:
    """Serves both ``aicoding`` and ``claude_code`` Bots."""

    def __init__(self, *, template_repo: TemplateRepository) -> None:
        self._template_repo = template_repo

    def collect(self, query: BotOverviewQuery) -> EngineBotOverview:
        bot = query.bot
        template_config = _resolve_template_config(
            bot_id=query.bot_id, bot=bot, template_repo=self._template_repo
        )
        config = template_config or {}
        return EngineBotOverview(
            default_model=_text(config.get("model")),
            payload=AICodingEnginePayload(
                engine=query.engine,
                template_type=_text(bot.get("template_type")),
                template_version=_text(config.get("template_version")),
                bot_type=_text(bot.get("bot_type")),
                template_factory=_template_factory(template_config),
                hosted_workspace_enabled=has_dima_workspace_enabled(config),
                devflow_workflow=_text(extract_workflow_name(config)),
                code_repos=extract_code_repo_urls(config),
                yuque_kb_repos=_yuque_source_urls(config),
            )
        )


__all__ = ["AICodingBotOverviewProvider"]
