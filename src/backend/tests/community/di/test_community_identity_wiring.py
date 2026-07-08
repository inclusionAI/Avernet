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
