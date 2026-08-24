"""Core fault-injection seam for public Local Skill activation commands."""

from __future__ import annotations

import pytest

from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    LocalSkillRuntimeSyncError,
    LocalSkillStorageError,
    SkillManagedBySkillSetError,
    SkillRuntimeNameConflictError,
)
from agentclaw.community.core.skill_center.services.local_skill_state_service import (
    LocalSkillStateService,
)
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeDesiredState,
    RuntimeProjectionResolver,
)
from agentclaw.community.core.skills_pool.models import (
    RegisteredSkillAsset,
    SkillMappingSourceLayout,
)


class _Skills:
    def __init__(self, *, active: bool = False, git_path: str = "local://one") -> None:
        self.active = active
        self.git_path = git_path

    def get_by_id(self, skill_id: str):
        if skill_id != "9":
            return None
        return {
            "id": "9",
            "user_id": "owner",
            "bolt_id": "bot",
            "git_path": self.git_path,
        }

    def list_skill_set_references(self, _skill_id: str, _skill_uuid: str | None):
        return []

    def get_bot_local_skill(self, **kwargs):
        if not self.git_path.startswith("local://"):
            return None
        return {
            "id": "9",
            "user_id": "owner",
            "bolt_id": "bot",
            "git_path": self.git_path,
            "name": "one",
            "active": self.active,
        }

    def list_bot_active_assets(self, **_kwargs):
        if not self.active:
            return []
        return [
            RegisteredSkillAsset(
                skill_id=9,
                name="one",
                git_path=self.git_path,
            )
        ]


class _Sets:
    def __init__(self, skills: _Skills, *, associated: bool = True) -> None:
        self.skills = skills
        self.events: list[str] = []
        self.associated = associated
        self.remove_all_calls = 0
        self.install_calls = 0

    def get_default(self, **kwargs):
        return {"id": "4"}

    def get_skills_in_set(self, _skill_set_id: str):
        return [{"id": "9"}] if self.associated else []

    def add_skill_to_set(self, *_args, **_kwargs):
        self.events.append("associate")
        self.associated = True
        return True

    def remove_default_skill_exclusion(self, *args):
        self.events.append("remove")
        self.skills.active = True
        return True

    def remove_all_default_skill_exclusions(self, *args):
        self.remove_all_calls += 1
        self.events.append("remove")
        self.skills.active = True
        return True

    def add_default_skill_exclusion(self, *args):
        self.events.append("add")
        self.skills.active = False
        return True

    # Compatibility fake for the new Installation Repository seam.  The event
    # labels intentionally retain the pre-migration assertions below.
    def install(self, **_kwargs):
        self.install_calls += 1
        if self.skills.active:
            return False
        self.events.append("remove")
        self.skills.active = True
        return True

    def uninstall(self, **_kwargs):
        if not self.skills.active:
            return False
        self.events.append("add")
        self.skills.active = False
        return True


class _Installations:
    def __init__(self, skills: _Skills) -> None:
        self.skills = skills
        self.events: list[str] = []

    def install(self, *, env: str, owner_id: str, bot_id: str, skill_id: str) -> bool:
        self.events.append(f"install:{env}:{bot_id}:{skill_id}")
        if self.skills.active:
            return False
        self.skills.active = True
        return True

    def uninstall(self, *, env: str, owner_id: str, bot_id: str, skill_id: str) -> bool:
        self.events.append(f"uninstall:{env}:{bot_id}:{skill_id}")
        if not self.skills.active:
            return False
        self.skills.active = False
        return True


class _Bots:
    def __init__(
        self,
        status: str,
        entity_id: str = "owner",
        engine: str = "openclaw",
        bot_type: str | None = None,
    ) -> None:
        self.status = status
        self.entity_id = entity_id
        self.engine = engine
        self.bot_type = bot_type

    def get_by_id_and_owner(self, *_args):
        return {
            "status": self.status,
            "active_engine": self.engine,
            "env": "pre",
            "entity_id": self.entity_id,
            "entity_type": "staff",
            **({"bot_type": self.bot_type} if self.bot_type is not None else {}),
        }


class _Collaborators:
    def check_collaborator_permission(self, *_args):
        return {"has_permission": True}


class _Runtime:
    def __init__(self, success: bool) -> None:
        self.success = success
        self.calls = 0
        self.publish_results = [True]
        self.verify_results = [True]
        self.publish_calls = []
        self.verify_calls = []

    def sync_runtime(self, *, desired_skills=None):
        self.calls += 1
        return self.success

    async def publish_mappings(self, **kwargs):
        self.publish_calls.append(kwargs)
        return self.publish_results.pop(0)

    async def verify_mappings(self, **kwargs):
        self.verify_calls.append(kwargs)
        return self.verify_results.pop(0)


class _RuntimeReconciler:
    def __init__(self, runtime: _Runtime, skills, *, pool_layout: bool = False) -> None:
        self.runtime = runtime
        self.skills = skills
        self.pool_layout = pool_layout
        self.calls: list[dict] = []
        self.cleanup_calls: list[dict] = []

    async def reconcile(self, **kwargs) -> None:
        self.calls.append(kwargs)
        projection = RuntimeProjectionResolver().resolve(
            RuntimeDesiredState(
                skills=tuple(
                    self.skills.list_bot_active_assets(
                        env="pre",
                        bot_id=kwargs["bot_id"],
                        user_id=kwargs["owner_id"],
                        engine="openclaw",
                    )
                )
            )
        )
        retired = list(kwargs.get("retired_mappings") or ())
        if (
            self.pool_layout
            or retired
            or any(mapping.corpus == "repo" for mapping in projection.skill_mappings)
        ):
            contract_mappings = [*projection.skill_mappings, *retired]
            contract = (
                "skills-pool-mapping-v3"
                if any(mapping.corpus == "center" for mapping in contract_mappings)
                else "skills-pool-mapping-v2"
            )
            published = await self.runtime.publish_mappings(
                mappings=list(projection.skill_mappings),
                retired_mappings=retired,
                source_layout=(
                    SkillMappingSourceLayout.POOL
                    if self.pool_layout
                    else SkillMappingSourceLayout.LEGACY
                ),
                mapping_contract_version=contract,
            )
            if not published or not await self.runtime.verify_mappings(
                mappings=list(projection.skill_mappings),
                retired_mappings=retired,
                source_layout=(
                    SkillMappingSourceLayout.POOL
                    if self.pool_layout
                    else SkillMappingSourceLayout.LEGACY
                ),
                mapping_contract_version=contract,
            ):
                raise RuntimeError("runtime reconcile failed")
            return
        if not self.runtime.sync_runtime(
            desired_skills=[
                {
                    "id": str(asset.skill_id),
                    "name": asset.name,
                    "git_path": asset.git_path,
                }
                for asset in projection.skill_assets
            ]
        ):
            raise RuntimeError("runtime reconcile failed")

    async def reconcile_cleanup(self, **kwargs) -> None:
        self.cleanup_calls.append(kwargs)
        if not self.runtime.sync_runtime(
            desired_skills=[
                {
                    "id": str(asset.skill_id),
                    "name": asset.name,
                    "git_path": asset.git_path,
                }
                for asset in self.skills.list_bot_active_assets(
                    env="pre",
                    bot_id=kwargs["bot_id"],
                    user_id=kwargs["owner_id"],
                    engine="aicoding",
                )
            ]
        ):
            raise RuntimeError("runtime cleanup failed")


class _Layouts:
    def __init__(self, *, pool: bool = False) -> None:
        self.pool = pool

    def get(self, scope):
        types = __import__(
            "agentclaw.community.core.skills_pool.types",
            fromlist=[
                "BotSkillLayoutState",
                "SkillLayout",
                "SkillLayoutPhase",
            ],
        )
        state = types.BotSkillLayoutState.legacy_default(scope)
        if not self.pool:
            return state
        return types.BotSkillLayoutState(
            scope=scope,
            active_layout=types.SkillLayout.POOL,
            target_layout=None,
            phase=types.SkillLayoutPhase.POOL_ACTIVE,
            migration_generation="generation",
            persisted=True,
        )


class _Factory:
    def __init__(self, runtime: _Runtime) -> None:
        self.runtime = runtime
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.runtime


def _service(
    *,
    active: bool = False,
    sync_success: bool = True,
    git_path: str = "local://one",
    status: str = "ACTIVE",
    entity_id: str = "owner",
    associated: bool = True,
    collaborators=None,
    pool_layout: bool = False,
    engine: str = "openclaw",
    bot_type: str | None = None,
):
    skills = _Skills(active=active, git_path=git_path)
    sets = _Sets(skills, associated=associated)
    runtime = _Runtime(sync_success)
    runtime_reconciler = _RuntimeReconciler(runtime, skills, pool_layout=pool_layout)
    factory = _Factory(runtime)
    service = LocalSkillStateService(
        skills,
        sets,
        _Bots(status, entity_id, engine=engine, bot_type=bot_type),
        collaborators or _Collaborators(),
        factory,
        skills,
        sets,
        runtime_reconciler,
    )
    return service, skills, sets, None, runtime, factory


@pytest.mark.asyncio
async def test_activate_changes_desired_state_then_reconciles():
    service, _skills, sets, _guard, runtime, factory = _service()

    result = await service.set_local_skill_active(
        skill_id="9", actor_id="owner", active=True
    )

    assert result["active"] is True
    assert result["changed"] is True
    assert sets.events == ["remove"]
    assert runtime.calls == 1
    assert service._runtime_reconciler.calls == [{"bot_id": "bot", "owner_id": "owner"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude_code", "aicoding"])
async def test_existing_coding_bot_can_activate_local_skill(engine: str) -> None:
    service, skills, installations, _guard, _runtime, _factory = _service(
        engine=engine,
        bot_type="personal",
    )

    result = await service.set_local_skill_active(
        skill_id="9", actor_id="owner", active=True
    )

    assert result["active"] is True
    assert skills.active is True
    assert installations.events == ["remove"]
    assert service._runtime_reconciler.calls == [
        {"bot_id": "bot", "owner_id": "owner"}
    ]


@pytest.mark.asyncio
async def test_activate_does_not_mutate_default_set_membership_before_runtime_sync():
    service, _skills, sets, _guard, runtime, _factory = _service(associated=False)

    result = await service.set_local_skill_active(
        skill_id="9", actor_id="owner", active=True
    )

    assert result["active"] is True
    assert sets.associated is False
    assert sets.events == ["remove"]
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_activate_leaves_legacy_exclusions_for_the_migration_adapter():
    service, _skills, sets, _guard, runtime, _factory = _service()

    await service.set_local_skill_active(skill_id="9", actor_id="owner", active=True)

    assert sets.remove_all_calls == 0
    assert sets.events == ["remove"]
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_idempotent_activate_runtime_failure_never_uninstalls_existing_desired_state():
    service, skills, installations, _guard, runtime, _factory = _service(
        active=True, sync_success=False
    )

    with pytest.raises(LocalSkillRuntimeSyncError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=True
        )

    assert skills.active is True
    assert installations.install_calls == 1
    assert installations.events == []
    assert runtime.calls == 1


class _RepoSkills(_Skills):
    def __init__(self, *, active: bool = False, references=None, name: str = "repo"):
        super().__init__(active=active, git_path="git://tools/repo")
        self.references = references or []
        self.name = name

    def get_by_id(self, skill_id: str):
        if skill_id != "9":
            return None
        return {
            "id": "9",
            "name": self.name,
            "user_id": None,
            # Historical scanner sentinel: this must remain consumable.
            "bolt_id": "default",
            "git_path": self.git_path,
        }

    def list_skill_set_references(self, skill_id: str):
        assert skill_id == "9"
        return self.references


class _RepoBots(_Bots):
    def __init__(self, *, bot_type: str = "personal", engine: str = "openclaw"):
        super().__init__("ACTIVE")
        self.bot_type = bot_type
        self.engine = engine

    def get_unique_by_id(self, bot_id: str):
        assert bot_id == "bot"
        return {
            "status": "ACTIVE",
            "active_engine": self.engine,
            "bot_type": self.bot_type,
            "env": "pre",
            "entity_id": "owner",
            "entity_type": "staff",
            "owner_id": "owner",
        }


class _RepoSetService:
    def __init__(self, *, normal_member: bool) -> None:
        self.normal_member = normal_member

    def get_skill_set(self, skill_set_id: str, user_id: str):
        assert skill_set_id == "17"
        assert user_id == "owner"
        return {"id": "17", "bolt_id": "bot", "is_default": not self.normal_member}


class _RepoFactory(_Factory):
    def __init__(self, runtime: _Runtime, *, normal_member: bool = False) -> None:
        super().__init__(runtime)
        self.set_service = _RepoSetService(normal_member=normal_member)

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.set_service if self.set_service.normal_member else self.runtime


def _repo_service(
    *,
    active: bool = False,
    sync_success: bool = True,
    references=None,
    bot_type: str = "personal",
    engine: str = "openclaw",
    active_assets=None,
):
    skills = _RepoSkills(active=active, references=references)
    installations = _Installations(skills)
    runtime = _Runtime(sync_success)
    runtime_reconciler = _RuntimeReconciler(runtime, skills)
    factory = _RepoFactory(runtime, normal_member=bool(references))
    if active_assets is not None:
        skills.list_bot_active_assets = lambda **_kwargs: active_assets
    service = LocalSkillStateService(
        skills,
        installations,
        _RepoBots(bot_type=bot_type, engine=engine),
        _Collaborators(),
        factory,
        skills,
        factory.set_service,
        runtime_reconciler,
    )
    return service, skills, installations, runtime


@pytest.mark.asyncio
async def test_repo_direct_accepts_shared_scanner_sentinel_and_reconciles():
    service, _skills, installations, runtime = _repo_service()

    result = await service.set_repo_skill_active(
        skill_id="9", bot_id="bot", owner_id="owner", actor_id="owner", active=True
    )

    assert result["active"] is True
    assert result["changed"] is True
    assert installations.events == ["install:pre:bot:9"]
    assert runtime.calls == 0
    assert len(runtime.publish_calls) == len(runtime.verify_calls) == 1


@pytest.mark.asyncio
async def test_repo_deactivate_publishes_complete_projection_with_retired_repo_link():
    service, _skills, installations, runtime = _repo_service(active=True)

    result = await service.set_repo_skill_active(
        skill_id="9", bot_id="bot", owner_id="owner", actor_id="owner", active=False
    )

    assert result["active"] is False
    assert installations.events == ["uninstall:pre:bot:9"]
    assert runtime.calls == 0
    assert [
        mapping.to_dict() for mapping in runtime.publish_calls[0]["retired_mappings"]
    ] == [{"corpus": "repo", "relative_path": "tools/repo", "link_name": "repo"}]


@pytest.mark.asyncio
async def test_repo_direct_rejects_normal_skill_set_membership_before_writing_state():
    service, _skills, installations, _runtime = _repo_service(
        references=[{"skill_set_id": "17"}]
    )

    with pytest.raises(SkillManagedBySkillSetError):
        await service.set_repo_skill_active(
            skill_id="9", bot_id="bot", owner_id="owner", actor_id="owner", active=True
        )

    assert installations.events == []


@pytest.mark.asyncio
async def test_repo_direct_uses_existing_bot_runtime_without_product_matrix():
    service, _skills, installations, runtime = _repo_service(
        bot_type="desktop", engine="claude_code"
    )

    result = await service.set_repo_skill_active(
        skill_id="9", bot_id="bot", owner_id="owner", actor_id="owner", active=True
    )

    assert result["active"] is True
    assert installations.events == ["install:pre:bot:9"]
    assert len(runtime.publish_calls) == len(runtime.verify_calls) == 1


@pytest.mark.asyncio
async def test_literal_aicoding_local_skill_can_deactivate_and_reactivate():
    service, skills, installations, _guard, runtime, _factory = _service(
        active=True,
        engine="aicoding",
        bot_type="personal",
    )

    result = await service.set_local_skill_active(
        skill_id="9", actor_id="owner", active=False
    )

    assert result["active"] is False
    assert skills.active is False
    assert installations.events == ["add"]
    assert service._runtime_reconciler.cleanup_calls == []
    assert len(runtime.publish_calls) == len(runtime.verify_calls) == 1

    result = await service.set_local_skill_active(
        skill_id="9", actor_id="owner", active=True
    )
    assert result["active"] is True
    assert skills.active is True
    assert installations.events == ["add", "remove"]


@pytest.mark.asyncio
async def test_literal_aicoding_repo_skill_can_deactivate_and_reactivate():
    service, _skills, installations, runtime = _repo_service(
        active=True,
        engine="aicoding",
    )

    result = await service.set_repo_skill_active(
        skill_id="9", bot_id="bot", owner_id="owner", actor_id="owner", active=False
    )

    assert result["active"] is False
    assert installations.events == ["uninstall:pre:bot:9"]
    assert service._runtime_reconciler.cleanup_calls == []
    assert len(runtime.publish_calls) == len(runtime.verify_calls) == 1

    runtime.publish_results.append(True)
    runtime.verify_results.append(True)
    result = await service.set_repo_skill_active(
        skill_id="9", bot_id="bot", owner_id="owner", actor_id="owner", active=True
    )
    assert result["active"] is True
    assert installations.events == [
        "uninstall:pre:bot:9",
        "install:pre:bot:9",
    ]


@pytest.mark.asyncio
async def test_repo_direct_rejects_runtime_name_conflict_before_writing_state():
    conflicting = RegisteredSkillAsset(
        skill_id=10, name="repo", git_path="git://other/repo"
    )
    service, _skills, installations, _runtime = _repo_service(
        active_assets=[conflicting]
    )

    with pytest.raises(SkillRuntimeNameConflictError):
        await service.set_repo_skill_active(
            skill_id="9", bot_id="bot", owner_id="owner", actor_id="owner", active=True
        )

    assert installations.events == []


@pytest.mark.asyncio
async def test_repo_direct_idempotent_runtime_failure_preserves_old_installation():
    service, skills, installations, runtime = _repo_service(
        active=True, sync_success=False
    )
    runtime.publish_results = [False]

    with pytest.raises(LocalSkillRuntimeSyncError):
        await service.set_repo_skill_active(
            skill_id="9", bot_id="bot", owner_id="owner", actor_id="owner", active=True
        )

    assert skills.active is True
    assert installations.events == ["install:pre:bot:9"]
    assert runtime.calls == 0


@pytest.mark.asyncio
async def test_runtime_sync_delegates_the_stable_bot_identity():
    service, _skills, _sets, _guard, _runtime, factory = _service(
        entity_id="project-entity"
    )

    await service.set_local_skill_active(skill_id="9", actor_id="owner", active=True)

    assert service._runtime_reconciler.calls == [{"bot_id": "bot", "owner_id": "owner"}]


@pytest.mark.asyncio
async def test_idempotent_deactivate_does_not_recreate_a_legacy_exclusion():
    service, _skills, sets, _guard, runtime, _factory = _service(active=False)

    result = await service.set_local_skill_active(
        skill_id="9", actor_id="owner", active=False
    )

    assert result["active"] is False
    assert result["changed"] is False
    assert sets.events == []
    assert runtime.calls == 0
    assert len(runtime.publish_calls) == len(runtime.verify_calls) == 1


@pytest.mark.asyncio
async def test_deactivate_retires_logical_mapping_without_legacy_file_delete():
    service, _skills, _sets, _guard, runtime, _factory = _service(active=True)

    await service.set_local_skill_active(skill_id="9", actor_id="owner", active=False)

    assert runtime.calls == 0
    assert len(runtime.publish_calls) == len(runtime.verify_calls) == 1
    call = runtime.publish_calls[0]
    assert call["mappings"] == []
    assert [mapping.to_dict() for mapping in call["retired_mappings"]] == [
        {"corpus": "local", "relative_path": "one", "link_name": "one"}
    ]
    assert call["source_layout"] is SkillMappingSourceLayout.LEGACY


@pytest.mark.asyncio
async def test_deactivate_publish_failure_restores_desired_and_runtime_state():
    service, skills, sets, _guard, runtime, _factory = _service(active=True)
    runtime.publish_results = [False]

    with pytest.raises(LocalSkillRuntimeSyncError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=False
        )

    assert sets.events == ["add", "remove"]
    assert skills.active is True
    assert len(runtime.publish_calls) == 1
    assert runtime.verify_calls == []
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_deactivate_verify_failure_restores_through_engine_compatible_sync():
    service, skills, sets, _guard, runtime, _factory = _service(active=True)
    runtime.verify_results = [False]

    with pytest.raises(LocalSkillRuntimeSyncError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=False
        )

    assert sets.events == ["add", "remove"]
    assert skills.active is True
    assert len(runtime.publish_calls) == len(runtime.verify_calls) == 1
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_deactivate_mapping_repository_failure_fails_closed_and_restores_state():
    service, skills, sets, _guard, runtime, _factory = _service(active=True)

    def fail_to_list_active_assets(**_kwargs):
        raise RuntimeError("repository unavailable")

    skills.list_bot_active_assets = fail_to_list_active_assets

    with pytest.raises(LocalSkillRuntimeSyncError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=False
        )

    assert sets.events == ["add", "remove"]
    assert skills.active is True
    assert runtime.publish_calls == []
    assert runtime.verify_calls == []
    assert runtime.calls == 0


@pytest.mark.asyncio
async def test_deactivate_selects_pool_source_layout_after_cutover():
    service, _skills, _sets, _guard, runtime, _factory = _service(
        active=True, pool_layout=True
    )

    await service.set_local_skill_active(skill_id="9", actor_id="owner", active=False)

    assert runtime.publish_calls[0]["source_layout"] is SkillMappingSourceLayout.POOL
    assert runtime.verify_calls[0]["source_layout"] is SkillMappingSourceLayout.POOL


@pytest.mark.asyncio
async def test_center_projection_uses_v3_adapter_contract_without_current_locator():
    service, skills, _sets, _guard, runtime, _factory = _service(active=True)
    skills.list_bot_active_assets = lambda **_kwargs: [
        RegisteredSkillAsset(
            skill_id=9,
            name="center-skill",
            git_path="center://skill-uuid",
            skill_uuid="skill-uuid",
            sc_version_number="42",
        )
    ]

    await service.set_local_skill_active(skill_id="9", actor_id="owner", active=False)

    assert (
        runtime.publish_calls[0]["mapping_contract_version"] == "skills-pool-mapping-v3"
    )
    assert (
        runtime.verify_calls[0]["mapping_contract_version"] == "skills-pool-mapping-v3"
    )
    assert runtime.publish_calls[0]["mappings"][0].to_dict() == {
        "corpus": "center",
        "skill_uuid": "skill-uuid",
        "sc_version_number": "42",
        "link_name": "center-skill",
    }


@pytest.mark.asyncio
async def test_deactivate_invalid_locator_fails_closed_and_restores_desired_state():
    service, skills, sets, _guard, runtime, _factory = _service(
        active=True, git_path="local://folder/../one"
    )

    with pytest.raises(LocalSkillRuntimeSyncError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=False
        )

    assert sets.events == ["add", "remove"]
    assert skills.active is True
    assert runtime.publish_calls == []
    assert runtime.calls == 0


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_runtime_failure_restores_previous_desired_state_before_fixed_failure():
    service, skills, sets, _guard, runtime, _factory = _service(
        active=False, sync_success=False
    )

    with pytest.raises(LocalSkillRuntimeSyncError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=True
        )

    assert skills.active is False
    assert sets.events == ["remove", "add"]
    assert runtime.calls == 2


@pytest.mark.asyncio
async def test_runtime_failure_republishes_the_rolled_back_desired_state():
    service, skills, sets, _guard, runtime, _factory = _service(active=False)
    outcomes = iter([False, True])

    def sync_with_recovery(*, desired_skills=None):
        runtime.calls += 1
        return next(outcomes)

    runtime.sync_runtime = sync_with_recovery
    with pytest.raises(LocalSkillRuntimeSyncError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=True
        )

    assert skills.active is False
    assert sets.events == ["remove", "add"]
    assert runtime.calls == 2


@pytest.mark.asyncio
async def test_runtime_reconciler_failure_also_compensates_before_fixed_failure():
    service, skills, sets, _guard, _runtime, _factory = _service(
        active=False, sync_success=True
    )

    async def fail_reconcile(**_kwargs):
        raise RuntimeError("private runtime resolution")

    service._runtime_reconciler.reconcile = fail_reconcile
    with pytest.raises(LocalSkillRuntimeSyncError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=True
        )

    assert skills.active is False
    assert sets.events == ["remove", "add"]


@pytest.mark.asyncio
async def test_failed_runtime_compensation_never_claims_runtime_sync_error():
    service, skills, sets, _guard, _runtime, _factory = _service(
        active=False, sync_success=False
    )

    def fail_restore(*_args, **_kwargs):
        sets.events.append("add")
        raise RuntimeError("private database failure")

    sets.uninstall = fail_restore
    with pytest.raises(LocalSkillStorageError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=True
        )

    assert skills.active is True
    assert sets.events == ["remove", "add"]


@pytest.mark.asyncio
async def test_non_local_skill_is_masked_before_lock_or_runtime():
    for source in ("git://market/one", "center://published-skill"):
        service, _skills, _sets, _guard, runtime, _factory = _service(git_path=source)

        with pytest.raises(LocalSkillNotFoundError):
            await service.set_local_skill_active(
                skill_id="9", actor_id="owner", active=True
            )

        assert runtime.calls == 0


@pytest.mark.asyncio
async def test_non_ready_or_unauthorized_request_never_mutates_or_syncs_runtime():
    service, _skills, _sets, _guard, runtime, _factory = _service(status="PENDING")
    with pytest.raises(LocalSkillNotReadyError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=True
        )
    assert runtime.calls == 0

    class _Denied:
        def check_collaborator_permission(self, *_args):
            return {"has_permission": False}

    service, _skills, _sets, _guard, runtime, _factory = _service(
        collaborators=_Denied()
    )
    with pytest.raises(LocalSkillNotFoundError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="attacker", active=True
        )
    assert runtime.calls == 0
