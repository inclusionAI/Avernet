"""The internal-token provider follows the project-wide SecretResolver pattern.

Same branches as ``BotDormantModule._resolved_dormant_token``:
  - secret name empty (community / singlebox / test) → local fallback token
    (short-circuit; the resolver is never called)
  - the resolver returns a secret with a value       → use it (prod / pre)
  - the resolver returns None                        → fallback token
  - ``secret_value`` blank                           → fallback token
  - the resolver raises (transient)                  → empty value, which
    makes the auth Depends 401 every request rather than authorize an
    unverified caller
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.di.config import (
    SecretNamesConfig,
    SkillCenterInternalToken,
)
from agentclaw.community.di.modules.skill_center_internal_token_module import (
    _SINGLEBOX_FALLBACK_TOKEN,
    SkillCenterInternalTokenBindings,
)

_TEST_SECRET_NAME = "test_skill_center_internal_token"


def _resolve(
    secret_resolver, secret_name: str = _TEST_SECRET_NAME
) -> SkillCenterInternalToken:
    """Invoke the unbound provider directly with mocks; bypasses the injector."""
    return SkillCenterInternalTokenBindings()._resolved_internal_token(
        secret_resolver=secret_resolver,
        secret_names=SecretNamesConfig(skill_center_internal_token=secret_name),
    )


@pytest.mark.unit
def test_empty_secret_name_short_circuits_to_fallback():
    resolver = MagicMock()

    result = _resolve(resolver, secret_name="")

    assert result.value == _SINGLEBOX_FALLBACK_TOKEN
    resolver.get_secret.assert_not_called()


@pytest.mark.unit
def test_a_resolved_secret_is_used():
    resolver = MagicMock()
    resolver.get_secret.return_value = SimpleNamespace(
        secret_user="ignored", secret_value="real-token-from-the-secret-store"
    )

    result = _resolve(resolver)

    assert result.value == "real-token-from-the-secret-store"
    resolver.get_secret.assert_called_once_with(secret_name=_TEST_SECRET_NAME)


@pytest.mark.unit
@pytest.mark.parametrize(
    "returned", [None, SimpleNamespace(secret_value=""), SimpleNamespace()]
)
def test_a_missing_or_blank_secret_falls_back(returned):
    resolver = MagicMock()
    resolver.get_secret.return_value = returned

    assert _resolve(resolver).value == _SINGLEBOX_FALLBACK_TOKEN


@pytest.mark.unit
def test_a_raising_resolver_fails_closed():
    """An outage must 401 every request, not authorize an unverified caller."""
    resolver = MagicMock()
    resolver.get_secret.side_effect = RuntimeError("secret store unreachable")

    assert _resolve(resolver).value == ""
