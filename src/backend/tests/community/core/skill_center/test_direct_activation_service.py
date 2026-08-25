"""Unit coverage for the direct (Set-free) activation command service."""

from __future__ import annotations

import pytest

from agentclaw.community.core.repository.capability_desired_state_types import (
    CapabilityDesiredState,
    DesiredStateMutation,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    LocalSkillRuntimeSyncError,
    McpPermissionDeniedError,
    SkillSetAccessDeniedError,
)
from agentclaw.community.core.skill_center.services.direct_activation_service import (
    DirectActivationService,
)

pytestmark = pytest.mark.unit

_SCOPE = {"engine_type": "openclaw", "default_engine_types": ("openclaw",)}


class _Repository:
    def __init__(self) -> None:
        self.install_mcp_calls: list[dict] = []
        self.uninstall_mcp_calls: list[dict] = []
        self.install_skill_calls: list[dict] = []
        self.uninstall_skill_calls: list[dict] = []
        self.restore_calls: list[dict] = []

    @staticmethod
    def _mutation() -> DesiredStateMutation:
        return DesiredStateMutation({}, True, CapabilityDesiredState(set(), {}, {}))

    def install_mcp(self, **kwargs) -> DesiredStateMutation:
        self.install_mcp_calls.append(kwargs)
        return self._mutation()

    def uninstall_mcp(self, **kwargs) -> DesiredStateMutation:
        self.uninstall_mcp_calls.append(kwargs)
        return self._mutation()

    def install_skill(self, **kwargs) -> DesiredStateMutation:
        self.install_skill_calls.append(kwargs)
        return self._mutation()

    def uninstall_skill(self, **kwargs) -> DesiredStateMutation:
        self.uninstall_skill_calls.append(kwargs)
        return self._mutation()

    def restore_desired_state(self, **kwargs) -> None:
        self.restore_calls.append(kwargs)


class _Bots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
        if owner_id != "true-owner":
            return None
        return {
            "bot_id": bot_id,
            "owner_id": "true-owner",
            "env": "dev",
            "entity_id": "entity-1",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "entity_type": "staff",
            "status": "ACTIVE",
        }


class _Skills:
    """One Local row and one shared Repo row."""

    _ROWS = {
        "7": {
            "id": 7,
            "name": "local-skill",
            "git_path": "local://local-skill",
            "user_id": "true-owner",
            "bolt_id": "bot-1",
        },
        "8": {
            "id": 8,
            "name": "repo-skill",
            "git_path": "git://team/repo-skill",
            "user_id": None,
            "bolt_id": "default",
        },
    }

    def get_by_id(self, skill_id: str) -> dict | None:
        return self._ROWS.get(skill_id)

    def get_bot_local_skill(self, *, skill_id: str, bot_id: str, user_id: str):
        row = self._ROWS.get(skill_id)
        if row and row["bolt_id"] == bot_id and row["user_id"] == user_id:
            return {**row, "active": False}
        return None


class _Authorization:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[dict] = []

    def can_manage_bot(self, *, bot_id, owner_id, actor_id) -> bool:
        self.calls.append(
            {"bot_id": bot_id, "owner_id": owner_id, "actor_id": actor_id}
        )
        return self.allowed


class _Audit:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def insert(self, data) -> None:
        self.actions.append(data["detail"])


class _SuccessfulRuntime:
    async def snapshot_skill_mappings(self, **_kwargs):
        return ()

    async def project(self, **_kwargs) -> None:
        return None


class _FailingRuntime(_SuccessfulRuntime):
    """The first projection fails; the compensating counter-projection holds."""

    def __init__(self) -> None:
        self.calls = 0

    async def project(self, **_kwargs) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("engine write failed")


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


class _Reader:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def active_mcp_server_codes(self, *, bot_id, owner_id, bot=None):
        self.calls.append({"bot_id": bot_id, "owner_id": owner_id, "bot": bot})
        return frozenset({"mcp.weather"})


def _service(
    *,
    repository=None,
    runtime=None,
    authorization=None,
    mcp_center=None,
    reader=None,
    audit=None,
) -> DirectActivationService:
    return DirectActivationService(
        repository if repository is not None else _Repository(),
        _Bots(),
        _Skills(),
        runtime if runtime is not None else _SuccessfulRuntime(),
        authorization if authorization is not None else _Authorization(),
        audit if audit is not None else _Audit(),
        mcp_center if mcp_center is not None else _McpCenter(allowed=True),
        reader if reader is not None else _Reader(),
    )


@pytest.mark.asyncio
async def test_mcp_direct_activation_checks_permission_before_writing():
    repository = _Repository()
    mcp_center = _McpCenter(allowed=True)
    service = _service(repository=repository, mcp_center=mcp_center)

    await service.activate_mcp(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner",
        server_code="mcp.weather",
    )

    assert mcp_center.calls == [("true-owner", "mcp.weather")]
    assert repository.install_mcp_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "server_code": "mcp.weather",
            **_SCOPE,
        }
    ]


@pytest.mark.asyncio
async def test_mcp_direct_activation_denies_before_writing():
    repository = _Repository()
    service = _service(repository=repository, mcp_center=_McpCenter(allowed=False))

    with pytest.raises(McpPermissionDeniedError):
        await service.activate_mcp(
            bot_id="bot-1", owner_id="true-owner", actor_id="true-owner",
            server_code="mcp.weather",
        )
    assert repository.install_mcp_calls == []


@pytest.mark.asyncio
async def test_mcp_direct_deactivation_needs_no_marketplace_permission():
    repository = _Repository()
    mcp_center = _McpCenter(allowed=False)
    service = _service(repository=repository, mcp_center=mcp_center)

    await service.deactivate_mcp(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner",
        server_code="mcp.weather",
    )

    assert mcp_center.calls == []
    assert repository.uninstall_mcp_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "server_code": "mcp.weather",
            **_SCOPE,
        }
    ]


@pytest.mark.asyncio
async def test_mcp_commands_refuse_a_non_collaborating_actor():
    repository = _Repository()
    service = _service(repository=repository, authorization=_Authorization(False))

    with pytest.raises(SkillSetAccessDeniedError):
        await service.activate_mcp(
            bot_id="bot-1", owner_id="true-owner", actor_id="stranger",
            server_code="mcp.weather",
        )
    assert repository.install_mcp_calls == []


def test_list_installed_mcps_answers_through_the_reader():
    reader = _Reader()
    service = _service(reader=reader)

    codes = service.list_installed_mcps(
        bot_id="bot-1", owner_id="true-owner", actor_id="true-owner"
    )

    assert codes == {"mcp.weather"}
    assert reader.calls[0]["bot_id"] == "bot-1"
    assert reader.calls[0]["owner_id"] == "true-owner"
    # The resolved Bot row rides along so the reader's flush does not re-read.
    assert reader.calls[0]["bot"]["owner_id"] == "true-owner"


@pytest.mark.asyncio
async def test_skill_direct_activation_writes_and_reports_the_asset():
    repository = _Repository()
    audit = _Audit()
    service = _service(repository=repository, audit=audit)

    result = await service.activate_skill(
        skill_id="7", bot_id="bot-1", owner_id="true-owner",
        actor_id="true-owner",
    )

    assert repository.install_skill_calls == [
        {
            "bot_id": "bot-1",
            "owner_id": "true-owner",
            "skill_id": "7",
            **_SCOPE,
        }
    ]
    assert result["active"] is True
    assert result["changed"] is True
    assert result["name"] == "local-skill"
    assert audit.actions == ['{"action":"skill_direct_activate"}']


@pytest.mark.asyncio
async def test_repo_skill_takes_the_addressed_bot_and_owner():
    repository = _Repository()
    service = _service(repository=repository)

    result = await service.deactivate_skill(
        skill_id="8", bot_id="bot-1", owner_id="true-owner",
        actor_id="true-owner",
    )

    assert repository.uninstall_skill_calls[0]["skill_id"] == "8"
    assert result["bolt_id"] == "bot-1"
    assert result["user_id"] == "true-owner"
    assert result["active"] is False


@pytest.mark.asyncio
async def test_skill_wire_masks_authorization_failure_as_not_found():
    service = _service(authorization=_Authorization(False))

    with pytest.raises(LocalSkillNotFoundError):
        await service.activate_skill(
            skill_id="7", bot_id="bot-1", owner_id="true-owner",
            actor_id="stranger",
        )


@pytest.mark.asyncio
async def test_skill_wire_translates_reconcile_failure_and_compensates():
    repository = _Repository()
    service = _service(repository=repository, runtime=_FailingRuntime())

    with pytest.raises(LocalSkillRuntimeSyncError):
        await service.activate_skill(
            skill_id="7", bot_id="bot-1", owner_id="true-owner",
            actor_id="true-owner",
        )

    assert len(repository.restore_calls) == 1


@pytest.mark.asyncio
async def test_a_not_ready_bot_refuses_before_any_write():
    class _PendingBots(_Bots):
        def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
            bot = super().get_by_id_and_owner(bot_id, owner_id)
            return {**bot, "status": "PENDING"} if bot else None

    repository = _Repository()
    service = DirectActivationService(
        repository, _PendingBots(), _Skills(), _SuccessfulRuntime(),
        _Authorization(), _Audit(), _McpCenter(allowed=True), _Reader(),
    )

    with pytest.raises(LocalSkillNotReadyError):
        await service.activate_skill(
            skill_id="7", bot_id="bot-1", owner_id="true-owner",
            actor_id="true-owner",
        )
    assert repository.install_skill_calls == []


@pytest.mark.asyncio
async def test_a_space_asset_and_a_mismatched_local_row_are_masked_as_not_found():
    class _MoreSkills(_Skills):
        _ROWS = {
            **_Skills._ROWS,
            "9": {
                "id": 9,
                "name": "space-skill",
                "git_path": "center://space-skill",
                "user_id": None,
                "bolt_id": None,
            },
        }

    repository = _Repository()
    service = DirectActivationService(
        repository, _Bots(), _MoreSkills(), _SuccessfulRuntime(),
        _Authorization(), _Audit(), _McpCenter(allowed=True), _Reader(),
    )

    # A Space (center://) asset has no direct-activation wire.
    with pytest.raises(LocalSkillNotFoundError):
        await service.activate_skill(
            skill_id="9", bot_id="bot-1", owner_id="true-owner",
            actor_id="true-owner",
        )
    # A Local row carries its own Bot: addressing it through another Bot
    # must not resolve.
    with pytest.raises(LocalSkillNotFoundError):
        await service.activate_skill(
            skill_id="7", bot_id="another-bot", owner_id="true-owner",
            actor_id="true-owner",
        )
    assert repository.install_skill_calls == []


@pytest.mark.asyncio
async def test_an_idempotent_deactivate_still_projects_the_runtime():
    """`changed=False` is not "skip the engine": the projection converges the
    runtime with desired state even when the row was already absent."""

    class _UnchangedRepository(_Repository):
        @staticmethod
        def _mutation() -> DesiredStateMutation:
            return DesiredStateMutation(
                {}, False, CapabilityDesiredState(set(), {}, {})
            )

    class _CountingRuntime(_SuccessfulRuntime):
        def __init__(self) -> None:
            self.projections = 0

        async def project(self, **_kwargs) -> None:
            self.projections += 1

    runtime = _CountingRuntime()
    service = _service(repository=_UnchangedRepository(), runtime=runtime)

    result = await service.deactivate_skill(
        skill_id="7", bot_id="bot-1", owner_id="true-owner",
        actor_id="true-owner",
    )

    assert result["changed"] is False
    assert result["active"] is False
    assert runtime.projections == 1
