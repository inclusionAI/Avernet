"""Behavior contract for exact Center Version materialization."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import (
    SkillGrant,
    SkillSpaceBinding,
    SkillVersion,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_version import (
    SkillVersionRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill_offline import (
    SpaceSkillOfflineRepository,
)
from agentclaw.community.core.service_bot.service_artifact_lineage_reader_protocol import (
    ServiceArtifactLineage,
)
from agentclaw.community.core.skill_center.draft_content import DraftRevisionRef
from agentclaw.community.core.skill_center.materialization_contract import (
    MaterializingSkillVersion,
    PublishedMaterializedSkillVersion,
    SkillVersionMaterializationError,
    SkillVersionMaterializationRequest,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.services.skill_version_materializer import (
    SkillVersionMaterializer,
)
from agentclaw.community.core.skill_center.services.published_version_draft import (
    PreparedPublishedVersionDraft,
)
from agentclaw.community.core.skill_center.services.space_skill_offline_service import (
    SpaceSkillOfflineService,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownload,
    SkillCenterMcpService,
    SkillCenterReadScope,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.testing.canonical_center_store import (
    LocalCanonicalCenterVersionStore,
)


def _package(*, name: str = "weather", wrapper: str | None = None) -> bytes:
    stream = io.BytesIO()
    prefix = f"{wrapper}/" if wrapper else ""
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            f"{prefix}SKILL.md",
            (
                "---\n"
                f"name: {name}\n"
                "description: Exact weather reports.\n"
                "config: [{name: city, required: true}]\n"
                "---\n# Weather\n"
            ),
        )
        archive.writestr(f"{prefix}scripts/fetch.py", "print('weather')\n")
    return stream.getvalue()


class _Response:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _Http:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls: list[str] = []

    def get(self, path: str, **_kwargs) -> _Response:
        self.calls.append(path)
        return _Response(self.content)


class _Gateway:
    def __init__(
        self,
        package: bytes,
        *,
        mcp_services: tuple[SkillCenterMcpService, ...] = (),
    ) -> None:
        self.package = package
        self.mcp_services = mcp_services
        self.calls = []

    def get_exact_download(self, request):
        self.calls.append(request)
        return SkillCenterExactDownload(
            skill_code=request.skill_code,
            version_number=request.version_number,
            download_url="https://download.example/exact.zip",
            sha256=hashlib.sha256(self.package).hexdigest(),
            mcp_services=self.mcp_services,
        )


class _Versions:
    def __init__(self, target: MaterializingSkillVersion) -> None:
        self.target = target
        self.published = None

    def get_materialization_target(self, **_kwargs):
        return self.target

    def publish_materialized(self, **kwargs):
        self.published = kwargs
        return PublishedMaterializedSkillVersion(
            skill_version_id=self.target.skill_version_id,
            skill_id=self.target.skill_id,
            version_ordinal=self.target.version_ordinal,
            status="PUBLISHED",
            skill_uuid=self.target.skill_uuid,
            sc_version_number=self.target.sc_version_number,
            sc_skill_id=self.target.sc_skill_id,
            sc_version_id=self.target.sc_version_id,
            name=kwargs["name"],
            description=kwargs["description"],
            metadata_json=kwargs["metadata_json"],
            published_at=datetime(2026, 8, 30, 12, 0),
        )


def _target() -> MaterializingSkillVersion:
    return MaterializingSkillVersion(
        skill_version_id=101,
        skill_id=10,
        version_ordinal=1,
        status="MATERIALIZING",
        skill_uuid="00000000-0000-4000-8000-000000000010",
        skill_code="public-weather",
        sc_version_number="1.0.0",
        sc_skill_id=10010,
        sc_version_id=20101,
        name="weather",
        description=None,
        metadata_json=None,
        published_at=None,
    )


def _materializer(
    *,
    package: bytes,
    versions: _Versions,
    mcp_services: tuple[SkillCenterMcpService, ...] = (),
):
    return SkillVersionMaterializer(
        versions=versions,
        gateway=_Gateway(package, mcp_services=mcp_services),
        http=_Http(package),
        validator=SkillPackageValidator(SkillParser()),
        store=LocalCanonicalCenterVersionStore(),
    )


def test_exact_package_becomes_published_only_after_all_ready_inputs_exist() -> None:
    package = _package()
    versions = _Versions(_target())
    materializer = _materializer(
        package=package,
        versions=versions,
        mcp_services=(
            SkillCenterMcpService(
                server_code="mcp.weather",
                name="Weather MCP",
                icon_url="https://example.com/weather.png",
                description="Weather tools",
            ),
        ),
    )

    published = materializer.materialize(
        SkillVersionMaterializationRequest(
            env="pre",
            skill_id=10,
            skill_version_id=101,
            scope=SkillCenterReadScope.PUBLIC,
        )
    )

    assert published.status == "PUBLISHED"
    assert published.skill_uuid == "00000000-0000-4000-8000-000000000010"
    assert published.sc_version_number == "1.0.0"
    assert versions.published is not None
    metadata = json.loads(versions.published["metadata_json"])
    assert metadata == {
        "config": [{"name": "city", "required": True}],
        "mcp_dependencies": [
            {
                "code": "mcp.weather",
                "description": "Weather tools",
                "icon_url": "https://example.com/weather.png",
                "name": "Weather MCP",
            }
        ],
        "risk_tags": [],
    }
    assert "sc_sha256" not in versions.published


def test_public_exact_package_uses_manifest_name_and_ignores_opaque_wrapper() -> None:
    package = _package(name="dima", wrapper="dima-official-skill")
    versions = _Versions(
        replace(
            _target(),
            skill_code="dima-official-skill",
            name="Dima-cli-skill",
        )
    )
    materializer = _materializer(
        package=package,
        versions=versions,
    )

    published = materializer.materialize(
        SkillVersionMaterializationRequest(
            env="pre",
            skill_id=10,
            skill_version_id=101,
            scope=SkillCenterReadScope.PUBLIC,
        )
    )

    assert published.name == "dima"
    assert versions.published is not None
    assert versions.published["name"] == "dima"


def test_public_exact_package_accepts_empty_sc_mcp_metadata_without_scanner() -> None:
    package = _package()
    versions = _Versions(_target())
    materializer = _materializer(
        package=package,
        versions=versions,
        mcp_services=(),
    )

    materializer.materialize(
        SkillVersionMaterializationRequest(
            env="pre",
            skill_id=10,
            skill_version_id=101,
            scope=SkillCenterReadScope.PUBLIC,
        )
    )

    assert versions.published is not None
    metadata = json.loads(versions.published["metadata_json"])
    assert metadata["mcp_dependencies"] == []
    assert metadata["risk_tags"] == []


def test_team_exact_package_uses_sc_mcp_metadata_without_scanner() -> None:
    package = _package()
    versions = _Versions(_target())
    materializer = _materializer(
        package=package,
        versions=versions,
        mcp_services=(
            SkillCenterMcpService(
                server_code="mcp.team",
                name="Team MCP",
                icon_url=None,
                description="Team tools",
            ),
        ),
    )

    materializer.materialize(
        SkillVersionMaterializationRequest(
            env="pre",
            skill_id=10,
            skill_version_id=101,
            scope=SkillCenterReadScope.TEAM,
            team_id="team-a",
        )
    )

    assert versions.published is not None
    metadata = json.loads(versions.published["metadata_json"])
    assert metadata["mcp_dependencies"] == [
        {"code": "mcp.team", "description": "Team tools", "name": "Team MCP"}
    ]
    assert metadata["risk_tags"] == []


def test_download_failure_logs_safe_structured_diagnostics(caplog) -> None:
    class _FailedResponse:
        status_code = 502
        headers = {"content-type": "text/html"}
        content = b"<html>gateway failure</html>"

        def raise_for_status(self) -> None:
            raise RuntimeError(
                "download https://signed.example/package.zip?signature=private-token failed"
            )

    class _FailedHttp:
        def get(self, _path: str, **_kwargs) -> _FailedResponse:
            return _FailedResponse()

    package = _package()
    versions = _Versions(_target())
    materializer = SkillVersionMaterializer(
        versions=versions,
        gateway=_Gateway(package),
        http=_FailedHttp(),
        validator=SkillPackageValidator(SkillParser()),
        store=LocalCanonicalCenterVersionStore(),
    )

    with pytest.raises(SkillVersionMaterializationError) as failure:
        materializer.materialize(
            SkillVersionMaterializationRequest(
                env="pre",
                skill_id=10,
                skill_version_id=101,
                scope=SkillCenterReadScope.PUBLIC,
            )
        )

    assert failure.value.stage == "package_download"
    assert versions.published is None
    assert "skill_center_materialization_failed" in caplog.text
    assert "stage=package_download" in caplog.text
    assert "download_host=download.example" in caplog.text
    assert "download_path=/exact.zip" in caplog.text
    assert "http_status=502" in caplog.text
    assert "http_content_type=text/html" in caplog.text
    assert "private-token" not in caplog.text
    assert "signature=" not in caplog.text


def test_team_exact_package_still_rejects_manifest_name_mismatch() -> None:
    versions = _Versions(_target())
    materializer = _materializer(
        package=_package(name="different-name"),
        versions=versions,
    )

    with pytest.raises(
        SkillVersionMaterializationError, match="name changed"
    ) as failure:
        materializer.materialize(
            SkillVersionMaterializationRequest(
                env="pre",
                skill_id=10,
                skill_version_id=101,
                scope=SkillCenterReadScope.TEAM,
                team_id="team-a",
            )
        )

    assert failure.value.stage == "name_match"
    assert versions.published is None


def test_team_exact_package_ignores_opaque_sc_wrapper() -> None:
    versions = _Versions(_target())
    materializer = _materializer(
        package=_package(
            name="weather", wrapper="00000000-0000-4000-8000-000000000010"
        ),
        versions=versions,
    )

    published = materializer.materialize(
        SkillVersionMaterializationRequest(
            env="pre",
            skill_id=10,
            skill_version_id=101,
            scope=SkillCenterReadScope.TEAM,
            team_id="10001",
        )
    )

    assert published.status == "PUBLISHED"
    assert versions.published is not None


class _RecoveryDatabase:
    def __init__(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self._factory = sessionmaker(bind=self.engine)

    @contextmanager
    def orm_session(self):
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    transactional_orm_session = orm_session


class _RecoveryAccess:
    def require_space_member(self, **_kwargs) -> None:
        return None


class _RecoveryLineage:
    def scan(self, **_kwargs) -> ServiceArtifactLineage:
        return ServiceArtifactLineage((), ())


class _RecoveryDrafts:
    def __init__(self, *, skill_uuid: str) -> None:
        self.skill_uuid = skill_uuid

    def prepare(self, *, identity, latest) -> PreparedPublishedVersionDraft:
        assert identity["skill_uuid"] == self.skill_uuid
        return PreparedPublishedVersionDraft(
            expected_version_id=int(latest["id"]),
            target_version=int(latest["version_ordinal"]) + 1,
            description="old",
            ref=DraftRevisionRef(
                tenant="teamclaw",
                env="pre",
                skill_uuid=self.skill_uuid,
                target_version=3,
                revision_id="33333333-3333-4333-8333-333333333333",
            ),
        )

    def discard(self, _prepared) -> None:
        raise AssertionError("successful Offline must retain the Draft revision")


def test_materializing_a_new_version_never_reactivates_a_terminally_offline_skill() -> None:
    db = _RecoveryDatabase()
    skill_uuid = "00000000-0000-4000-8000-000000000010"
    with db.orm_session() as session:
        space = SpaceModel(
            space_code="materializer-recovery",
            space_type="TEAM",
            name="Recovery",
            created_by="owner",
            updated_by="owner",
            env="pre",
        )
        skill = Skill(
            name="weather",
            git_path=f"center://{skill_uuid}",
            skill_uuid=skill_uuid,
            status="PUBLISHED",
            env="pre",
        )
        session.add_all((space, skill))
        session.flush()
        skill_id = int(skill.id)
        session.add_all(
            (
                SkillSpaceBinding(
                    skill_id=skill_id,
                    space_id=int(space.id),
                    created_by="owner",
                    env="pre",
                ),
                SkillGrant(
                    skill_id=skill_id,
                    user_id="owner",
                    role="OWNER",
                    status="ACTIVE",
                    owner_slot=1,
                    granted_by="owner",
                    env="pre",
                ),
                SkillVersion(
                    id=101,
                    skill_id=skill_id,
                    version_ordinal=2,
                    status="PUBLISHED",
                    sc_version_number="2.0.0",
                    sc_skill_id=1010,
                    sc_version_id=2101,
                    name="weather",
                    description="old",
                    metadata_json='{"mcp_dependencies": []}',
                    published_at=datetime(2026, 8, 29, tzinfo=UTC),
                    created_by="owner",
                    env="pre",
                ),
            )
        )
        space_id = int(space.id)

    offline = SpaceSkillOfflineService(
        access=_RecoveryAccess(),
        repository=SpaceSkillOfflineRepository(db),
        lineage=_RecoveryLineage(),
        drafts=_RecoveryDrafts(skill_uuid=skill_uuid),
        env_provider=lambda: "pre",
        tenant_provider=lambda: "teamclaw",
    )
    committed = offline.offline(
        space_id=space_id,
        skill_id=skill_id,
        actor_id="owner",
    )
    assert committed.draft is None
    with db.orm_session() as session:
        persisted = session.query(Skill).filter_by(id=skill_id).one()
        assert persisted.offline_at is not None
        session.add(
            SkillVersion(
                id=102,
                skill_id=skill_id,
                publication_attempt_id=701,
                version_ordinal=3,
                status="MATERIALIZING",
                sc_version_number="3.0.0",
                sc_skill_id=1010,
                sc_version_id=2102,
                name="weather",
                created_by="owner",
                env="pre",
            )
        )

    package = _package()
    published = SkillVersionMaterializer(
        versions=SkillVersionRepository(db),
        gateway=_Gateway(package),
        http=_Http(package),
        validator=SkillPackageValidator(SkillParser()),
        store=LocalCanonicalCenterVersionStore(),
    ).materialize(
        SkillVersionMaterializationRequest(
            env="pre",
            skill_id=skill_id,
            skill_version_id=102,
            scope=SkillCenterReadScope.TEAM,
            team_id="91",
        )
    )

    assert published.status == "PUBLISHED"
    assert published.version_ordinal == 3
    with db.orm_session() as session:
        recovered = session.query(Skill).filter_by(id=skill_id).one()
        assert recovered.offline_at is not None
        assert recovered.offline_by == "owner"
        assert session.query(SkillVersion).filter_by(id=101).one().status == "PUBLISHED"
