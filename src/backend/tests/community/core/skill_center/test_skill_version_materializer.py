"""Behavior contract for exact Center Version materialization."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from contextlib import contextmanager
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
    SkillVersionScanResult,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.services.skill_version_materializer import (
    SdkSkillVersionScanner,
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
    SkillCenterReadScope,
)
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.testing.canonical_center_store import (
    LocalCanonicalCenterVersionStore,
)


def _package(*, name: str = "weather") -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "SKILL.md",
            (
                "---\n"
                f"name: {name}\n"
                "description: Exact weather reports.\n"
                "config: [{name: city, required: true}]\n"
                "---\n# Weather\n"
            ),
        )
        archive.writestr("scripts/fetch.py", "print('weather')\n")
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
    def __init__(self, package: bytes) -> None:
        self.package = package
        self.calls = []

    def get_exact_download(self, request):
        self.calls.append(request)
        return SkillCenterExactDownload(
            skill_code=request.skill_code,
            version_number=request.version_number,
            download_url="https://download.example/exact.zip",
            sha256=hashlib.sha256(self.package).hexdigest(),
            mcp_services=(),
        )


class _Scanner:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def scan(self, package):
        self.calls += 1
        if self.fail:
            raise RuntimeError("scanner unavailable")
        assert package.name == "weather"
        return SkillVersionScanResult(
            risk_tags=({"name": "network", "level": "LOW"},),
            mcp_dependencies=(
                {"code": "mcp.weather", "name": "Weather MCP"},
            ),
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
            name=self.target.name,
            description=kwargs["description"],
            metadata_json=kwargs["metadata_json"],
            published_at=kwargs["published_at"],
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


def _materializer(*, package: bytes, scanner: _Scanner, versions: _Versions):
    return SkillVersionMaterializer(
        versions=versions,
        gateway=_Gateway(package),
        http=_Http(package),
        validator=SkillPackageValidator(SkillParser()),
        scanner=scanner,
        store=LocalCanonicalCenterVersionStore(),
        clock=lambda: datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
    )


def test_exact_package_becomes_published_only_after_all_ready_inputs_exist() -> None:
    package = _package()
    versions = _Versions(_target())
    scanner = _Scanner()
    materializer = _materializer(
        package=package, scanner=scanner, versions=versions
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
    assert scanner.calls == 1
    assert versions.published is not None
    metadata = json.loads(versions.published["metadata_json"])
    assert metadata == {
        "config": [{"name": "city", "required": True}],
        "mcp_dependencies": [
            {"code": "mcp.weather", "name": "Weather MCP"}
        ],
        "risk_tags": [{"level": "LOW", "name": "network"}],
    }
    assert "sc_sha256" not in versions.published


@pytest.mark.parametrize(
    ("package", "scanner"),
    [
        (_package(name="different-name"), _Scanner()),
        (_package(), _Scanner(fail=True)),
    ],
    ids=("name-mismatch", "scanner-failure"),
)
def test_validation_or_scanner_failure_never_publishes(
    package: bytes, scanner: _Scanner
) -> None:
    versions = _Versions(_target())
    materializer = _materializer(
        package=package, scanner=scanner, versions=versions
    )

    with pytest.raises(SkillVersionMaterializationError):
        materializer.materialize(
            SkillVersionMaterializationRequest(
                env="pre",
                skill_id=10,
                skill_version_id=101,
                scope=SkillCenterReadScope.PUBLIC,
            )
        )

    assert versions.published is None


def test_sdk_scanner_reads_only_the_validated_exact_package(tmp_path) -> None:
    package = SkillPackageValidator(SkillParser()).validate_zip(_package())

    class _Scan:
        risk_tags = [{"name": "filesystem", "level": "MEDIUM"}]

    class _Sdk:
        def scan(self, skill_path: str):
            assert skill_path.endswith("/SKILL.md")
            assert open(skill_path, "rb").read().startswith(b"---\n")
            return _Scan()

        def get_mcp_dependencies(self, *, skill_path, base_dir, min_confidence):
            assert skill_path.endswith("/SKILL.md")
            assert base_dir == skill_path.removesuffix("/SKILL.md")
            assert min_confidence == 0.8
            return [{"code": "mcp.fs", "name": "FS"}]

    class _Plugin:
        def create_sdk(self):
            return _Sdk()

    result = SdkSkillVersionScanner(_Plugin()).scan(package)

    assert result == SkillVersionScanResult(
        risk_tags=({"name": "filesystem", "level": "MEDIUM"},),
        mcp_dependencies=({"code": "mcp.fs", "name": "FS"},),
    )


def test_sdk_scanner_unavailable_fails_closed() -> None:
    package = SkillPackageValidator(SkillParser()).validate_zip(_package())

    class _Plugin:
        def create_sdk(self):
            return None

    with pytest.raises(SkillVersionMaterializationError, match="unavailable"):
        SdkSkillVersionScanner(_Plugin()).scan(package)


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


def test_offline_vn_plus_one_recovers_through_unified_materializer() -> None:
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
    assert committed.draft.target_version == 3
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
        scanner=_Scanner(),
        store=LocalCanonicalCenterVersionStore(),
        clock=lambda: datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
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
        assert recovered.offline_at is None
        assert recovered.offline_by is None
        assert (
            session.query(SkillVersion).filter_by(id=101).one().status
            == "PUBLISHED"
        )
