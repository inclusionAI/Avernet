"""``AcliCliService`` — the spec's acceptance criteria, against fakes.

The fakes model the two systems that must stay in step: an AgentPass grant
list and the container's installed programs. Most tests assert both, because
the point of the ordering and rollback is that they never disagree.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.acli_clis.errors import (
    AcliCliAlreadyGrantedError,
    AcliCliNameMismatchError,
    AcliCliNotGrantedError,
    AcliContainerError,
    AcliDefaultCliError,
    AcliEngineUnsupportedError,
    AcliRollbackFailedError,
)
from agentclaw.community.core.acli_clis.port import (
    AcliMarketPage,
    AcliPortError,
    AcliTool,
)
from agentclaw.community.core.acli_clis.service import AcliCliService
from agentclaw.community.core.caller_identity.contracts import (
    CallerCliSyncError,
    CallerIdentityIrreversibleError,
    CallerIdentityPermissionError,
    CallerIdentityReadOnlyError,
    CallerLockEpochError,
)
from agentclaw.community.core.caller_identity.models import McpCallType

pytestmark = pytest.mark.asyncio

DIR = "/home/admin/.agentclaw/bin"
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


class _Mcps:
    def collect_bot_active_mcps(self, **kwargs: Any):
        return [{"server_code": "mcp.a"}, "junk"]


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
    managed_bin_dir = DIR

    def required_cli_items(self, engine_type, template_type):
        return [{"cli_code": "dataphin"}] if engine_type == "openclaw" else []


class _Container:
    """The programs acli has installed, plus scripted failures."""

    def __init__(self, installed: set[str] | None = None) -> None:
        self.installed = set(installed or ())
        self.install_error: AcliPortError | None = None
        self.uninstall_errors: list[AcliPortError] = []
        self.market_error: AcliPortError | None = None
        self.resolves_to: dict[str, str] = {"di": "deepinsight-cli"}
        self.calls: list[tuple] = []

    async def install(self, *, bot_id, owner_id, name, install_dir):
        self.calls.append(("install", name, install_dir, owner_id))
        if self.install_error:
            raise self.install_error
        market_name = self.resolves_to.get(name, name)
        self.installed.add(market_name)
        return AcliTool(market_name, "cmd", f"{market_name} from market", "1.0.0")

    async def uninstall(self, *, bot_id, owner_id, name, install_dir):
        self.calls.append(("uninstall", name, install_dir))
        if self.uninstall_errors:
            raise self.uninstall_errors.pop(0)
        present = name in self.installed
        self.installed.discard(name)
        return present

    async def market(self, *, bot_id, owner_id, keyword, page_no, page_size):
        self.calls.append(("market", keyword, page_no, page_size))
        if self.market_error:
            raise self.market_error
        return AcliMarketPage((AcliTool("dataphin", "dataphin", "d", "1.4.14"),), 1, False)


def _service(
    *,
    bot: dict[str, Any] | None = None,
    granted: list[str] | None = None,
    container: _Container | None = None,
    identities: _Identities | None = None,
    lock: Any = None,
):
    agentpass = _AgentPass(["dataphin"] if granted is None else granted)
    container = container or _Container()
    identities = identities or _Identities()
    service = AcliCliService(
        bot_repository=_Bots(_bot() if bot is None else bot),  # type: ignore[arg-type]
        lock_repository=_Locks(lock),  # type: ignore[arg-type]
        identity_repository=identities,  # type: ignore[arg-type]
        mcp_provider=_Mcps(),  # type: ignore[arg-type]
        scope_reconciler=agentpass,  # type: ignore[arg-type]
        manifest_resolver=_Manifest(),  # type: ignore[arg-type]
        port=container,  # type: ignore[arg-type]
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


async def test_search_runs_in_the_bots_container() -> None:
    service, _, container, _ = _service()

    page = await service.search_market(
        bot_id="bot-1", entity_id=OWNER, keyword="data", page_no=2, page_size=5
    )

    assert [t.name for t in page.items] == ["dataphin"]
    assert container.calls == [("market", "data", 2, 5)]


async def test_search_without_a_running_container_fails() -> None:
    container = _Container()
    container.market_error = AcliPortError("not_running", "no container")
    service, *_ = _service(container=container)

    with pytest.raises(AcliContainerError) as caught:
        await service.search_market(
            bot_id="bot-1", entity_id=OWNER, keyword="", page_no=1, page_size=5
        )
    assert caught.value.code == "not_running"


# ── list ─────────────────────────────────────────────────────────────────


def test_list_returns_every_grant_with_identity_and_default_flag() -> None:
    service, *_ = _service(
        granted=["dataphin", "adev-cli"], identities=_Identities({"adev-cli": "caller"})
    )

    listed = service.list_bot_clis(bot_id="bot-1", entity_id=OWNER)

    assert [(c.cli_code, c.identity_mode, c.default) for c in listed] == [
        ("dataphin", "owner", True),
        ("adev-cli", "caller", False),
    ]
    assert listed[1].cli_desc == "adev-cli desc"


# ── add ──────────────────────────────────────────────────────────────────


async def test_add_installs_then_grants_as_owner() -> None:
    service, agentpass, container, _ = _service()

    tool = await _add(service)

    assert tool.name == "yuque-cli"
    assert container.calls == [("install", "yuque-cli", DIR, OWNER)]
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

    with pytest.raises(CallerCliSyncError):
        await _add(service)

    assert agentpass.codes == ["dataphin"]
    assert "yuque-cli" not in container.installed


async def test_failed_grant_and_failed_uninstall_reports_the_state() -> None:
    service, agentpass, container, _ = _service()
    agentpass.add_errors = [RuntimeError("agentpass down")]
    container.uninstall_errors = [AcliPortError("uninstall_failed", "busy")]

    with pytest.raises(AcliRollbackFailedError) as caught:
        await _add(service)

    assert caught.value.state == "installed_not_granted"


async def test_alias_is_refused_with_the_market_name_and_undone() -> None:
    service, agentpass, container, _ = _service()

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
    assert container.calls == [("uninstall", "adev-cli", DIR)]


async def test_failed_uninstall_regrants_with_the_same_identity() -> None:
    container = _Container({"yuque-cli"})
    container.uninstall_errors = [AcliPortError("uninstall_failed", "busy")]
    identities = _Identities({"yuque-cli": "caller"})
    service, agentpass, _, identities = _service(
        granted=["dataphin", "yuque-cli"], container=container, identities=identities
    )

    with pytest.raises(AcliContainerError) as caught:
        await _remove(service)

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

    with pytest.raises(CallerCliSyncError):
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


async def test_remove_clears_a_caller_override_so_readd_starts_at_owner() -> None:
    identities = _Identities({"yuque-cli": "caller"})
    service, _, _, identities = _service(granted=["dataphin", "yuque-cli"], identities=identities)

    await _remove(service)

    [call] = identities.replaced
    assert call["call_type"] is McpCallType.OWNER
    assert call["effective_cli_codes"] == {"dataphin"}
    assert call["effective_server_codes"] == {"mcp.a"}
    assert call["lock_key"] == f"bot-1:{OWNER}"
    assert identities.overrides == {}


@pytest.mark.parametrize("error", [CallerIdentityIrreversibleError(), RuntimeError("db")])
async def test_override_that_cannot_be_cleared_keeps_the_remove(error) -> None:
    identities = _Identities({"yuque-cli": "caller"})
    identities.error = error
    service, agentpass, _, _ = _service(granted=["dataphin", "yuque-cli"], identities=identities)

    await _remove(service)

    assert agentpass.codes == ["dataphin"]


async def test_remove_without_override_does_not_write_identity() -> None:
    service, _, _, identities = _service(granted=["dataphin", "yuque-cli"])

    await _remove(service)

    assert identities.replaced == []


# ── shared checks ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("bot", "actor", "error"),
    [
        (_bot(), "someone-else", CallerIdentityPermissionError),
        (_bot(status="STOPPED"), OWNER, CallerIdentityReadOnlyError),
        (_bot(bot_type="personal"), OWNER, CallerIdentityReadOnlyError),
        (_bot(active_engine="hermes"), OWNER, AcliEngineUnsupportedError),
    ],
)
async def test_writes_are_owner_only_on_active_service_bots_with_acli(bot, actor, error) -> None:
    service, agentpass, container, _ = _service(bot=bot, granted=["dataphin", "yuque-cli"])

    with pytest.raises(error):
        await _add(service, "x", actor=actor)
    with pytest.raises(error):
        await _remove(service, "yuque-cli", actor=actor)

    assert container.calls == []
    assert agentpass.codes == ["dataphin", "yuque-cli"]


async def test_a_stale_lock_epoch_is_refused() -> None:
    lock = SimpleNamespace(id=5, holder_user_id=OWNER)
    service, _, container, _ = _service(lock=lock)

    with pytest.raises(CallerLockEpochError):
        await _add(service, lock_epoch=None)
    with pytest.raises(CallerLockEpochError):
        await _add(service, lock_epoch=4)
    await _add(service, lock_epoch=5)

    assert [c[0] for c in container.calls] == ["install"]


async def test_search_on_an_engine_without_acli_is_unsupported() -> None:
    service, *_ = _service(bot=_bot(active_engine="hermes"))

    with pytest.raises(AcliEngineUnsupportedError):
        await service.search_market(
            bot_id="bot-1", entity_id=OWNER, keyword="", page_no=1, page_size=5
        )


async def test_engine_without_endpoints_reads_as_unsupported() -> None:
    container = _Container()
    container.install_error = AcliPortError("engine_unsupported", "old engine")
    service, *_ = _service(container=container)

    with pytest.raises(AcliEngineUnsupportedError):
        await _add(service)
