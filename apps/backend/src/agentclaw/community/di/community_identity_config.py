"""Community identity mode knob — ``user_config.community_identity``.

Which community passport the
simulator stack runs — the stateless ``SelfIssuedPassportPlugin`` (every apply
answers ISSUED) or the manual-consent plugin that mirrors prod AgentPass: an
apply leaves a PENDING application with the simulator's mock-services AgentPass
service, and only a human clicking that service's consent page can decide it.
The knob follows the teclaw delivery-mode precedent: a strict parser
whose yaml key, its two permitted values, and the typed result live together,
read exactly once at composition time — a deployment-time fact settled at
deployment time.

Strict is the point: a typo must fail the boot, not silently reselect a mode,
because the difference between the two modes is whether bot creation **can**
complete without a human. Absent is the one legitimate "unset" spelling: a
deployment that never wrote the block runs the pre-existing self-issued
behavior, byte-identically.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: The yaml block name under ``user_config``::
#:
#:     user_config:
#:       community_identity:
#:         passport_mode: manual_consent
#:         consent_base_url: "http://mock-services-simulator:31270/agentpass"
BLOCK_KEY = "community_identity"

#: The base URL key inside the block — required iff the mode is manual_consent,
#: because the manual plugin calls the mock-services AgentPass facade at this
#: address and no host may be hardcoded in code (arch rule). Supplied without
#: a trailing slash.
CONSENT_BASE_URL_KEY = "consent_base_url"


class CommunityPassportMode(StrEnum):
    """Which community passport implementation this deployment runs - a boot-time fact.

    Two values, lowercase on the wire, each naming an implementation rather
    than a setting (the teclaw delivery-mode vocabulary works the same way).
    """

    #: The pre-existing behavior: every apply answers ISSUED inline.
    SELF_ISSUED = "self_issued"
    #: Prod-parity: applies register PENDING; only a human decides.
    MANUAL_CONSENT = "manual_consent"


@dataclass(frozen=True)
class CommunityIdentityConfig:
    """Typed ``user_config.community_identity`` — read once at composition time.

    ``consent_base_url`` is the (whitespace-trimmed) base URL of the
    mock-services AgentPass facade — the simulator service that owns the
    passport application registry and serves the human consent page — and is
    empty exactly when the mode does not need one: the strict parser refuses a
    ``manual_consent`` block without it (the plugin could not reach the
    facade) and validates the scheme of whichever value it does find.
    """

    passport_mode: CommunityPassportMode = CommunityPassportMode.SELF_ISSUED
    consent_base_url: str = ""


_HTTP_PREFIXES = ("http://", "https://")


def community_identity_from_block(
    block: Mapping[str, Any] | None,
) -> CommunityIdentityConfig:
    """Strictly parse the ``community_identity`` block; raise ``ValueError`` on a mistake.

    ``block`` is the raw block mapping (see :data:`BLOCK_KEY`); ``None`` or a
    non-mapping reads as absent — the same "missing block" spelling the sibling
    config readers fall back from — and answers the default mode. Anything
    **written** is validated instead:

    ==============================  =========================================
    Value at ``passport_mode``      Mode
    ==============================  =========================================
    absent, or block missing       ``SELF_ISSUED``
    ``None`` (key with nothing      ``SELF_ISSUED``
    after it)
    ``"self_issued"`` /             the named mode (whitespace- and
    ``"manual_consent"``            case-insensitive, like the teclaw reader)
    anything else                   ``ValueError``
    ==============================  =========================================

    ``consent_base_url``, when written, must be a string and, after trimming,
    start with ``http://`` or ``https://``; a manual_consent block without it
    raises. Called by ``CommunityIdentityModule.configure`` at boot, where a
    config mistake belongs.
    """
    if block is None:
        return CommunityIdentityConfig()
    if not isinstance(block, Mapping):
        raise ValueError(
            f"user_config.{BLOCK_KEY}: must be a mapping, got {type(block).__name__}"
        )

    mode = CommunityPassportMode.SELF_ISSUED
    if "passport_mode" in block:
        raw_mode = block["passport_mode"]
        if raw_mode is not None:
            if not isinstance(raw_mode, str):
                raise ValueError(
                    f"user_config.{BLOCK_KEY}.passport_mode: not a mode name: {raw_mode!r}"
                )
            mode_text = raw_mode.strip().lower()
            try:
                mode = CommunityPassportMode(mode_text)
            except ValueError:
                raise ValueError(
                    f"user_config.{BLOCK_KEY}.passport_mode: "
                    f"expected "
                    f"'{CommunityPassportMode.SELF_ISSUED.value}' or "
                    f"'{CommunityPassportMode.MANUAL_CONSENT.value}': {raw_mode!r}"
                ) from None

    base_url = ""
    if CONSENT_BASE_URL_KEY in block:
        raw_url = block[CONSENT_BASE_URL_KEY]
        if raw_url is not None:
            if not isinstance(raw_url, str):
                raise ValueError(
                    f"user_config.{BLOCK_KEY}.{CONSENT_BASE_URL_KEY}: "
                    f"not a URL string: {raw_url!r}"
                )
            base_url = raw_url.strip()
    if base_url and not base_url.startswith(_HTTP_PREFIXES):
        raise ValueError(
            f"user_config.{BLOCK_KEY}.{CONSENT_BASE_URL_KEY}: "
            f"must start with http:// or https://: {base_url!r}"
        )

    if mode is CommunityPassportMode.MANUAL_CONSENT and not base_url:
        raise ValueError(
            f"user_config.{BLOCK_KEY}.{CONSENT_BASE_URL_KEY} is required when "
            f"passport_mode is '{CommunityPassportMode.MANUAL_CONSENT.value}'"
        )

    return CommunityIdentityConfig(
        passport_mode=mode,
        consent_base_url=base_url,
    )


__all__ = [
    "BLOCK_KEY",
    "CONSENT_BASE_URL_KEY",
    "CommunityIdentityConfig",
    "CommunityPassportMode",
    "community_identity_from_block",
]