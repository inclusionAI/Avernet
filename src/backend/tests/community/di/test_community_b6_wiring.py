"""Community device-column wiring (B6).

Pins that the community profile binds the B6 device-family Protocols to their
real community implementations, and that none is a ``MockSeam`` test double (the
community column ships real, deployable impls). Mirrors
``test_community_data_infra_wiring`` / ``test_community_b7_wiring``.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.devices.services.device_sync_dispatcher import (
    DeviceSyncDispatcher,
)
from agentclaw.community.di.container import build_injector
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport
from agentclaw.community.plugin_api.health_probe import HealthProbePlugin
from agentclaw.community.plugin_api.outbound_rules import OutboundRuleProvider
from agentclaw.community.plugins.community.device_adapter_transport import (
    CommunityDeviceAdapterTransport,
)
from agentclaw.community.plugins.community.device_sync import (
    CommunityDeviceSyncDispatcher,
)
from agentclaw.community.plugins.community.health_probe import CommunityHealthProbe
from agentclaw.community.plugins.community.outbound_rules import CommunityOutboundRuleProvider
from agentclaw.community.plugins.local._mock_seam import MockSeam


@pytest.fixture(scope="module")
def community_injector():
    return build_injector(profile=DeployProfile.COMMUNITY)


def test_community_binds_health_probe(community_injector):
    resolved = community_injector.get(HealthProbePlugin)
    assert isinstance(resolved, CommunityHealthProbe)
    assert not isinstance(resolved, MockSeam)
    assert resolved.mode_label == "community"


def test_community_binds_outbound_rule_provider(community_injector):
    resolved = community_injector.get(OutboundRuleProvider)
    assert isinstance(resolved, CommunityOutboundRuleProvider)
    assert not isinstance(resolved, MockSeam)


def test_community_binds_device_sync_resolver(community_injector):
    resolved = community_injector.get(DeviceSyncDispatcher)
    assert isinstance(resolved, CommunityDeviceSyncDispatcher)
    assert not isinstance(resolved, MockSeam)


def test_community_binds_device_adapter_transport(community_injector):
    resolved = community_injector.get(DeviceAdapterTransport)
    assert isinstance(resolved, CommunityDeviceAdapterTransport)
    assert not isinstance(resolved, MockSeam)
