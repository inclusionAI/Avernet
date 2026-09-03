"""SC Public identity crosses the real Materializer and Canonical Store."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import zipfile
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import SkillVersion
from agentclaw.community.core.repository.implementations.skill_center.skill_center_reference import (
    SkillCenterReferenceRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_version import (
    SkillVersionRepository,
)
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersionIdentity,
    CanonicalCenterVersionRef,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    SkillVersionMaterializationRequest,
)
from agentclaw.community.core.skill_center.public_center_identity import (
    PublicCenterSkillIdentity,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.services.skill_version_materializer import (
    SkillVersionMaterializer,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownload,
    SkillCenterReadScope,
)
from agentclaw.community.testing.canonical_center_store import (
    LocalCanonicalCenterVersionStore,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


class _Database:
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


def _package() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "SKILL.md",
            "---\nname: weather\ndescription: Exact weather.\n---\n# Weather\n",
        )
    return stream.getvalue()


def _wrapped_public_package() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "dima-official-skill/SKILL.md",
            "---\nname: dima\ndescription: Dima CLI.\n---\n# Dima\n",
        )
    return stream.getvalue()


class _Response:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _Http:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def get(self, _url: str, **_kwargs) -> _Response:
        return _Response(self._content)


class _Gateway:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def get_exact_download(self, request) -> SkillCenterExactDownload:
        return SkillCenterExactDownload(
            skill_code=request.skill_code,
            version_number=request.version_number,
            download_url="https://signed.example/exact.zip?signature=secret",
            sha256=hashlib.sha256(self._content).hexdigest(),
            mcp_services=(),
        )


def test_deterministic_public_identity_passes_real_canonical_ready_gate() -> None:
    db = _Database()
    references = SkillCenterReferenceRepository(db)
    versions = SkillVersionRepository(db)
    identity = PublicCenterSkillIdentity.derive(
        tenant="teamclaw", env="pre", skill_code="public-weather"
    )
    package = _package()
    store = LocalCanonicalCenterVersionStore()

    with avernet_tenant_scope("teamclaw"):
        target = references.ensure_public_version(
            env="pre",
            actor_id="actor",
            locator=identity.locator,
            skill_uuid=identity.skill_uuid,
            skill_name="weather",
            description="Exact weather.",
            sc_skill_id=9001,
            sc_version_number="1.0.0",
            sc_version_id=10001,
        )
        published = SkillVersionMaterializer(
            versions=versions,
            gateway=_Gateway(package),
            http=_Http(package),
            validator=SkillPackageValidator(SkillParser()),
            store=store,
        ).materialize(
            SkillVersionMaterializationRequest(
                env="pre",
                skill_id=target.skill_id,
                skill_version_id=target.skill_version_id,
                scope=SkillCenterReadScope.PUBLIC,
            )
        )

    assert UUID(published.skill_uuid).version == 4
    assert published.skill_uuid == identity.skill_uuid
    assert store.verify_version(
        CanonicalCenterVersionRef(
            CanonicalCenterVersionIdentity(
                skill_uuid=identity.skill_uuid,
                sc_version_number="1.0.0",
            )
        )
    )


def test_public_identity_converges_and_remains_scope_isolated() -> None:
    first = PublicCenterSkillIdentity.derive(
        tenant="teamclaw", env="pre", skill_code="opaque-Code"
    )
    replay = PublicCenterSkillIdentity.derive(
        tenant="teamclaw", env="pre", skill_code="opaque-Code"
    )

    assert replay == first
    assert UUID(first.skill_uuid).version == 4
    assert first.locator == "center://opaque-Code"
    assert (
        PublicCenterSkillIdentity.derive(
            tenant="teamclaw", env="prod", skill_code="opaque-Code"
        ).skill_uuid
        != first.skill_uuid
    )
    assert (
        PublicCenterSkillIdentity.derive(
            tenant="another-tenant", env="pre", skill_code="opaque-Code"
        ).skill_uuid
        != first.skill_uuid
    )


def test_public_wrapper_and_market_display_name_converge_to_manifest_name() -> None:
    db = _Database()
    references = SkillCenterReferenceRepository(db)
    versions = SkillVersionRepository(db)
    identity = PublicCenterSkillIdentity.derive(
        tenant="teamclaw", env="pre", skill_code="dima-official-skill"
    )
    package = _wrapped_public_package()

    store = LocalCanonicalCenterVersionStore()
    with avernet_tenant_scope("teamclaw"):
        target = references.ensure_public_version(
            env="pre",
            actor_id="actor",
            locator=identity.locator,
            skill_uuid=identity.skill_uuid,
            skill_name="Dima-cli-skill",
            description="SC market description",
            sc_skill_id=9001,
            sc_version_number="1.0.0",
            sc_version_id=10001,
        )
        published = SkillVersionMaterializer(
            versions=versions,
            gateway=_Gateway(package),
            http=_Http(package),
            validator=SkillPackageValidator(SkillParser()),
            store=store,
        ).materialize(
            SkillVersionMaterializationRequest(
                env="pre",
                skill_id=target.skill_id,
                skill_version_id=target.skill_version_id,
                scope=SkillCenterReadScope.PUBLIC,
            )
        )
        next_target = references.ensure_public_version(
            env="pre",
            actor_id="actor",
            locator=identity.locator,
            skill_uuid=identity.skill_uuid,
            skill_name="A newer SC display name",
            description="SC market V2 description",
            sc_skill_id=9001,
            sc_version_number="2.0.0",
            sc_version_id=10002,
        )
        next_published = SkillVersionMaterializer(
            versions=versions,
            gateway=_Gateway(package),
            http=_Http(package),
            validator=SkillPackageValidator(SkillParser()),
            store=store,
        ).materialize(
            SkillVersionMaterializationRequest(
                env="pre",
                skill_id=next_target.skill_id,
                skill_version_id=next_target.skill_version_id,
                scope=SkillCenterReadScope.PUBLIC,
            )
        )

    assert published.name == "dima"
    assert next_published.name == "dima"
    assert next_published.version_ordinal == 2
    with db.orm_session() as session:
        skill = session.get(Skill, target.skill_id)
        version = session.get(SkillVersion, target.skill_version_id)
        assert skill is not None
        assert skill.git_path == "center://dima-official-skill"
        assert skill.skill_uuid == identity.skill_uuid
        assert skill.name == "dima"
        assert version is not None and version.name == "dima"
        next_version = session.get(SkillVersion, next_target.skill_version_id)
        assert next_version is not None and next_version.name == "dima"
