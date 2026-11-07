"""Task bot identity helpers for external bot-addressed calls."""

from __future__ import annotations


_INVALID_SINGLE_BOT_ASSIGNEES = {"NOT_DECIDED"}
_DEFAULT_PRODUCT_BOT_ID = "default"


def is_dispatchable_single_bot_assignee(assignee: str | None) -> bool:
    """Whether a Single-Bot node carries an actually dispatchable Bot identity.

    Relay planning must never persist a decision placeholder as later runtime state.
    ``NOT_DECIDED`` describes the fact that S5D has not produced a real
    candidate; it is not a Bot ID and cannot be delivered to OpenAPI/BCS.
    """
    normalized = str(assignee or "").strip()
    return bool(normalized) and normalized not in _INVALID_SINGLE_BOT_ASSIGNEES


def compose_bbs_author_identity(bot_id: str, owner_user_id: str) -> str:
    """Return the identity that BBS stores as a BOT author or actor.

    The product ``default`` Bot id is shared by every owner, so BBS writes must
    make the implicit owner explicit as ``default:<owner_user_id>``. Named Bot
    ids are already unique product entities and remain bare.  Transport calls
    still use :func:`compose_bot_identity`, which addresses every named Bot as
    ``bot_id:owner_user_id``.
    """
    normalized_bot_id = str(bot_id or "").strip()
    normalized_owner_id = str(owner_user_id or "").strip()
    if not normalized_bot_id:
        raise ValueError("bot_id is required for a BBS author")
    if normalized_bot_id != _DEFAULT_PRODUCT_BOT_ID:
        return normalized_bot_id
    if not normalized_bot_id or not normalized_owner_id:
        raise ValueError("owner_user_id is required for the default BBS Bot")
    return compose_bot_identity(normalized_bot_id, normalized_owner_id)


def is_bare_default_bot_identity(bot_identity: str) -> bool:
    """Whether an identity is the shared, owner-ambiguous bare ``default`` id."""
    return str(bot_identity or "").strip() == _DEFAULT_PRODUCT_BOT_ID and bool(
        str(bot_identity or "").strip()
    )


def parse_scoped_bot_identity(bot_identity: str) -> tuple[str, str] | None:
    """Split a composite ``bot_id:owner_id`` address into its two parts.

    This is the opposite of :func:`compose_bot_identity`.  A value with no
    ``:`` is treated as a bare product Bot id and returns ``None``, so callers
    can fall back to their existing bare-id lookup.
    """
    normalized = str(bot_identity or "").strip()
    bot_id, separator, owner_id = normalized.partition(":")
    if not separator or not bot_id or not owner_id:
        return None
    return bot_id, owner_id


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
