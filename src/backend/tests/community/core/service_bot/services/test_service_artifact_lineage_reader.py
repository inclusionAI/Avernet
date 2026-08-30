"""Behavior tests for the unique Service Artifact lineage read seam."""

from __future__ import annotations

from datetime import datetime

from agentclaw.community.api.service_artifact_lineage import (
    ServiceArtifactLineageReaderProtocol,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishLineagePage,
    BotPublishRecord,
)
from agentclaw.community.core.service_bot.services.service_artifact_lineage_reader import (
    ServiceArtifactLineageReader,
)


_UUID = "11111111-1111-4111-8111-111111111111"


def _record(
    publish_id: int,
    *,
    status: str = "success",
    ext: dict | None = None,
) -> BotPublishRecord:
    now = datetime(2026, 8, 30, 8, 0, 0)
    return BotPublishRecord(
        id=publish_id,
        source_bot_pk=publish_id,
        source_bot_id=f"service-{publish_id}",
        publish_bot_id=f"service-{publish_id}-pub",
        name=f"Service {publish_id}",
        owner_id="owner-1",
        status=status,
        version=publish_id,
        env="test",
        ext=ext,
        permission_owner="owner-1",
        gmt_create=now,
        gmt_modified=now,
    )


class _Pages:
    def __init__(self, pages: list[BotPublishLineagePage]) -> None:
        self.pages = pages
        self.calls: list[int | None] = []

    def list_lineage_candidates_page(self, *, env, after_id, limit):
        assert env == "test"
        assert limit == 100
        self.calls.append(after_id)
        return self.pages[len(self.calls) - 1]


def test_reader_finds_file_and_teclaw_exact_refs_across_complete_pages():
    file_record = _record(
        1,
        ext={
            "migration_path": "/artifact/v1",
            "skills_manifest": {
                "schema_version": 1,
                "engine": "openclaw",
                "active_layout": "pool",
                "layout_contract_version": "skills-pool-p3-v1",
                "center_skills": [
                    {
                        "runtime_name": "pdf",
                        "skill_uuid": _UUID,
                        "sc_version_number": "1.0.0",
                        "mcp_dependencies": ["mcp.a"],
                    }
                ],
                "shared_corpora": [
                    {
                        "corpus": "repo",
                        "runtime_path": "/runtime/skills-pool/skills-repo",
                        "store_prefix": "skills-repo",
                        "layout_contract_version": "skills-pool-p3-v1",
                        "permission": "read_only",
                        "snapshot_policy": "exclude",
                    },
                    {
                        "corpus": "center",
                        "runtime_path": "/runtime/skills-pool/skill-center",
                        "store_prefix": "skills-center",
                        "layout_contract_version": "skills-pool-p3-v1",
                        "permission": "read_only",
                        "snapshot_policy": "exclude",
                    },
                ],
            },
        },
    )
    teclaw_record = _record(
        2,
        status="upgraded",
        ext={
            "config_artifact": {
                "schema_version": 4,
                "engine_type": "teclaw",
                "skills": [
                    {
                        "name": "pdf",
                        "scope": "shared",
                        "store": "skill-center",
                        "path": f"{_UUID}/2.0.0",
                    }
                ],
                "stores": {
                    "skill-center": {
                        "type": "oss",
                        "bucket": "bucket",
                        "base": "skills-center",
                    }
                },
            }
        },
    )
    pages = _Pages(
        [
            BotPublishLineagePage(
                records=(file_record,), next_cursor=1, complete=False
            ),
            BotPublishLineagePage(
                records=(teclaw_record,), next_cursor=None, complete=True
            ),
        ]
    )

    reader: ServiceArtifactLineageReaderProtocol = ServiceArtifactLineageReader(pages)
    result = reader.scan(skill_uuid=_UUID, env="test")

    assert [(item.publish_id, item.sc_version_number) for item in result.references] == [
        (1, "1.0.0"),
        (2, "2.0.0"),
    ]
    assert result.unknown == ()
    assert pages.calls == [None, 1]


def test_reader_keeps_old_artifacts_compatible_and_skips_non_replayable_rows():
    pages = _Pages(
        [
            BotPublishLineagePage(
                records=(
                    _record(1, ext={"migration_path": "/legacy/v1"}),
                    _record(2, status="draft", ext={"skills_manifest": "broken"}),
                    _record(
                        3,
                        status="failed",
                        ext={"source_status": "building"},
                    ),
                ),
                next_cursor=None,
                complete=True,
            )
        ]
    )

    result = ServiceArtifactLineageReader(pages).scan(skill_uuid=_UUID, env="test")

    assert result.references == ()
    assert result.unknown == ()


def test_reader_blocks_failed_record_that_can_retry_frozen_artifact():
    pages = _Pages(
        [
            BotPublishLineagePage(
                records=(
                    _record(
                        4,
                        status="failed",
                        ext={
                            "source_status": "success",
                            "config_artifact": {
                                "schema_version": 4,
                                "engine_type": "teclaw",
                                "skills": [
                                    {
                                        "name": "pdf",
                                        "scope": "shared",
                                        "store": "skill-center",
                                        "path": f"{_UUID}/4.0.0",
                                    }
                                ],
                                "stores": {
                                    "skill-center": {
                                        "type": "oss",
                                        "bucket": "bucket",
                                        "base": "skills-center",
                                    }
                                },
                            },
                        },
                    ),
                ),
                next_cursor=None,
                complete=True,
            )
        ]
    )

    result = ServiceArtifactLineageReader(pages).scan(
        skill_uuid=_UUID,
        env="test",
    )

    assert result.unknown == ()
    assert [(item.publish_id, item.sc_version_number) for item in result.references] == [
        (4, "4.0.0")
    ]


def test_reader_fails_closed_for_corrupt_artifact_and_incomplete_pagination():
    pages = _Pages(
        [
            BotPublishLineagePage(
                records=(
                    _record(
                        7,
                        ext={
                            "config_artifact": {
                                "schema_version": 4,
                                "engine_type": "teclaw",
                                "skills": [
                                    {
                                        "name": "bad",
                                        "scope": "shared",
                                        "store": "skill-center",
                                        "path": "../latest",
                                    }
                                ],
                                "stores": {},
                            }
                        },
                    ),
                ),
                next_cursor=None,
                complete=False,
            )
        ]
    )

    result = ServiceArtifactLineageReader(pages).scan(skill_uuid=_UUID, env="test")

    assert result.references == ()
    assert {item.resource_id for item in result.unknown} == {
        "7",
        "artifact-scan",
    }
