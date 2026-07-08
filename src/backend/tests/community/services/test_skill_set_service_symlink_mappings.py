"""Verify get_symlink_mappings emits skill_uuid + version for center:// skills."""
from __future__ import annotations

import pytest

from agentclaw.community.core.skill_center.services.skill_set_service import (
    SynlinkMappingInfo,
)

pytestmark = pytest.mark.unit


def test_synlink_mapping_info_has_uuid_and_version_fields():
    m = SynlinkMappingInfo(
        source="/a", target="/b", skill_uuid="u1", version=2,
    )
    assert m.skill_uuid == "u1"
    assert m.version == 2


def test_synlink_mapping_info_to_dict_includes_uuid_and_version():
    m = SynlinkMappingInfo(
        source="/a", target="/b", skill_uuid="u1", version=2,
    )
    d = m.to_dict()
    assert d["skill_uuid"] == "u1"
    assert d["version"] == 2


def test_synlink_mapping_info_optional_fields_default_none():
    m = SynlinkMappingInfo(source="/a", target="/b")
    assert m.skill_uuid is None
    assert m.version is None
    d = m.to_dict()
    # to_dict should still emit keys (so engine schema is stable)
    assert "skill_uuid" in d
    assert "version" in d
