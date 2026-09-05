"""Control-plane invariants that cannot live in the HTTP adapter."""

from __future__ import annotations

import importlib
import inspect
import json
import pathlib
import re
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agentclaw.community.core.repository.implementations.skill_center.capability_desired_state import (
    CapabilityDesiredState,
    DesiredStateMutation,
)
from agentclaw.community.core.repository.capability_desired_state_types import (
    InstallationFlushPlan,
)
from agentclaw.community.core.caller_identity.models import McpCallType
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    ProjectionScope,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetControlPlaneConflictError,
    SkillSetControlPlaneNotFoundError,
    SkillSetRuntimeReconcileError,
    McpPermissionDeniedError,
)
from agentclaw.community.core.skill_center.services.skill_set_management_service import (
    SkillSetManagementService,
)
from agentclaw.community.core.skill_center.skill_set_batch import (
    SkillSetSkillOutcome,
)
from agentclaw.community.core.skill_center.services.bot_capability_state_reader import (
    BotCapabilityStateReader,
)
from tests.community.skill_version_fakes import PassthroughSkillVersionResolver
from agentclaw.community.core.skill_center.services.bot_runtime_projector import (
    BotRuntimeProjector,
)
from agentclaw.community.core.skill_center.legacy_skill_set_compatibility import (
    LegacySkillSetScope,
)
from agentclaw.community.core.skills_pool.models import (
    PoolSkillMapping,
    RegisteredSkillAsset,
)


class _Repository:
    def __init__(self) -> None:
        self.restore_calls = []
        self.set_active_calls = []
        self.update_calls = []
        self.add_mcp_calls = []

    def update_set(self, **kwargs):
        self.update_calls.append(kwargs)
        return {"id": kwargs["set_id"], "is_default": False}

    def set_skill_set_active(self, **kwargs) -> DesiredStateMutation:
        self.set_active_calls.append(kwargs)
        return DesiredStateMutation(
            item={"id": "set-1", "name": "set", "is_default": False, "is_active": True},
            changed=True,
            previous_state=CapabilityDesiredState(set(), {}, {}),
        )

    def restore_desired_state(self, **kwargs) -> None:
        self.restore_calls.append(kwargs)

    def add_mcp(self, **kwargs) -> DesiredStateMutation:
        self.add_mcp_calls.append(kwargs)
        return DesiredStateMutation(
            item={"server_code": kwargs["server_code"]},
            changed=True,
            previous_state=CapabilityDesiredState(set(), {}, {}),
            mcp_codes=frozenset({kwargs["server_code"]}),
        )

    def list_mcps(self, **_kwargs):
        return []

    def get_set(self, **_kwargs):
        return {"id": "set-1", "is_default": False, "is_active": True}

    def delete_set(self, **_kwargs) -> None:
        return None


class _CreateRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.create_calls = []

    def create_set(self, **kwargs):
        self.create_calls.append(kwargs)
        return {
            "id": "set-1",
            "name": kwargs["name"],
            "bolt_id": kwargs["bot_id"],
            "is_default": False,
            "is_active": True,
        }


class _LegacyScopeRepository(_Repository):
    def __init__(self, scope: LegacySkillSetScope | None) -> None:
        super().__init__()
        self.scope = scope
        self.scope_calls: list[str] = []

    def resolve_legacy_set_scope(self, *, set_id: str):
        self.scope_calls.append(set_id)
        return self.scope


class _InactiveMembershipRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.membership_calls: list[tuple[str, dict]] = []

    @staticmethod
    def _mutation() -> DesiredStateMutation:
        return DesiredStateMutation(
            item={
                "id": "set-1",
                "name": "draft",
                "is_default": False,
                "is_active": False,
            },
            changed=True,
            previous_state=CapabilityDesiredState(set(), {}, {}),
        )

    def get_set(self, **_kwargs):
        return {"id": "set-1", "is_default": False, "is_active": False}

    def add_skill(self, **kwargs) -> DesiredStateMutation:
        self.membership_calls.append(("add_skill", kwargs))
        return self._mutation()

    def remove_skill(self, **kwargs) -> DesiredStateMutation:
        self.membership_calls.append(("remove_skill", kwargs))
        return self._mutation()

    def add_mcp(self, **kwargs) -> DesiredStateMutation:
        self.membership_calls.append(("add_mcp", kwargs))
        return self._mutation()

    def remove_mcp(self, **kwargs) -> DesiredStateMutation:
        self.membership_calls.append(("remove_mcp", kwargs))
        return self._mutation()


class _ActiveMembershipRepository(_InactiveMembershipRepository):
    @staticmethod
    def _mutation() -> DesiredStateMutation:
        mutation = _InactiveMembershipRepository._mutation()
        return DesiredStateMutation(
            item={**mutation.item, "is_active": True},
            changed=mutation.changed,
            previous_state=mutation.previous_state,
        )

    def get_set(self, **_kwargs):
        return {"id": "set-1", "is_default": False, "is_active": True}


class _Bots:
    def get_unique_by_id(self, bot_id: str) -> dict:
        assert bot_id == "bot-1"
        return {
            "owner_id": "true-owner",
            "env": "dev",
            "entity_id": "entity-1",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "entity_type": "staff",
            "status": "ACTIVE",
        }

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
        bot = self.get_unique_by_id(bot_id)
        return bot if owner_id == bot["owner_id"] else None


class _MissingBots:
    def get_unique_by_id(self, _bot_id: str):
        return None

    def get_by_id_and_owner(self, _bot_id: str, _owner_id: str):
        return None


class _SharedDefaultBots:
    """A real shared ``default`` Bot namespace with no global lookup."""

    def __init__(self, owner_id: str) -> None:
        self.owner_id = owner_id
        self.lookups: list[tuple[str, str]] = []

    def get_unique_by_id(self, _bot_id: str) -> dict:
        raise AssertionError("legacy default must never use a global bot lookup")

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
        self.lookups.append((bot_id, owner_id))
        if (bot_id, owner_id) != ("default", self.owner_id):
            return None
        return {
            "owner_id": owner_id,
            "env": "pre",
            "entity_id": owner_id,
            "active_engine": "openclaw",
            "bot_type": "personal",
            "entity_type": "staff",
            "status": "ACTIVE",
        }


class _UnsupportedBots(_Bots):
    def get_unique_by_id(self, bot_id: str) -> dict:
        bot = super().get_unique_by_id(bot_id)
        return {**bot, "bot_type": "desktop", "active_engine": "claude_code"}


class _AicodingImageBots(_Bots):
    def get_unique_by_id(self, bot_id: str) -> dict:
        return {
            **super().get_unique_by_id(bot_id),
            "active_engine": "claude_code",
            "template_type": "personalCoding",
        }


class _PlainClaudeCodeBots(_Bots):
    def get_unique_by_id(self, bot_id: str) -> dict:
        return {
            **super().get_unique_by_id(bot_id),
            "active_engine": "claude_code",
            "template_type": "normalCC",
        }


class _LiteralAicodingBots(_Bots):
    def get_unique_by_id(self, bot_id: str) -> dict:
        return {**super().get_unique_by_id(bot_id), "active_engine": "aicoding"}


class _NotReadyApplicationCodingBots(_Bots):
    def get_unique_by_id(self, bot_id: str) -> dict:
        return {
            **super().get_unique_by_id(bot_id),
            "status": "PENDING",
            "active_engine": "claude_code",
            "template_type": "applicationCoding",
        }


class _Runtime:
    def __init__(self, *, snapshots=(), fail_first: bool = True) -> None:
        self.owners = []
        self.reconcile_calls: list[dict] = []
        self.snapshot_calls: list[dict] = []
        self._fail_first = fail_first
        self._snapshots = list(snapshots) or [(), self._skill_mappings()]

    @staticmethod
    def _skill_mappings():
        return (
            PoolSkillMapping(corpus="local", relative_path="qa", link_name="qa"),
            PoolSkillMapping(
                corpus="repo", relative_path="business/eva", link_name="eva"
            ),
        )

    async def snapshot_skill_mappings(self, *, bot_id: str, owner_id: str):
        assert bot_id == "bot-1"
        self.snapshot_calls.append({"bot_id": bot_id, "owner_id": owner_id})
        return self._snapshots[len(self.snapshot_calls) - 1]

    def resolve_plan(self, *, bot_id: str, owner_id: str, **_kwargs):
        assert bot_id == "bot-1"
        self.snapshot_calls.append({"bot_id": bot_id, "owner_id": owner_id})
        mappings = self._snapshots[len(self.snapshot_calls) - 1]
        return SimpleNamespace(
            bot_id=bot_id,
            owner_id=owner_id,
            projection=SimpleNamespace(skill_mappings=mappings),
        )

    async def apply_plan(self, *, plan, **kwargs) -> None:
        await self.project(
            bot_id=plan.bot_id,
            owner_id=plan.owner_id,
            **kwargs,
        )

    async def project(
        self,
        *,
        bot_id: str,
        owner_id: str,
        retired_mappings=(),
        scope: ProjectionScope = ProjectionScope.everything(),
    ) -> None:
        assert bot_id == "bot-1"
        self.owners.append(owner_id)
        self.reconcile_calls.append(
            {
                "bot_id": bot_id,
                "owner_id": owner_id,
                "retired_mappings": tuple(retired_mappings),
                "scope": scope,
            }
        )
        if self._fail_first and len(self.owners) == 1:
            raise RuntimeError("runtime failed")



class _Authorization:
    """The hook the control plane still calls.

    It stays after the seam migration because ``/api/skillsets`` reaches this
    service with four routes that carry no gate of their own — see the comment
    at ``SkillSetManagementService._bot``. Defaults to allow, so these tests
    stay about what the service does once admitted.
    """

    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[dict] = []

    def can_manage_bot(self, *, bot_id, owner_id, actor_id) -> bool:
        self.calls.append(
            {"bot_id": bot_id, "owner_id": owner_id, "actor_id": actor_id}
        )
        return self.allowed


class _Audit:
    def insert(self, _data) -> None:
        return None


class _SuccessfulRuntime:
    async def snapshot_skill_mappings(self, **_kwargs):
        return ()

    async def project(self, **_kwargs) -> None:
        return None

    def resolve_plan(self, *, bot_id: str, owner_id: str, **_kwargs):
        return SimpleNamespace(
            bot_id=bot_id,
            owner_id=owner_id,
            projection=SimpleNamespace(skill_mappings=()),
        )

    async def apply_plan(self, *, plan, **kwargs) -> None:
        await self.project(
            bot_id=plan.bot_id,
            owner_id=plan.owner_id,
            **kwargs,
        )



class _LegacyResolutionRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.identifiers: list[str] = []

    def resolve_legacy_skill_id(self, *, bot_id: str, identifier: str) -> str:
        assert bot_id == "bot-1"
        self.identifiers.append(identifier)
        raise SkillSetControlPlaneNotFoundError()


class _LegacySkillSetService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.default_mcp_calls: list[dict] = []

    def resolve_or_create_legacy_market_skill(
        self, *, identifier: str, owner_id: str, bot_id: str
    ) -> str:
        self.calls.append((identifier, owner_id, bot_id))
        return "stable-skill-id"

    def get_set_mcp_servers(self, skill_set_id: str, **kwargs) -> list[dict]:
        self.default_mcp_calls.append({"skill_set_id": skill_set_id, **kwargs})
        return [{"server_code": "legacy-default-mcp"}]


class _LegacyFactory:
    def __init__(self) -> None:
        self.service = _LegacySkillSetService()
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.service


class _McpRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.direct_calls: list[dict] = []

    def install_mcp(self, **kwargs) -> DesiredStateMutation:
        self.direct_calls.append(kwargs)
        return DesiredStateMutation({}, True, CapabilityDesiredState(set(), {}, {}))


class _ResourceRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.list_set_calls: list[dict] = []

    def list_sets(self, **kwargs) -> list[dict]:
        self.list_set_calls.append(kwargs)
        return []


class _DefaultResourceRepository(_ResourceRepository):
    def __init__(self) -> None:
        super().__init__()
        self.list_mcp_calls: list[dict] = []

    def list_sets(self, **kwargs) -> list[dict]:
        self.list_set_calls.append(kwargs)
        return [{"id": "global-default", "is_default": True}]

    def list_mcps(self, **kwargs):
        self.list_mcp_calls.append(kwargs)
        return [{"server_code": "visible-default-mcp"}]


class _MixedResourceRepository(_DefaultResourceRepository):
    def list_sets(self, **kwargs) -> list[dict]:
        self.list_set_calls.append(kwargs)
        return [
            {"id": "global-default", "is_default": True},
            {"id": "ordinary-set", "is_default": False},
        ]

    def list_mcps(self, **kwargs):
        self.list_mcp_calls.append(kwargs)
        return [{"server_code": "ordinary-set-mcp"}]


class _ResourceLegacyFactory:
    def __init__(self) -> None:
        self.service = _LegacySkillSetService()
        self.calls: list[dict] = []

    def create(self, **_kwargs):
        self.calls.append(_kwargs)
        return self.service


class _McpAuth:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str]] = []

    def check_mcp_permission_detail(self, actor_id: str, server_code: str) -> dict:
        self.calls.append((actor_id, server_code))
        return {
            "has_permission": self.allowed,
            "access_level": "LOCAL" if self.allowed else "PRIVATE",
        }

    def apply_permission(self, **_kwargs) -> dict:
        return {"success": True, "process_url": None, "error": None}


class _McpCenter:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str]] = []

    def check_mcp_permission_detail(self, actor_id: str, server_code: str) -> dict:
        self.calls.append((actor_id, server_code))
        return {
            "has_permission": self.allowed,
            "access_level": "LOCAL" if self.allowed else "PRIVATE",
        }

    def get_mcp_detail(self, _server_code: str) -> dict:
        return {"accessLevel": "PUBLIC"}


def _registry(*, pool_runtime, pool_layouts):
    """A real registry over the real implementations.

    Deliberately not a fake: a stub registry would assert that the projector
    calls *something*, which is wiring, not behaviour. These tests care which
    runtime contract an engine actually gets, so they exercise the same
    resolution production does.
    """
    from agentclaw.community.core.skill_center.services.runtime_projections.per_domain import (
        PerDomainRuntimeProjection,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.registry import (
        EngineRuntimeProjectionRegistry,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.whole_artifact import (
        WholeArtifactRuntimeProjection,
    )

    # The same routing the DI provider builds. If these two drift, these tests
    # stop describing production.
    return EngineRuntimeProjectionRegistry(
        default=PerDomainRuntimeProjection(
            pool_runtime=pool_runtime,
            pool_layouts=pool_layouts,
        ),
        by_engine={"teclaw": WholeArtifactRuntimeProjection()},
    )


class _RuntimeFactoryService:
    def __init__(self) -> None:
        self.mcp_codes: set[str] | None = None
        self.collect_calls: list[dict] = []
        self.desired_skills: list[dict] | None = None
        # Declaration and delivery are separate acts, so the double records
        # them separately: mcp_codes is the whole allow-list, deliveries are
        # only what a mutation actually changed.
        self.deliveries: list[tuple[frozenset[str], frozenset[str]]] = []
        # Same reason as ``deliveries``, for the other two calls: the scalars
        # above are last-write-wins and cannot tell one call from four, which
        # is the whole question on a whole-artifact engine.
        self.runtime_syncs: list[list[dict]] = []
        self.mcp_projections: list[
            tuple[frozenset[str], frozenset[str], set[str]]
        ] = []
        # What each delivery was handed as the already-resolved MCP set. A
        # whole-artifact delivery composes from the database, so this is what
        # decides whether that compose re-reads what plan resolution read.
        self.delivered_effective_mcps: list[list[dict] | None] = []

    async def project_skills(
        self,
        *,
        desired_skills: list[dict],
        effective_mcps: list[dict] | None = None,
    ) -> bool:
        self.desired_skills = desired_skills
        self.runtime_syncs.append(desired_skills)
        self.delivered_effective_mcps.append(effective_mcps)
        return True

    async def project_whole_artifact(
        self,
        *,
        desired_skills: list[dict],
        effective_mcps: list[dict] | None = None,
    ) -> bool:
        self.desired_skills = desired_skills
        self.runtime_syncs.append(desired_skills)
        self.delivered_effective_mcps.append(effective_mcps)
        return True

    async def sync_mcp_delivery(
        self, *, claimed: frozenset[str], released: frozenset[str]
    ) -> bool:
        self.deliveries.append((claimed, released))
        return True

    async def sync_mcp_desired_state(self, *, server_codes: set[str]) -> bool:
        self.mcp_codes = server_codes
        return True

    async def project_mcps(
        self,
        *,
        claimed: frozenset[str],
        released: frozenset[str],
        declared: set[str],
    ) -> bool:
        """The projector's single MCP entry point.

        Composed the same way the real service composes it, rather than
        recorded as one opaque call: the deliver-before-declare order is the
        contract these tests assert on, so a double that flattened it could
        not catch the order being lost.
        """
        self.mcp_projections.append((claimed, released, declared))
        if not await self.sync_mcp_delivery(claimed=claimed, released=released):
            return False
        return await self.sync_mcp_desired_state(server_codes=declared)

    def collect_bot_active_mcps(self, **kwargs) -> list[dict]:
        self.collect_calls.append(kwargs)
        # ``hitl`` is a real LOCAL/stdio default: it belongs in the runtime
        # projection but must never be declared to AgentPass.
        return [
            {"server_code": "mcp.template-preset"},
            {"server_code": "hitl", "source": "local"},
        ]


class _RuntimeFactory:
    def __init__(self) -> None:
        self.service = _RuntimeFactoryService()
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.service


class _OverlappingCollectService(_RuntimeFactoryService):
    """collect_bot_active_mcps is itself default ∪ installed after Group 5,
    so its answer overlaps the projector's installed input."""

    def collect_bot_active_mcps(self, **kwargs) -> list[dict]:
        self.collect_calls.append(kwargs)
        return [
            {"server_code": "mcp.weather"},
            {"server_code": "mcp.template-preset"},
        ]


class _OverlappingCollectFactory(_RuntimeFactory):
    def __init__(self) -> None:
        super().__init__()
        self.service = _OverlappingCollectService()


class _FailingPolicyCollectService(_RuntimeFactoryService):
    def collect_bot_active_mcps(self, **kwargs) -> list[dict]:
        self.collect_calls.append(kwargs)
        assert kwargs["strict_policy_context"] is True
        raise RuntimeError("template policy unavailable")


class _FailingPolicyCollectFactory(_RuntimeFactory):
    def __init__(self) -> None:
        super().__init__()
        self.service = _FailingPolicyCollectService()


class _RuntimeBots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        assert (bot_id, owner_id) == ("bot-1", "true-owner")
        return {
            # Every persisted Bot has a primary key; identity lookups key on it.
            "id": 42,
            "entity_id": "entity-1",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "entity_type": "staff",
            "env": "pre",
        }


class _AicodingImageRuntimeBots(_RuntimeBots):
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        return {
            **super().get_by_id_and_owner(bot_id, owner_id),
            "active_engine": "claude_code",
            "template_type": "personalCoding",
        }


class _PlainClaudeCodeRuntimeBots(_RuntimeBots):
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        return {
            **super().get_by_id_and_owner(bot_id, owner_id),
            "active_engine": "claude_code",
            "template_type": "normalCC",
        }


class _HistoricalAicodingRuntimeBots(_RuntimeBots):
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        return {
            **super().get_by_id_and_owner(bot_id, owner_id),
            "active_engine": "aicoding",
        }


class _TeclawRuntimeBots(_RuntimeBots):
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        return {
            **super().get_by_id_and_owner(bot_id, owner_id),
            "bot_type": "personal",
            "active_engine": "teclaw",
        }


class _McpInstallations:
    def __init__(self) -> None:
        self.flush_calls: list[dict] = []
        self.list_installed_calls: list[dict] = []

    def flush_installations(self, **kwargs) -> InstallationFlushPlan:
        self.flush_calls.append(kwargs)
        return InstallationFlushPlan(frozenset(), frozenset(), frozenset())

    def list_installed_mcps(self, *, bot_id: str, owner_id: str) -> set[str]:
        self.list_installed_calls.append(
            {"bot_id": bot_id, "owner_id": owner_id}
        )
        assert bot_id == "bot-1"
        assert owner_id == "true-owner"
        return {"mcp.weather"}


class _FailingFlushRepository(_McpInstallations):
    def flush_installations(self, **_kwargs) -> InstallationFlushPlan:
        raise RuntimeError("installation persistence unavailable")


class _RuntimeSkills:
    def __init__(self, assets=()) -> None:
        self._assets = list(assets)

    def list_bot_installed_assets(self, **kwargs):
        assert kwargs == {
            "env": "pre",
            "bot_id": "bot-1",
            "owner_id": "true-owner",
        }
        return self._assets


class _RuntimePool:
    def __init__(self, *, published=True, verified=True) -> None:
        self.publish_calls: list[dict] = []
        self.verify_calls: list[dict] = []
        self._published = published
        self._verified = verified

    async def probe(self, **_kwargs):
        raise AssertionError("non-Center projection must keep the legacy adapter")

    async def publish_mappings(self, **kwargs):
        self.publish_calls.append(kwargs)
        return self._published

    async def verify_mappings(self, **kwargs):
        self.verify_calls.append(kwargs)
        return self._verified


class _CenterRuntimePool(_RuntimePool):
    def __init__(self) -> None:
        super().__init__()
        self.probe_calls: list[dict] = []

    async def probe(self, **_kwargs):
        self.probe_calls.append(_kwargs)
        return SimpleNamespace(
            evidence={
                "supported_mapping_contract_versions": [
                    "skills-pool-mapping-v2",
                    "skills-pool-mapping-v3",
                ]
            }
        )


class _CenterMountMissingRuntimePool(_CenterRuntimePool):
    async def probe(self, **_kwargs):
        self.probe_calls.append(_kwargs)
        return SimpleNamespace(
            evidence={
                "supported_mapping_contract_versions": [
                    "skills-pool-mapping-v2"
                ],
                "center_mount": {
                    "status": "NOT_READY",
                    "reason": "center_mount_missing",
                    "restart_required": True,
                },
            }
        )


class _CenterRuntimeSkills:
    def list_bot_installed_assets(self, **_kwargs):
        return [
            RegisteredSkillAsset(
                skill_id=7,
                name="center-skill",
                git_path="center://public-skill",
                skill_uuid="00000000-0000-4000-8000-000000000007",
                sc_version_number="3.0.0",
            )
        ]


class _TeclawRuntimeSkills:
    def list_bot_installed_assets(self, **_kwargs):
        return [
            RegisteredSkillAsset(
                skill_id=8,
                name="repo-skill",
                git_path="git://team/repo-skill",
            )
        ]


def _reader(skills, repository=None, bots=None):
    """The real reader over this file's fakes — flush ordering stays pinned."""
    return BotCapabilityStateReader(
        repository=repository if repository is not None else _McpInstallations(),
        bot_repo=bots if bots is not None else _RuntimeBots(),
        pool_skills=skills,
        version_resolver=PassthroughSkillVersionResolver(),
    )


class _RuntimeLayouts:
    def get(self, _scope):
        return None


class _RuntimePassport:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.query_calls: list[tuple[str, str]] = []

    def update_passport(self, **kwargs) -> None:
        self.calls.append(kwargs)

    def query_passport_clis(self, bot_id: str, owner_id: str) -> list[dict]:
        self.query_calls.append((bot_id, owner_id))
        assert (bot_id, owner_id) == ("bot-1", "true-owner")
        # This is the effective Default CLI scope after a user removed a
        # static default. A reconcile must preserve it exactly, not revive the
        # engine's static list.
        return [{"cli_code": "kept-cli", "cli_name": "Kept"}]

    def query_agent_passport(self, bot_id: str, owner_id: str) -> dict:
        assert (bot_id, owner_id) == ("bot-1", "true-owner")
        return {
            "mcps": [],
            "clis": [{"cli_code": "kept-cli", "cli_name": "Kept", "identity_mode": "owner"}],
        }


class _RuntimeCallerIdentity:
    """Caller-identity source for the Passport MCP scope.

    ``call_types`` is sparse exactly like the real table: a missing entry
    means Owner, so the default stub asserts nothing and every projected MCP
    resolves to ``owner``.
    """

    def __init__(self, call_types: dict[str, McpCallType] | None = None) -> None:
        self.call_types = call_types or {}
        self.calls: list[tuple[int, str]] = []

    def list_draft_call_types(self, bot_pk: int, engine_type: str):
        self.calls.append((bot_pk, engine_type))
        return dict(self.call_types)


class _FailingRuntimePassport(_RuntimePassport):
    def query_passport_clis(self, bot_id: str, owner_id: str) -> list[dict]:
        raise RuntimeError("passport unavailable")


@pytest.mark.asyncio
async def test_collaborator_command_keeps_desired_state_and_uses_true_owner():
    repository = _Repository()
    runtime = _Runtime()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=runtime,
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    result = await service.activate(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="collaborator",
        set_id="set-1",
    )

    # The hook that recorded (bot, true-owner, actor) is gone: the seam
    # adjudicates before the handler runs, against the same OwnerIdDep the
    # handler acts on. What is still this service's to prove is that the *true*
    # owner — not the caller — reaches the runtime, which the next lines do.
    assert result["runtime_projection"]["status"] == "PENDING"
    assert runtime.owners == ["true-owner"]
    assert runtime.snapshot_calls == [
        {"bot_id": "bot-1", "owner_id": "true-owner"},
        {"bot_id": "bot-1", "owner_id": "true-owner"},
    ]
    _activate_scope = ProjectionScope(skills=True, mcp=True, claimed_mcp=frozenset())
    assert runtime.reconcile_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "retired_mappings": (),
            "scope": _activate_scope,
        },
    ]
    assert repository.restore_calls == []


@pytest.mark.asyncio
async def test_deactivate_retires_mappings_removed_from_the_runtime_projection():
    class _McpSetRepository(_Repository):
        def set_skill_set_active(self, **kwargs) -> DesiredStateMutation:
            mutation = super().set_skill_set_active(**kwargs)
            return replace(
                mutation,
                mcp_codes=frozenset({"mcp.weather"}),
            )

    repository = _McpSetRepository()
    runtime = _Runtime(snapshots=[_Runtime._skill_mappings(), ()], fail_first=False)
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=runtime,
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    await service.deactivate(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="collaborator",
        set_id="set-1",
    )

    assert runtime.reconcile_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "retired_mappings": _Runtime._skill_mappings(),
            # Deactivation withdraws callable state but retains the Set's MCP
            # configuration. The mutation result deliberately carries the
            # Set's code to prove it is not treated as a physical release.
            "scope": ProjectionScope(skills=True, mcp=True),
        }
    ]


def test_create_rejects_missing_bot_instead_of_creating_orphan_set():
    service = SkillSetManagementService(
        repository=_Repository(),
        bot_repo=_MissingBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    # The control plane speaks one error vocabulary: an invisible Bot scope is
    # a SkillSet not-found, so the HTTP adapter maps a single family rather
    # than also having to know about the Local Skill errors.
    with pytest.raises(SkillSetControlPlaneNotFoundError):
        service.create_set(
            bot_id="missing",
            owner_id="actor",
            user_id="actor",
            name="set",
            description=None,
        )


def test_default_create_rejects_missing_bot_instead_of_creating_orphan_set():
    repository = _CreateRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_MissingBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    with pytest.raises(SkillSetControlPlaneNotFoundError):
        service.create_set(
            bot_id="default",
            owner_id="actor",
            user_id="actor",
            name="set",
            description=None,
        )

    assert repository.create_calls == []


def test_default_create_uses_owner_qualified_bot_lookup():
    owner_id = "owner-a"
    bots = _SharedDefaultBots(owner_id)
    repository = _CreateRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=bots,
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    service.create_set(
        bot_id="default",
        owner_id=owner_id,
        user_id=owner_id,
        name="owner set",
        description=None,
    )

    assert bots.lookups == [
        ("default", owner_id),
    ]
    assert repository.create_calls[0]["owner_id"] == owner_id


def test_legacy_set_scope_recovers_persisted_bot_then_applies_actor_acl() -> None:
    repository = _LegacyScopeRepository(
        LegacySkillSetScope(owner_id="true-owner", bot_id="bot-1")
    )
    authorization = _Authorization()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=authorization,
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    scope = service.resolve_legacy_set_scope(
        set_id="set-1",
        actor_id="collaborator",
        owner_id_hint=None,
    )

    assert scope == LegacySkillSetScope(owner_id="true-owner", bot_id="bot-1")
    assert repository.scope_calls == ["set-1"]
    assert authorization.calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "actor_id": "collaborator",
        }
    ]


def test_legacy_set_scope_rejects_conflicting_owner_hint() -> None:
    repository = _LegacyScopeRepository(
        LegacySkillSetScope(owner_id="true-owner", bot_id="bot-1")
    )
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    with pytest.raises(SkillSetControlPlaneNotFoundError):
        service.resolve_legacy_set_scope(
            set_id="set-1",
            actor_id="collaborator",
            owner_id_hint="wrong-owner",
        )


def test_addressed_create_persists_metadata_without_runtime_reconcile() -> None:
    repository = _CreateRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    result = service.create_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        name="metadata-only",
        description=None,
    )

    assert result["id"] == "set-1"
    assert repository.create_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "name": "metadata-only",
            "description": None,
            "engine_type": "openclaw",
        }
    ]


def test_create_active_empty_set_does_not_require_runtime_readiness() -> None:
    repository = _CreateRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_NotReadyApplicationCodingBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    result = service.create_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        name="draft",
        description=None,
    )

    assert result["is_active"] is True
    assert repository.create_calls[0]["engine_type"] == "claude_code"


def test_inactive_set_metadata_updates_do_not_require_runtime_readiness() -> None:
    repository = _Repository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_NotReadyApplicationCodingBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    updated = service.update_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        name="renamed draft",
        description=None,
    )
    service.delete_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
    )

    assert updated["id"] == "set-1"
    assert repository.update_calls[0]["engine_type"] == "claude_code"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "resource_kwargs"),
    [
        ("add_skills", {"skill_ids": ["skill-1"]}),
        ("remove_skills", {"skill_ids": ["skill-1"]}),
        ("add_mcp", {"server_code": "mcp.weather"}),
        ("remove_mcp", {"server_code": "mcp.weather"}),
    ],
)
async def test_inactive_set_membership_does_not_require_runtime_readiness(
    method_name: str, resource_kwargs: dict[str, str]
) -> None:
    repository = _InactiveMembershipRepository()
    runtime = _Runtime(fail_first=False)
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_NotReadyApplicationCodingBots(),
        runtime=runtime,
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    result = await getattr(service, method_name)(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        **resource_kwargs,
    )

    if method_name in {"add_skills", "remove_skills"}:
        assert result == [SkillSetSkillOutcome(skill_id="skill-1", changed=True)]
    else:
        assert result["is_active"] is False
    assert repository.membership_calls[0][0] == (
        (
            "add_skill"
            if method_name == "add_skills"
            else "remove_skill"
            if method_name == "remove_skills"
            else method_name
        )
    )
    assert runtime.snapshot_calls == []
    assert runtime.reconcile_calls == []


@pytest.mark.asyncio
async def test_active_set_membership_commits_when_runtime_is_not_ready() -> None:
    repository = _ActiveMembershipRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_NotReadyApplicationCodingBots(),
        runtime=_Runtime(fail_first=False),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    outcomes = await service.add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["skill-1"],
    )

    assert outcomes[0].changed is True
    assert len(repository.membership_calls) == 1


def test_default_read_rejects_missing_bot():
    service = SkillSetManagementService(
        repository=_Repository(),
        bot_repo=_MissingBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    with pytest.raises(SkillSetControlPlaneNotFoundError):
        service.get_set(
            bot_id="default", owner_id="owner", user_id="owner", set_id="set-1"
        )


@pytest.mark.asyncio
async def test_legacy_sync_activates_additively_without_replacing_other_sets():
    repository = _Repository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    await service.legacy_activate(
        bot_id="bot-1",
        owner_id="true-owner",
        actor_id="true-owner",
        set_id="set-1",
    )

    assert repository.set_active_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "set_id": "set-1",
            "active": True,
            "engine_type": "openclaw",
            "default_engine_types": ("openclaw",),
        }
    ]


@pytest.mark.asyncio
async def test_legacy_default_sync_uses_owner_qualified_bot_lookup():
    owner_id = "owner-a"
    bots = _SharedDefaultBots(owner_id)
    repository = _Repository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=bots,
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    await service.legacy_activate(
        bot_id="default",
        owner_id=owner_id,
        actor_id=owner_id,
        set_id="set-1",
    )

    assert bots.lookups == [("default", owner_id)]
    assert repository.set_active_calls[0]["owner_id"] == owner_id


def test_skill_set_acl_denial_is_adjudicated_at_both_gates():
    """Two gates at one bar, and which one answers depends on the surface.

    On ``/openapi/v1`` all nineteen operations carry ``Check(MEMBER)``, so
    ``bot_access`` refuses before the handler runs and answers
    ``BotAccessRefusedError`` — **404 Not found, byte-identical to a bot that
    does not exist.** That is caller-visible and intended: a 403 tells a caller
    the bot exists and they may not reach it, and the seam's masked-404 contract
    is that authorization and existence must be indistinguishable.

    The service's own ``SkillSetAccessDeniedError`` (403, code 403201) is
    **not** gone, because ``/api/skillsets`` reaches this service on four routes
    that carry no gate of their own. There it is still the only refusal, and it
    is still a 403 — that surface was never behind the masked-404 contract and
    this feature does not change it. See ``_bot`` for the route list, and
    ``test_the_control_plane_check_the_legacy_surface_relies_on_still_exists``.

    An earlier revision of this test said the denial had *moved*. It had not; it
    had been deleted, which is what left ``/api/skillsets`` open.
    """
    from agentclaw.community.adapters.http.openapi_v1.authorization import (
        AUTHORIZATION,
        Check,
    )
    from agentclaw.community.core.bot_collaborator.models import PermissionLevel

    adjudicated = {
        key: rule
        for key, rule in AUTHORIZATION.items()
        if isinstance(rule, Check)
        and ("/skill-sets" in key[1] or key[1].endswith("/mcps"))
    }
    assert adjudicated, "the skill-set operations are no longer adjudicated"
    assert all(rule.level is PermissionLevel.MEMBER for rule in adjudicated.values()), (
        "the bar moved off MEMBER, which can_manage_bot enforced before the seam"
    )


def test_the_control_plane_check_the_legacy_surface_relies_on_still_exists():
    """``can_manage_bot`` must stay while a surface with no gate reaches here.

    The nineteen ``/openapi/v1`` rows are ``Check(MEMBER)`` and ``bot_access``
    adjudicates them first, so on that surface this call is a redundant second
    gate. It is not redundant on the other one:
    ``adapters/http/skill_center/skillsets.py`` is mounted at ``/api/skillsets``,
    governed by no row in ``AUTHORIZATION``, and four of its routes carry no
    ``CollaboratorPermissionInterceptor``. All four take ``entity_id`` and
    ``bot_id`` as caller-supplied query parameters, so deleting this call lets an
    authenticated stranger read — and on the ``PUT``, write — another owner's
    SkillSet.

    That deletion was made and shipped to the PR before a review caught it. This
    test is what makes the same mistake fail here instead of there: it asserts
    the call exists, that the bar is MEMBER, and that the four legacy routes are
    still ungated (if someone gives them an interceptor, this test should be
    revisited — not deleted quietly).
    """
    source = inspect.getsource(SkillSetManagementService._bot)
    assert "can_manage_bot" in source, (
        "_bot no longer adjudicates the caller. The /openapi/v1 rows are fine — "
        "the seam adjudicates them — but /api/skillsets reaches this service "
        "with no route-level gate at all, and is now open to any authenticated "
        "caller who can name an entity_id and a bot_id"
    )

    hook = importlib.import_module(
        "agentclaw.community.core.skill_center.authorization_hook"
    )
    hook_source = inspect.getsource(
        hook.CollaboratorBotCapabilityAuthorizationHook.can_manage_bot
    )
    assert "PermissionLevel.MEMBER" in hook_source, (
        "the hook no longer checks at MEMBER, which is the bar the nineteen "
        "migrated rows were derived from; re-derive before changing it"
    )

    legacy = pathlib.Path(
        "src/agentclaw/community/adapters/http/skill_center/skillsets.py"
    )
    if not legacy.exists():  # pragma: no cover - depends on the runner's cwd
        pytest.skip("legacy skillsets adapter not reachable from this working dir")
    text = legacy.read_text()
    assert 'APIRouter(prefix="/api/skillsets"' in text, (
        "the legacy skillsets router moved; if it now lives under /openapi/v1 "
        "it is governed by AUTHORIZATION and this reasoning has to be redone"
    )

    routes = _ungated_control_plane_routes(text)
    assert routes, (
        "no ungated /api/skillsets route reaches the control plane any more. "
        "If every one of them gained a CollaboratorPermissionInterceptor, this "
        "check may finally be deletable — verify that deliberately rather than "
        "assuming it, and update _bot's comment with what you found"
    )


def _ungated_control_plane_routes(text: str) -> list[str]:
    """``METHOD path`` for each legacy route that reaches the control plane unguarded."""
    lines = text.splitlines()
    starts = [
        (i, m.group(1).upper(), m.group(2))
        for i, line in enumerate(lines)
        if (m := re.match(r'@router\.(get|post|put|delete|patch)\(\s*"([^"]*)"', line))
    ]
    ungated = []
    for index, (line_no, method, path) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        block = "\n".join(lines[line_no:end])
        if (
            "control_plane." in block
            and "CollaboratorPermissionInterceptor" not in block
        ):
            ungated.append(f"{method} {path}")
    return ungated


def test_the_service_keeps_writing_its_own_audit_row_after_the_seam_took_over():
    """``_audit`` survives the migration, and deliberately so.

    The plan expected this write to be deleted once ``bot_access`` began
    auditing these operations, so that one mutation left exactly one row.
    Reading both writers side by side shows that would drop rows rather than
    de-duplicate them:

    * The seam audits **only non-owner** mutations — ``bot_access`` guards its
      write with ``level < PermissionLevel.OWNER``. A bot owner editing their
      own skill sets is audited here today and would be audited nowhere at all
      afterwards.
    * The two rows do not carry the same thing. The seam's ``detail`` is
      ``{"route": ..., "method": ...}``, which is what the *transport* knows;
      this one is ``{"action": "skill_set_create"}``, a domain name no route
      template encodes.

    So the overlap is a second row for a non-owner on four operations, and the
    alternative is a coverage hole. This write was never the authorization
    check — it runs *after* the mutation and was never consulted to permit one
    — so consolidating the check did not make it redundant.
    """
    rows: list[dict] = []

    class _RecordingAudit:
        def insert(self, data) -> None:
            rows.append(data)

    service = SkillSetManagementService(
        repository=_CreateRepository(),
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_RecordingAudit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    # The owner acting on their own bot: the case the seam does not audit.
    service.create_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        name="set",
        description=None,
    )

    assert len(rows) == 1, "the owner's own mutation stopped being audited"
    assert rows[0]["bot_id"] == "bot-1"
    assert rows[0]["owner_id"] == "true-owner"
    assert rows[0]["operator_id"] == "true-owner"
    assert json.loads(rows[0]["detail"]) == {"action": "skill_set_create"}, (
        "the semantic action name is what this row adds over the seam's "
        "route-and-method one; losing it makes the two rows redundant"
    )


def test_resources_forwards_resolved_bot_owner_to_owner_scoped_set_listing():
    repository = _ResourceRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=_ResourceLegacyFactory(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    assert (
        service.list_resources(
            bot_id="bot-1", owner_id="true-owner", user_id="true-owner"
        )
        == []
    )
    assert repository.list_set_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "engine_type": "openclaw",
            "default_engine_types": ("openclaw",),
        }
    ]


def test_list_sets_uses_aicoding_default_then_claude_code_fallback_for_coding_image():
    repository = _ResourceRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_AicodingImageBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=_ResourceLegacyFactory(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    assert (
        service.list_sets(bot_id="bot-1", owner_id="true-owner", user_id="true-owner")
        == []
    )
    assert repository.list_set_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "engine_type": "claude_code",
            "default_engine_types": ("aicoding", "claude_code"),
        }
    ]


def test_update_set_uses_runtime_default_candidates_for_coding_image():
    repository = _Repository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_AicodingImageBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    service.update_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="default-id",
        name=None,
        description="ignored by the repository fake",
    )

    assert repository.update_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "set_id": "default-id",
            "name": None,
            "description": "ignored by the repository fake",
            "engine_type": "claude_code",
            "default_engine_types": ("aicoding", "claude_code"),
        }
    ]


def test_resources_reads_global_default_mcp_projection_for_collaborator_owner_scope():
    repository = _DefaultResourceRepository()
    legacy = _ResourceLegacyFactory()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=legacy,
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    result = service.list_resources(
        bot_id="bot-1", owner_id="true-owner", user_id="collaborator"
    )

    assert result[0]["mcps"] == [{"server_code": "legacy-default-mcp"}]
    # See above: the collaborator adjudication moved to the seam, so there
    # is no hook call left to observe here.
    assert repository.list_mcp_calls == []
    assert legacy.service.default_mcp_calls == [
        {
            "skill_set_id": "global-default",
            "user_id": "true-owner",
            "bot_id": "bot-1",
            "engine_type": "openclaw",
        }
    ]


def test_resources_keeps_ordinary_mcp_membership_on_canonical_repository_path():
    repository = _MixedResourceRepository()
    legacy = _ResourceLegacyFactory()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=legacy,
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    result = service.list_resources(
        bot_id="bot-1", owner_id="true-owner", user_id="true-owner"
    )

    assert result[0]["mcps"] == [{"server_code": "legacy-default-mcp"}]
    assert result[1]["mcps"] == [{"server_code": "ordinary-set-mcp"}]
    assert repository.list_mcp_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "set_id": "ordinary-set",
            "engine_type": "openclaw",
            "default_engine_types": ("openclaw",),
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("bots", [_PlainClaudeCodeBots(), _LiteralAicodingBots()])
async def test_existing_coding_bot_can_activate_skill_set(bots) -> None:
    repository = _Repository()
    runtime = _SuccessfulRuntime()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=bots,
        runtime=runtime,
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    result = await service.activate(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
    )

    assert result["is_active"] is True
    assert repository.set_active_calls[0]["active"] is True


@pytest.mark.asyncio
async def test_existing_claude_code_skill_set_deactivate_uses_full_projection():
    repository = _Repository()
    runtime = _Runtime(
        snapshots=[_Runtime._skill_mappings(), ()],
        fail_first=False,
    )
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_PlainClaudeCodeBots(),
        runtime=runtime,
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    await service.deactivate(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
    )

    assert repository.set_active_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "set_id": "set-1",
            "active": False,
            "engine_type": "claude_code",
            "default_engine_types": ("claude_code",),
        }
    ]
    assert runtime.reconcile_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "retired_mappings": _Runtime._skill_mappings(),
            # deactivate declares what it released rather than reconciling:
            # the Set's MCP codes come back on the mutation result, resolved
            # under the row lock that uninstalled them.
            "scope": ProjectionScope(skills=True, mcp=True, released_mcp=frozenset()),
        }
    ]


@pytest.mark.parametrize("bots", [_PlainClaudeCodeBots(), _LiteralAicodingBots()])
def test_existing_coding_bot_metadata_mutations_ignore_product_creation_matrix(
    bots,
) -> None:
    repository = _CreateRepository()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=bots,
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    created = service.create_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        name="set",
        description=None,
    )
    updated = service.update_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        name="new-name",
        description=None,
    )

    assert created["id"] == "set-1"
    assert updated["id"] == "set-1"


def test_historical_bot_may_delete_an_inactive_skill_set_without_new_runtime_write():
    service = SkillSetManagementService(
        repository=_Repository(),
        bot_repo=_UnsupportedBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    service.delete_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
    )


def test_legacy_name_or_git_path_materializes_market_repo_skill_before_membership():
    """The legacy batch adapter keeps its historical implicit Repo creation."""
    repository = _LegacyResolutionRepository()
    factory = _LegacyFactory()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=factory,
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    stable_id = service.resolve_legacy_skill_id(
        bot_id="bot-1",
        owner_id="true-owner",
        actor_id="true-owner",
        identifier="market/example",
    )

    assert stable_id == "stable-skill-id"
    assert repository.identifiers == ["market/example"]
    assert factory.calls == [
        {
            "entity_id": "entity-1",
            "bot_id": "bot-1",
            "engine_type": "openclaw",
            "entity_type": "staff",
        }
    ]
    assert factory.service.calls == [("market/example", "true-owner", "bot-1")]


@pytest.mark.asyncio
async def test_runtime_mapping_snapshot_has_no_runtime_side_effects():
    repository = _McpInstallations()
    pool = _RuntimePool()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=repository,
        reader=_reader(
            _RuntimeSkills(
                [
                    RegisteredSkillAsset(
                        skill_id=1,
                        name="qa",
                        git_path="local://qa",
                    ),
                    RegisteredSkillAsset(
                        skill_id=2,
                        name="eva",
                        git_path="git://business/eva",
                    ),
                ]
            ),
            repository=repository,
        ),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    snapshot = await runtime.snapshot_skill_mappings(
        bot_id="bot-1", owner_id="true-owner"
    )

    assert snapshot == (
        PoolSkillMapping(corpus="local", relative_path="qa", link_name="qa"),
        PoolSkillMapping(corpus="repo", relative_path="business/eva", link_name="eva"),
    )
    # The reader's DB-side flush runs on every read; what a snapshot must
    # never do is touch the engine.
    assert len(repository.flush_calls) == 1
    assert pool.publish_calls == []
    assert pool.verify_calls == []


@pytest.mark.asyncio
async def test_runtime_projection_flushes_installations_first():
    repository = _McpInstallations()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=repository,
        reader=_reader(_RuntimeSkills(), repository=repository),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert repository.flush_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "env": "pre",
            "engine_type": "openclaw",
            "default_engine_types": ("openclaw",),
        }
    ]


@pytest.mark.asyncio
async def test_projection_flush_prefers_the_layout_engine_for_default_sets():
    """A coding template runs in an AICoding image while staying claude_code
    logically, so the Default-Set scope tries the filesystem identity first."""
    repository = _McpInstallations()
    bots = _AicodingImageRuntimeBots()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=bots,
        repository=repository,
        reader=_reader(_RuntimeSkills(), repository=repository, bots=bots),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert repository.flush_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "env": "pre",
            "engine_type": "claude_code",
            "default_engine_types": ("aicoding", "claude_code"),
        }
    ]


@pytest.mark.asyncio
async def test_runtime_projection_fails_before_engine_writes_when_flush_fails():
    repository = _FailingFlushRepository()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=repository,
        reader=_reader(_RuntimeSkills(), repository=repository),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    with pytest.raises(RuntimeError, match="installation persistence unavailable"):
        await runtime.project(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope.everything(),
        )


@pytest.mark.asyncio
async def test_runtime_projection_fails_closed_when_default_mcp_policy_is_unavailable():
    factory = _FailingPolicyCollectFactory()
    passport = _RuntimePassport()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    with pytest.raises(SkillSetRuntimeReconcileError):
        await runtime.project(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope.everything(),
        )

    assert factory.service.mcp_codes is None
    assert passport.calls == []


@pytest.mark.asyncio
async def test_runtime_projection_mcp_inputs_agree_when_the_union_overlaps():
    """installed ∪ effective_default: a code arriving through both inputs —
    the repo's installed listing and the collect union that now contains
    installed codes too — is delivered exactly once and drops nothing."""
    factory = _OverlappingCollectFactory()
    passport = _RuntimePassport()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.service.mcp_codes == {"mcp.weather", "mcp.template-preset"}
    assert passport.calls[0]["resource_scope"]["mcp_codes"] == [
        "mcp.template-preset",
        "mcp.weather",
    ]


@pytest.mark.asyncio
async def test_runtime_reconcile_projects_full_mcp_desired_state():
    factory = _RuntimeFactory()
    passport = _RuntimePassport()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.kwargs == {
        "user_id": "true-owner",
        "entity_id": "entity-1",
        "bot_id": "bot-1",
        "engine_type": "openclaw",
        "entity_type": "staff",
    }
    assert factory.service.mcp_codes == {
        "mcp.weather",
        "mcp.template-preset",
        "hitl",
    }
    assert factory.service.collect_calls == [
        {
            "entity_id": "entity-1",
            "bot_id": "bot-1",
            "user_id": "true-owner",
            "entity_type": "staff",
            "engine_type": "openclaw",
            "strict_policy_context": True,
        }
    ]
    assert passport.calls == [
        {
            "bot_id": "bot-1",
            "user_id": "true-owner",
            "engine_type": "openclaw",
            "resource_scope": {
                "mcp_codes": ["mcp.template-preset", "mcp.weather"],
                # Identity travels with every projection. Omitting mcp_items
                # does not preserve existing identity: the Passport port
                # substitutes bare items and writes identity_mode="owner"
                # explicitly, so a Caller MCP would be demoted by any
                # unrelated SkillSet mutation.
                "mcp_items": [
                    {"mcp_code": "mcp.template-preset", "identity_mode": "owner"},
                    {"mcp_code": "mcp.weather", "identity_mode": "owner"},
                ],
                "cli_items": [{"cli_code": "kept-cli", "cli_name": "Kept", "cli_desc": None, "identity_mode": "owner"}],
            },
        }
    ]


def _passport_mcp_items(passport: _RuntimePassport) -> list[dict]:
    """The MCP identity scope from the single updatePassport call."""
    assert len(passport.calls) == 1
    return passport.calls[0]["resource_scope"]["mcp_items"]


@pytest.mark.asyncio
async def test_projection_preserves_caller_identity_for_configured_mcp():
    """A Caller MCP keeps Caller across a projection that never mentions it.

    This is the regression: updatePassport replaces the MCP list wholesale
    and the Passport port writes identity_mode="owner" for any item that
    arrives without one, so a projection that omitted identity moved every
    Caller MCP onto the bot owner's credential.
    """
    passport = _RuntimePassport()
    identity = _RuntimeCallerIdentity({"mcp.weather": McpCallType.CALLER})
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=identity,
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert _passport_mcp_items(passport) == [
        {"mcp_code": "mcp.template-preset", "identity_mode": "owner"},
        {"mcp_code": "mcp.weather", "identity_mode": "caller"},
    ]
    # Looked up by the Bot's primary key and the engine it actually runs.
    assert identity.calls == [(42, "openclaw")]


@pytest.mark.asyncio
async def test_projection_preserves_agentpass_only_caller_identity_without_sparse_row():
    """A full AgentPass snapshot colours desired MCP membership before overwrite."""

    class _HistoricalCallerPassport(_RuntimePassport):
        def query_agent_passport(self, bot_id: str, owner_id: str) -> dict:
            assert (bot_id, owner_id) == ("bot-1", "true-owner")
            return {
                "mcps": [
                    {"mcp_code": "mcp.weather", "identity_mode": "caller"},
                ],
                "clis": [{"cli_code": "kept-cli", "cli_name": "Kept", "identity_mode": "owner"}],
            }

    passport = _HistoricalCallerPassport()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert _passport_mcp_items(passport) == [
        {"mcp_code": "mcp.template-preset", "identity_mode": "owner"},
        {"mcp_code": "mcp.weather", "identity_mode": "caller"},
    ]


@pytest.mark.asyncio
async def test_projection_logs_agentpass_scope_success_without_snapshot_secret(caplog):
    """AgentPass projection observability exposes scope counts, not its raw snapshot."""

    class _TokenBearingPassport(_RuntimePassport):
        def query_agent_passport(self, bot_id: str, owner_id: str) -> dict:
            snapshot = super().query_agent_passport(bot_id, owner_id)
            return {**snapshot, "token": "passport-token-secret"}

    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_TokenBearingPassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    with caplog.at_level("INFO", logger="start"):
        await runtime.project(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope.everything(),
        )

    logged = caplog.text
    assert "agentpass_runtime_scope_update_requested" in logged
    assert "agentpass_runtime_scope_update_succeeded" in logged
    assert "status=succeeded" in logged
    assert "duration_ms" in logged
    assert "passport-token-secret" not in logged


@pytest.mark.asyncio
async def test_projection_logs_agentpass_snapshot_failure_without_secret(caplog):
    """The snapshot failure event records exception type only before wrapping."""

    class _FailingPassport(_RuntimePassport):
        def query_agent_passport(self, bot_id: str, owner_id: str) -> dict:
            raise RuntimeError("passport-token-secret")

    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_FailingPassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    with caplog.at_level("INFO", logger="start"):
        result = await runtime.project(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope.everything(),
        )

    assert result.status.value == "PENDING"
    assert result.issues[0].code == "PASSPORT_RUNTIME_UNAVAILABLE"
    logged = caplog.text
    assert "agentpass_runtime_scope_update_requested" in logged
    assert "agentpass_runtime_scope_update_failed" in logged
    assert "stage=snapshot" in logged
    assert "error_type=RuntimeError" in logged
    assert "duration_ms" in logged
    assert "passport-token-secret" not in logged


@pytest.mark.asyncio
async def test_projection_logs_agentpass_update_failure_without_secret(caplog):
    """The overwrite failure has the same low-sensitive event contract."""

    class _FailingPassport(_RuntimePassport):
        def update_passport(self, **kwargs) -> None:
            raise RuntimeError("passport-token-secret")

    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_FailingPassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    with caplog.at_level("INFO", logger="start"):
        result = await runtime.project(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope.everything(),
        )

    assert result.status.value == "PENDING"
    assert result.issues[0].code == "PASSPORT_RUNTIME_UNAVAILABLE"
    assert "agentpass_runtime_scope_update_requested" in caplog.text
    assert "agentpass_runtime_scope_update_failed" in caplog.text
    assert "stage=update" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "duration_ms=" in caplog.text
    assert "passport-token-secret" not in caplog.text


@pytest.mark.asyncio
async def test_projection_defaults_to_owner_without_a_call_config_row():
    """The call-config table is sparse: a missing row means Owner."""
    passport = _RuntimePassport()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert _passport_mcp_items(passport) == [
        {"mcp_code": "mcp.template-preset", "identity_mode": "owner"},
        {"mcp_code": "mcp.weather", "identity_mode": "owner"},
    ]


@pytest.mark.asyncio
async def test_projection_scope_is_the_projected_codes_not_the_config_rows():
    """The projection decides the scope; the call-config table only colours it.

    The table is per-Bot, not per-projection, so it can hold rows for MCPs
    this Bot no longer has. Those must not reappear in the manifest —
    ``updatePassport`` replaces the list wholesale, so an extra item would
    re-grant a capability the Bot has lost.
    """
    passport = _RuntimePassport()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=_RuntimeCallerIdentity(
            {
                "mcp.weather": McpCallType.CALLER,
                "mcp.retired": McpCallType.CALLER,
            }
        ),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    scope = passport.calls[0]["resource_scope"]
    assert [item["mcp_code"] for item in scope["mcp_items"]] == scope["mcp_codes"]
    assert "mcp.retired" not in scope["mcp_codes"]


class _PkLessRuntimeBots(_RuntimeBots):
    """A Bot record without a primary key — not what the projector assumes."""

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        record = dict(super().get_by_id_and_owner(bot_id, owner_id))
        record.pop("id")
        return record


@pytest.mark.asyncio
async def test_projection_fails_closed_without_a_bot_primary_key():
    """No primary key fails the projection instead of guessing Owner.

    Defaulting here would be the same silent demotion the missing mcp_items
    caused, just from a different direction, so it fails closed like every
    other unreadable input on this path.
    """
    passport = _RuntimePassport()
    identity = _RuntimeCallerIdentity({"mcp.weather": McpCallType.CALLER})
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_PkLessRuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=identity,
    )

    with pytest.raises(SkillSetRuntimeReconcileError):
        await runtime.project(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope.everything(),
        )

    # Closed means nothing was written, not merely that it stopped: the
    # device allow-list and the symlink sync both precede the Passport call,
    # and a mid-write abort would leave the device holding forward state that
    # the compensating projection could not undo.
    assert factory.service.mcp_codes is None
    assert factory.service.desired_skills is None
    assert passport.calls == []
    assert identity.calls == []


@pytest.mark.asyncio
async def test_failed_mcp_projection_keeps_the_declared_mcp_delta():
    repository = _Repository()
    runtime = _Runtime()
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=runtime,
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )

    result = await service.add_mcp(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        server_code="mcp.new",
    )

    assert result["runtime_projection"]["status"] == "PENDING"
    assert len(runtime.reconcile_calls) == 1
    assert runtime.reconcile_calls[0]["scope"].claimed_mcp == frozenset({"mcp.new"})
    assert runtime.reconcile_calls[0]["scope"].released_mcp == frozenset()


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["add_mcp", "remove_mcp"])
async def test_an_unchanged_mcp_membership_declares_no_runtime_delta(
    method_name: str,
) -> None:
    class _UnchangedMcpRepository(_Repository):
        @staticmethod
        def _unchanged(server_code: str) -> DesiredStateMutation:
            return DesiredStateMutation(
                item={"server_code": server_code},
                changed=False,
                previous_state=CapabilityDesiredState(set(), {}, {}),
            )

        def add_mcp(self, **kwargs) -> DesiredStateMutation:
            return self._unchanged(kwargs["server_code"])

        def remove_mcp(self, **kwargs) -> DesiredStateMutation:
            return self._unchanged(kwargs["server_code"])

    runtime = _Runtime(fail_first=False)
    service = _skill_service(_UnchangedMcpRepository(), runtime)

    result = await getattr(service, method_name)(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        server_code="mcp.weather",
    )

    assert result["changed"] is False
    (call,) = runtime.reconcile_calls
    assert call["scope"] == ProjectionScope()


@pytest.mark.asyncio
async def test_reconcile_scope_claims_every_projected_code():
    """A restart or upload has no mutation to ask, so nothing is assumed.

    The device may hold no configuration at all on that path, so every
    projected code is treated as newly claimed — the behaviour that existed
    before delivery was scoped, preserved exactly.
    """
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    ((claimed, released),) = factory.service.deliveries
    assert claimed == frozenset(factory.service.mcp_codes)
    assert released == frozenset()


@pytest.mark.asyncio
async def test_a_declared_claim_delivers_only_that_code():
    """Problem 3 at the projector: the guard cannot enlarge a declared scope.

    The Bot's projected set has three codes; declaring one claim must deliver
    exactly that one while the allow-list still declares all three.
    """
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.weather"})),
    )

    assert factory.service.deliveries == [(frozenset({"mcp.weather"}), frozenset())]
    # Declaration stays total even though delivery did not.
    assert factory.service.mcp_codes == {
        "mcp.weather",
        "mcp.template-preset",
        "hitl",
    }


@pytest.mark.asyncio
async def test_a_release_still_supplied_by_policy_is_not_deleted():
    """REL's removal guard, recovered structurally.

    ``mcp.template-preset`` reaches the projection through the engine/template
    default policy rather than Set membership, so a Set dropping its claim
    must not delete it from the device. Nothing else stops that — the
    ``- codes`` guard is the whole protection.
    """
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(
            mcp=True, released_mcp=frozenset({"mcp.template-preset"})
        ),
    )

    assert factory.service.deliveries == [(frozenset(), frozenset())]


@pytest.mark.asyncio
async def test_a_release_no_longer_supplied_is_deleted():
    """The other side of the guard: a genuinely gone code is withdrawn.

    Without this, removing an MCP left its endpoint, api_key and headers on
    the container forever — the regression with no production caller at all.
    """
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(mcp=True, released_mcp=frozenset({"mcp.gone"})),
    )

    assert factory.service.deliveries == [(frozenset(), frozenset({"mcp.gone"}))]


@pytest.mark.asyncio
async def test_runtime_reconcile_fails_closed_when_effective_cli_scope_cannot_be_read():
    factory = _RuntimeFactory()
    passport = _FailingRuntimePassport()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport,
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    with pytest.raises(SkillSetRuntimeReconcileError):
        await runtime.project(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope.everything(),
        )

    assert passport.calls == []


@pytest.mark.asyncio
async def test_runtime_reconcile_requires_and_uses_mapping_v3_for_center():
    factory = _RuntimeFactory()
    pool = _CenterRuntimePool()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_CenterRuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.service.mcp_codes is not None
    assert len(pool.publish_calls) == len(pool.verify_calls) == 1
    # A product capability mutation uses the tolerant contract.  Pool
    # cutover/recovery remains STRICT at its own call sites; this assertion
    # protects the public Runtime Projector seam from accidentally turning a
    # normal SkillSet operation back into an all-or-nothing filesystem gate.
    assert pool.publish_calls[0]["apply_mode"].value == "BEST_EFFORT"
    assert pool.verify_calls[0]["apply_mode"].value == "BEST_EFFORT"
    assert pool.publish_calls[0]["mapping_contract_version"] == (
        "skills-pool-mapping-v3"
    )
    assert pool.publish_calls[0]["mappings"][0].to_dict() == {
        "corpus": "center",
        "link_name": "center-skill",
            "skill_uuid": "00000000-0000-4000-8000-000000000007",
        "sc_version_number": "3.0.0",
    }


@pytest.mark.asyncio
async def test_center_projection_requests_bot_restart_when_mount_is_missing():
    pool = _CenterMountMissingRuntimePool()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_CenterRuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    result = await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert result.status.value == "PENDING"
    assert result.issues[0].code == "CENTER_RUNTIME_RESTART_REQUIRED"
    assert result.issues[0].reason == (
        "Bot 尚未加载 Skill Center 目录，请重启 Bot 后重试"
    )
    assert pool.publish_calls == []
    assert pool.verify_calls == []


@pytest.mark.asyncio
async def test_transient_mapping_publish_failure_stays_pending_after_verify():
    """A failed link write must remain retryable, not become a false conflict."""
    from agentclaw.community.core.skills_pool.models import (
        MappingItemResult,
        MappingProjectionStatus,
        MappingPublishResult,
        MappingVerificationResult,
    )

    target = "/home/admin/.claude/skills/center-skill"
    source = "/home/admin/.claude_code/workspace/skills-pool/skill-center/00000000-0000-4000-8000-000000000007/3.0.0"
    pool = _CenterRuntimePool()
    pool._published = MappingPublishResult(
        published=False,
        status=MappingProjectionStatus.PENDING,
        items=(
            MappingItemResult(
                target=target,
                source=source,
                status=MappingProjectionStatus.PENDING,
                code="MAPPING_PUBLISH_IO_ERROR",
                retryable=True,
            ),
        ),
    )
    pool._verified = MappingVerificationResult(
        valid=False,
        status=MappingProjectionStatus.DEGRADED,
        items=(
            MappingItemResult(
                target=target,
                source=source,
                status=MappingProjectionStatus.DEGRADED,
                code="TARGET_NOT_SYMLINK",
            ),
        ),
    )
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_CenterRuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    result = await runtime.project(
        bot_id="bot-1", owner_id="true-owner", scope=ProjectionScope(skills=True)
    )

    assert result.status.value == "PENDING"
    assert result.issues[0].code == "MAPPING_PUBLISH_IO_ERROR"
    assert result.issues[0].retryable is True


@pytest.mark.asyncio
async def test_coding_template_uses_aicoding_for_center_probe_but_keeps_logical_engine():
    factory = _RuntimeFactory()
    pool = _CenterRuntimePool()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_AicodingImageRuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_CenterRuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.kwargs["engine_type"] == "claude_code"
    assert pool.probe_calls == [
        {"bot_id": "bot-1", "user_id": "true-owner", "engine": "aicoding"}
    ]
    assert len(pool.publish_calls) == len(pool.verify_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bots", "skills", "expected_engine"),
    [
        (_PlainClaudeCodeRuntimeBots(), _RuntimeSkills(), "claude_code"),
        (_HistoricalAicodingRuntimeBots(), _RuntimeSkills(), "aicoding"),
    ],
)
async def test_existing_coding_runtime_uses_its_resolved_layout(
    bots, skills, expected_engine: str
) -> None:
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=bots,
        repository=_McpInstallations(),
        reader=_reader(skills),
        registry=_registry(pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.kwargs["engine_type"] == expected_engine
    assert factory.service.desired_skills == []


@pytest.mark.asyncio
async def test_teclaw_v4_consumes_exact_center_through_whole_artifact():
    pool = _CenterRuntimePool()
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_TeclawRuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_CenterRuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert pool.probe_calls == []
    assert pool.publish_calls == []
    assert factory.service.desired_skills == [
        {
            "id": "7",
            "name": "center-skill",
            "git_path": "center://public-skill",
            "skill_uuid": "00000000-0000-4000-8000-000000000007",
            "sc_version_number": "3.0.0",
        }
    ]


@pytest.mark.asyncio
async def test_teclaw_v4_repo_projection_uses_artifact_runtime_not_pool_mapping():
    pool = _RuntimePool()
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_TeclawRuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_TeclawRuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.service.desired_skills == [
        {
            "id": "8",
            "name": "repo-skill",
            "git_path": "git://team/repo-skill",
            "skill_uuid": None,
            "sc_version_number": None,
        }
    ]
    assert pool.publish_calls == []
    assert pool.verify_calls == []


@pytest.mark.asyncio
async def test_non_skill_projection_never_writes_skill_mappings():
    pool = _RuntimePool()
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project_mcp_and_cli(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claim_all_mcp=True),
    )

    assert factory.service.desired_skills is None
    assert factory.service.mcp_codes is not None
    assert pool.publish_calls == []
    assert pool.verify_calls == []


@pytest.mark.asyncio
async def test_non_skill_entry_rejects_a_scope_without_mcp():
    runtime = _scoped_projector()

    with pytest.raises(ValueError, match="requires scope.mcp=True"):
        await runtime.project_mcp_and_cli(
            bot_id="bot-1",
            owner_id="true-owner",
            scope=ProjectionScope(skills=True),
        )



def _teclaw_runtime(
    factory, *, pool=None, passport=None, repository=None, identity=None
):
    """A projector over a teclaw Bot, wired the way production wires one."""
    return BotRuntimeProjector(
        factory=factory,
        bot_repo=_TeclawRuntimeBots(),
        repository=repository or _McpInstallations(),
        reader=_reader(_TeclawRuntimeSkills()),
        registry=_registry(
            pool_runtime=pool or _RuntimePool(),
            pool_layouts=_RuntimeLayouts(),
        ),
        passport=passport or _RuntimePassport(),
        caller_identity_repo=identity or _RuntimeCallerIdentity(),
    )


@pytest.mark.parametrize(
    "scope",
    [
        ProjectionScope(skills=True),
        ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.a"})),
        ProjectionScope(mcp=True, released_mcp=frozenset({"mcp.a"})),
        ProjectionScope(
            skills=True, mcp=True, claimed_mcp=frozenset({"mcp.a"})
        ),
        ProjectionScope.everything(),
    ],
    ids=["skills", "mcp-claim", "mcp-release", "both", "everything"],
)
@pytest.mark.asyncio
async def test_teclaw_projects_the_whole_artifact_once_per_scope_shape(scope):
    """One projection, one delivery — whatever the mutation declared.

    Every shape a production caller constructs. The runtime recomposes the
    whole artifact from the database and discards its arguments, so a second
    call could only restate the first; which half a mutation touched cannot
    change that.
    """
    factory = _RuntimeFactory()
    runtime = _teclaw_runtime(factory)

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner", scope=scope,
    )

    assert len(factory.service.runtime_syncs) == 1
    assert factory.service.mcp_projections == []


@pytest.mark.asyncio
async def test_teclaw_mcp_only_scope_still_delivers_the_skill_bearing_artifact():
    """An MCP-only scope still delivers the Skills, because it must.

    Pins the behaviour most likely to be optimised back out: on a per-domain
    engine ``skills=False`` means "leave the Skill half alone", but here there
    is no Skill half to leave alone — the one document carries both.
    """
    factory = _RuntimeFactory()
    runtime = _teclaw_runtime(factory)

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.a"})),
    )

    assert len(factory.service.runtime_syncs) == 1
    assert factory.service.runtime_syncs[0] == [
        {
            "id": "8",
            "name": "repo-skill",
            "git_path": "git://team/repo-skill",
            "skill_uuid": None,
            "sc_version_number": None,
        }
    ]


@pytest.mark.asyncio
async def test_teclaw_delivery_carries_the_mcp_set_plan_resolution_read():
    """One projection reads the effective MCP set once, then hands it over.

    Plan resolution has to collect it — the projected codes and the Passport
    scope are derived from it — and the whole-artifact delivery recomposes the
    bot's document from the same database moments later. Without the handover
    that compose repeats the identical ``collect_bot_active_mcps`` query for an
    answer the projection is already holding; both sides ask with
    ``strict_policy_context=True``, so it is the same answer by contract.

    Asserted on the collect count and the delivered value rather than on
    timing: what makes the second read unnecessary is that the first one's
    result reaches the composer, and that is what would silently regress.
    """
    factory = _RuntimeFactory()
    runtime = _teclaw_runtime(factory)

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner", scope=ProjectionScope.everything(),
    )

    assert len(factory.service.collect_calls) == 1
    assert factory.service.delivered_effective_mcps == [
        [
            {"server_code": "mcp.template-preset"},
            {"server_code": "hitl", "source": "local"},
        ]
    ]


@pytest.mark.asyncio
async def test_a_per_domain_delivery_carries_no_mcp_set():
    """The handover is for engines that compose; a per-domain one does not.

    Its Skill call writes symlinks and its MCP half goes to a separate
    endpoint, so there is no compose behind ``project_skills`` to spare a read
    — passing the set would only imply a re-use that never happens.
    """
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(
            pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()
        ),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner", scope=ProjectionScope.everything(),
    )

    assert factory.service.delivered_effective_mcps == [None]


@pytest.mark.asyncio
async def test_teclaw_still_updates_the_passport_with_identity_coloured_items():
    """The Passport is the platform's record, not the runtime's — it still runs.

    A whole-artifact container is issued a passport-service token as an egress
    rule, so the manifest must keep pace with the configuration the artifact
    delivered. And it must still carry ``identity_mode``: sending codes alone
    asserts Owner for every MCP.
    """
    passport = _RuntimePassport()
    runtime = _teclaw_runtime(_RuntimeFactory(), passport=passport)

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.a"})),
    )

    assert len(passport.calls) == 1
    scope_sent = passport.calls[0]["resource_scope"]
    assert "mcp_codes" in scope_sent
    assert "cli_items" in scope_sent
    assert all("identity_mode" in item for item in scope_sent["mcp_items"])


@pytest.mark.asyncio
async def test_teclaw_skill_only_scope_makes_no_passport_call():
    """No MCP change declared, no manifest write — unchanged from today."""
    passport = _RuntimePassport()
    factory = _RuntimeFactory()
    repository, identity = _McpInstallations(), _RuntimeCallerIdentity()
    runtime = _teclaw_runtime(
        factory,
        passport=passport,
        repository=repository,
        identity=identity,
    )

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner",
        scope=ProjectionScope(skills=True),
    )

    assert passport.calls == []
    # Teclaw still delivers exactly one full artifact, whose ConfigComposer
    # independently reads persisted MCP/CLI state. The projector must not
    # duplicate those reads for a Skill-only mutation.
    assert len(factory.service.runtime_syncs) == 1
    assert factory.service.delivered_effective_mcps == [None]
    assert factory.service.collect_calls == []
    assert repository.list_installed_calls == []
    assert passport.query_calls == []
    assert identity.calls == []


@pytest.mark.asyncio
async def test_teclaw_empty_scope_delivers_nothing():
    """A scope declaring neither half stays a no-op, as it is today."""
    passport = _RuntimePassport()
    factory = _RuntimeFactory()
    runtime = _teclaw_runtime(factory, passport=passport)

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner", scope=ProjectionScope(),
    )

    assert factory.service.runtime_syncs == []
    assert factory.service.mcp_projections == []
    assert passport.calls == []


@pytest.mark.asyncio
async def test_teclaw_failed_delivery_returns_pending_and_keeps_other_domains():
    passport = _RuntimePassport()
    factory = _RuntimeFactory()
    async def _refuse(**_):
        return False

    factory.service.project_whole_artifact = _refuse
    runtime = _teclaw_runtime(factory, passport=passport)

    result = await runtime.project(
        bot_id="bot-1", owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.a"})),
    )

    assert result.status == "PENDING"
    assert result.issues[0].code == "ARTIFACT_APPLY_FAILED"
    assert passport.calls


@pytest.mark.asyncio
async def test_runtime_delivery_never_runs_on_the_event_loop():
    """``project_skills`` dispatches its blocking work off the loop's thread.

    Behind it sits a device resolution with a blocking ws-info HTTP call, and
    on a whole-artifact engine a full artifact compose and the outbound apply
    request. Callers reach it from async HTTP handlers
    (``DirectActivationService`` is one), so running it inline lets one slow
    container stall unrelated requests on the same worker — the reason
    ``sync_mcp_desired_state`` already wraps its device calls.

    Asserted against ``SkillSetService`` itself rather than at the projection
    call sites, because that is where the guarantee now lives. It used to be
    the caller's job, parametrised over each call site so an edit that
    un-wrapped one would be caught; making ``project_skills`` async moved the
    ``to_thread`` inside it, so a call site can no longer get this wrong —
    awaiting is the only way to invoke it. One test at the real boundary
    replaces three at its callers.

    Driven through the unbound method over a stub ``self``: the body touches
    only these three attributes, and constructing a whole ``SkillSetService``
    would test its collaborators rather than this dispatch.

    Asserted by thread identity, not by patching ``asyncio.to_thread``: what
    matters is that the blocking work left the loop's thread, not which API
    moved it.
    """
    import threading

    from agentclaw.community.core.skill_center.services.skill_set_service import (
        SkillSetService,
    )

    loop_thread = threading.get_ident()
    seen: list[int] = []

    class _StubService:
        user_id = "owner-1"
        entity_id = "owner-1"

        def _sync_symlinks_to_device_if_needed(
            self, user_id, desired_skills, effective_mcps
        ):
            seen.append(threading.get_ident())
            return True

    assert await SkillSetService.project_skills(
        _StubService(), desired_skills=[{"id": "1"}]
    )

    assert seen, "the blocking call never ran — this test proves nothing"
    assert loop_thread not in seen, (
        "project_skills ran its blocking work on the event loop thread; it "
        "must dispatch through asyncio.to_thread so a slow device cannot "
        "block the worker"
    )



def test_engine_projections_declare_the_protocol_as_a_base():
    """Both implementations extend ``EngineRuntimeProjection`` nominally.

    ``runtime_checkable`` would accept them structurally, so this is not about
    making them work — it is about making the contract they answer to visible
    at the class statement, the way ``BaasDeviceSyncService(DeviceSync)`` and
    ``TeclawDeviceSyncService(DeviceSync)`` already do. An implementation that
    dropped the base would still pass every behavioural test here while
    hiding which seam it belongs to.
    """
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        EngineRuntimeProjection,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.per_domain import (
        PerDomainRuntimeProjection,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.whole_artifact import (
        WholeArtifactRuntimeProjection,
    )

    for impl in (PerDomainRuntimeProjection, WholeArtifactRuntimeProjection):
        assert EngineRuntimeProjection in impl.__mro__, (
            f"{impl.__name__} must declare EngineRuntimeProjection as a base, "
            "not merely satisfy it structurally"
        )


def test_an_incomplete_engine_projection_cannot_be_constructed():
    """Omitting half the contract must fail at construction, not at use.

    ``@abstractmethod`` is what turns the explicit base into a check. Without
    it a subclass could declare the protocol, implement only ``apply``, pass
    every registry and wiring test, and then raise ``AttributeError`` the
    first time plan resolution asked it to validate — deep inside a mutation,
    with the flush already run.
    """
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        EngineRuntimeProjection,
    )

    class MissingValidate(EngineRuntimeProjection):
        async def apply(self, *, plan, scope, retired_mappings=()) -> None:
            return None

    class MissingApply(EngineRuntimeProjection):
        def validate_plan(self, *, skill_assets, retired_mappings=()) -> None:
            return None

    for incomplete, missing in (
        (MissingValidate, "validate_plan"),
        (MissingApply, "apply"),
    ):
        with pytest.raises(TypeError) as excinfo:
            incomplete()
        assert missing in str(excinfo.value)


def test_the_capability_plan_names_a_boundary_not_a_service():
    """``ResolvedCapabilityPlan.service`` must stay a declared boundary.

    The seam is only replaceable if the contract names what a projection may
    *do*, not who happens to do it today. Importing ``SkillSetService`` here
    would make every implementation and every contract-only reader depend on
    that class, so substituting the runtime service would mean editing the
    contract — which is the coupling the registry exists to remove.

    Both halves matter, so both are asserted: the contract must not reach for
    the concrete service, *and* the concrete service must still satisfy the
    narrowed boundary. Checking only the first would let the boundary drift
    away from the class the composition root actually pairs it with, and the
    mismatch would not surface until a projection ran.
    """
    import inspect
    from pathlib import Path

    from agentclaw.community.core.skill_center import (
        runtime_projection_contract,
    )
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        CapabilityRuntimeBoundary,
    )
    from agentclaw.community.core.skill_center.services.skill_set_service import (
        SkillSetService,
    )

    source = Path(runtime_projection_contract.__file__).read_text(encoding="utf-8")
    # Matched on the module path, not on ``import SkillSetService``: the form
    # this replaced was a parenthesised multi-line import, which that narrower
    # string would have walked straight past.
    assert "skill_set_service" not in source, (
        "runtime_projection_contract must not import SkillSetService. Type "
        "ResolvedCapabilityPlan.service against CapabilityRuntimeBoundary and "
        "let the composition root pair the boundary with an implementation."
    )

    def shape(func) -> tuple:
        # ``eval_str`` resolves the contract's annotations, which are strings
        # because that module has ``from __future__ import annotations`` and
        # the service does not. Without it the two sides would differ on
        # nothing but quoting.
        signature = inspect.signature(func, eval_str=True)
        return (
            inspect.iscoroutinefunction(func),
            signature.return_annotation,
            tuple(
                (name, parameter.kind, parameter.annotation, parameter.default)
                for name, parameter in signature.parameters.items()
            ),
        )

    assert issubclass(SkillSetService, CapabilityRuntimeBoundary)
    for name in ("project_skills", "project_mcps"):
        assert shape(getattr(SkillSetService, name)) == shape(
            getattr(CapabilityRuntimeBoundary, name)
        ), (
            f"SkillSetService.{name} no longer matches the boundary it is "
            "wired to. Update CapabilityRuntimeBoundary alongside it."
        )



def test_registry_defaults_unknown_engines_to_the_per_domain_projection():
    """An unregistered engine gets the per-domain contract.

    What keeps ``claude_code`` / ``aicoding`` / ``hermes`` working without an
    entry each, and what makes mis-routing an engine take a wrong entry rather
    than a forgotten right one.
    """
    from agentclaw.community.core.skill_center.services.runtime_projections.per_domain import (
        PerDomainRuntimeProjection,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.whole_artifact import (
        WholeArtifactRuntimeProjection,
    )

    registry = _registry(
        pool_runtime=_RuntimePool(), pool_layouts=_RuntimeLayouts()
    )

    for engine in ("openclaw", "claude_code", "aicoding", "hermes", "unheard-of"):
        assert isinstance(
            registry.for_engine(engine), PerDomainRuntimeProjection
        ), engine
    assert isinstance(
        registry.for_engine("teclaw"), WholeArtifactRuntimeProjection
    )


def test_projector_and_per_domain_contain_no_engine_identity_test():
    """Neither module may test which engine it is looking at.

    The point of the seam: how a runtime consumes a projection is the engine's
    fact, answered by its own implementation. A reintroduced ``== "teclaw"``
    here would work, which is exactly why it needs catching mechanically.
    """
    from pathlib import Path

    from agentclaw.community.core.skill_center.services import (
        bot_runtime_projector,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections import (
        per_domain,
    )

    for module in (bot_runtime_projector, per_domain):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "teclaw" not in source, (
            f"{Path(module.__file__).name} names an engine. Engine-specific "
            "runtime behaviour belongs in that engine's "
            "EngineRuntimeProjection, registered by engine, not branched on "
            "here."
        )



@pytest.mark.asyncio
async def test_per_domain_engine_keeps_the_scope_split():
    """A per-domain engine still writes only the half the mutation declared.

    The regression guard for the whole change: whatever a whole-artifact
    engine does, an engine whose Skill and MCP halves have separate runtime
    endpoints must keep saving the round trip. An MCP-only scope touches the
    MCP half and nothing else, and the claimed set is still guarded down to
    what the projection actually resolved.
    """
    pool = _RuntimePool()
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjector(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=pool, pool_layouts=_RuntimeLayouts()),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.a"})),
    )

    assert factory.service.runtime_syncs == []
    assert len(factory.service.mcp_projections) == 1
    claimed, released, declared = factory.service.mcp_projections[0]
    # Guarded against the projected set: a code the projection never resolved
    # cannot be claimed just because the mutation named it.
    assert claimed <= set(declared)
    assert released == frozenset()
    assert pool.publish_calls == []


# ── Skill mutations carry the Skill's MCP dependencies ───────────────
#
# A Skill's ``mcp_dependencies`` join the Bot's MCP set along with the Skill,
# so a Skill mutation is an MCP mutation too — but only when the Skill
# actually declares any.


class _SkillRepository(_Repository):
    """Answers the Skill commands with the dependencies the Skill carries."""

    def __init__(self, mcp_codes=frozenset()) -> None:
        super().__init__()
        self._mcp_codes = frozenset(mcp_codes)
        self.skill_calls: list[dict] = []

    def _mutation(self, **kwargs) -> DesiredStateMutation:
        self.skill_calls.append(kwargs)
        return DesiredStateMutation(
            item={"skill_id": kwargs["skill_id"]},
            changed=True,
            previous_state=CapabilityDesiredState(set(), {}, {}),
            mcp_codes=self._mcp_codes,
        )

    def add_skill(self, **kwargs) -> DesiredStateMutation:
        return self._mutation(**kwargs)

    def remove_skill(self, **kwargs) -> DesiredStateMutation:
        return self._mutation(**kwargs)


def _skill_service(repository, runtime):
    return SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=runtime,
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=lambda _bot_id: None,
    )


@pytest.mark.asyncio
async def test_add_skill_claims_the_skill_s_mcp_dependencies():
    repository = _SkillRepository({"mcp.weather"})
    runtime = _Runtime(fail_first=False)

    await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7"],
    )

    (call,) = runtime.reconcile_calls
    assert call["scope"].skills is True
    assert call["scope"].mcp is True
    assert call["scope"].claimed_mcp == frozenset({"mcp.weather"})
    assert call["scope"].released_mcp == frozenset()


@pytest.mark.asyncio
async def test_add_skill_without_dependencies_leaves_the_mcp_half_alone():
    """The whole point of the lookup: a dependency-free Skill is Skills-only.

    Without it every Skill change re-declared the MCP allow-list and re-pushed
    the Passport manifest to say nothing had changed.
    """
    repository = _SkillRepository()
    runtime = _Runtime(fail_first=False)

    await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7"],
    )

    (call,) = runtime.reconcile_calls
    assert call["scope"].skills is True
    assert call["scope"].mcp is False


@pytest.mark.asyncio
async def test_batch_add_to_an_active_set_projects_the_final_state_once():
    """A legacy batch must not publish one runtime artifact per member."""
    repository = _SkillRepository({"mcp.weather"})
    runtime = _ProjectionCountingRuntime()

    outcomes = await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7", "8"],
    )

    assert outcomes == [
        SkillSetSkillOutcome(skill_id="7", changed=True),
        SkillSetSkillOutcome(skill_id="8", changed=True),
    ]
    assert [call["skill_id"] for call in repository.skill_calls] == ["7", "8"]
    assert runtime.projections == 1
    assert runtime.scopes == [
        ProjectionScope(
            skills=True,
            mcp=True,
            claimed_mcp=frozenset({"mcp.weather"}),
        )
    ]


@pytest.mark.asyncio
async def test_batch_add_keeps_legacy_partial_success_and_projects_once():
    class _PartialRepository(_SkillRepository):
        def add_skill(self, **kwargs) -> DesiredStateMutation:
            if kwargs["skill_id"] == "missing":
                raise SkillSetControlPlaneNotFoundError()
            return super().add_skill(**kwargs)

    repository = _PartialRepository()
    runtime = _ProjectionCountingRuntime()

    outcomes = await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7", "missing"],
    )

    assert outcomes[0] == SkillSetSkillOutcome(skill_id="7", changed=True)
    assert outcomes[1].skill_id == "missing"
    assert isinstance(outcomes[1].error, SkillSetControlPlaneNotFoundError)
    assert runtime.projections == 1


@pytest.mark.asyncio
async def test_batch_add_runtime_failure_keeps_the_whole_committed_batch_once():
    repository = _SkillRepository({"mcp.weather"})
    runtime = _Runtime()

    outcomes = await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7", "8"],
    )

    assert [outcome.changed for outcome in outcomes] == [True, True]
    assert [call["skill_id"] for call in repository.skill_calls] == ["7", "8"]
    assert repository.restore_calls == []
    assert len(runtime.reconcile_calls) == 1


@pytest.mark.asyncio
async def test_batch_add_restores_prior_members_when_a_later_write_fails():
    class _FailingSecondWriteRepository(_SkillRepository):
        def add_skill(self, **kwargs) -> DesiredStateMutation:
            if kwargs["skill_id"] == "8":
                raise RuntimeError("database unavailable")
            return super().add_skill(**kwargs)

    repository = _FailingSecondWriteRepository()
    runtime = _ProjectionCountingRuntime()

    with pytest.raises(RuntimeError, match="database unavailable"):
        await _skill_service(repository, runtime).add_skills(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="set-1",
            skill_ids=["7", "8"],
        )

    assert len(repository.restore_calls) == 1
    assert runtime.projections == 0


@pytest.mark.asyncio
async def test_batch_remove_from_an_active_set_projects_the_final_state_once():
    repository = _SkillRepository({"mcp.weather"})
    runtime = _ProjectionCountingRuntime()

    outcomes = await _skill_service(repository, runtime).remove_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7", "8"],
    )

    assert outcomes == [
        SkillSetSkillOutcome(skill_id="7", changed=True),
        SkillSetSkillOutcome(skill_id="8", changed=True),
    ]
    assert runtime.projections == 1
    assert runtime.scopes == [
        ProjectionScope(
            skills=True,
            mcp=True,
            released_mcp=frozenset({"mcp.weather"}),
        )
    ]


@pytest.mark.asyncio
async def test_batch_remove_runtime_failure_keeps_the_whole_committed_batch_once():
    repository = _SkillRepository({"mcp.weather"})
    runtime = _Runtime()

    outcomes = await _skill_service(repository, runtime).remove_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7", "8"],
    )

    assert [outcome.changed for outcome in outcomes] == [True, True]
    assert repository.restore_calls == []
    (forward,) = runtime.reconcile_calls
    assert forward["scope"].released_mcp == frozenset({"mcp.weather"})


@pytest.mark.asyncio
async def test_remove_skill_releases_the_skill_s_mcp_dependencies():
    repository = _SkillRepository({"mcp.weather"})
    runtime = _Runtime(fail_first=False)

    await _skill_service(repository, runtime).remove_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7"],
    )

    (call,) = runtime.reconcile_calls
    assert call["scope"].released_mcp == frozenset({"mcp.weather"})
    assert call["scope"].claimed_mcp == frozenset()


@pytest.mark.asyncio
async def test_a_failed_skill_projection_keeps_the_dependencies_it_claimed():
    repository = _SkillRepository({"mcp.weather"})
    runtime = _Runtime()

    outcomes = await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7"],
    )

    assert outcomes == [SkillSetSkillOutcome(skill_id="7", changed=True)]
    (forward,) = runtime.reconcile_calls
    assert forward["scope"].claimed_mcp == frozenset({"mcp.weather"})


@pytest.mark.asyncio
async def test_an_inactive_set_still_skips_projection_entirely():
    """Declaring a scope must not resurrect projection for an inactive Set."""

    class _InactiveSetRepository(_SkillRepository):
        def get_set(self, **_kwargs):
            return {"id": "set-1", "is_default": False, "is_active": False}

    repository = _InactiveSetRepository({"mcp.weather"})
    runtime = _Runtime(fail_first=False)

    await _skill_service(repository, runtime).add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="set-1",
        skill_ids=["7"],
    )

    assert runtime.reconcile_calls == []


# ── Scoped projection: a mutation writes only the half it changed ────
#
# Both halves are whole-snapshot writes, so re-sending the unchanged one costs
# a device round trip (or a Pool publish plus verify) to restate what is
# already there. The scope the command declares is what decides.


def _scoped_projector(
    pool=None, passport=None, factory=None, repository=None, identity=None
):
    return BotRuntimeProjector(
        factory=factory or _RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=repository or _McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=_registry(pool_runtime=pool or _RuntimePool(), pool_layouts=_RuntimeLayouts()),
        passport=passport or _RuntimePassport(),
        caller_identity_repo=identity or _RuntimeCallerIdentity(),
    )


@pytest.mark.asyncio
async def test_an_mcp_only_scope_does_not_touch_the_skill_runtime():
    """``add_mcp`` / ``remove_mcp`` declare ``mcp=True`` and nothing else.

    Republishing the Skill snapshot for them would restate mappings no
    mutation touched — the fan-out this change exists to stop, in its other
    half.
    """
    pool, factory = _RuntimePool(), _RuntimeFactory()
    runtime = _scoped_projector(pool=pool, factory=factory)

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.weather"})),
    )

    assert factory.service.desired_skills is None
    assert pool.publish_calls == []
    assert pool.verify_calls == []
    # ...while the half it did declare still ran in full.
    assert factory.service.mcp_codes is not None


@pytest.mark.asyncio
async def test_a_skill_only_scope_touches_neither_the_device_mcps_nor_passport():
    """The MCP allow-list and the Passport manifest are both overwrite-style.

    A mutation that changed no MCP has nothing new to say to either, and
    saying it anyway is a device write plus an authorization-service call per
    mutation.
    """
    passport, factory = _RuntimePassport(), _RuntimeFactory()
    repository, identity = _McpInstallations(), _RuntimeCallerIdentity()
    runtime = _scoped_projector(
        passport=passport,
        factory=factory,
        repository=repository,
        identity=identity,
    )

    await runtime.project(
        bot_id="bot-1", owner_id="true-owner", scope=ProjectionScope(skills=True)
    )

    assert factory.service.desired_skills is not None
    assert factory.service.mcp_codes is None
    assert factory.service.deliveries == []
    assert passport.calls == []
    # No MCP mutation means no MCP/CLI read-side pre-flight either.  Those
    # facts would only be consumed by a device-MCP write or Passport update,
    # both of which this scope explicitly omits.
    assert factory.service.collect_calls == []
    assert repository.list_installed_calls == []
    assert passport.query_calls == []
    assert identity.calls == []


@pytest.mark.asyncio
async def test_projector_exposes_skill_and_complete_plan_shapes_at_the_engine_seam():
    """The engine seam receives an honest type, never an incomplete full plan."""
    from agentclaw.community.core.skill_center.runtime_projection_contract import (
        EngineRuntimeProjection,
        ResolvedCapabilityPlan,
        ResolvedSkillPlan,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.registry import (
        EngineRuntimeProjectionRegistry,
    )

    plans = []

    class _RecordingProjection(EngineRuntimeProjection):
        def validate_plan(self, *, skill_assets, retired_mappings=()) -> None:
            return None

        async def apply(self, *, plan, scope, retired_mappings=()) -> None:
            plans.append(plan)

    projection = _RecordingProjection()
    runtime = BotRuntimeProjector(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        reader=_reader(_RuntimeSkills()),
        registry=EngineRuntimeProjectionRegistry(default=projection),
        passport=_RuntimePassport(),
        caller_identity_repo=_RuntimeCallerIdentity(),
    )

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(skills=True),
    )
    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(mcp=True),
    )

    assert type(plans[0]) is ResolvedSkillPlan
    assert type(plans[1]) is ResolvedCapabilityPlan


@pytest.mark.asyncio
async def test_retired_mappings_project_skills_even_when_the_scope_omits_them():
    """Retirements are evidence, not a declaration.

    They are computed from the actual before/after snapshots, so they outrank
    a scope that says Skills did not change — skipping them would strand a
    published mapping the desired state no longer holds.
    """
    pool, factory = _RuntimePool(), _RuntimeFactory()
    runtime = _scoped_projector(pool=pool, factory=factory)

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        retired_mappings=[
            PoolSkillMapping(corpus="repo", relative_path="gone", link_name="gone")
        ],
        scope=ProjectionScope(mcp=True),
    )

    assert pool.publish_calls, "a retirement must still reach the runtime"


def test_claiming_every_code_is_independent_of_which_halves_run():
    """``claim_all_mcp`` answers "which MCPs are claimed", not "which halves".

    An MCP-only reconcile is a real shape — it is what the cutover listener
    passes to ``project_mcp_and_cli``, where declaring the Skill half would
    fight the task that owns the mappings.
    """
    mcp_only = ProjectionScope(mcp=True, claim_all_mcp=True)

    assert mcp_only.skills is False
    assert mcp_only.claim_all_mcp is True
    assert ProjectionScope.everything().claim_all_mcp is True


@pytest.mark.asyncio
async def test_a_reconcile_scope_still_projects_both_halves():
    """The undeclared default is unchanged: everything, as before."""
    passport, factory = _RuntimePassport(), _RuntimeFactory()
    runtime = _scoped_projector(passport=passport, factory=factory)

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope.everything(),
    )

    assert factory.service.desired_skills is not None
    assert factory.service.mcp_codes is not None
    assert passport.calls


@pytest.mark.asyncio
async def test_the_projector_makes_one_mcp_call_not_two():
    """Delivery and declaration reach the device through a single entry point.

    How many device writes an MCP projection takes, and in what order, belongs
    to the service that owns device resolution — not to the projector.
    """
    calls: list[dict] = []

    class _RecordingService(_RuntimeFactoryService):
        async def sync_mcp_delivery(self, **kwargs):
            raise AssertionError("the projector must not call delivery directly")

        async def sync_mcp_desired_state(self, **kwargs):
            raise AssertionError("the projector must not call declaration directly")

        async def project_mcps(self, **kwargs) -> bool:
            calls.append(kwargs)
            return True

    class _RecordingFactory(_RuntimeFactory):
        def __init__(self) -> None:
            super().__init__()
            self.service = _RecordingService()

    runtime = _scoped_projector(factory=_RecordingFactory())

    await runtime.project(
        bot_id="bot-1",
        owner_id="true-owner",
        scope=ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.weather"})),
    )

    assert len(calls) == 1
    assert set(calls[0]) == {"claimed", "released", "declared"}


# ── Default-Set exclusion wire (restored opt-out, spec E.11) ─────────


class _DefaultTargetRepository(_Repository):
    """The addressed Set resolves to the Bot's Default."""

    def __init__(self, excluded_ids=(), excluded_codes=()) -> None:
        super().__init__()
        self.exclusion_calls: list[tuple[str, dict]] = []
        self._excluded_ids = set(excluded_ids)
        self._excluded_codes = set(excluded_codes)

    def get_set(self, **_kwargs):
        return {"id": "9", "is_default": True, "is_active": True}

    def excluded_default_skill_ids(self, **_kwargs) -> set[int]:
        return set(self._excluded_ids)

    def excluded_default_mcp_codes(self, **_kwargs) -> set[str]:
        return set(self._excluded_codes)

    @staticmethod
    def _mutation() -> DesiredStateMutation:
        return DesiredStateMutation(
            {"id": "9", "is_default": True},
            True,
            CapabilityDesiredState(set(), {}, {}),
        )

    def exclude_default_skill(self, **kwargs) -> DesiredStateMutation:
        self.exclusion_calls.append(("exclude_default_skill", kwargs))
        return self._mutation()

    def unexclude_default_skill(self, **kwargs) -> DesiredStateMutation:
        self.exclusion_calls.append(("unexclude_default_skill", kwargs))
        return self._mutation()

    def exclude_default_mcp(self, **kwargs) -> DesiredStateMutation:
        self.exclusion_calls.append(("exclude_default_mcp", kwargs))
        return replace(self._mutation(), mcp_codes=frozenset({kwargs["server_code"]}))

    def unexclude_default_mcp(self, **kwargs) -> DesiredStateMutation:
        self.exclusion_calls.append(("unexclude_default_mcp", kwargs))
        return replace(self._mutation(), mcp_codes=frozenset({kwargs["server_code"]}))


class _ProjectionCountingRuntime(_SuccessfulRuntime):
    def __init__(self) -> None:
        self.projections = 0
        self.scopes: list[ProjectionScope] = []

    async def project(self, *, scope: ProjectionScope, **_kwargs) -> None:
        self.projections += 1
        self.scopes.append(scope)


def _default_wire_service(
    repository, runtime=None, ext_info_provider=None
) -> SkillSetManagementService:
    return SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=runtime if runtime is not None else _SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        ext_info_provider=(
            ext_info_provider if ext_info_provider is not None else lambda _bot_id: None
        ),
    )


@pytest.mark.asyncio
async def test_removing_a_default_member_performs_the_exclusion_and_reconciles():
    repository = _DefaultTargetRepository()
    runtime = _ProjectionCountingRuntime()
    service = _default_wire_service(repository, runtime)

    (result,) = await service.remove_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="9",
        skill_ids=["7"],
    )

    assert result.changed is True
    assert [name for name, _ in repository.exclusion_calls] == ["exclude_default_skill"]
    assert repository.exclusion_calls[0][1]["skill_id"] == "7"
    assert runtime.projections == 1


@pytest.mark.asyncio
async def test_adding_back_an_excluded_default_member_unexcludes():
    repository = _DefaultTargetRepository(excluded_ids={7})
    service = _default_wire_service(repository)

    (result,) = await service.add_skills(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="9",
        skill_ids=["7"],
    )

    assert result.changed is True
    assert [name for name, _ in repository.exclusion_calls] == [
        "unexclude_default_skill"
    ]


@pytest.mark.asyncio
async def test_default_mcp_exclusion_passes_the_platform_default_policy():
    """The service resolves the unmaterialized half and hands it to the UoW.

    The exclusion command can read association rows itself, but the
    engine/template default codes are read-time policy (spec A.2) — the
    service resolves them with the same context the read-side union uses,
    ext info included, so the command's stray-code gate cannot refuse a
    genuine platform default.
    """
    from agentclaw.community.core.mcp.services._defaults import (
        get_default_mcp_server_codes,
    )

    repository = _DefaultTargetRepository()
    ext_calls: list[str] = []

    def _ext(bot_id: str):
        ext_calls.append(bot_id)
        return None

    service = _default_wire_service(repository, ext_info_provider=_ext)

    await service.remove_mcp(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="9",
        server_code="mcp.gone",
    )

    assert ext_calls == ["bot-1"]
    name, kwargs = repository.exclusion_calls[0]
    assert name == "exclude_default_mcp"
    assert kwargs["platform_default_codes"] == frozenset(
        get_default_mcp_server_codes("openclaw", None, ext_info=None)
    )


@pytest.mark.asyncio
async def test_default_mcp_exclusion_propagates_a_template_context_failure():
    """A failed ext lookup is an error, never a silently narrower gate.

    Template presets can be the only place a default MCP exists (the
    AICoding resolver merges them onto the engine defaults), so degrading
    to base defaults on a transient template-service failure would make the
    exclusion command mis-read that member as a stray and report the
    removal as an unchanged no-op — persisting the wrong answer instead of
    surfacing a retryable failure.
    """
    repository = _DefaultTargetRepository()

    def _broken(_bot_id: str):
        raise RuntimeError("template service unavailable")

    service = _default_wire_service(repository, ext_info_provider=_broken)

    with pytest.raises(RuntimeError, match="template service unavailable"):
        await service.remove_mcp(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="9",
            server_code="mcp.gone",
        )
    assert repository.exclusion_calls == []


@pytest.mark.asyncio
async def test_adding_a_new_member_to_the_default_stays_immutable():
    repository = _DefaultTargetRepository()
    service = _default_wire_service(repository)

    with pytest.raises(
        SkillSetControlPlaneConflictError, match="SYSTEM_DEFAULT_IMMUTABLE"
    ):
        await service.add_skills(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="9",
            skill_ids=["7"],
        )
    assert repository.exclusion_calls == []


@pytest.mark.asyncio
async def test_default_mcp_exclusion_wire_mirrors_the_skill_wire():
    repository = _DefaultTargetRepository(excluded_codes={"mcp.back"})
    runtime = _ProjectionCountingRuntime()
    service = _default_wire_service(repository, runtime)

    removed = await service.remove_mcp(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="9",
        server_code="mcp.gone",
    )
    added = await service.add_mcp(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="9",
        server_code="mcp.back",
    )

    assert removed["changed"] is True and added["changed"] is True
    assert [name for name, _ in repository.exclusion_calls] == [
        "exclude_default_mcp",
        "unexclude_default_mcp",
    ]
    assert runtime.projections == 2
    assert runtime.scopes == [
        ProjectionScope(mcp=True, released_mcp=frozenset({"mcp.gone"})),
        ProjectionScope(mcp=True, claimed_mcp=frozenset({"mcp.back"})),
    ]

    with pytest.raises(
        SkillSetControlPlaneConflictError, match="SYSTEM_DEFAULT_IMMUTABLE"
    ):
        await service.add_mcp(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="9",
            server_code="mcp.never-member",
        )


@pytest.mark.asyncio
async def test_unexcluding_a_default_mcp_still_requires_marketplace_permission():
    repository = _DefaultTargetRepository(excluded_codes={"mcp.back"})
    service = SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=False),
        mcp_auth=_McpAuth(allowed=False),
        ext_info_provider=lambda _bot_id: None,
    )

    with pytest.raises(McpPermissionDeniedError):
        await service.add_mcp(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="9",
            server_code="mcp.back",
        )
    assert repository.exclusion_calls == []
