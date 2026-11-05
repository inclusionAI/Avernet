"""Read-only stable Center lookup and exact, write-only public preparation."""

from __future__ import annotations

from agentclaw.community.core.repository.protocols.skill_center import (
    SkillRepository,
    SkillVersionRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center_reference import (
    SkillCenterReferenceRepositoryProtocol,
)
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersionIdentity,
    CanonicalCenterVersionRef,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.skill_center.mcp_dependency_scope import (
    mcp_dependency_codes,
)
from agentclaw.community.core.skill_center.materialization_contract import (
    SkillVersionMaterializationRequest,
    SkillVersionMaterializerProtocol,
)
from agentclaw.community.core.skill_center.public_center_identity import (
    PublicCenterSkillIdentity,
)
from agentclaw.community.core.skill_center.skill_query_service_protocol import (
    SkillQueryServiceProtocol,
)
from agentclaw.community.core.skill_center.skill_reference_contract import (
    PublicSkillPreview,
    ReadySharedSkill,
    ReadyRepoSkill,
    SkillAccessScope,
    SkillReferenceServiceProtocol,
)
from agentclaw.community.core.skill_center.skill_center_gateway_service_protocol import (
    SkillCenterGatewayServiceProtocol,
)
from agentclaw.community.core.skill_center.track_latest_service_protocol import (
    TrackLatestServiceProtocol,
)
from agentclaw.community.core.skill_center.version_resolution_contract import (
    SkillVersionResolverProtocol,
)
from agentclaw.community.core.ports.shared_skill_port import SharedSkillNotFound
from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterExactDownloadRequest,
    SkillCenterPublicSkillDetailRequest,
    SkillCenterReadScope,
    SkillCenterVersionListRequest,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class SkillReferenceService(SkillReferenceServiceProtocol):
    def __init__(
        self,
        *,
        skills: SkillRepository,
        query: SkillQueryServiceProtocol,
        versions: SkillVersionRepositoryProtocol,
        resolver: SkillVersionResolverProtocol,
        store: CanonicalCenterVersionStore,
        references: SkillCenterReferenceRepositoryProtocol,
        gateway: SkillCenterGatewayServiceProtocol,
        materializer: SkillVersionMaterializerProtocol,
        track_latest: TrackLatestServiceProtocol,
    ) -> None:
        self._skills, self._query = skills, query
        self._versions, self._resolver, self._store = versions, resolver, store
        self._references, self._gateway = references, gateway
        self._materializer, self._track_latest = materializer, track_latest

    @staticmethod
    def _require_scope(scope: SkillAccessScope) -> None:
        # Legacy Skill query/repository reads use current env. Do not let an
        # explicitly supplied scope silently address another environment.
        if scope.env != get_current_env():
            raise SharedSkillNotFound(
                "Skill reference is outside the current environment"
            )

    def resolve_shared_skill_id(
        self, *, scope: SkillAccessScope, skill_id: str, expected_name: str
    ) -> ReadySharedSkill | ReadyRepoSkill:
        """Read facts for IDs returned by legacy SharedSkillPort without reconcile."""
        self._require_scope(scope)
        skill = self._query.resolve_skill(
            skill_id=skill_id,
            bot_id=scope.bot_id,
            owner_id=scope.owner_id,
            user_id=scope.actor_id,
        )
        source = str(skill.get("git_path") or "")
        if source.startswith("center://"):
            skill_uuid = skill.get("skill_uuid")
            if not isinstance(skill_uuid, str) or not skill_uuid:
                raise SharedSkillNotFound("Center Skill has no stable UUID")
            return self._ready(scope, skill_id, skill_uuid, expected_name)
        if not source.startswith("git://") or skill.get("name") != expected_name:
            raise SharedSkillNotFound(
                "Shared Skill source or runtime name does not match"
            )
        # Query owns the historical Repo NULL/empty -> [] decoding. Preserve it;
        # only exact Center/public metadata has an unknown dependency state.
        dependencies = skill.get("mcp_dependencies")
        if not isinstance(dependencies, list):
            raise ValueError("Repo Skill MCP dependencies must be a list")
        codes = frozenset(mcp_dependency_codes(dependencies))
        if any(not code for code in codes):
            raise ValueError("Repo Skill has an empty MCP dependency code")
        return ReadyRepoSkill(
            skill_id=skill_id,
            name=expected_name,
            git_path=source,
            mcp_dependencies=codes,
        )

    def resolve_center_uuid(
        self, *, scope: SkillAccessScope, skill_uuid: str, expected_name: str
    ) -> ReadySharedSkill:
        self._require_scope(scope)
        skill = self._skills.get_by_uuid(skill_uuid, env=scope.env)
        if (
            skill is None
            or skill.get("skill_uuid") != skill_uuid
            or not str(skill.get("git_path", "")).startswith("center://")
        ):
            raise SharedSkillNotFound("Center UUID is not available in this scope")
        return self._ready(scope, str(skill["id"]), skill_uuid, expected_name)

    def preview_public_reference(
        self, *, scope: SkillAccessScope, skill_code: str, expected_name: str
    ) -> ReadySharedSkill | PublicSkillPreview:
        self._require_scope(scope)
        identity = PublicCenterSkillIdentity.derive(
            tenant=get_current_avernet_tenant(), env=scope.env, skill_code=skill_code
        )
        skill = self._references.find_public_asset(
            env=scope.env, locator=identity.locator
        )
        stable_uuid = identity.skill_uuid
        if skill is not None:
            stable_uuid = skill.skill_uuid
            # Check access/offline before deciding whether preparation is needed.
            self._query.resolve_skill(
                skill_id=str(skill.skill_id),
                bot_id=scope.bot_id,
                owner_id=scope.owner_id,
                user_id=scope.actor_id,
            )
            if self._versions.list_latest_published(
                env=scope.env, skill_ids=(skill.skill_id,)
            ):
                return self._ready(
                    scope, str(skill.skill_id), stable_uuid, expected_name
                )
        detail = self._gateway.get_public_skill(
            SkillCenterPublicSkillDetailRequest(skill_code)
        )
        if (
            detail is None
            or detail.skill_code != skill_code
            or not detail.latest_version_number
            or detail.skill_id is None
        ):
            raise SharedSkillNotFound("Public Skill has no exact available version")
        versions = self._gateway.list_versions(
            SkillCenterVersionListRequest(
                skill_code=skill_code, scope=SkillCenterReadScope.PUBLIC
            )
        )
        exact = next(
            (v for v in versions if v.version_number == detail.latest_version_number),
            None,
        )
        if exact is None or exact.version_id is None:
            raise SharedSkillNotFound(
                "Public Skill latest version has no exact identity"
            )
        content = self._materializer.preview_exact(
            SkillCenterExactDownloadRequest(
                skill_code=skill_code,
                version_number=exact.version_number,
                scope=SkillCenterReadScope.PUBLIC,
            )
        )
        if content.package.name != expected_name:
            raise ValueError(
                "Public package runtime name does not match the declaration"
            )
        sc_skill_id, sc_version_id = int(detail.skill_id), int(exact.version_id)
        if sc_skill_id < 1 or sc_version_id < 1:
            raise ValueError("Public Skill exact identifiers must be positive")
        preview = PublicSkillPreview(
            skill_code=skill_code,
            skill_uuid=stable_uuid,
            name=expected_name,
            sc_skill_id=sc_skill_id,
            sc_version_number=exact.version_number,
            sc_version_id=sc_version_id,
            content=content,
        )
        preview.mcp_dependencies  # fail closed on unknown metadata
        return preview

    def prepare_public_reference(
        self, *, scope: SkillAccessScope, preview: PublicSkillPreview
    ) -> ReadySharedSkill:
        self._require_scope(scope)
        current = self._references.find_public_asset(
            env=scope.env, locator=f"center://{preview.skill_code}"
        )
        if current is None:
            candidate = PublicCenterSkillIdentity.derive(
                tenant=get_current_avernet_tenant(),
                env=scope.env,
                skill_code=preview.skill_code,
            )
            if preview.skill_uuid != candidate.skill_uuid:
                raise ValueError("Public preview identity is outside the current scope")
        else:
            if current.skill_uuid != preview.skill_uuid:
                raise ValueError("Public preview has a conflicting asset identity")
            self._query.resolve_skill(
                skill_id=str(current.skill_id),
                bot_id=scope.bot_id,
                owner_id=scope.owner_id,
                user_id=scope.actor_id,
            )
        target = self._references.ensure_apply_public_version(
            env=scope.env,
            actor_id=scope.actor_id,
            locator=f"center://{preview.skill_code}",
            skill_uuid=preview.skill_uuid,
            skill_name=preview.name,
            description=preview.content.package.description,
            sc_skill_id=preview.sc_skill_id,
            sc_version_number=preview.sc_version_number,
            sc_version_id=preview.sc_version_id,
            expected_name=preview.name,
            expected_mcp_dependencies=preview.mcp_dependencies,
        )
        request = SkillVersionMaterializationRequest(
            env=scope.env,
            skill_id=target.skill_id,
            skill_version_id=target.skill_version_id,
            scope=SkillCenterReadScope.PUBLIC,
        )
        published = (
            self._materializer.materialize(request)
            if target.status == "PUBLISHED"
            else self._materializer.materialize_verified(request, preview.content)
        )
        self._track_latest.version_published(published)
        ready = self._ready(
            scope, str(target.skill_id), preview.skill_uuid, preview.name
        )
        if ready.mcp_dependencies != preview.mcp_dependencies:
            raise ValueError("Ready Skill dependencies changed since preview")
        return ready

    def _ready(
        self,
        scope: SkillAccessScope,
        skill_id: str,
        skill_uuid: str,
        expected_name: str,
    ) -> ReadySharedSkill:
        self._query.resolve_skill(
            skill_id=skill_id,
            bot_id=scope.bot_id,
            owner_id=scope.owner_id,
            user_id=scope.actor_id,
        )
        version = self._resolver.resolve_latest_published(
            env=scope.env, skill_id=int(skill_id)
        )
        if version.name != expected_name:
            raise ValueError("Center runtime name does not match the declaration")
        ref = CanonicalCenterVersionRef(
            CanonicalCenterVersionIdentity(
                skill_uuid=skill_uuid, sc_version_number=version.sc_version_number
            )
        )
        if not self._store.verify_version(ref):
            raise ValueError("Ready Skill has no verified canonical content")
        ready = ReadySharedSkill(
            skill_id=skill_id, skill_uuid=skill_uuid, name=version.name, version=version
        )
        ready.mcp_dependencies
        return ready


__all__ = ["SkillReferenceService"]
