"""Community identity column wiring (B4 T9).

Pins that the community profile binds all four identity Protocols to their real
community implementations, and that none of them is a ``MockSeam`` test double
(the community column ships real, deployable impls).
"""
from __future__ import annotations

import pytest

from agentclaw.community.di.container import build_injector
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.plugin_api.auth import AuthPlugin
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.token_exchange import TokenExchangePlugin
from agentclaw.community.plugins.community.auth import OidcAuthPlugin
from agentclaw.community.plugins.community.auth_relationship import (
    CommunityAuthRelationshipPlugin,
)
from agentclaw.community.plugins.community.passport import SelfIssuedPassportPlugin
from agentclaw.community.plugins.community.passport_consent import (
    ManualConsentPassportPlugin,
)
from agentclaw.community.plugins.community.token_exchange import (
    PassthroughTokenExchangePlugin,
)
from agentclaw.community.plugins.local._mock_seam import MockSeam

_EXPECTED = {
    AuthPlugin: OidcAuthPlugin,
    PassportPlugin: SelfIssuedPassportPlugin,
    AuthRelationshipPlugin: CommunityAuthRelationshipPlugin,
    TokenExchangePlugin: PassthroughTokenExchangePlugin,
}

_BASE_URL = "http://mock-services-simulator.stable.alipay.net:31270/agentpass"


@pytest.fixture(scope="module")
def community_injector():
    return build_injector(profile=DeployProfile.COMMUNITY)


@pytest.mark.parametrize("protocol,impl", list(_EXPECTED.items()))
def test_community_binds_identity_impl(community_injector, protocol, impl):
    resolved = community_injector.get(protocol)
    assert isinstance(resolved, impl)


@pytest.mark.parametrize("impl", list(_EXPECTED.values()))
def test_community_identity_impls_are_not_mock_seam(impl):
    assert not issubclass(impl, MockSeam)


# ── the manual-consent column ────────────────────────────────────────────────


def _injector_with_user_config(monkeypatch, user_config):
    from agentclaw.community.di.container import build_injector
    from agentclaw.community.di.modules import config_module

    monkeypatch.setattr(
        config_module, "_user_config", lambda: dict(user_config)
    )
    return build_injector(profile=DeployProfile.COMMUNITY)


def test_default_mode_binds_self_issued_passport(community_injector):
    """Shipped default: every apply answers ISSUED inline."""
    resolved = community_injector.get(PassportPlugin)
    assert isinstance(resolved, SelfIssuedPassportPlugin)
    assert not isinstance(resolved, ManualConsentPassportPlugin)


def test_manual_mode_binds_the_mock_services_client_plugin(monkeypatch):
    injector = _injector_with_user_config(
        monkeypatch,
        {
            "community_identity": {
                "passport_mode": "manual_consent",
                "consent_base_url": _BASE_URL,
            }
        },
    )

    resolved = injector.get(PassportPlugin)
    assert isinstance(resolved, ManualConsentPassportPlugin)
    assert resolved._base_url == _BASE_URL


def test_invalid_mode_fails_the_composition(monkeypatch):
    with pytest.raises(ValueError, match="passport_mode"):
        _injector_with_user_config(
            monkeypatch,
            {"community_identity": {"passport_mode": "coin-flip"}},
        )


def test_manual_mode_without_base_url_fails_the_composition(monkeypatch):
    with pytest.raises(ValueError, match="consent_base_url"):
        _injector_with_user_config(
            monkeypatch,
            {"community_identity": {"passport_mode": "manual_consent"}},
        )