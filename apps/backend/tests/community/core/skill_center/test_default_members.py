"""Historical Default members resolve by stable identity and supply state."""

from agentclaw.community.core.skill_center.default_members import (
    effective_default_members,
)


def test_stable_center_uuid_wins_over_raw_alias_even_with_legacy_status():
    uuid = "00000000-0000-4000-8000-000000000001"
    rows = [
        dict(
            id=1,
            skill_uuid=uuid,
            git_path="center://a",
            status="DEVELOPING",
            version=1,
            has_published_version=True,
        ),
        dict(id=999, skill_uuid=None, git_path="git://other", status="DEVELOPING"),
    ]
    found, skipped = effective_default_members([(999, uuid)], rows)
    assert found[999]["id"] == 1
    assert not skipped


def test_unavailable_default_members_have_specific_reasons():
    rows = [
        dict(
            id=1,
            skill_uuid="a",
            git_path="center://a",
            status="PUBLISHED",
            version=1,
            has_published_version=False,
        ),
        dict(
            id=2,
            skill_uuid="b",
            git_path="center://b",
            status="PUBLISHED",
            version=1,
            offline_at="offline",
        ),
        dict(
            id=3,
            skill_uuid="c",
            git_path="center://c",
            status="DRAFT",
            version=1,
            has_published_version=False,
        ),
        dict(id=4, git_path="git://old", status="OFFLINE"),
        dict(id=5, git_path=None, status="DEVELOPING"),
    ]
    found, skipped = effective_default_members(
        [(101, None), (102, "a"), (103, "b"), (104, "c"), (4, None), (5, None)], rows
    )
    assert not found
    assert skipped == {
        101: "missing_asset",
        102: "no_published_version",
        103: "offline",
        104: "no_published_version",
        4: "offline",
        5: "missing_source",
    }
