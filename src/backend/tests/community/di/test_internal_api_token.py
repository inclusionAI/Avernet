"""The shared internal-token provider, branch by branch.

Same SecretResolver pattern as ``BotDormantModule._resolved_dormant_token``,
with one deliberate difference this file pins: when no secret name is
configured, only a **local** profile falls back to the published constant. A
corp deployment gets ``""`` — the routes close rather than being gated by a
token anyone can read in this repository.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.di.config import InternalApiToken, SecretNamesConfig
from agentclaw.community.di.modules.internal_api_token_module import (
    _SINGLEBOX_FALLBACK_TOKEN,
    InternalApiTokenBindings,
)

_TEST_SECRET_NAME = "test_internal_api_token"


def _resolve(
    secret_resolver,
    secret_name: str = _TEST_SECRET_NAME,
    *,
    local: bool = True,
) -> InternalApiToken:
    """Invoke the unbound provider directly with mocks; bypasses the injector."""
    return InternalApiTokenBindings(local=local)._resolved_internal_api_token(
        secret_resolver=secret_resolver,
        secret_names=SecretNamesConfig(internal_api_token=secret_name),
    )


@pytest.mark.unit
def test_empty_secret_name_short_circuits_to_local_fallback():
    resolver = MagicMock()

    result = _resolve(resolver, secret_name="", local=True)

    assert result.value == _SINGLEBOX_FALLBACK_TOKEN
    resolver.get_secret.assert_not_called()


@pytest.mark.unit
def test_empty_secret_name_in_corp_closes_the_routes():
    """An unconfigured corp deployment must not fall back to a public token."""
    resolver = MagicMock()

    result = _resolve(resolver, secret_name="", local=False)

    assert result.value == ""
    resolver.get_secret.assert_not_called()


@pytest.mark.unit
def test_a_resolved_secret_is_used():
    resolver = MagicMock()
    resolver.get_secret.return_value = SimpleNamespace(
        secret_user="ignored", secret_value="real-token-from-the-secret-store"
    )

    result = _resolve(resolver, local=False)

    assert result.value == "real-token-from-the-secret-store"
    resolver.get_secret.assert_called_once_with(secret_name=_TEST_SECRET_NAME)


@pytest.mark.unit
@pytest.mark.parametrize("resolved", [None, SimpleNamespace(secret_value="")])
def test_unresolvable_secret_follows_the_profile(resolved):
    resolver = MagicMock()
    resolver.get_secret.return_value = resolved

    assert _resolve(resolver, local=True).value == _SINGLEBOX_FALLBACK_TOKEN
    assert _resolve(resolver, local=False).value == ""


@pytest.mark.unit
@pytest.mark.parametrize("local", [True, False])
def test_a_raising_resolver_is_failure_closed(local):
    """A transient outage never authorizes a caller, local profile included."""
    resolver = MagicMock()
    resolver.get_secret.side_effect = RuntimeError("secret store unreachable")

    assert _resolve(resolver, local=local).value == ""
