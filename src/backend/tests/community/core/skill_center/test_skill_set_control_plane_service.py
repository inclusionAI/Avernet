"""Control-plane invariants that cannot live in the HTTP adapter."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.repository.implementations.skill_center.skill_set_control_plane import (
    SkillSetDesiredState,
    SkillSetMutation,
)
from agentclaw.community.core.skill_center.errors import (
    SkillSetAccessDeniedError,
    SkillSetControlPlaneNotFoundError,
    SkillSetRuntimeReconcileError,
    SkillEngineNotSupportedError,
    McpPermissionDeniedError,
)
from agentclaw.community.core.skill_center.services.skill_set_control_plane import (
    SkillSetControlPlaneService,
)
from agentclaw.community.core.skill_center.services.bot_runtime_projection_reconciler import (
    BotRuntimeProjectionReconciler,
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

    def update_set(self, **kwargs):
        self.update_calls.append(kwargs)
        return {"id": kwargs["set_id"], "is_default": False}

    def set_active(self, **kwargs) -> SkillSetMutation:
        self.set_active_calls.append(kwargs)
        return SkillSetMutation(
            item={"id": "set-1", "name": "set", "is_default": False, "is_active": True},
            changed=True,
            previous_state=SkillSetDesiredState(set(), {}, {}),
        )

    def restore_desired_state(self, **kwargs) -> None:
        self.restore_calls.append(kwargs)

    def list_mcps(self, **_kwargs):
        return []

    def get_set(self, **_kwargs):
        return {"is_default": False}

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
            "is_active": False,
        }


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


class _Collaborators:
    def __init__(self) -> None:
        self.calls = []

    def can_manage_bot(self, **kwargs):
        self.calls.append(kwargs)
        return True


class _DeniedCollaborators(_Collaborators):
    def can_manage_bot(self, **kwargs):
        self.calls.append(kwargs)
        return False


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

    async def reconcile(
        self, *, bot_id: str, owner_id: str, retired_mappings=()
    ) -> None:
        assert bot_id == "bot-1"
        self.owners.append(owner_id)
        self.reconcile_calls.append(
            {
                "bot_id": bot_id,
                "owner_id": owner_id,
                "retired_mappings": tuple(retired_mappings),
            }
        )
        if self._fail_first and len(self.owners) == 1:
            raise RuntimeError("runtime failed")

    async def reconcile_cleanup(self, *, bot_id: str, owner_id: str) -> None:
        await self.reconcile(bot_id=bot_id, owner_id=owner_id)


class _Audit:
    def insert(self, _data) -> None:
        return None


class _SuccessfulRuntime:
    async def snapshot_skill_mappings(self, **_kwargs):
        return ()

    async def reconcile(self, **_kwargs) -> None:
        return None

    async def reconcile_cleanup(self, **_kwargs) -> None:
        return None


class _CleanupRuntime(_SuccessfulRuntime):
    def __init__(self) -> None:
        self.cleanup_calls: list[dict] = []

    async def reconcile(self, **_kwargs) -> None:
        raise AssertionError("cleanup-only Bot must not receive a full projection")

    async def reconcile_cleanup(self, **kwargs) -> None:
        self.cleanup_calls.append(kwargs)


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

    def activate_mcp_direct(self, **kwargs) -> SkillSetMutation:
        self.direct_calls.append(kwargs)
        return SkillSetMutation({}, True, SkillSetDesiredState(set(), {}, {}))


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


class _RuntimeFactoryService:
    def __init__(self) -> None:
        self.mcp_codes: set[str] | None = None
        self.collect_calls: list[dict] = []
        self.desired_skills: list[dict] | None = None

    def sync_runtime(self, *, desired_skills: list[dict]) -> bool:
        self.desired_skills = desired_skills
        return True

    async def sync_mcp_desired_state(self, *, server_codes: set[str]) -> bool:
        self.mcp_codes = server_codes
        return True

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


class _RuntimeBots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        assert (bot_id, owner_id) == ("bot-1", "true-owner")
        return {
            "entity_id": "entity-1",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "entity_type": "staff",
            "env": "pre",
        }


class _UnsupportedRuntimeBots(_RuntimeBots):
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        return {
            **super().get_by_id_and_owner(bot_id, owner_id),
            "bot_type": "desktop",
            "active_engine": "claude_code",
        }


class _AicodingImageRuntimeBots(_RuntimeBots):
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        return {
            **super().get_by_id_and_owner(bot_id, owner_id),
            "active_engine": "claude_code",
            "template_type": "personalCoding",
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
        self.materialize_calls: list[dict] = []

    def ensure_active_skillset_installations(self, **kwargs) -> int:
        self.materialize_calls.append(kwargs)
        return 0

    def list_installed_mcps(self, *, bot_id: str, owner_id: str) -> set[str]:
        assert bot_id == "bot-1"
        assert owner_id == "true-owner"
        return {"mcp.weather"}


class _FailingMaterializationRepository(_McpInstallations):
    def ensure_active_skillset_installations(self, **_kwargs) -> int:
        raise RuntimeError("installation persistence unavailable")


class _RuntimeSkills:
    def __init__(self, assets=()) -> None:
        self._assets = list(assets)

    def list_bot_active_assets(self, **kwargs):
        assert kwargs == {
            "env": "pre",
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "engine": "openclaw",
        }
        return self._assets


class _HistoricalAicodingRuntimeSkills(_RuntimeSkills):
    def list_bot_active_assets(self, **kwargs):
        assert kwargs == {
            "env": "pre",
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "engine": "aicoding",
        }
        return []


class _RuntimePool:
    def __init__(self) -> None:
        self.publish_calls: list[dict] = []
        self.verify_calls: list[dict] = []

    async def probe(self, **_kwargs):
        raise AssertionError("non-Center projection must keep the legacy adapter")

    async def publish_mappings(self, **kwargs):
        self.publish_calls.append(kwargs)
        return True

    async def verify_mappings(self, **kwargs):
        self.verify_calls.append(kwargs)
        return True


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


class _CenterRuntimeSkills:
    def list_bot_active_assets(self, **_kwargs):
        return [
            RegisteredSkillAsset(
                skill_id=7,
                name="center-skill",
                git_path="center://stable-skill-uuid",
                skill_uuid="stable-skill-uuid",
                sc_version_number="3.0.0",
            )
        ]


class _AicodingImageCenterRuntimeSkills(_CenterRuntimeSkills):
    def list_bot_active_assets(self, **kwargs):
        assert kwargs["engine"] == "claude_code"
        return super().list_bot_active_assets(**kwargs)


class _TeclawRuntimeSkills:
    def list_bot_active_assets(self, **_kwargs):
        return [
            RegisteredSkillAsset(
                skill_id=8,
                name="repo-skill",
                git_path="git://team/repo-skill",
            )
        ]


class _RuntimeLayouts:
    def get(self, _scope):
        return None


class _RuntimePassport:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def update_passport(self, **kwargs) -> None:
        self.calls.append(kwargs)

    def query_passport_clis(self, bot_id: str, owner_id: str) -> list[dict]:
        assert (bot_id, owner_id) == ("bot-1", "true-owner")
        # This is the effective Default CLI scope after a user removed a
        # static default. A reconcile must preserve it exactly, not revive the
        # engine's static list.
        return [{"cli_code": "kept-cli", "cli_name": "Kept"}]


class _FailingRuntimePassport(_RuntimePassport):
    def query_passport_clis(self, bot_id: str, owner_id: str) -> list[dict]:
        raise RuntimeError("passport unavailable")


@pytest.mark.asyncio
async def test_collaborator_command_restores_desired_state_and_uses_true_owner():
    repository = _Repository()
    collaborators = _Collaborators()
    runtime = _Runtime()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=runtime,
        legacy_factory=object(),
        passport=object(),
        authorization=collaborators,
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    with pytest.raises(SkillSetRuntimeReconcileError):
        await service.activate(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="collaborator",
            set_id="set-1",
        )

    assert collaborators.calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "actor_id": "collaborator",
        }
    ]
    assert runtime.owners == ["true-owner", "true-owner"]
    assert runtime.snapshot_calls == [
        {"bot_id": "bot-1", "owner_id": "true-owner"},
        {"bot_id": "bot-1", "owner_id": "true-owner"},
    ]
    assert runtime.reconcile_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "retired_mappings": (),
        },
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "retired_mappings": (
                PoolSkillMapping(corpus="local", relative_path="qa", link_name="qa"),
                PoolSkillMapping(
                    corpus="repo", relative_path="business/eva", link_name="eva"
                ),
            ),
        },
    ]
    assert len(repository.restore_calls) == 1


@pytest.mark.asyncio
async def test_deactivate_retires_mappings_removed_from_the_runtime_projection():
    repository = _Repository()
    runtime = _Runtime(snapshots=[_Runtime._skill_mappings(), ()], fail_first=False)
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=runtime,
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
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
        }
    ]


def test_create_rejects_missing_bot_instead_of_creating_orphan_set():
    service = SkillSetControlPlaneService(
        repository=_Repository(),
        bot_repo=_MissingBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
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
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_MissingBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
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
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=bots,
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
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


def test_addressed_create_persists_metadata_without_runtime_reconcile() -> None:
    repository = _CreateRepository()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
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


def test_default_read_rejects_missing_bot():
    service = SkillSetControlPlaneService(
        repository=_Repository(),
        bot_repo=_MissingBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    with pytest.raises(SkillSetControlPlaneNotFoundError):
        service.get_set(
            bot_id="default", owner_id="owner", user_id="owner", set_id="set-1"
        )


@pytest.mark.asyncio
async def test_legacy_sync_activates_additively_without_replacing_other_sets():
    repository = _Repository()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    await service.sync(
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
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=bots,
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    await service.sync(
        bot_id="default",
        owner_id=owner_id,
        actor_id=owner_id,
        set_id="set-1",
    )

    assert bots.lookups == [("default", owner_id)]
    assert repository.set_active_calls[0]["owner_id"] == owner_id


def test_skill_set_acl_denial_is_forbidden_not_not_found():
    service = SkillSetControlPlaneService(
        repository=_Repository(),
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_DeniedCollaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    with pytest.raises(SkillSetAccessDeniedError):
        service.list_sets(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="collaborator",
        )


def test_resources_forwards_resolved_bot_owner_to_owner_scoped_set_listing():
    repository = _ResourceRepository()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=_ResourceLegacyFactory(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    assert service.resources(
        bot_id="bot-1", owner_id="true-owner", user_id="true-owner"
    ) == []
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
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_AicodingImageBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=_ResourceLegacyFactory(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    assert service.list_sets(
        bot_id="bot-1", owner_id="true-owner", user_id="true-owner"
    ) == []
    assert repository.list_set_calls == [{
        "bot_id": "bot-1",
        "owner_id": "true-owner",
        "engine_type": "claude_code",
        "default_engine_types": ("aicoding", "claude_code"),
    }]


def test_update_set_uses_runtime_default_candidates_for_coding_image():
    repository = _Repository()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_AicodingImageBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    service.update_set(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        set_id="default-id",
        name=None,
        description="ignored by the repository fake",
    )

    assert repository.update_calls == [{
        "bot_id": "bot-1",
        "owner_id": "true-owner",
        "set_id": "default-id",
        "name": None,
        "description": "ignored by the repository fake",
        "engine_type": "claude_code",
        "default_engine_types": ("aicoding", "claude_code"),
    }]


def test_resources_reads_global_default_mcp_projection_for_collaborator_owner_scope():
    repository = _DefaultResourceRepository()
    authorization = _Collaborators()
    legacy = _ResourceLegacyFactory()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=legacy,
        passport=object(),
        authorization=authorization,
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    result = service.resources(
        bot_id="bot-1", owner_id="true-owner", user_id="collaborator"
    )

    assert result[0]["mcps"] == [{"server_code": "legacy-default-mcp"}]
    assert authorization.calls == [{
        "bot_id": "bot-1", "owner_id": "true-owner", "actor_id": "collaborator"
    }]
    assert repository.list_mcp_calls == []
    assert legacy.service.default_mcp_calls == [{
        "skill_set_id": "global-default",
        "user_id": "true-owner",
        "bot_id": "bot-1",
        "engine_type": "openclaw",
    }]


def test_resources_keeps_ordinary_mcp_membership_on_canonical_repository_path():
    repository = _MixedResourceRepository()
    legacy = _ResourceLegacyFactory()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=legacy,
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    result = service.resources(
        bot_id="bot-1", owner_id="true-owner", user_id="true-owner"
    )

    assert result[0]["mcps"] == [{"server_code": "legacy-default-mcp"}]
    assert result[1]["mcps"] == [{"server_code": "ordinary-set-mcp"}]
    assert repository.list_mcp_calls == [{
        "bot_id": "bot-1",
        "owner_id": "true-owner",
        "set_id": "ordinary-set",
        "engine_type": "openclaw",
        "default_engine_types": ("openclaw",),
    }]


@pytest.mark.asyncio
async def test_skill_set_mutation_fails_closed_for_unsupported_bot_engine_pair():
    repository = _Repository()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_UnsupportedBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    with pytest.raises(SkillEngineNotSupportedError):
        await service.activate(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="set-1",
        )

    assert repository.set_active_calls == []


@pytest.mark.asyncio
async def test_historical_bot_skill_set_deactivate_uses_cleanup_projection():
    repository = _Repository()
    runtime = _CleanupRuntime()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_UnsupportedBots(),
        runtime=runtime,
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
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
    assert runtime.cleanup_calls == [{"bot_id": "bot-1", "owner_id": "true-owner"}]


def test_skill_set_metadata_mutations_share_the_runtime_matrix_gate():
    service = SkillSetControlPlaneService(
        repository=_Repository(),
        bot_repo=_UnsupportedBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
    )

    operations = [
        lambda: service.create_set(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            name="set",
            description=None,
        ),
        lambda: service.update_set(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            set_id="set-1",
            name="new-name",
            description=None,
        ),
    ]

    for operation in operations:
        with pytest.raises(SkillEngineNotSupportedError):
            operation()


def test_historical_bot_may_delete_an_inactive_skill_set_without_new_runtime_write():
    service = SkillSetControlPlaneService(
        repository=_Repository(),
        bot_repo=_UnsupportedBots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
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
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=factory,
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=True),
        mcp_auth=_McpAuth(allowed=True),
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
async def test_mcp_direct_activation_checks_permission_before_writing_desired_state():
    repository = _McpRepository()
    auth = _McpAuth(allowed=True)
    mcp_center = _McpCenter(allowed=True)
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=mcp_center,
        mcp_auth=auth,
    )

    await service.activate_mcp_direct(
        bot_id="bot-1",
        owner_id="true-owner",
        user_id="true-owner",
        server_code="mcp.weather",
    )

    assert mcp_center.calls == [("true-owner", "mcp.weather")]
    assert repository.direct_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "server_code": "mcp.weather",
            "engine_type": "openclaw",
        }
    ]


@pytest.mark.asyncio
async def test_mcp_direct_activation_denies_before_writing_desired_state():
    repository = _McpRepository()
    service = SkillSetControlPlaneService(
        repository=repository,
        bot_repo=_Bots(),
        runtime=_SuccessfulRuntime(),
        legacy_factory=object(),
        passport=object(),
        authorization=_Collaborators(),
        audit_log_repo=_Audit(),
        mcp_center=_McpCenter(allowed=False),
        mcp_auth=_McpAuth(allowed=False),
    )

    with pytest.raises(McpPermissionDeniedError):
        await service.activate_mcp_direct(
            bot_id="bot-1",
            owner_id="true-owner",
            user_id="true-owner",
            server_code="mcp.weather",
        )
    assert repository.direct_calls == []


@pytest.mark.asyncio
async def test_runtime_mapping_snapshot_has_no_runtime_side_effects():
    repository = _McpInstallations()
    pool = _RuntimePool()
    runtime = BotRuntimeProjectionReconciler(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=repository,
        pool_skills=_RuntimeSkills(
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
        pool_runtime=pool,
        pool_layouts=_RuntimeLayouts(),
        passport=_RuntimePassport(),
    )

    snapshot = await runtime.snapshot_skill_mappings(
        bot_id="bot-1", owner_id="true-owner"
    )

    assert snapshot == (
        PoolSkillMapping(corpus="local", relative_path="qa", link_name="qa"),
        PoolSkillMapping(
            corpus="repo", relative_path="business/eva", link_name="eva"
        ),
    )
    assert repository.materialize_calls == []
    assert pool.publish_calls == []
    assert pool.verify_calls == []


@pytest.mark.asyncio
async def test_runtime_reconcile_materializes_active_ordinary_skillset_members_first():
    repository = _McpInstallations()
    runtime = BotRuntimeProjectionReconciler(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=repository,
        pool_skills=_RuntimeSkills(),
        pool_runtime=_RuntimePool(),
        pool_layouts=_RuntimeLayouts(),
        passport=_RuntimePassport(),
    )

    await runtime.reconcile(bot_id="bot-1", owner_id="true-owner")

    assert repository.materialize_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "engine_type": "openclaw",
        }
    ]


@pytest.mark.asyncio
async def test_runtime_reconcile_fails_before_runtime_projection_when_materialization_fails():
    runtime = BotRuntimeProjectionReconciler(
        factory=_RuntimeFactory(),
        bot_repo=_RuntimeBots(),
        repository=_FailingMaterializationRepository(),
        pool_skills=_RuntimeSkills(),
        pool_runtime=_RuntimePool(),
        pool_layouts=_RuntimeLayouts(),
        passport=_RuntimePassport(),
    )

    with pytest.raises(RuntimeError, match="installation persistence unavailable"):
        await runtime.reconcile(bot_id="bot-1", owner_id="true-owner")


@pytest.mark.asyncio
async def test_runtime_reconcile_projects_full_mcp_desired_state():
    factory = _RuntimeFactory()
    passport = _RuntimePassport()
    runtime = BotRuntimeProjectionReconciler(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        pool_skills=_RuntimeSkills(),
        pool_runtime=_RuntimePool(),
        pool_layouts=_RuntimeLayouts(),
        passport=passport,
    )

    await runtime.reconcile(bot_id="bot-1", owner_id="true-owner")

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
        }
    ]
    assert passport.calls == [
        {
            "bot_id": "bot-1",
            "user_id": "true-owner",
            "engine_type": "openclaw",
            "resource_scope": {
                "mcp_codes": ["mcp.template-preset", "mcp.weather"],
                "cli_items": [{"cli_code": "kept-cli", "cli_name": "Kept"}],
            },
        }
    ]


@pytest.mark.asyncio
async def test_runtime_reconcile_fails_closed_when_effective_cli_scope_cannot_be_read():
    factory = _RuntimeFactory()
    passport = _FailingRuntimePassport()
    runtime = BotRuntimeProjectionReconciler(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        pool_skills=_RuntimeSkills(),
        pool_runtime=_RuntimePool(),
        pool_layouts=_RuntimeLayouts(),
        passport=passport,
    )

    with pytest.raises(SkillSetRuntimeReconcileError):
        await runtime.reconcile(bot_id="bot-1", owner_id="true-owner")

    assert passport.calls == []


@pytest.mark.asyncio
async def test_runtime_reconcile_requires_and_uses_mapping_v3_for_center():
    factory = _RuntimeFactory()
    pool = _CenterRuntimePool()
    runtime = BotRuntimeProjectionReconciler(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        pool_skills=_CenterRuntimeSkills(),
        pool_runtime=pool,
        pool_layouts=_RuntimeLayouts(),
        passport=_RuntimePassport(),
    )

    await runtime.reconcile(bot_id="bot-1", owner_id="true-owner")

    assert factory.service.mcp_codes is not None
    assert len(pool.publish_calls) == len(pool.verify_calls) == 1
    assert pool.publish_calls[0]["mapping_contract_version"] == (
        "skills-pool-mapping-v3"
    )
    assert pool.publish_calls[0]["mappings"][0].to_dict() == {
        "corpus": "center",
        "link_name": "center-skill",
        "skill_uuid": "stable-skill-uuid",
        "sc_version_number": "3.0.0",
    }


@pytest.mark.asyncio
async def test_coding_template_uses_aicoding_for_center_probe_but_keeps_logical_engine():
    factory = _RuntimeFactory()
    pool = _CenterRuntimePool()
    runtime = BotRuntimeProjectionReconciler(
        factory=factory,
        bot_repo=_AicodingImageRuntimeBots(),
        repository=_McpInstallations(),
        pool_skills=_AicodingImageCenterRuntimeSkills(),
        pool_runtime=pool,
        pool_layouts=_RuntimeLayouts(),
        passport=_RuntimePassport(),
    )

    await runtime.reconcile(bot_id="bot-1", owner_id="true-owner")

    assert factory.kwargs["engine_type"] == "claude_code"
    assert pool.probe_calls == [
        {"bot_id": "bot-1", "user_id": "true-owner", "engine": "aicoding"}
    ]
    assert len(pool.publish_calls) == len(pool.verify_calls) == 1


@pytest.mark.asyncio
async def test_historical_aicoding_cleanup_uses_legacy_runtime_not_pool_mapping():
    factory = _RuntimeFactory()
    pool = _RuntimePool()
    runtime = BotRuntimeProjectionReconciler(
        factory=factory,
        bot_repo=_HistoricalAicodingRuntimeBots(),
        repository=_McpInstallations(),
        pool_skills=_HistoricalAicodingRuntimeSkills(),
        pool_runtime=pool,
        pool_layouts=_RuntimeLayouts(),
        passport=_RuntimePassport(),
    )

    await runtime.reconcile_cleanup(bot_id="bot-1", owner_id="true-owner")

    assert factory.kwargs["engine_type"] == "aicoding"
    assert factory.service.desired_skills == []
    assert factory.service.mcp_codes is not None
    assert pool.publish_calls == []
    assert pool.verify_calls == []


@pytest.mark.asyncio
async def test_historical_cleanup_rejects_center_before_runtime_or_mcp_delivery():
    factory = _RuntimeFactory()
    pool = _CenterRuntimePool()
    runtime = BotRuntimeProjectionReconciler(
        factory=factory,
        bot_repo=_HistoricalAicodingRuntimeBots(),
        repository=_McpInstallations(),
        pool_skills=_CenterRuntimeSkills(),
        pool_runtime=pool,
        pool_layouts=_RuntimeLayouts(),
        passport=_RuntimePassport(),
    )

    with pytest.raises(SkillSetRuntimeReconcileError):
        await runtime.reconcile_cleanup(bot_id="bot-1", owner_id="true-owner")

    assert factory.service.desired_skills is None
    assert factory.service.mcp_codes is None
    assert pool.probe_calls == []
    assert pool.publish_calls == []


@pytest.mark.asyncio
async def test_runtime_reconcile_fails_closed_for_unsupported_bot_engine_pair():
    runtime = BotRuntimeProjectionReconciler(
        factory=_RuntimeFactory(),
        bot_repo=_UnsupportedRuntimeBots(),
        repository=_McpInstallations(),
        pool_skills=_RuntimeSkills(),
        pool_runtime=_RuntimePool(),
        pool_layouts=_RuntimeLayouts(),
        passport=_RuntimePassport(),
    )

    with pytest.raises(SkillEngineNotSupportedError):
        await runtime.reconcile(bot_id="bot-1", owner_id="true-owner")


@pytest.mark.asyncio
async def test_teclaw_v4_rejects_center_without_any_center_runtime_request():
    pool = _CenterRuntimePool()
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjectionReconciler(
        factory=factory,
        bot_repo=_TeclawRuntimeBots(),
        repository=_McpInstallations(),
        pool_skills=_CenterRuntimeSkills(),
        pool_runtime=pool,
        pool_layouts=_RuntimeLayouts(),
        passport=_RuntimePassport(),
    )

    with pytest.raises(SkillSetRuntimeReconcileError):
        await runtime.reconcile(bot_id="bot-1", owner_id="true-owner")

    assert pool.probe_calls == []
    assert pool.publish_calls == []
    assert factory.service.collect_calls == []


@pytest.mark.asyncio
async def test_teclaw_v4_repo_projection_uses_artifact_runtime_not_pool_mapping():
    pool = _RuntimePool()
    factory = _RuntimeFactory()
    runtime = BotRuntimeProjectionReconciler(
        factory=factory,
        bot_repo=_TeclawRuntimeBots(),
        repository=_McpInstallations(),
        pool_skills=_TeclawRuntimeSkills(),
        pool_runtime=pool,
        pool_layouts=_RuntimeLayouts(),
        passport=_RuntimePassport(),
    )

    await runtime.reconcile(bot_id="bot-1", owner_id="true-owner")

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
    runtime = BotRuntimeProjectionReconciler(
        factory=factory,
        bot_repo=_RuntimeBots(),
        repository=_McpInstallations(),
        pool_skills=_RuntimeSkills(),
        pool_runtime=pool,
        pool_layouts=_RuntimeLayouts(),
        passport=_RuntimePassport(),
    )

    await runtime.reconcile_non_skill_projection(bot_id="bot-1", owner_id="true-owner")

    assert factory.service.desired_skills is None
    assert factory.service.mcp_codes is not None
    assert pool.publish_calls == []
    assert pool.verify_calls == []
