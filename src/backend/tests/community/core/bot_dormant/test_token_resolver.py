"""Verify BotDormantModule._resolved_dormant_token follows the project-wide
SecretResolver pattern.

Resolution branches:
  - secret name empty (community / singlebox / test) → local fallback token
    (short-circuit; resolver not called)
  - Mist returns secret with value → use it (prod / pre path)
  - Mist returns None              → fallback to singlebox token (local path)
  - secret_value blank             → fallback to singlebox token
  - resolver raises (transient)    → empty value (failure-closed → 401)

The secret name comes from ``SecretNamesConfig.dormant_internal_token`` (the
``secret_names`` yaml block) — the neutral code no longer hardcodes it.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.common_config import CommonWhiteListService
from agentclaw.community.di.config import (
    DormantConfig,
    DormantInternalToken,
    DormantNotifyConfig,
    SecretNamesConfig,
)
from agentclaw.community.di.modules.bot_dormant_module import (
    BotDormantModule,
    _SINGLEBOX_FALLBACK_TOKEN,
)

_TEST_SECRET_NAME = "test_dormant_internal_token"


def _resolve(secret_resolver, secret_name: str = _TEST_SECRET_NAME) -> DormantInternalToken:
    """Invoke the unbound provider directly with mocks; bypasses injector."""
    module = BotDormantModule()
    return module._resolved_dormant_token(
        secret_resolver=secret_resolver,
        secret_names=SecretNamesConfig(dormant_internal_token=secret_name),
    )


@pytest.mark.unit
def test_empty_secret_name_short_circuits_to_fallback():
    """No configured name (community / singlebox / test) → local fallback token,
    without ever calling the resolver."""
    resolver = MagicMock()

    result = _resolve(resolver, secret_name="")

    assert result.value == _SINGLEBOX_FALLBACK_TOKEN
    resolver.get_secret.assert_not_called()


@pytest.mark.unit
def test_mist_returns_secret_uses_secret_value():
    """Prod path: the configured name resolves a secret → use its secret_value,
    and the resolver is called with exactly the configured name."""
    resolver = MagicMock()
    resolver.get_secret.return_value = SimpleNamespace(
        secret_user="ignored",
        secret_value="real-token-from-mist-32-chars",
    )

    result = _resolve(resolver)

    assert result.value == "real-token-from-mist-32-chars"
    resolver.get_secret.assert_called_once_with(secret_name=_TEST_SECRET_NAME)


@pytest.mark.unit
def test_mist_returns_none_falls_back_to_singlebox_token():
    """Configured name but resolver returns None → fallback token."""
    resolver = MagicMock()
    resolver.get_secret.return_value = None

    result = _resolve(resolver)

    assert result.value == _SINGLEBOX_FALLBACK_TOKEN


@pytest.mark.unit
def test_mist_returns_secret_with_empty_value_falls_back():
    """Edge: the row exists but its secret_value is blank → fallback."""
    resolver = MagicMock()
    resolver.get_secret.return_value = SimpleNamespace(secret_user="u", secret_value="")

    result = _resolve(resolver)

    assert result.value == _SINGLEBOX_FALLBACK_TOKEN


@pytest.mark.unit
def test_resolver_exception_yields_empty_token_failure_closed():
    """Transient Mist failure with a configured name → fail closed (401)."""
    resolver = MagicMock()
    resolver.get_secret.side_effect = RuntimeError("mist down")

    result = _resolve(resolver)

    assert result.value == ""


@pytest.mark.unit
def test_dormant_service_provider_passes_common_whitelist_service():
    module = BotDormantModule()
    common_whitelist_service = MagicMock(spec=CommonWhiteListService)

    service = module._dormant_bot_service(
        db=MagicMock(),
        baas_client=MagicMock(),
        bot_service=MagicMock(),
        passport_plugin=MagicMock(),
        scan_policy=MagicMock(),
        common_whitelist_service=common_whitelist_service,
        config=DormantConfig(),
        notify_config=DormantNotifyConfig(),
    )

    assert service._common_whitelist_service is common_whitelist_service
