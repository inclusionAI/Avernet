"""Service tests for Published Version and consumable reads."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersion,
    CanonicalCenterVersionIdentity,
)
from agentclaw.community.core.skill_center.services.space_skill_version_query_service import (
    SpaceSkillVersionQueryService,
)


def _service():
    access = MagicMock()
    repository = MagicMock()
    canonical = MagicMock()
    return (
        SpaceSkillVersionQueryService(access, repository, canonical),
        access,
        repository,
        canonical,
    )


def _version_record(version=2):
    return {
        "id": 12,
        "skill_id": 51,
        "skill_uuid": "11111111-1111-4111-8111-111111111111",
        "version_ordinal": version,
        "status": "PUBLISHED",
        "sc_version_number": f"{version}.0.0",
        "sc_skill_id": None,
        "sc_version_id": None,
        "name": "risk-review",
        "description": "Published",
        "metadata_json": '{"mcp_dependencies":["mcp.a"]}',
        "published_at": datetime(2026, 8, 30, 8),
    }


def test_version_file_reads_use_business_ordinal_and_exact_canonical_identity(
    monkeypatch,
):
    service, access, repository, canonical = _service()
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.space_skill_version_query_service.get_current_env",
        lambda: "test",
    )
    repository.get_published_ordinal.return_value = _version_record()
    canonical.read_version.return_value = CanonicalCenterVersion.from_files(
        CanonicalCenterVersionIdentity(
            skill_uuid="11111111-1111-4111-8111-111111111111",
            sc_version_number="2.0.0",
        ),
        {"SKILL.md": b"# Published", "references/a.md": b"a"},
    )

    tree = service.get_version_file_tree(
        space_id=7, skill_id=51, version=2, actor_id="viewer"
    )
    file = service.read_version_file(
        space_id=7,
        skill_id=51,
        version=2,
        actor_id="viewer",
        path="SKILL.md",
    )

    assert tree["version"] == 2
    assert file == {"version": 2, "path": "SKILL.md", "content": "# Published"}
    repository.get_published_ordinal.assert_called_with(
        space_id=7, skill_id=51, version=2, env="test"
    )
    access.require_space_member.assert_called_with(space_id=7, user_id="viewer")


def test_consumable_filters_canonical_readiness_before_pagination(monkeypatch):
    service, _access, repository, canonical = _service()
    monkeypatch.setattr(
        "agentclaw.community.core.skill_center.services.space_skill_version_query_service.get_current_env",
        lambda: "test",
    )
    repository.list_consumable_candidates.return_value = [
        {
            "skill_id": index,
            "skill_uuid": f"{index:08d}-1111-4111-8111-111111111111",
            "name": f"Skill {index}",
            "description": None,
            "version_ordinal": 1,
            "sc_version_number": "1.0.0",
            "published_at": datetime(2026, 8, 30, 8),
        }
        for index in (1, 2, 3)
    ]
    canonical.verify_version.side_effect = [False, True, True]

    total, items = service.list_consumable(
        space_id=7,
        actor_id="viewer",
        keyword=None,
        page=2,
        page_size=1,
    )

    assert total == 2
    assert [item["skill_id"] for item in items] == ["3"]
