"""Task bot identity helpers for external bot-addressed calls."""
from __future__ import annotations


def compose_bot_identity(bot_id: str, owner_id: str | None = None) -> str:
    """Return the bot address expected by bot-addressed OpenAPI/BCS calls.

    Product task state stores ``bot_id`` and ``owner_id`` separately. Older
    callers may already provide the composite ``bot_id:owner_id`` value. When
    both fields are present, the explicit owner is authoritative: preserve the
    Bot portion and rebuild the canonical composite address. This prevents a
    stale or mismatched embedded owner from reaching the external API.
    """
    normalized_bot_id = str(bot_id or "").strip()
    if not owner_id:
        return normalized_bot_id
    normalized_owner_id = str(owner_id).strip()
    if not normalized_owner_id:
        return normalized_bot_id
    product_bot_id = normalized_bot_id.partition(":")[0]
    return f"{product_bot_id}:{normalized_owner_id}"
