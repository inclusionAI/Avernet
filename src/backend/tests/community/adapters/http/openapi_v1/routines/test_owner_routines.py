"""openapi_v1 owner-level routine listing handler unit tests."""

from types import SimpleNamespace

import pytest

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CODE_OK,
    Envelope,
    Page,
    PageParams,
)
from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.errors import GrantNotResolvableError
from agentclaw.community.adapters.http.openapi_v1.principal import (
    require_granted_user,
)
from agentclaw.community.adapters.http.openapi_v1.routines.owner_router import (
    list_owner_routines,
)


def _app(user_id: str, *, user_grant: bool) -> ActingCaller:
    """An application caller, with or without the user's account-level grant.

    ``require_user()`` reads the user-level grant protocol; a stub answering
    inline keeps the test off the database.
    """

    class _UserGrants:
        def find(self, *, user_id, app_id):
            return SimpleNamespace(user_id=user_id) if user_grant else None

    return ActingCaller(user_id=user_id, app_id=7, user_grants=_UserGrants())


def _request_without_trace() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())


class _StubCronService:
    """Minimal stub satisfying the CronRelayServiceProtocol list_all_crons seam."""

    def __init__(self, payload):
        self._payload = payload
        self.last_call_kwargs: dict = {}

    async def list_all_crons(self, *args, **kwargs):
        self.last_call_kwargs = dict(kwargs)
        return {"success": True, "data": self._payload, "total": len(self._payload)}


def _adapter_dict(**overrides):
    base = {
        "id": "t1",
        "bot_id": "bot-x",
        "bot_name": "Bot X",
        "owner_id": "u1",
        "runtime_stage": "online",
        "name": "cron1",
        "enabled": True,
        "schedule": {"expr": "0 9 * * *", "tz": "Asia/Shanghai"},
        "payload": {"message": "echo hi"},
        "created_at_ms": 1722165600000,
        "updated_at_ms": 1722165600000,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_returns_envelope_page_with_bot_metadata():
    service = _StubCronService([_adapter_dict()])

    env = await list_owner_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        factory=service,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.data is not None
    assert isinstance(env.data, Page)
    assert env.data.total == 1
    item = env.data.items[0]
    assert item.bot_id == "bot-x"
    assert item.bot_name == "Bot X"
    assert item.owner_id == "u1"
    assert item.runtime_stage == "online"


@pytest.mark.asyncio
async def test_asks_for_the_user_whole_fleet_all_stages():
    """The aggregate names no bot and no runtime stage.

    ``bot_id=None`` is the service's all-bots mode and ``runtime_stage=None``
    aggregates draft, verify and online — the opposite of the per-bot draft
    workspace the rest of the routines group serves.
    """
    service = _StubCronService([])

    await list_owner_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        factory=service,
        request=_request_without_trace(),
    )

    assert service.last_call_kwargs.get("user_id") == "u1"
    assert service.last_call_kwargs.get("nick_name") == "u1"
    assert service.last_call_kwargs.get("bot_id") is None
    assert service.last_call_kwargs.get("runtime_stage") is None


@pytest.mark.asyncio
async def test_paginates_items():
    service = _StubCronService(
        [_adapter_dict(id="t1"), _adapter_dict(id="t2"), _adapter_dict(id="t3")]
    )

    env = await list_owner_routines(
        page=PageParams(page=2, page_size=1),
        owner_id="u1",
        factory=service,
        request=_request_without_trace(),
    )

    assert env.data.total == 3
    assert [i.routine_id for i in env.data.items] == ["t2"]


@pytest.mark.asyncio
async def test_empty_fleet_answers_an_empty_page():
    service = _StubCronService([])

    env = await list_owner_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        factory=service,
        request=_request_without_trace(),
    )

    assert env.code == CODE_OK
    assert env.data.total == 0
    assert env.data.items == []


@pytest.mark.asyncio
async def test_refuses_an_application_with_no_account_level_grant():
    """An app naming a user it holds no account-level grant from learns nothing.

    The gate is the route's ``DelegatedUserIdDep`` — the same one the ceiling
    declares — so the refusal happens before the handler runs and is answered
    with the masked 404 ``GrantNotResolvableError`` maps to.
    """
    with pytest.raises(GrantNotResolvableError):
        await require_granted_user(_app("u1", user_grant=False))


@pytest.mark.asyncio
async def test_admits_an_application_with_an_account_level_grant():
    assert await require_granted_user(_app("u1", user_grant=True)) == "u1"


@pytest.mark.asyncio
async def test_a_human_caller_passes_the_gate_without_a_grant():
    assert await require_granted_user(ActingCaller(user_id="u1", app_id=None)) == "u1"


@pytest.mark.asyncio
async def test_partial_failure_returns_the_successes():
    """A dict-shaped ``data`` envelope unwraps to its ``items``.

    The service may answer ``{"data": {"items": [...]}}`` on partial failure
    paths; the listing keeps the succeeded rows like the per-bot route does,
    and never surfaces ``failed_targets`` on the public face.
    """
    service = _StubCronService({"items": [_adapter_dict(id="t1")]})

    env = await list_owner_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        factory=service,
        request=_request_without_trace(),
    )

    assert env.data.total == 1
    assert env.data.items[0].routine_id == "t1"
