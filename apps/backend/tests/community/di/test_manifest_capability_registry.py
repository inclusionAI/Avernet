"""The actual delivery bundles register one complete capability writer."""

import pytest

from agentclaw.community.core.apply_engine.constructs import Category, Section
from agentclaw.community.core.apply_engine.delivery import DevicePorts, PlatformPorts
from agentclaw.community.core.apply_engine.registry import build_materialisers
from agentclaw.community.core.apply_engine.skill_set_apply_delegates import (
    DeviceSkillSetApply,
    PlatformSkillSetApply,
)


@pytest.mark.parametrize(
    "bundle_type,delegate",
    [
        (DevicePorts, DeviceSkillSetApply),
        (PlatformPorts, PlatformSkillSetApply),
    ],
)
def test_bound_delivery_registers_one_capability_writer(
    test_injector, bundle_type, delegate
):
    ports = test_injector.get(bundle_type)()
    writers = build_materialisers(**ports.as_kwargs())
    assert set(writers) == {
        Section.SCRIPT,
        Category.CAPABILITIES,
        Category.IDENTITY,
        Category.RESOURCES,
        Category.CLI_TOOLS,
    }
    assert isinstance(ports.skill_sets, delegate)
