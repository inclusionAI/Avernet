"""Rule 25 conformance — DRMReaderPlugin.

Consumer under test: ``ArcaBotCreateBaasRolloutConfigProvider``, which reads a
DRM flag via the injected ``DRMReaderPlugin`` and parses it into a rollout
config. We exercise that consumer against the deployable impls and assert the
Protocol contract holds:

- community ``CommunityDRMReader`` → no center ⇒ ``read`` is ``None`` ⇒ rollout
  fails closed (disabled).
- local ``NoopDRMReader`` → same unset semantics (local/test parity).
- the DI-resolved ``world`` form: the test-column impl resolves and a consumer
  reading through it sees every flag unset.
"""
from __future__ import annotations

from agentclaw.community.core.devices.services.arca_bot_create_baas_rollout_config import (
    ArcaBotCreateBaasRolloutConfigProvider,
)
from agentclaw.community.plugin_api.drm import DRMReaderPlugin
from agentclaw.community.plugins.community.drm import CommunityDRMReader
from agentclaw.community.plugins.local.drm import NoopDRMReader


def test_community_reader_makes_rollout_fail_closed():
    provider = ArcaBotCreateBaasRolloutConfigProvider(drm_reader=CommunityDRMReader())
    assert provider.get().enabled is False


def test_noop_reader_makes_rollout_fail_closed():
    provider = ArcaBotCreateBaasRolloutConfigProvider(drm_reader=NoopDRMReader())
    assert provider.get().enabled is False


def test_world_resolved_reader_flows_through_consumer(world):
    """Canonical Rule 25 form: the DI-bound reader (test column → NoopDRMReader)
    is resolved via the injector and read through the consumer — every flag is
    unset ⇒ rollout disabled."""
    reader = world.get(DRMReaderPlugin)
    assert reader.read("any-drm-id") is None
    provider = ArcaBotCreateBaasRolloutConfigProvider(drm_reader=reader)
    assert provider.get().enabled is False
