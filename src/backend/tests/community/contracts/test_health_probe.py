"""Rule 25 conformance — HealthProbePlugin.

Consumer under test: ``GET /api/system/health/engine``
(api/system/router.py:43). The endpoint resolves the plugin via DI,
asks it for engine health + reads ``probe.mode_label``, and folds the
result into a fixed envelope.

Plugin-hit assertion: ``mode_label`` is the only field on the
endpoint's response sourced exclusively from the plugin. The local
``LocalHealthProbe`` returns ``"local"`` — observing that string
proves the consumer reached the plugin.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_engine_health_endpoint_reflects_plugin_mode_label(
    app_with_testing_modules,
) -> None:
    client = TestClient(app_with_testing_modules)
    resp = client.get(
        "/api/system/health/engine", cookies={"staff_id": "alice"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "local"


def test_community_world_binds_real_health_probe(community_world) -> None:
    """B6: the community profile resolves ``HealthProbePlugin`` to a real impl
    (``CommunityHealthProbe``, not a ``MockSeam``) satisfying the contract —
    ``mode_label`` reads ``"community"`` (the field the consumer surfaces)."""
    from agentclaw.community.plugin_api.health_probe import HealthProbePlugin
    from agentclaw.community.plugins.community.health_probe import CommunityHealthProbe
    from agentclaw.community.plugins.local._mock_seam import MockSeam

    probe = community_world.get(HealthProbePlugin)
    assert isinstance(probe, CommunityHealthProbe)
    assert not isinstance(probe, MockSeam)
    assert probe.mode_label == "community"
