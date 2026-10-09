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
from agentclaw.community.core.forum.models import AUTHOR_TYPE_BOT
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


def enrich_bot_display_names(
    records: Sequence[Any], *, request: Any
) -> list[Any]:
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
    needs_lookup = {
        record.author_id
        for record in records
        if getattr(record, "author_type", None) == AUTHOR_TYPE_BOT
        and record.display_name is None
        and record.author_id
    }
    lookup = {bid: _bot_display_name(bot_service, bid) for bid in needs_lookup}
    enriched: list[Any] = []
    for record in records:
        if (
            getattr(record, "author_type", None) == AUTHOR_TYPE_BOT
            and record.display_name is None
            and record.author_id in lookup
        ):
            name = lookup.get(record.author_id)
            if name:
                enriched.append(replace(record, display_name=name))
                continue
        enriched.append(record)
    return enriched


def enrich_bot_display_name(record: Any, *, request: Any) -> Any:
    """Single-record helper for the topic-detail read path."""
    return enrich_bot_display_names((record,), request=request)[0]
