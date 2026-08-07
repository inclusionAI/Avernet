"""Composition of the auth subsystem (composition root, Rule 14).

Concrete authn strategies are provided by the DI container. This module only
turns the configured strategy names and route-security table into the core
``Authenticator`` model; it does not construct plugins and does not read any
module-level strategy registry.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, cast

from gateway.community.config import UserConfig
from gateway.community.core.authn import Authenticator, IdentityChain, RouteSecurity
from gateway.community.spi.authn import (
    AuthStrategy,
    PrincipalType,
)

_logger = logging.getLogger("bootstrap")

# Fail-closed default: every route requires an authenticated user.
_DEFAULT_TABLE = {"/**": {"user": "required"}}


def _default_chains(
    pool: Mapping[str, AuthStrategy],
) -> dict[PrincipalType, IdentityChain]:
    return {
        PrincipalType.USER: IdentityChain(PrincipalType.USER, (pool["google"],)),
        PrincipalType.BOT: IdentityChain(PrincipalType.BOT, (pool["bot_token"],)),
        PrincipalType.APP: IdentityChain(PrincipalType.APP, (pool["app_token"],)),
        PrincipalType.ACCESS_KEY: IdentityChain(
            PrincipalType.ACCESS_KEY, (pool["access_key_token"],)
        ),
    }


def build_authenticator(
    *,
    strategies: Mapping[str, AuthStrategy],
    user_config: UserConfig,
) -> Authenticator:
    """Build the identity-chain registry + route table from DI and typed config."""
    return Authenticator(
        strategies=_strategy_chains(strategies, user_config=user_config),
        route_security=_load_route_security(user_config),
    )


def _strategy_chains(
    pool: Mapping[str, AuthStrategy],
    *,
    user_config: UserConfig | None = None,
) -> dict[PrincipalType, IdentityChain]:
    """Parse ``user_config.identity_strategies`` using a DI-resolved pool.

    ``user_config`` is optional only for narrow legacy unit tests; production
    callers pass the typed ``UserConfig`` through ``build_authenticator``.
    """
    if user_config is None:
        from gateway.community.config import ConfigLoader

        user_config = ConfigLoader.load().user_config

    _logger.info(
        "authn strategy pool: [%s]",
        ", ".join(
            f"{name}: {type(strategy).__name__}"
            for name, strategy in sorted(pool.items())
        ),
    )
    defaults = _default_chains(pool)
    declared = user_config.identity_strategies
    chains: dict[PrincipalType, IdentityChain] = {}
    for identity_value, names in declared.items():
        try:
            identity = PrincipalType(identity_value)
        except ValueError as exc:
            raise KeyError(
                f"unknown identity '{identity_value}' "
                f"in application.yaml user_config.identity_strategies"
            ) from exc
        declared_chain: list[AuthStrategy] = []
        for name in names or []:
            if name not in pool:
                raise KeyError(
                    f"unknown strategy '{name}' for identity '{identity.value}' "
                    f"in application.yaml user_config.identity_strategies"
                )
            declared_chain.append(pool[name])
        chains[identity] = IdentityChain(identity, tuple(declared_chain))
    for identity, default_chain in defaults.items():
        chains.setdefault(identity, default_chain)

    _logger.info(
        "identity strategies (application.yaml user_config.identity_strategies): %d chains\n%s",
        len(chains),
        "\n".join(
            f"  {idty.value}: [{', '.join(s.name for s in chain._strategies)}]"
            for idty, chain in chains.items()
        ),
    )
    return chains


def _load_route_security(user_config: UserConfig | None = None) -> RouteSecurity:
    if user_config is None:
        from gateway.community.config import ConfigLoader

        user_config = ConfigLoader.load().user_config
    table = cast(dict[str, Any], user_config.route_security or _DEFAULT_TABLE)
    _logger.info(
        "loading route security from application.yaml user_config.route_security"
    )
    rules = RouteSecurity.from_table(table)
    _logger.info(
        "route security (application.yaml user_config.route_security): %d routes\n%s",
        len(rules._rules),
        "\n".join(
            f"  {rule.method or '*'} /{'/'.join(rule.pattern.segments)} → "
            + str({idty.value: pres.value for idty, pres in rule.requirement.items()})
            for rule in rules._rules
        ),
    )
    return rules
