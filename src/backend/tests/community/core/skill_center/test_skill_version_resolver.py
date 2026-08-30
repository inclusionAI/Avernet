"""Exact published-version resolution for Runtime consumers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentclaw.community.core.skill_center.version_resolution_contract import (
    PublishedSkillVersion,
    SkillVersionResolutionError,
)
from agentclaw.community.core.skill_center.services.skill_version_resolver import (
    SkillVersionResolver,
)
from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset


def _version(
    *,
    skill_id: int,
    version_id: int,
    ordinal: int,
    number: str,
    metadata_json: str | None = '{"mcp_dependencies": []}',
) -> dict[str, object]:
    return {
        "id": version_id,
        "skill_id": skill_id,
        "version_ordinal": ordinal,
        "status": "PUBLISHED",
        "sc_version_number": number,
        "sc_skill_id": 1000 + skill_id,
        "sc_version_id": 2000 + version_id,
        "name": f"version-name-{skill_id}",
        "description": f"version-description-{skill_id}",
        "metadata_json": metadata_json,
        "published_at": datetime(2026, 8, 30, tzinfo=UTC),
    }


class _Versions:
    def __init__(self, rows: tuple[dict[str, object], ...] = ()) -> None:
        self.rows = rows
        self.latest_calls: list[dict[str, object]] = []
        self.exact_calls: list[dict[str, object]] = []

    def list_latest_published(
        self, *, env: str, skill_ids: tuple[int, ...]
    ) -> tuple[dict[str, object], ...]:
        self.latest_calls.append({"env": env, "skill_ids": skill_ids})
        selected = set(skill_ids)
        return tuple(row for row in self.rows if int(row["skill_id"]) in selected)

    def get_exact_published(
        self, *, env: str, skill_id: int, skill_version_id: int
    ) -> dict[str, object] | None:
        self.exact_calls.append(
            {
                "env": env,
                "skill_id": skill_id,
                "skill_version_id": skill_version_id,
            }
        )
        return next(
            (
                row
                for row in self.rows
                if int(row["skill_id"]) == skill_id
                and int(row["id"]) == skill_version_id
            ),
            None,
        )


def test_local_and_repo_assets_do_not_query_versions() -> None:
    versions = _Versions()
    resolver = SkillVersionResolver(versions)
    assets = (
        RegisteredSkillAsset(skill_id=1, name="local", git_path="local://local"),
        RegisteredSkillAsset(skill_id=2, name="repo", git_path="git://repo/path"),
    )

    assert resolver.resolve_latest_runtime_assets(env="pre", assets=assets) == assets
    assert versions.latest_calls == []


def test_center_assets_use_one_batch_and_preserve_input_order() -> None:
    versions = _Versions(
        (
            _version(
                skill_id=20,
                version_id=202,
                ordinal=2,
                number="2.0.0",
                metadata_json=(
                    '{"mcp_dependencies": '
                    '[{"code": "mcp.search", "name": "Search", "url": "x"}]}'
                ),
            ),
            _version(skill_id=10, version_id=101, ordinal=1, number="1.0.0"),
        )
    )
    resolver = SkillVersionResolver(versions)
    local = RegisteredSkillAsset(skill_id=1, name="local", git_path="local://local")
    center_v1 = RegisteredSkillAsset(
        skill_id=10,
        name="stable-runtime-name-1",
        git_path="center://public-one",
        skill_uuid="00000000-0000-4000-8000-000000000010",
        sc_version_number="stale-value-must-be-replaced",
    )
    center_v2 = RegisteredSkillAsset(
        skill_id=20,
        name="stable-runtime-name-2",
        git_path="center://public-two",
        skill_uuid="00000000-0000-4000-8000-000000000020",
    )

    resolved = resolver.resolve_latest_runtime_assets(
        env="pre", assets=(center_v1, local, center_v2)
    )

    assert versions.latest_calls == [{"env": "pre", "skill_ids": (10, 20)}]
    assert [asset.skill_id for asset in resolved] == [10, 1, 20]
    assert resolved[0].name == "stable-runtime-name-1"
    assert resolved[0].sc_version_number == "1.0.0"
    assert resolved[0].mcp_dependencies == ()
    assert resolved[1] is local
    assert resolved[2].sc_version_number == "2.0.0"
    assert resolved[2].mcp_dependencies == (
        {"code": "mcp.search", "name": "Search", "url": "x"},
    )


@pytest.mark.parametrize(
    ("asset", "rows"),
    [
        (
            RegisteredSkillAsset(
                skill_id=10,
                name="center",
                git_path="center://public",
                skill_uuid="00000000-0000-4000-8000-000000000010",
            ),
            (),
        ),
        (
            RegisteredSkillAsset(
                skill_id=10,
                name="center",
                git_path="center://public",
                skill_uuid=None,
            ),
            (_version(skill_id=10, version_id=101, ordinal=1, number="1.0.0"),),
        ),
    ],
)
def test_center_asset_without_complete_published_identity_fails_closed(
    asset: RegisteredSkillAsset, rows: tuple[dict[str, object], ...]
) -> None:
    resolver = SkillVersionResolver(_Versions(rows))

    with pytest.raises(SkillVersionResolutionError):
        resolver.resolve_latest_runtime_assets(env="pre", assets=(asset,))


def test_invalid_version_dependency_metadata_fails_closed() -> None:
    versions = _Versions(
        (
            _version(
                skill_id=10,
                version_id=101,
                ordinal=1,
                number="1.0.0",
                metadata_json='{"mcp_dependencies": [{"unexpected": "shape"}]}',
            ),
        )
    )
    resolver = SkillVersionResolver(versions)

    with pytest.raises(SkillVersionResolutionError):
        resolver.resolve_latest_runtime_assets(
            env="pre",
            assets=(
                RegisteredSkillAsset(
                    skill_id=10,
                    name="center",
                    git_path="center://public",
                    skill_uuid="00000000-0000-4000-8000-000000000010",
                ),
            ),
        )


@pytest.mark.parametrize(
    "metadata_json",
    [
        None,
        "{}",
        '{"mcp_dependencies": [{"code": ""}]}',
    ],
)
def test_published_version_requires_explicit_complete_dependency_metadata(
    metadata_json: str | None,
) -> None:
    resolver = SkillVersionResolver(
        _Versions(
            (
                _version(
                    skill_id=10,
                    version_id=101,
                    ordinal=1,
                    number="1.0.0",
                    metadata_json=metadata_json,
                ),
            )
        )
    )

    with pytest.raises(SkillVersionResolutionError):
        resolver.resolve_latest_runtime_assets(
            env="pre",
            assets=(
                RegisteredSkillAsset(
                    skill_id=10,
                    name="center",
                    git_path="center://public",
                    skill_uuid="00000000-0000-4000-8000-000000000010",
                ),
            ),
        )


def test_exact_resolution_requires_the_addressed_published_version() -> None:
    versions = _Versions(
        (_version(skill_id=10, version_id=101, ordinal=1, number="1.0.0"),)
    )
    resolver = SkillVersionResolver(versions)

    resolved = resolver.resolve_exact_published(
        env="pre", skill_id=10, skill_version_id=101
    )

    assert resolved == PublishedSkillVersion(
        skill_version_id=101,
        skill_id=10,
        version_ordinal=1,
        sc_version_number="1.0.0",
        sc_skill_id=1010,
        sc_version_id=2101,
        name="version-name-10",
        description="version-description-10",
        mcp_dependencies=(),
        published_at=datetime(2026, 8, 30, tzinfo=UTC),
    )
    assert versions.exact_calls == [
        {"env": "pre", "skill_id": 10, "skill_version_id": 101}
    ]

    with pytest.raises(SkillVersionResolutionError):
        resolver.resolve_exact_published(env="pre", skill_id=10, skill_version_id=999)
