"""Capture is an explicit, strictly validated deployment choice, not a Sink flag."""

import pytest

from agentclaw.community.core.config.sofa import sofa_config
from agentclaw.community.di import DeployProfile, build_injector


@pytest.mark.parametrize(
    "block",
    [None, True, [], "true", {"enabled": "true"}, {"enabled": 1}, {"enabeld": True}],
)
def test_capture_configuration_rejects_malformed_blocks(monkeypatch, block):
    monkeypatch.setitem(
        vars(sofa_config), "user_config", {"space_knowledge_capture": block}
    )
    with pytest.raises(ValueError, match="space_knowledge_capture"):
        build_injector(profile=DeployProfile.TEST)
