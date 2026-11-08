"""Shared stable reference service behavior with real repositories and package validation."""

from types import SimpleNamespace

import pytest

from agentclaw.community.core.repository.implementations.skill_center.skill import (
    SkillRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_version import (
    SkillVersionRepository,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.services.skill_query_service import (
    SkillQueryService,
)
from agentclaw.community.core.skill_center.services.skill_version_materializer import (
    SkillVersionMaterializer,
)
from agentclaw.community.core.skill_center.services.skill_version_resolver import (
    SkillVersionResolver,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.core.skill_center.services.skill_reference_service import (
    SkillReferenceService,
)
from agentclaw.community.core.skill_center.skill_reference_contract import (
    SkillAccessScope,
    PublicSkillPreview,
    ReadySharedSkill,
)
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterVersion,
    SkillCenterMcpService,
)
from agentclaw.community.testing.canonical_center_store import (
    LocalCanonicalCenterVersionStore,
)
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.core.skill_center.test_skill_version_materializer import (
    _Gateway,
    _Http,
    _package,
)
from tests.community.core.skill_center.test_skill_query_service import (
    _Bots,
    _Collaborators,
)
from tests.community.repository.skill_center.test_skill_center_reference_repository import (
    _Database,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_center_reference import (
    SkillCenterReferenceRepository,
)
from agentclaw.community.core.skill_center.services.track_latest import (
    TrackLatestService,
)


class _PublicGateway(_Gateway):
    def get_public_skill(self, request):
        return SimpleNamespace(
            skill_code=request.skill_code,
            skill_id="7",
            skill_name="Market display name",
            latest_version_number="1.0",
            description="weather",
        )

    def list_versions(self, request):
        return (SkillCenterVersion(version_number="1.0", version_id="10"),)


class _TaskStore:
    def enqueue(self, *_args, **_kwargs):
        pass


def _service():
    db = _Database()
    repo = SkillRepository(db)
    versions = SkillVersionRepository(db)
    resolver = SkillVersionResolver(versions)
    store = LocalCanonicalCenterVersionStore()
    package = _package()
    gateway = _PublicGateway(
        package, mcp_services=(SkillCenterMcpService("mcp.weather"),)
    )
    query = SkillQueryService(
        repo,
        _Bots(),
        _Collaborators(),
        object(),
        object(),
        object(),
        lambda: object(),
        versions,
        resolver,
        store,
        object(),
    )
    materializer = SkillVersionMaterializer(
        versions=versions,
        gateway=gateway,
        http=_Http(package),
        validator=SkillPackageValidator(SkillParser()),
        store=store,
    )
    references = SkillCenterReferenceRepository(db)
    return (
        SkillReferenceService(
            skills=repo,
            query=query,
            versions=versions,
            resolver=resolver,
            store=store,
            references=references,
            gateway=gateway,
            materializer=materializer,
            track_latest=TrackLatestService(_TaskStore()),
        ),
        repo,
        gateway,
        references,
        db,
    )


def test_public_preview_is_read_only_then_prepare_makes_stable_reference_ready():
    service, repo, gateway, _references, _db = _service()
    scope = SkillAccessScope(
        env=get_current_env(), bot_id="bot", owner_id="owner", actor_id="owner"
    )

    preview = service.preview_public_reference(
        scope=scope, skill_code="weather", expected_name="weather"
    )
    assert isinstance(preview, PublicSkillPreview)
    assert preview.mcp_dependencies == frozenset({"mcp.weather"})
    assert repo.get_by_git_path("center://weather") is None

    gateway.package = b"upstream content changed after exact preview"
    ready = service.prepare_public_reference(scope=scope, preview=preview)
    assert isinstance(ready, ReadySharedSkill)
    assert ready.skill_uuid == preview.skill_uuid
    assert ready.mcp_dependencies == frozenset({"mcp.weather"})
    assert (
        service.resolve_center_uuid(
            scope=scope, skill_uuid=ready.skill_uuid, expected_name="weather"
        )
        == ready
    )

    # A Ready reference is a local platform fact and does not refresh the market.
    gateway.package = b"upstream unavailable"
    assert (
        service.preview_public_reference(
            scope=scope, skill_code="weather", expected_name="weather"
        )
        == ready
    )


def test_missing_uuid_and_public_name_mismatch_never_register_an_asset():
    from agentclaw.community.core.ports.shared_skill_port import SharedSkillNotFound

    service, repo, _gateway, _references, _db = _service()
    scope = SkillAccessScope(
        env=get_current_env(), bot_id="bot", owner_id="owner", actor_id="owner"
    )
    with pytest.raises(SharedSkillNotFound):
        service.resolve_center_uuid(
            scope=scope,
            skill_uuid="00000000-0000-4000-8000-000000000001",
            expected_name="weather",
        )
    with pytest.raises(ValueError, match="runtime name"):
        service.preview_public_reference(
            scope=scope, skill_code="weather", expected_name="other-name"
        )
    assert repo.get_by_git_path("center://weather") is None


def test_existing_unprepared_public_code_preserves_its_persisted_uuid():
    service, _repo, _gateway, references, _db = _service()
    # Seed legacy/public identity through the database seam, then exercise only
    # the service boundary. Existing identities are not deterministically renamed.
    stable_uuid = "00000000-0000-4000-8000-000000000007"
    references.ensure_public_version(
        env=get_current_env(),
        actor_id="owner",
        locator="center://weather",
        skill_uuid=stable_uuid,
        skill_name="weather",
        description=None,
        sc_skill_id=7,
        sc_version_number="1.0",
        sc_version_id=10,
    )
    scope = SkillAccessScope(
        env=get_current_env(), bot_id="bot", owner_id="owner", actor_id="owner"
    )
    preview = service.preview_public_reference(
        scope=scope, skill_code="weather", expected_name="weather"
    )
    assert preview.skill_uuid == stable_uuid
    assert (
        service.prepare_public_reference(scope=scope, preview=preview).skill_uuid
        == stable_uuid
    )


def test_ready_version_with_unknown_dependencies_does_not_fall_back_to_market_preview():
    service, _repo, _gateway, references, _db = _service()
    scope = SkillAccessScope(
        env=get_current_env(), bot_id="bot", owner_id="owner", actor_id="owner"
    )
    preview = service.preview_public_reference(
        scope=scope, skill_code="weather", expected_name="weather"
    )
    ready = service.prepare_public_reference(scope=scope, preview=preview)
    # Corrupt a persistence fact at the database seam to model legacy metadata.
    with _db.orm_session() as session:
        from agentclaw.community.core.models.space_skill import SkillVersion

        version = session.get(SkillVersion, ready.version.skill_version_id)
        version.metadata_json = "{}"
    with pytest.raises(ValueError, match="MCP dependency metadata"):
        service.preview_public_reference(
            scope=scope, skill_code="weather", expected_name="weather"
        )


def test_public_preview_refuses_duplicate_persisted_locator_identities():
    from agentclaw.community.core.models.skill import Skill
    from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

    service, _repo, _gateway, _references, db = _service()
    with db.orm_session() as session:
        for suffix in (1, 2):
            session.add(
                Skill(
                    name="weather",
                    git_path="center://weather",
                    is_public=True,
                    env=get_current_env(),
                    avernet_tenant=get_current_avernet_tenant(),
                    skill_uuid=f"00000000-0000-4000-8000-00000000000{suffix}",
                )
            )
    scope = SkillAccessScope(
        env=get_current_env(), bot_id="bot", owner_id="owner", actor_id="owner"
    )
    with pytest.raises(ValueError, match="conflicting"):
        service.preview_public_reference(
            scope=scope, skill_code="weather", expected_name="weather"
        )


@pytest.mark.parametrize(
    "metadata, expected",
    [(None, frozenset()), ('[{"code":"mcp.repo"}]', frozenset({"mcp.repo"}))],
)
def test_shared_repo_reference_exposes_actual_dependencies_with_legacy_null_semantics(
    metadata, expected
):
    from agentclaw.community.core.models.skill import Skill
    from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

    service, _repo, _gateway, _references, db = _service()
    with db.orm_session() as session:
        asset = Skill(
            name="repo-weather",
            git_path="git://tools/weather",
            is_public=True,
            user_id=None,
            env=get_current_env(),
            avernet_tenant=get_current_avernet_tenant(),
            mcp_dependencies=metadata,
        )
        session.add(asset)
        session.flush()
        skill_id = str(asset.id)
    scope = SkillAccessScope(
        env=get_current_env(), bot_id="bot", owner_id="owner", actor_id="owner"
    )
    ready = service.resolve_shared_skill_id(
        scope=scope, skill_id=skill_id, expected_name="repo-weather"
    )
    assert (ready.skill_id, ready.name, ready.git_path, ready.mcp_dependencies) == (
        skill_id,
        "repo-weather",
        "git://tools/weather",
        expected,
    )
