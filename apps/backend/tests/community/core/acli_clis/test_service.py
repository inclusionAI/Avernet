"""``AcliCliService`` — the spec's acceptance criteria, against fakes.

The fakes model the two systems that must stay in step: an AgentPass grant
list and the container's installed programs. Most tests assert both, because
the point of the ordering and rollback is that they never disagree.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.acli_clis.errors import (
    AcliBotNotRunningError,
    AcliCliAlreadyGrantedError,
    AcliCliNameMismatchError,
    AcliCliNotGrantedError,
    AcliContainerError,
    AcliDefaultCliError,
    AcliEngineOutdatedError,
    AcliEngineUnsupportedError,
    AcliGrantSyncError,
    AcliMarketUnavailableError,
    AcliOperationFailedError,
    AcliRequestRejectedError,
    AcliRollbackFailedError,
)
from agentclaw.community.core.acli_clis.port import (
    AcliMarketPage,
    AcliPortError,
    AcliTool,
)
from agentclaw.community.core.acli_clis.service import AcliCliService
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityPermissionError,
    CallerLockEpochError,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.caller_identity.models import McpCallType

pytestmark = pytest.mark.asyncio

OWNER = "owner-1"


def _bot(**overrides: Any) -> dict[str, Any]:
    bot = {
        "id": 7,
        "bot_id": "bot-1",
        "owner_id": OWNER,
        "entity_id": OWNER,
        "bot_type": "service",
        "status": "ACTIVE",
        "active_engine": "openclaw",
        "template_type": None,
    }
    bot.update(overrides)
    return bot


class _Bots:
    def __init__(self, bot: dict[str, Any] | None) -> None:
        self.bot = bot

    def get_by_id_and_entity(self, bot_id: str, entity_id: str):
        return self.bot

    def get_unique_by_id(self, bot_id: str):
        return self.bot


class _Locks:
    def __init__(self, lock: Any = None) -> None:
        self.lock = lock

    def get_by_key(self, key: str):
        return self.lock


class _Identities:
    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self.overrides = dict(overrides or {})
        self.error: Exception | None = None
        self.replaced: list[dict[str, Any]] = []

    def list_draft_cli_call_types(self, bot_pk: int, engine_type: str):
        return {code: McpCallType(mode) for code, mode in self.overrides.items()}

    def replace_draft_cli_call_type(self, **kwargs: Any):
        self.replaced.append(kwargs)
        if self.error:
            raise self.error
        self.overrides.pop(kwargs["cli_code"], None)


class _AgentPass:
    """The bot's grant list, written through ``add_cli`` / ``remove_cli``."""

    def __init__(self, codes: list[str]) -> None:
        self.items = [
            {"cli_code": c, "cli_name": c, "cli_desc": f"{c} desc", "identity_mode": "owner"}
            for c in codes
        ]
        self.add_errors: list[Exception] = []
        self.remove_error: Exception | None = None

    def current_passport_cli_items(self, *, bot):
        return [dict(item) for item in self.items]

    def add_cli(self, *, bot, item):
        if self.add_errors:
            raise self.add_errors.pop(0)
        self.items.append(dict(item))

    def remove_cli(self, *, bot, cli_code):
        if self.remove_error:
            raise self.remove_error
        self.items = [i for i in self.items if i["cli_code"] != cli_code]

    @property
    def codes(self) -> list[str]:
        return [i["cli_code"] for i in self.items]


class _Manifest:
    def required_cli_items(self, engine_type, template_type):
        return [{"cli_code": "dataphin"}] if engine_type == "openclaw" else []


class _Container:
    """The programs acli has installed, plus scripted failures."""

    def __init__(self, installed: set[str] | None = None) -> None:
        self.installed = set(installed or ())
        self.install_error: AcliPortError | None = None
        self.uninstall_errors: list[AcliPortError] = []
        self.resolves_to: dict[str, str] = {"di": "deepinsight-cli"}
        self.calls: list[tuple] = []

    async def install(self, *, bot_id, owner_id, name):
        self.calls.append(("install", name, owner_id))
        if self.install_error:
            raise self.install_error
        market_name = self.resolves_to.get(name, name)
        self.installed.add(market_name)
        return AcliTool(market_name, "cmd", f"{market_name} from market", "1.0.0")

    async def uninstall(self, *, bot_id, owner_id, name):
        self.calls.append(("uninstall", name))
        if self.uninstall_errors:
            raise self.uninstall_errors.pop(0)
        present = name in self.installed
        self.installed.discard(name)
        return present



class _Market:
    """The market search, which needs no bot or container."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.tools = (
            AcliTool("dataphin", "dataphin", "d", "1.4.14"),
            AcliTool("deepinsight-cli", "di", "1", "1.0.9"),
        )
        self.error: Exception | None = None

    def search(self, *, keyword, page_no, page_size):
        self.calls.append((keyword, page_no, page_size))
        if self.error:
            raise self.error
        matches = tuple(t for t in self.tools if keyword in t.name or keyword in t.command)
        return AcliMarketPage(matches, len(matches), False)


class _Collaborators:
    """Collaborator levels per user; the owner is always OWNER."""

    def __init__(self, levels: dict[str, PermissionLevel] | None = None) -> None:
        self.levels = levels or {}

    def get_operable_permission_level(self, *, bot, user_id, env=None):
        return self.levels.get(user_id, PermissionLevel.NONE)


def _service(
    *,
    bot: dict[str, Any] | None = None,
    granted: list[str] | None = None,
    container: _Container | None = None,
    identities: _Identities | None = None,
    lock: Any = None,
    collaborators: _Collaborators | None = None,
):
    agentpass = _AgentPass(["dataphin"] if granted is None else granted)
    container = container or _Container()
    identities = identities or _Identities()
    service = AcliCliService(
        bot_repository=_Bots(_bot() if bot is None else bot),  # type: ignore[arg-type]
        lock_repository=_Locks(lock),  # type: ignore[arg-type]
        identity_repository=identities,  # type: ignore[arg-type]
        scope_reconciler=agentpass,  # type: ignore[arg-type]
        manifest_resolver=_Manifest(),  # type: ignore[arg-type]
        port=container,  # type: ignore[arg-type]
        market=_Market(),  # type: ignore[arg-type]
        collaborators=collaborators or _Collaborators(),  # type: ignore[arg-type]
    )
    return service, agentpass, container, identities


async def _add(service, code="yuque-cli", *, actor=OWNER, lock_epoch=None):
    return await service.add(
        bot_id="bot-1", entity_id=OWNER, actor_id=actor, cli_code=code, lock_epoch=lock_epoch
    )


async def _remove(service, code="yuque-cli", *, actor=OWNER, lock_epoch=None):
    return await service.remove(
        bot_id="bot-1", entity_id=OWNER, actor_id=actor, cli_code=code, lock_epoch=lock_epoch
    )


# ── search ───────────────────────────────────────────────────────────────


async def test_search_asks_the_market_and_touches_no_bot_or_container() -> None:
    service, _, container, _ = _service()

    page = await service.search_market(keyword="data", page_no=2, page_size=5)

    assert [t.name for t in page.items] == ["dataphin"]
    assert service._market.calls == [("data", 2, 5)]
    assert container.calls == []


# ── list ─────────────────────────────────────────────────────────────────


def test_list_returns_every_grant_with_identity_and_default_flag() -> None:
    service, *_ = _service(
        granted=["dataphin", "adev-cli"], identities=_Identities({"adev-cli": "caller"})
    )

    listed = service.list_bot_clis(bot_id="bot-1", entity_id=OWNER, actor_id=OWNER)

    assert [(c.cli_code, c.identity_mode, c.default) for c in listed] == [
        ("dataphin", "owner", True),
        ("adev-cli", "caller", False),
    ]
    assert listed[1].cli_desc == "adev-cli desc"


def test_a_member_may_list_a_bot_s_clis() -> None:
    service, *_ = _service(collaborators=_Collaborators({"member-1": PermissionLevel.MEMBER}))

    listed = service.list_bot_clis(bot_id="bot-1", entity_id=OWNER, actor_id="member-1")

    assert [c.cli_code for c in listed] == ["dataphin"]


def test_someone_with_no_role_on_the_bot_cannot_list_its_clis() -> None:
    """Locating a bot by its owner's entity is not permission to read it."""
    service, *_ = _service()

    with pytest.raises(CallerIdentityPermissionError):
        service.list_bot_clis(bot_id="bot-1", entity_id=OWNER, actor_id="stranger")


# ── add ──────────────────────────────────────────────────────────────────


async def test_add_installs_then_grants_as_owner() -> None:
    service, agentpass, container, _ = _service()

    tool = await _add(service)

    assert tool.name == "yuque-cli"
    assert container.calls == [("install", "yuque-cli", OWNER)]
    assert agentpass.items[-1] == {
        "cli_code": "yuque-cli",
        "cli_name": "yuque-cli",
        "cli_desc": "yuque-cli from market",
        "identity_mode": "owner",
    }


async def test_failed_install_changes_nothing() -> None:
    container = _Container()
    container.install_error = AcliPortError("install_failed", "no such cli")
    service, agentpass, _, _ = _service(container=container)

    with pytest.raises(AcliContainerError) as caught:
        await _add(service, "nope")

    assert isinstance(caught.value, AcliRequestRejectedError)
    assert (caught.value.code, caught.value.detail) == ("install_failed", "no such cli")
    assert agentpass.codes == ["dataphin"]


async def test_add_without_a_running_container_changes_nothing() -> None:
    container = _Container()
    container.install_error = AcliPortError("not_running", "no container")
    service, agentpass, _, _ = _service(container=container)

    with pytest.raises(AcliContainerError) as caught:
        await _add(service)

    assert caught.value.code == "not_running"
    assert agentpass.codes == ["dataphin"]


async def test_failed_grant_uninstalls_again() -> None:
    service, agentpass, container, _ = _service()
    agentpass.add_errors = [RuntimeError("agentpass down")]

    with pytest.raises(AcliGrantSyncError):
        await _add(service)

    assert agentpass.codes == ["dataphin"]
    assert "yuque-cli" not in container.installed


async def test_grant_passport_does_not_keep_is_undone() -> None:
    """A Passport that accepts the write but stores nothing (community self-issued)."""
    service, agentpass, container, _ = _service()
    agentpass.add_cli = lambda *, bot, item: None  # type: ignore[method-assign]

    with pytest.raises(AcliGrantSyncError):
        await _add(service)

    assert agentpass.codes == ["dataphin"]
    assert "yuque-cli" not in container.installed


async def test_grant_passport_does_not_keep_with_failed_uninstall_reports_the_state() -> None:
    service, agentpass, container, _ = _service()
    agentpass.add_cli = lambda *, bot, item: None  # type: ignore[method-assign]
    container.uninstall_errors = [AcliPortError("uninstall_failed", "busy")]

    with pytest.raises(AcliRollbackFailedError) as caught:
        await _add(service)

    assert caught.value.state == "installed_not_granted"


async def test_unreadable_read_back_keeps_the_program_and_says_so() -> None:
    """An accepted write that cannot be confirmed is reported, not called a success."""
    service, agentpass, container, _ = _service()
    real_read = agentpass.current_passport_cli_items
    reads = {"n": 0}

    def read_once(*, bot):
        reads["n"] += 1
        if reads["n"] > 1:
            raise RuntimeError("down")
        return real_read(bot=bot)

    agentpass.current_passport_cli_items = read_once  # type: ignore[method-assign]

    with pytest.raises(AcliRollbackFailedError) as caught:
        await _add(service)

    assert caught.value.state == "grant_unknown_installed"
    assert "yuque-cli" in container.installed
    assert [c[0] for c in container.calls] == ["install"]


async def test_revoke_passport_does_not_keep_is_refused_before_uninstalling() -> None:
    """A revoke that is accepted but not kept must not be followed by an uninstall."""
    service, agentpass, container, _ = _service(
        granted=["dataphin", "yuque-cli"], container=_Container({"yuque-cli"})
    )
    agentpass.remove_cli = lambda *, bot, cli_code: None  # type: ignore[method-assign]

    with pytest.raises(AcliGrantSyncError):
        await _remove(service)

    assert "yuque-cli" in agentpass.codes
    assert "yuque-cli" in container.installed
    assert container.calls == []


async def test_regrant_passport_does_not_keep_reports_revoked_still_installed() -> None:
    """A compensating re-grant that returns cleanly but is not kept is still a failed undo."""
    container = _Container({"yuque-cli"})
    container.uninstall_errors = [AcliPortError("uninstall_failed", "busy")]
    service, agentpass, _, _ = _service(granted=["dataphin", "yuque-cli"], container=container)
    agentpass.add_cli = lambda *, bot, item: None  # type: ignore[method-assign]

    with pytest.raises(AcliRollbackFailedError) as caught:
        await _remove(service)

    assert caught.value.state == "revoked_still_installed"
    assert "yuque-cli" in container.installed


async def test_cancelled_install_is_cleaned_up_and_the_cancel_propagates() -> None:
    """An abandoned request must not leave a program the engine finished installing."""
    container = _Container()
    real_install = container.install

    async def install_then_cancel(**kwargs):
        await real_install(**kwargs)
        raise asyncio.CancelledError

    container.install = install_then_cancel  # type: ignore[method-assign]
    service, agentpass, _, _ = _service(container=container)

    with pytest.raises(asyncio.CancelledError):
        await _add(service)

    assert "yuque-cli" not in container.installed
    assert agentpass.codes == ["dataphin"]


async def test_cancelled_uninstall_stays_revoked_and_the_cancel_propagates() -> None:
    container = _Container({"yuque-cli"})

    async def cancelled(**kwargs):
        raise asyncio.CancelledError

    container.uninstall = cancelled  # type: ignore[method-assign]
    service, agentpass, _, _ = _service(granted=["dataphin", "yuque-cli"], container=container)

    with pytest.raises(asyncio.CancelledError):
        await _remove(service)

    assert agentpass.codes == ["dataphin"]


async def test_failed_grant_and_failed_uninstall_reports_the_state() -> None:
    service, agentpass, container, _ = _service()
    agentpass.add_errors = [RuntimeError("agentpass down")]
    container.uninstall_errors = [AcliPortError("uninstall_failed", "busy")]

    with pytest.raises(AcliRollbackFailedError) as caught:
        await _add(service)

    assert caught.value.state == "installed_not_granted"


async def test_alias_is_refused_before_installing_when_the_market_knows_it() -> None:
    service, agentpass, container, _ = _service()

    with pytest.raises(AcliCliNameMismatchError) as caught:
        await _add(service, "di")

    assert caught.value.market_name == "deepinsight-cli"
    assert container.calls == []
    assert agentpass.codes == ["dataphin"]


async def test_an_unreachable_market_leaves_the_install_reply_as_the_alias_check() -> None:
    service, _, container, _ = _service()
    service._market.error = AcliMarketUnavailableError()

    with pytest.raises(AcliCliNameMismatchError):
        await _add(service, "di")

    assert [c[0] for c in container.calls] == ["install", "uninstall"]


async def test_cancelled_cleanup_uninstall_still_runs_and_the_cancel_propagates() -> None:
    """The undo after a failed grant is shielded like the install-time cleanup."""
    service, agentpass, container, _ = _service()
    agentpass.add_errors = [RuntimeError("agentpass down")]
    real_uninstall = container.uninstall
    gate = asyncio.Event()

    async def slow_uninstall(**kwargs):
        await gate.wait()
        return await real_uninstall(**kwargs)

    container.uninstall = slow_uninstall  # type: ignore[method-assign]
    task = asyncio.create_task(_add(service))
    for _ in range(50):
        await asyncio.sleep(0)
    task.cancel()
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(50):
        await asyncio.sleep(0)

    assert "yuque-cli" not in container.installed


async def test_alias_is_refused_with_the_market_name_and_undone() -> None:
    """Without the market's answer, the install reply catches the alias."""
    service, agentpass, container, _ = _service()
    service._market.error = AcliMarketUnavailableError()

    with pytest.raises(AcliCliNameMismatchError) as caught:
        await _add(service, "di")

    assert caught.value.market_name == "deepinsight-cli"
    assert "deepinsight-cli" not in container.installed
    assert agentpass.codes == ["dataphin"]


async def test_alias_of_a_held_tool_does_not_uninstall_it() -> None:
    container = _Container({"deepinsight-cli"})
    service, _, container, _ = _service(granted=["deepinsight-cli"], container=container)

    with pytest.raises(AcliCliNameMismatchError):
        await _add(service, "di")

    # The market resolves the alias first, so nothing is installed at all.
    assert "deepinsight-cli" in container.installed
    assert container.calls == []


async def test_alias_of_a_held_tool_without_the_market_does_not_uninstall_it() -> None:
    container = _Container({"deepinsight-cli"})
    service, _, container, _ = _service(granted=["deepinsight-cli"], container=container)
    service._market.error = AcliMarketUnavailableError()

    with pytest.raises(AcliCliNameMismatchError):
        await _add(service, "di")

    assert "deepinsight-cli" in container.installed
    assert [c[0] for c in container.calls] == ["install"]


async def test_adding_a_held_tool_is_a_conflict_and_touches_nothing() -> None:
    service, agentpass, container, _ = _service()

    with pytest.raises(AcliCliAlreadyGrantedError):
        await _add(service, "dataphin")

    assert container.calls == []
    assert agentpass.codes == ["dataphin"]


# ── remove ───────────────────────────────────────────────────────────────


async def test_remove_revokes_then_uninstalls() -> None:
    container = _Container({"yuque-cli"})
    service, agentpass, container, _ = _service(granted=["dataphin", "yuque-cli"], container=container)

    await _remove(service)

    assert agentpass.codes == ["dataphin"]
    assert "yuque-cli" not in container.installed


async def test_remove_of_a_tool_never_installed_through_the_api_just_revokes() -> None:
    service, agentpass, container, _ = _service(granted=["dataphin", "adev-cli"])

    await _remove(service, "adev-cli")

    assert agentpass.codes == ["dataphin"]
    assert container.calls == [("uninstall", "adev-cli")]


async def test_failed_uninstall_regrants_with_the_same_identity() -> None:
    container = _Container({"yuque-cli"})
    container.uninstall_errors = [AcliPortError("uninstall_failed", "busy")]
    identities = _Identities({"yuque-cli": "caller"})
    service, agentpass, _, identities = _service(
        granted=["dataphin", "yuque-cli"], container=container, identities=identities
    )

    with pytest.raises(AcliContainerError) as caught:
        await _remove(service)

    assert isinstance(caught.value, AcliOperationFailedError)
    assert caught.value.code == "uninstall_failed"
    assert agentpass.codes == ["dataphin", "yuque-cli"]
    assert identities.overrides == {"yuque-cli": "caller"}


async def test_failed_uninstall_and_failed_regrant_reports_the_state() -> None:
    container = _Container({"yuque-cli"})
    container.uninstall_errors = [AcliPortError("uninstall_failed", "busy")]
    service, agentpass, _, _ = _service(granted=["dataphin", "yuque-cli"], container=container)
    agentpass.add_errors = [RuntimeError("agentpass down")]

    with pytest.raises(AcliRollbackFailedError) as caught:
        await _remove(service)

    assert caught.value.state == "revoked_still_installed"


async def test_failed_revoke_touches_nothing_else() -> None:
    container = _Container({"yuque-cli"})
    service, agentpass, _, _ = _service(granted=["dataphin", "yuque-cli"], container=container)
    agentpass.remove_error = RuntimeError("agentpass down")

    with pytest.raises(AcliGrantSyncError):
        await _remove(service)

    assert container.calls == []


async def test_removing_a_default_is_refused() -> None:
    service, agentpass, container, _ = _service()

    with pytest.raises(AcliDefaultCliError):
        await _remove(service, "dataphin")

    assert agentpass.codes == ["dataphin"]
    assert container.calls == []


async def test_removing_a_tool_the_bot_lacks_is_not_found() -> None:
    service, *_ = _service()

    with pytest.raises(AcliCliNotGrantedError):
        await _remove(service, "yuque-cli")


async def test_remove_keeps_the_identity_and_readd_restores_it() -> None:
    """The owner's caller/owner choice outlives a remove; re-adding brings it back."""
    identities = _Identities({"yuque-cli": "caller"})
    service, agentpass, _, identities = _service(
        granted=["dataphin", "yuque-cli"], identities=identities
    )

    await _remove(service)
    assert identities.replaced == []
    assert identities.overrides == {"yuque-cli": "caller"}

    await _add(service)
    [item] = [i for i in agentpass.items if i["cli_code"] == "yuque-cli"]
    assert item["identity_mode"] == "caller"


async def test_add_without_a_kept_identity_grants_as_owner() -> None:
    service, agentpass, _, _ = _service()

    await _add(service)

    [item] = [i for i in agentpass.items if i["cli_code"] == "yuque-cli"]
    assert item["identity_mode"] == "owner"


async def test_unreadable_identity_stops_the_add_before_installing() -> None:
    identities = _Identities()
    identities.list_draft_cli_call_types = _raise  # type: ignore[method-assign]
    service, agentpass, container, _ = _service(identities=identities)

    with pytest.raises(RuntimeError):
        await _add(service)

    assert container.calls == []
    assert agentpass.codes == ["dataphin"]


def _raise(*_args, **_kwargs):
    raise RuntimeError("identity table down")


# ── shared checks ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("bot", "actor", "error"),
    [
        (_bot(), "someone-else", CallerIdentityPermissionError),
        (_bot(status="STOPPED"), OWNER, AcliBotNotRunningError),
        (_bot(status="STOPPED"), "someone-else", AcliBotNotRunningError),
        (_bot(active_engine="hermes"), OWNER, AcliEngineUnsupportedError),
    ],
)
async def test_writes_are_owner_only_on_active_bots_with_acli(bot, actor, error) -> None:
    service, agentpass, container, _ = _service(bot=bot, granted=["dataphin", "yuque-cli"])

    with pytest.raises(error):
        await _add(service, "x", actor=actor)
    with pytest.raises(error):
        await _remove(service, "yuque-cli", actor=actor)

    assert container.calls == []
    assert agentpass.codes == ["dataphin", "yuque-cli"]


@pytest.mark.parametrize("bot_type", ["personal", "assistant"])
async def test_any_bot_type_can_add_and_remove(bot_type) -> None:
    """Adding a tool involves no caller identity, so it is not a service-bot-only change."""
    service, agentpass, container, _ = _service(bot=_bot(bot_type=bot_type))

    await _add(service, "deepinsight-cli")
    assert "deepinsight-cli" in agentpass.codes
    await _remove(service, "deepinsight-cli")

    assert "deepinsight-cli" not in agentpass.codes
    assert [call[0] for call in container.calls] == ["install", "uninstall"]


async def test_a_stale_lock_epoch_is_refused() -> None:
    lock = SimpleNamespace(id=5, holder_user_id=OWNER)
    service, _, container, _ = _service(lock=lock)

    with pytest.raises(CallerLockEpochError):
        await _add(service, lock_epoch=None)
    with pytest.raises(CallerLockEpochError):
        await _add(service, lock_epoch=4)
    await _add(service, lock_epoch=5)

    assert [c[0] for c in container.calls] == ["install"]


async def test_an_engine_that_refuses_acli_reads_as_unsupported() -> None:
    container = _Container()
    container.install_error = AcliPortError("engine_unsupported", "engine answered 501")
    service, *_ = _service(container=container)

    with pytest.raises(AcliEngineUnsupportedError):
        await _add(service)


async def test_an_outdated_engine_is_not_called_unsupported_and_changes_nothing() -> None:
    """A supported engine type on an old build: the bot needs an update, not a refusal."""
    container = _Container()
    container.install_error = AcliPortError("engine_outdated", "old build")
    service, agentpass, _, _ = _service(container=container)

    with pytest.raises(AcliEngineOutdatedError) as caught:
        await _add(service)

    assert caught.value.code == "engine_outdated"
    assert agentpass.codes == ["dataphin"]


async def test_remove_on_an_outdated_engine_regrants_because_nothing_ran() -> None:
    container = _Container(installed={"yuque-cli"})
    container.uninstall_errors = [AcliPortError("engine_outdated", "old build")]
    service, agentpass, _, _ = _service(container=container, granted=["dataphin", "yuque-cli"])

    with pytest.raises(AcliEngineOutdatedError):
        await _remove(service)

    assert agentpass.codes == ["dataphin", "yuque-cli"]


async def test_grant_that_landed_despite_an_error_is_a_success() -> None:
    service, agentpass, container, _ = _service()

    class _LandsThenRaises(RuntimeError):
        pass

    def add_then_raise(*, bot, item):
        agentpass.items.append(dict(item))
        raise _LandsThenRaises("timeout after write")

    agentpass.add_cli = add_then_raise  # type: ignore[method-assign]

    tool = await _add(service)

    assert tool.name == "yuque-cli"
    assert "yuque-cli" in container.installed
    assert [c[0] for c in container.calls] == ["install"]


async def test_unknown_grant_keeps_the_program_and_says_so() -> None:
    """When Passport cannot be re-read, uninstalling could strand a granted tool."""
    service, agentpass, container, _ = _service()
    agentpass.add_errors = [RuntimeError("agentpass down")]
    reads = {"n": 0}
    real_read = agentpass.current_passport_cli_items

    def read_once(*, bot):
        reads["n"] += 1
        if reads["n"] > 1:
            raise RuntimeError("still down")
        return real_read(bot=bot)

    agentpass.current_passport_cli_items = read_once  # type: ignore[method-assign]

    with pytest.raises(AcliRollbackFailedError) as caught:
        await _add(service)

    assert caught.value.state == "grant_unknown_installed"
    assert "yuque-cli" in container.installed
    assert [c[0] for c in container.calls] == ["install"]


@pytest.mark.parametrize("code", ["timeout", "engine_unreachable", "bad_output"])
async def test_uncertain_uninstall_stays_revoked_and_says_so(code) -> None:
    container = _Container({"yuque-cli"})
    container.uninstall_errors = [AcliPortError(code, "no answer")]
    service, agentpass, _, _ = _service(granted=["dataphin", "yuque-cli"], container=container)

    with pytest.raises(AcliRollbackFailedError) as caught:
        await _remove(service)

    assert caught.value.state == "revoked_install_unknown"
    assert agentpass.codes == ["dataphin"]



@pytest.mark.parametrize("code", ["timeout", "engine_unreachable", "bad_output"])
async def test_uncertain_install_is_cleaned_up_and_never_granted(code) -> None:
    """The install may have landed although its answer was lost: uninstall it."""
    container = _Container()
    container.install_error = AcliPortError(code, "no answer")
    service, agentpass, container, _ = _service(container=container)

    with pytest.raises(AcliOperationFailedError) as caught:
        await _add(service)

    assert caught.value.code == code
    assert [c[0] for c in container.calls] == ["install", "uninstall"]
    assert agentpass.codes == ["dataphin"]


async def test_uncertain_install_with_failed_cleanup_reports_the_state() -> None:
    container = _Container()
    container.install_error = AcliPortError("timeout", "no answer")
    container.uninstall_errors = [AcliPortError("engine_unreachable", "gone")]
    service, agentpass, _, _ = _service(container=container)

    with pytest.raises(AcliRollbackFailedError) as caught:
        await _add(service)

    assert caught.value.state == "install_unknown_not_granted"
    assert agentpass.codes == ["dataphin"]



async def test_revoke_that_landed_despite_an_error_finishes_the_remove() -> None:
    container = _Container({"yuque-cli"})
    service, agentpass, container, _ = _service(granted=["dataphin", "yuque-cli"], container=container)

    def revoke_then_raise(*, bot, cli_code):
        agentpass.items = [i for i in agentpass.items if i["cli_code"] != cli_code]
        raise RuntimeError("timeout after write")

    agentpass.remove_cli = revoke_then_raise  # type: ignore[method-assign]

    await _remove(service)

    assert agentpass.codes == ["dataphin"]
    assert "yuque-cli" not in container.installed


async def test_revoke_with_unknown_outcome_keeps_the_program_and_says_so() -> None:
    container = _Container({"yuque-cli"})
    service, agentpass, container, _ = _service(granted=["dataphin", "yuque-cli"], container=container)
    agentpass.remove_error = RuntimeError("agentpass down")
    reads = {"n": 0}
    real_read = agentpass.current_passport_cli_items

    def read_once(*, bot):
        reads["n"] += 1
        if reads["n"] > 1:
            raise RuntimeError("still down")
        return real_read(bot=bot)

    agentpass.current_passport_cli_items = read_once  # type: ignore[method-assign]

    with pytest.raises(AcliRollbackFailedError) as caught:
        await _remove(service)

    assert caught.value.state == "revoke_unknown_installed"
    assert container.calls == []



async def test_alias_cleanup_uses_the_pre_install_snapshot() -> None:
    """A Passport outage after the install must not strand the alias's program."""
    service, agentpass, container, _ = _service()
    service._market.error = AcliMarketUnavailableError()
    real_read = agentpass.current_passport_cli_items
    reads = {"n": 0}

    def read_once(*, bot):
        reads["n"] += 1
        if reads["n"] > 1:
            raise RuntimeError("passport down")
        return real_read(bot=bot)

    agentpass.current_passport_cli_items = read_once  # type: ignore[method-assign]

    with pytest.raises(AcliCliNameMismatchError):
        await _add(service, "di")

    assert "deepinsight-cli" not in container.installed


async def test_regrant_that_landed_despite_an_error_reports_the_uninstall_failure() -> None:
    container = _Container({"yuque-cli"})
    container.uninstall_errors = [AcliPortError("uninstall_failed", "busy")]
    service, agentpass, _, _ = _service(granted=["dataphin", "yuque-cli"], container=container)

    def regrant_then_raise(*, bot, item):
        agentpass.items.append(dict(item))
        raise RuntimeError("timeout after write")

    agentpass.add_cli = regrant_then_raise  # type: ignore[method-assign]

    with pytest.raises(AcliOperationFailedError) as caught:
        await _remove(service)

    assert caught.value.code == "uninstall_failed"
    assert agentpass.codes == ["dataphin", "yuque-cli"]


async def test_regrant_with_unknown_outcome_says_so() -> None:
    container = _Container({"yuque-cli"})
    container.uninstall_errors = [AcliPortError("uninstall_failed", "busy")]
    service, agentpass, _, _ = _service(granted=["dataphin", "yuque-cli"], container=container)
    agentpass.add_errors = [RuntimeError("agentpass down")]
    real_read = agentpass.current_passport_cli_items
    reads = {"n": 0}

    def read_once(*, bot):
        reads["n"] += 1
        if reads["n"] > 1:
            raise RuntimeError("still down")
        return real_read(bot=bot)

    agentpass.current_passport_cli_items = read_once  # type: ignore[method-assign]

    with pytest.raises(AcliRollbackFailedError) as caught:
        await _remove(service)

    assert caught.value.state == "revoke_unknown_installed"


async def test_add_and_remove_on_one_bot_run_one_at_a_time() -> None:
    """A remove waiting on its uninstall cannot be overtaken by an add of the same tool."""
    service, agentpass, container, _ = _service(granted=["dataphin", "yuque-cli"])
    container.installed.add("yuque-cli")
    release = asyncio.Event()
    real_uninstall = container.uninstall

    async def slow_uninstall(**kwargs):
        await release.wait()
        return await real_uninstall(**kwargs)

    container.uninstall = slow_uninstall  # type: ignore[method-assign]
    removing = asyncio.create_task(_remove(service))
    await asyncio.sleep(0)
    adding = asyncio.create_task(_add(service))
    await asyncio.sleep(0)

    assert [c[0] for c in container.calls] == []  # the add has not installed yet
    release.set()
    await removing
    await adding

    assert "yuque-cli" in agentpass.codes
    assert "yuque-cli" in container.installed
