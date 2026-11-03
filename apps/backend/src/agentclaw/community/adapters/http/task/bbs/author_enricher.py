"""BOT-only author display-name enrichment for BBS reads.

Why split the policy by author kind:

* HUMAN ``display_name`` / ``avatar_url`` are an OPTIONAL write snapshot the
  write caller already holds (the staff directory is not wired in every
  deployment, so there is nothing for the backend to look up). Persisted
  verbatim on the row and returned as stored — no enrichment.

* BOT names, by contrast, are ALWAYS available server-side through
  ``BotServiceProtocol``, so a BOT-authored row does NOT depend on the
  write side supplying its own name. At read time the backend fills
  ``display_name`` from the bot inventory (``get_bot_by_id`` → ``bot_name``)
  when the stored snapshot is null. Bots carry no avatar on this surface:
  ``avatar_url`` is returned verbatim (the write snapshot, typically null).

Best-effort and never raises: a bare Request with no injector (direct unit
test calls) is a no-op; an unbound ``BotService`` or a lookup miss leaves the
stored ``display_name`` unchanged. Resolves the bot service off the request
injector only — no new Injected dependency, so existing direct route call
assertions (a fixed service-call key set) are unaffected.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.task.forum.interaction.models import (
    ForumInteractionTargetType,
)
from agentclaw.community.core.task.forum.models import AUTHOR_TYPE_BOT
from agentclaw.community.log import get_logger

logger = get_logger()


def _request_injector(request: Any) -> Any:
    try:
        app = getattr(request, "app", None)
    except Exception:  # noqa: BLE001 — bare test requests have no app state
        return None
    if app is None:
        return None
    try:
        return getattr(app.state, "injector", None)
    except Exception:  # noqa: BLE001 — defensive: never fail the read
        return None


def _bot_service(injector: Any) -> Any:
    if injector is None:
        return None
    try:
        return injector.get(BotServiceProtocol)
    except Exception:  # noqa: BLE001 — not wired ⇒ None, never raises
        logger.warning(
            "no %s bound; BOT author rows keep stored display_name",
            BotServiceProtocol.__name__,
        )
        return None


def _bot_display_name(bot_service: Any, bot_id: str) -> str | None:
    if bot_service is None:
        return None
    try:
        row = bot_service.get_bot_by_id(bot_id)
    except Exception:  # noqa: BLE001 — lookup failure ⇒ null, not a 5xx
        return None
    if not row:
        return None
    name = row.get("bot_name") if hasattr(row, "get") else None
    return str(name or "").strip() or None


def enrich_bot_display_names(records: Sequence[Any], *, request: Any) -> list[Any]:
    """Return ``records`` with BOT-authored rows' ``display_name`` filled from
    the bot inventory when the stored snapshot is null.

    Order-preserving. HUMAN rows pass through untouched; BOT rows already
    carrying a non-null ``display_name`` keep it (the write snapshot wins).
    ``avatar_url`` is never touched here — bots carry no avatar on this surface.
    """
    if not records:
        return list(records)
    injector = _request_injector(request)
    bot_service = _bot_service(injector)

    def _flat_candidates(source: Sequence[Any]) -> list[Any]:
        candidates: list[Any] = []
        for record in source:
            candidates.append(record)
            children = getattr(record, "children", ())
            if children:
                candidates.extend(_flat_candidates(tuple(children)))
        return candidates

    needs_lookup = {
        record.author_id
        for record in _flat_candidates(records)
        if getattr(record, "author_type", None) == AUTHOR_TYPE_BOT
        and record.display_name is None
        and record.author_id
    }
    lookup = {bid: _bot_display_name(bot_service, bid) for bid in needs_lookup}

    def _enrich_record(record: Any) -> Any:
        if (
            getattr(record, "author_type", None) == AUTHOR_TYPE_BOT
            and record.display_name is None
            and record.author_id in lookup
        ):
            name = lookup.get(record.author_id)
            if name:
                record = replace(record, display_name=name)
        children = getattr(record, "children", ())
        if children:
            enriched_children = tuple(_enrich_record(child) for child in children)
            if enriched_children != tuple(children):
                record = replace(record, children=enriched_children)
        return record

    return [_enrich_record(record) for record in records]


def enrich_bot_display_name(record: Any, *, request: Any) -> Any:
    """Single-record helper for the topic-detail read path."""
    return enrich_bot_display_names((record,), request=request)[0]


def _needs_personal_center_bot_name(display_name: object, identity_id: str) -> bool:
    normalized = str(display_name or "").strip()
    return not normalized or normalized == identity_id


def _personal_center_bot_ids(records: Sequence[Any]) -> set[str]:
    bot_ids: set[str] = set()
    for record in records:
        topic = getattr(record, "topic_context", None)
        if (
            topic is not None
            and getattr(topic, "author_type", None) == AUTHOR_TYPE_BOT
            and _needs_personal_center_bot_name(
                topic.author_display_name, topic.author_id
            )
        ):
            bot_ids.add(topic.author_id)

        reply = getattr(record, "replied_to_post", None)
        if (
            reply is not None
            and getattr(reply, "author_type", None) == AUTHOR_TYPE_BOT
            and _needs_personal_center_bot_name(
                reply.author_display_name, reply.author_id
            )
        ):
            bot_ids.add(reply.author_id)

        followed = getattr(record, "followed_context", None)
        if (
            followed is not None
            and getattr(followed, "target_type", None) == ForumInteractionTargetType.BOT
            and _needs_personal_center_bot_name(
                followed.display_name, followed.target_id
            )
        ):
            bot_ids.add(followed.target_id)
    return bot_ids


def enrich_personal_center_bot_display_names(page: Any, *, request: Any) -> Any:
    """Fill the three Bot display slots in a personal-center activity page.

    Personal-center projections fall back to the stable id when the row has no
    write-side snapshot. Those id-only BOT values are resolved from the backend
    Bot inventory here; any real stored name keeps precedence. Human identities,
    Bot avatars, and lookup misses are untouched so this read remains best-effort.
    """
    if page is None:
        return page
    items = tuple(page.items)
    if not items:
        return page

    bot_ids = _personal_center_bot_ids(items)
    if not bot_ids:
        return page
    bot_service = _bot_service(_request_injector(request))
    lookup = {
        bot_id: _bot_display_name(bot_service, bot_id) for bot_id in sorted(bot_ids)
    }
    if not any(lookup.values()):
        return page

    def _replace_id_fallback(value: object, identity_id: str) -> object:
        name = lookup.get(identity_id)
        if name and _needs_personal_center_bot_name(value, identity_id):
            return name
        return value

    enriched_items: list[Any] = []
    for record in items:
        topic = getattr(record, "topic_context", None)
        if topic is not None and getattr(topic, "author_type", None) == AUTHOR_TYPE_BOT:
            topic = replace(
                topic,
                author_display_name=_replace_id_fallback(
                    topic.author_display_name, topic.author_id
                ),
            )

        reply = getattr(record, "replied_to_post", None)
        if reply is not None and getattr(reply, "author_type", None) == AUTHOR_TYPE_BOT:
            reply = replace(
                reply,
                author_display_name=_replace_id_fallback(
                    reply.author_display_name, reply.author_id
                ),
            )

        followed = getattr(record, "followed_context", None)
        if (
            followed is not None
            and getattr(followed, "target_type", None) == ForumInteractionTargetType.BOT
        ):
            followed = replace(
                followed,
                display_name=_replace_id_fallback(
                    followed.display_name, followed.target_id
                ),
            )

        enriched_items.append(
            replace(
                record,
                topic_context=topic,
                replied_to_post=reply,
                followed_context=followed,
            )
        )
    return replace(page, items=tuple(enriched_items))
