"""Control-plane invariants that cannot live in the HTTP adapter."""

from __future__ import annotations
from tests.community.core.skill_center._skill_set_management_bot_doubles import (
    _Bots as _Bots,
    _MissingBots as _MissingBots,
    _SharedDefaultBots as _SharedDefaultBots,
    _UnsupportedBots as _UnsupportedBots,
    _AicodingImageBots as _AicodingImageBots,
    _PlainClaudeCodeBots as _PlainClaudeCodeBots,
    _LiteralAicodingBots as _LiteralAicodingBots,
    _NotReadyApplicationCodingBots as _NotReadyApplicationCodingBots,
    _RuntimeBots as _RuntimeBots,
    _AicodingImageRuntimeBots as _AicodingImageRuntimeBots,
    _PlainClaudeCodeRuntimeBots as _PlainClaudeCodeRuntimeBots,
    _HistoricalAicodingRuntimeBots as _HistoricalAicodingRuntimeBots,
    _TeclawRuntimeBots as _TeclawRuntimeBots,
    _PkLessRuntimeBots as _PkLessRuntimeBots,
)






import re

from dataclasses import replace

from types import SimpleNamespace

from unittest.mock import MagicMock


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
    SkillSetControlPlaneNotFoundError,
)

from agentclaw.community.core.skill_center.services.skill_set_management_service import (
    SkillSetManagementService,
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

    def get_set(self, **kwargs):
        return {
            "id": kwargs["set_id"],
            "is_default": kwargs["set_id"] == "global-default",
            "is_active": True,
        }

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
        self.apply_calls: list[dict] = []

    def check_mcp_permission_detail(self, actor_id: str, server_code: str) -> dict:
        self.calls.append((actor_id, server_code))
        return {
            "has_permission": self.allowed,
            "access_level": "LOCAL" if self.allowed else "PRIVATE",
        }

    def apply_permission(self, **_kwargs) -> dict:
        self.apply_calls.append(_kwargs)
        return {"success": True, "process_url": None, "error": None}

class _McpConfig:
    def build_mcp_sync_payload(self, **_kwargs) -> tuple[None, dict[str, str], str, None]:
        return None, {}, "PROD", None

class _OwnerSensitiveMcpConfig:
    def __init__(self) -> None:
        self.user_ids: list[str] = []

    def build_mcp_sync_payload(self, **kwargs) -> tuple[None, dict[str, str], str, None]:
        user_id = kwargs["user_id"]
        self.user_ids.append(user_id)
        return None, {}, "PROD" if user_id == "collaborator" else "PRE", None

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
        return {
            "accessLevel": "PUBLIC",
            "runMode": "REMOTE",
            "endpoints": [
                {
                    "networkType": "OFFICE",
                    "env": "PROD",
                    "transportProtocol": "STREAMABLE_HTTP",
                    "url": "https://mcp.example.test/streamable/mcp",
                }
            ],
        }

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
    from agentclaw.community.core.skill_center.services.runtime_projections.skill_runtime_delivery import (
        SkillRuntimeDelivery,
    )
    from agentclaw.community.core.skill_center.services.runtime_projections.whole_artifact import (
        WholeArtifactRuntimeProjection,
    )
    from agentclaw.community.core.devices.services.device_context import DeviceContext

    class _Contexts:
        def resolve_for_bot(self, bot_id: str, user_id: str) -> DeviceContext:
            return DeviceContext(
                provider="local",
                conn_info={},
                binding_id=1,
                bot_id=bot_id,
                user_id=user_id,
                bot_type="personal",
            )

    class _UnusedCenterContent:
        def lookup(self, _identity):
            raise AssertionError("cloud test projection must not resolve downloads")

        def prepare(self, _identity):
            raise AssertionError("foreground projection must not prepare downloads")

    # The same routing the DI provider builds. If these two drift, these tests
    # stop describing production.
    return EngineRuntimeProjectionRegistry(
        default=PerDomainRuntimeProjection(
            skill_delivery=SkillRuntimeDelivery(
                pool_runtime=pool_runtime,
                pool_layouts=pool_layouts,
                device_contexts=_Contexts(),
                center_content=_UnusedCenterContent(),
            ),
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
        assert owner_id == "entity-1"
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
            "owner_id": "entity-1",
        }
        return self._assets

class _RuntimePool:
    def __init__(self, *, published=True, verified=True) -> None:
        self.apply_calls: list[dict] = []
        self.publish_calls: list[dict] = []
        self.verify_calls: list[dict] = []
        self._published = published
        self._verified = verified

    async def probe(self, **_kwargs):
        raise AssertionError("non-Center projection must keep the legacy adapter")

    async def apply_mappings(self, **kwargs):
        from agentclaw.community.core.skills_pool.ports import (
            LegacyMappingApplyRequired,
        )

        self.apply_calls.append(kwargs)
        raise LegacyMappingApplyRequired()

    async def publish_mappings(self, **kwargs):
        self.publish_calls.append(kwargs)
        return self._published

    async def verify_mappings(self, **kwargs):
        self.verify_calls.append(kwargs)
        return self._verified

class _FailingPublishRuntimePool(_RuntimePool):
    async def publish_mappings(self, **kwargs):
        self.publish_calls.append(kwargs)
        raise RuntimeError("device unavailable")

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

class _ResourcePassport:
    def __init__(self, passport: dict) -> None:
        self.passport = passport
        self.calls: list[tuple[str, str]] = []

    def query_agent_passport(self, bot_id: str, owner_id: str) -> dict:
        self.calls.append((bot_id, owner_id))
        return self.passport

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

def _passport_mcp_items(passport: _RuntimePassport) -> list[dict]:
    """The MCP identity scope from the single updatePassport call."""
    assert len(passport.calls) == 1
    return passport.calls[0]["resource_scope"]["mcp_items"]


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
        mcp_config=_McpConfig(),
        ext_info_provider=lambda _bot_id: None,
        recovery=MagicMock(),
    )

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
    repository, runtime=None, ext_info_provider=None, mcp_center=None
) -> SkillSetManagementService:
    return SkillSetManagementService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=runtime if runtime is not None else _SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Authorization(),
        audit_log_repo=_Audit(),
        mcp_center=mcp_center if mcp_center is not None else _McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
        mcp_config=_McpConfig(),
        ext_info_provider=(
            ext_info_provider if ext_info_provider is not None else lambda _bot_id: None
        ),
        recovery=MagicMock(),
    )

