"""The one rule for "may this Bot supply identity?".

Three boundaries ask the same question and must not answer it differently:

* the agentswarm submit seam (``plugins/community/agentswarm/bot_authorization``),
  before any run row is written;
* the creation boundary (``core/bot_management/identity_source.resolve_identity_source``),
  before any manifest is stored or Passport applied for;
* authorization completion (``core/bot_management/identity_source.inherited_agent_code``),
  where the created Bot is finally minted under that identity.

They were written at different times and each read only the cached copy on the
Bot row (``ac_bots.ext.passport``). That copy is a snapshot of what the Passport
provider once said, and Bots created before it existed carry none — so all
three refused a Bot whose passport the provider still answers for. Fixing one
boundary alone moved the failure later, into the swarm's spawn, where it read
as a swarm bug.

Ownership is settled first and by the caller: this module never looks a Bot up,
so it can be shared by an adapter that resolves through a repository and by one
that resolves through a service, without either learning the other's world.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agentclaw.community.core.agentswarm.service.protocols import (
    IdentitySourceRefusal,
    IDENTITY_SOURCE_NO_PASSPORT,
    IDENTITY_SOURCE_UNVERIFIED,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import PassportPlugin

logger = get_logger()

#: The cached status that is a real answer rather than a silent cache: the Bot
#: applied and a human has not decided. The provider is about to refuse it too,
#: so falling back past it would accept a Bot that cannot spawn.
PENDING_STATUS = "PENDING"

#: What a provider sends when it reports an issued passport. Providers that
#: answer with an agent code at all are those that issue on approval; some omit
#: the field entirely (the local and self-issued plugins, and the prod facade,
#: which sends no status). Absent therefore reads as issued; only a *present*
#: non-issued status refuses.
ISSUED_STATUS = "ISSUED"


def cached_agent_code(passport: Mapping[str, Any] | None) -> str:
    """The usable agent code in a Bot row's cached passport, or ``""``.

    ``""`` covers both "no cached record" and "a record with no code" — the
    two states that mean the cache has nothing to say and the provider must be
    asked.
    """
    if not isinstance(passport, Mapping):
        return ""
    if passport.get("status") == PENDING_STATUS:
        return ""
    return str(passport.get("agent_code") or "").strip()


def cached_status_is_pending(passport: Mapping[str, Any] | None) -> bool:
    """Whether the cache answered "not decided yet" rather than "unknown"."""
    return isinstance(passport, Mapping) and passport.get("status") == PENDING_STATUS


def provider_agent_code(
    *,
    passport_plugin: PassportPlugin | None,
    bot_id: str,
    owner_id: str,
) -> str | IdentitySourceRefusal:
    """The agent code the Passport provider holds for an owned Bot.

    Called only after ownership is settled, so nothing here can probe another
    account's passport. A provider failure refuses — accepting on failure would
    let an outage mint an identity — and says "could not be verified" rather
    than blaming the Bot for a passport it may well have.
    """
    if passport_plugin is None:
        logger.warning(
            "[identity_source] provider lookup skipped for bot=%s: no passport "
            "plugin bound (source is unverified)",
            bot_id,
        )
        return IdentitySourceRefusal(
            IDENTITY_SOURCE_UNVERIFIED,
            f"identity source {bot_id!r} could not be verified: this "
            "deployment has no passport provider bound",
        )
    try:
        answer = passport_plugin.query_agent_passport(bot_id, owner_id)
    except Exception:  # noqa: BLE001 — refuse, never accept on failure
        logger.warning(
            "[identity_source] passport lookup failed for bot=%s owner=%s "
            "(source is unverified)",
            bot_id, owner_id,
        )
        return IdentitySourceRefusal(
            IDENTITY_SOURCE_UNVERIFIED,
            f"identity source {bot_id!r} could not be verified: the passport "
            "provider failed to answer",
        )
    return issued_agent_code_from_provider_answer(
        answer=answer, bot_id=bot_id,
    )


def issued_agent_code_from_provider_answer(
    *, answer: Mapping[str, Any] | None, bot_id: str,
) -> str | IdentitySourceRefusal:
    """Read one provider answer: an issued code, or a refusal.

    A provider that answers with an agent code has answered — the code is only
    minted once a passport is issued. A status that is present and not issued is
    a positive "not issued" and refuses.
    """
    facts = answer if isinstance(answer, Mapping) else {}
    code = str(facts.get("agent_code") or "").strip()
    status = str(facts.get("status") or "").strip()
    if code and status in ("", ISSUED_STATUS):
        return code
    return IdentitySourceRefusal(
        IDENTITY_SOURCE_NO_PASSPORT,
        f"identity source {bot_id!r} holds no issued passport: neither the Bot "
        "record nor the passport provider reports an issued agent code",
    )


def resolve_agent_code(
    *,
    passport: Mapping[str, Any] | None,
    passport_plugin: PassportPlugin | None,
    bot_id: str,
    owner_id: str,
) -> str | IdentitySourceRefusal:
    """The identity one owned Bot supplies: cache first, provider fallback.

    The order is the whole point (design D1). A cached code is accepted without
    asking the provider; a cached ``PENDING`` is refused without asking, because
    the cache has answered; only a cache that says nothing falls through to the
    provider.
    """
    code = cached_agent_code(passport)
    if code:
        return code
    if cached_status_is_pending(passport):
        return IdentitySourceRefusal(
            IDENTITY_SOURCE_NO_PASSPORT,
            f"identity source {bot_id!r} holds no issued passport yet: its "
            "passport application is still pending authorization",
        )
    return provider_agent_code(
        passport_plugin=passport_plugin, bot_id=bot_id, owner_id=owner_id,
    )