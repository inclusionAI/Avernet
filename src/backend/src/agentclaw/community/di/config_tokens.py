"""Bearer tokens resolved at DI time from ``SecretResolver``.

These are **values**, not names: each is produced by a composition-root
provider that looks up the registry name in ``SecretNamesConfig`` and hands
back what it found. They live apart from ``config.py`` because none of them is
a yaml block — nothing here is typed from ``user_config``, and a token value
must never be written into a config file.

Every one of them shares a failure mode: an empty ``value`` closes the routes
it gates (the auth Depends answers 401) rather than authorizing an unverified
caller. Each provider's docstring says which conditions produce it.

Re-exported from ``agentclaw.community.di.config`` so existing
``from ...di.config import DormantInternalToken`` imports keep working.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DormantInternalToken:
    """Resolved bearer token for /api/internal/dormant/* endpoints.

    Produced by ``BotDormantModule._resolved_dormant_token``. Single source
    of truth for the secret name is the constant in BotDormantModule; YAML
    does NOT carry the token (would leak it in repo). Resolution rules
    match the project-wide pattern (see ``plugins/prod/outbound_rules.py``
    theat_token handling, ``core/skill_center/services/skill_scan.py``
    skillscan_agent_api_key):

      - Mist returns a secret object  → ``.value = secret.secret_value``
      - Mist returns None (singlebox /  → ``.value = <fallback constant>``
        Mist unreachable / no secret)     (so singlebox联调依然可用)
      - resolver raises                → ``.value = ""`` (failure-closed)

    Empty ``value`` makes the auth Depends 401 all requests
    (feature-off failure mode).
    """

    value: str = ""


@dataclass(frozen=True)
class TcFileServiceToken:
    """Resolved shared Bearer token for the OCB ↔ ECB TC integration."""

    value: str = ""


@dataclass(frozen=True)
class SkillCenterInternalToken:
    """Resolved bearer token for ``/api/internal/skill-center/*`` endpoints.

    Produced by ``SkillCenterInternalTokenBindings._resolved_internal_token``,
    with the same
    resolution rules and the same failure-closed empty default as
    ``DormantInternalToken``: an empty ``value`` makes the auth Depends 401
    every request rather than authorize an unverified caller.

    Separate from the dormant token on purpose — these endpoints converge
    capability state for whole pages of Bots, so the two operations are
    granted independently.
    """

    value: str = ""


@dataclass(frozen=True)
class InternalApiToken:
    """Resolved Bearer token for routes with no end-user credential to check.

    The general-purpose sibling of ``DormantInternalToken`` /
    ``SkillCenterInternalToken``: those gate one operation each and are granted
    independently, this one is the shared grant for machine callers —
    ``/api/public/bots/*`` is the first. Produced by
    ``InternalApiTokenBindings._resolved_internal_api_token``, which documents
    the resolution rules; empty ``value`` always means the routes are closed.
    """

    value: str = ""
