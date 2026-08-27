"""openapi_v1 routines handler unit tests."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    CODE_CREATED,
    CODE_OK,
    Envelope,
    Page,
    PageParams,
)
from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.routines.router import (
    _map_routine,
    _map_run,
    create_routine,
    delete_routine,
    get_routine,
    list_routines,
    list_routine_runs,
    run_routine,
    update_routine,
)
from agentclaw.community.adapters.http.openapi_v1.routines.schemas import (
    Routine,
    RoutineSpec,
    RoutineRun,
    RoutineUpdate,
    ScheduleTrigger,
)


def _human(user_id: str) -> ActingCaller:
    """A caller with a person on the wire, so no grant governs the request.

    ``app_id=None`` is what makes ``require_bot`` a no-op — the same thing that
    keeps every human request off the grant path in production.
    """
    return ActingCaller(user_id=user_id, app_id=None)


def _request_without_trace() -> SimpleNamespace:
    """A request whose tracer middleware did not run — ``state.trace_id`` unset.

    ``responses._trace_id`` reads ``request.state.trace_id`` and falls back to
    ``""`` when absent, so the envelope's ``request_id`` is empty.
    """
    return SimpleNamespace(state=SimpleNamespace())


def _request_with_trace(trace_id: str) -> SimpleNamespace:
    """A request whose tracer middleware stamped ``trace_id`` on ``state``."""
    return SimpleNamespace(state=SimpleNamespace(trace_id=trace_id))


class _StubCronService:
    """Minimal stub satisfying the CronRelayServiceProtocol list_all_crons seam."""

    def __init__(self, payload):
        # payload is the inner ``data`` value (list of adapter dicts).
        self._payload = payload
        self.last_call_kwargs: dict = {}

    async def list_all_crons(self, *args, **kwargs):
        self.last_call_kwargs = dict(kwargs)
        return {"success": True, "data": self._payload, "total": len(self._payload)}


def _adapter_dict(**overrides):
    base = {
        "id": "t1",
        "bot_id": "bot-x",
        "name": "cron1",
        "enabled": True,
        "schedule": {"kind": "cron", "expr": "0 9 * * *", "tz": "Asia/Shanghai"},
        "payload": {"kind": "message", "message": "echo hi"},
        "created_at_ms": 1722165600000,
        "updated_at_ms": 1722165600000,
    }
    base.update(overrides)
    return base


# ── _map_routine: adapter dict → openapi Routine (Phase 1 Task 2) ───────


def test_map_routine_flattens_adapter_dict():
    adapter = _adapter_dict()
    r = _map_routine(adapter)
    assert isinstance(r, Routine)
    assert r.routine_id == "t1"
    assert r.bot_id == "bot-x"
    assert r.name == "cron1"
    assert isinstance(r.trigger, ScheduleTrigger)
    assert r.trigger.type == "schedule"
    assert r.trigger.cron == "0 9 * * *"
    assert r.timezone == "Asia/Shanghai"
    assert r.command == "echo hi"
    assert r.enabled is True
    assert r.gmt_create  # non-empty ISO str
    assert r.gmt_modified  # non-empty ISO str


def test_map_routine_handles_missing_fields():
    r = _map_routine({})
    assert r.routine_id == ""
    assert r.bot_id == ""
    assert r.name == ""
    assert r.trigger.cron == ""
    assert r.timezone is None
    assert r.command == ""
    assert r.enabled is False
    assert r.gmt_create == ""
    assert r.gmt_modified == ""


def test_map_routine_converts_ms_to_iso():
    # 1722165600000 ms = 2024-07-28T10:00:00Z
    r = _map_routine(
        _adapter_dict(created_at_ms=1722165600000, updated_at_ms=1722165600000)
    )
    assert r.gmt_create.startswith("2024-07-28")
    assert r.gmt_modified.startswith("2024-07-28")


def test_map_routine_invalid_ms_returns_empty_string():
    r = _map_routine(_adapter_dict(created_at_ms="not-a-number", updated_at_ms=None))
    assert r.gmt_create == ""
    assert r.gmt_modified == ""


def test_map_routine_carries_bot_metadata():
    """The aggregate listing decorates every adapter item with bot/owner/stage.

    ``cron_runtime_targets.py`` decorates ``bot_id``/``bot_name``/``owner_id``
    on every item and ``runtime_stage`` on a service bot's — the owner-level
    listing needs all of them mapped, and the per-bot route answers
    ``bot_name`` from the same dict for free.
    """
    adapter = _adapter_dict(
        bot_name="TicketBot",
        owner_id="209800",
        runtime_stage="online",
    )
    r = _map_routine(adapter)
    assert r.bot_name == "TicketBot"
    assert r.owner_id == "209800"
    assert r.runtime_stage == "online"


def test_map_routine_bot_metadata_defaults_to_none():
    """Absent or empty decoration maps to None, never to an empty string.

    The three metadata fields are optional additions; a producer that reports
    none (e.g. a draft-stage item has no ``runtime_stage``) must surface as
    null, which is what the schema documents.
    """
    r = _map_routine({})
    assert r.bot_name is None
    assert r.owner_id is None
    assert r.runtime_stage is None
    r_blank = _map_routine({"bot_name": "", "owner_id": "", "runtime_stage": ""})
    assert r_blank.bot_name is None
    assert r_blank.owner_id is None
    assert r_blank.runtime_stage is None


# ── list_routines handler wiring (Phase 1 Task 2) ──────────────────────
#
# Direct handler invocation (退路 B per task spec): bypasses FastAPI's
# dependency wiring and supplies a stub factory. `principal` carries
# `{"user_id": "u1"}` so `caller_owner_id` resolves the caller.
# Handlers take a required `request: Request` (mirroring the bots router);
# tests pass a `SimpleNamespace` stub whose `state.trace_id` is unset (empty
# `request_id`) or set to a known value (threaded via `responses.envelope`).


@pytest.mark.asyncio
async def test_list_routines_returns_envelope_page():
    service = _StubCronService([_adapter_dict()])

    env = await list_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        bot_id="bot-x",
        status=None,
        factory=service,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.request_id == ""
    assert env.data is not None
    assert isinstance(env.data, Page)
    assert env.data.total == 1
    assert len(env.data.items) == 1
    item = env.data.items[0]
    assert item.routine_id == "t1"
    assert item.bot_id == "bot-x"
    assert item.trigger.cron == "0 9 * * *"
    assert item.command == "echo hi"
    # owner fallback threaded through to list_all_crons
    assert service.last_call_kwargs.get("user_id") == "u1"
    assert service.last_call_kwargs.get("nick_name") == "u1"
    assert service.last_call_kwargs.get("bot_id") == "bot-x"


@pytest.mark.asyncio
async def test_list_routines_asks_for_the_draft_stage_only():
    """The list is draft-only, like every other route in this group.

    ``list_all_crons`` fans a service bot out over draft, verify and online
    runtimes when no stage is given — but this public surface operates a bot's
    pre-publication workspace, so listing here must neither show nor query the
    published runtimes' crons.
    """
    service = _StubCronService([_adapter_dict()])

    await list_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        bot_id="bot-x",
        status=None,
        factory=service,
        request=_request_without_trace(),
    )

    assert service.last_call_kwargs.get("runtime_stage") == "draft"


@pytest.mark.asyncio
async def test_list_routines_paginates_items():
    items = [
        _adapter_dict(id="t1"),
        _adapter_dict(id="t2"),
        _adapter_dict(id="t3"),
    ]
    service = _StubCronService(items)

    env = await list_routines(
        page=PageParams(page=2, page_size=1),
        owner_id="u1",
        bot_id="bot-x",
        status=None,
        factory=service,
        request=_request_without_trace(),
    )

    assert env.data.total == 3
    assert [i.routine_id for i in env.data.items] == ["t2"]


@pytest.mark.asyncio
async def test_list_routines_handles_empty_data_list():
    service = _StubCronService([])

    env = await list_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        bot_id="bot-x",
        status=None,
        factory=service,
        request=_request_without_trace(),
    )

    assert env.code == CODE_OK
    assert env.data.total == 0
    assert env.data.items == []


@pytest.mark.asyncio
async def test_list_routines_handles_dict_data_envelope():
    # Defensive: if the engine ever wraps data as {"items": [...]}, still extract.
    class _DictWrapService:
        async def list_all_crons(self, *args, **kwargs):
            return {"success": True, "data": {"items": [_adapter_dict()]}}

    service = _DictWrapService()

    env = await list_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        bot_id="bot-x",
        status=None,
        factory=service,
        request=_request_without_trace(),
    )

    assert env.data.total == 1
    assert env.data.items[0].routine_id == "t1"


@pytest.mark.asyncio
async def test_list_routines_reads_x_trace_id_from_request():
    service = _StubCronService([])
    request = _request_with_trace("trace-abc")

    env = await list_routines(
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        bot_id="bot-x",
        status=None,
        factory=service,
        request=request,
    )

    assert env.request_id == "trace-abc"


# ── create_routine handler wiring (Phase 1 Task 3) ──────────────────────
#
# adapter body shape (from legacy cron/router.py create_cron +
# device_adapter_transport._build_item): ``schedule`` is the raw cron
# expression STRING, not a nested dict. The adapter wraps it into
# ``{kind:"cron", expr, tz}`` on read in ``_build_item``. ``timezone``
# defaults to ``Asia/Shanghai``.


class _StubCronCreateService:
    """Minimal stub satisfying the CronRelayServiceProtocol create_cron seam."""

    def __init__(self, payload):
        # payload is the inner ``data`` value (adapter cron dict).
        self._payload = payload
        self.last_call_kwargs: dict = {}

    async def create_cron(self, *, bot_id, user_id, nick_name, body):
        self.last_call_kwargs = {
            "bot_id": bot_id,
            "user_id": user_id,
            "nick_name": nick_name,
            "body": body,
        }
        return {"success": True, "data": self._payload}


@pytest.mark.asyncio
async def test_create_routine_returns_201_envelope():
    service = _StubCronCreateService(_adapter_dict())
    body = RoutineSpec(
                name="cron1",
        trigger=ScheduleTrigger(cron="0 9 * * *"),
        command="echo hi",
    )

    env = await create_routine(
        bot_id="bot-x",
        body=body,
        owner_id="u1",
        factory=service,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_CREATED
    assert env.message == "Created"
    assert env.request_id == ""
    assert env.data is not None
    assert isinstance(env.data, Routine)
    assert env.data.routine_id == "t1"
    assert env.data.bot_id == "bot-x"
    assert env.data.trigger.cron == "0 9 * * *"
    assert env.data.command == "echo hi"


@pytest.mark.asyncio
async def test_create_routine_uses_the_addressed_bot_for_owner_and_call():
    service = _StubCronCreateService(_adapter_dict())
    body = RoutineSpec(
                name="cron1",
        trigger=ScheduleTrigger(cron="0 9 * * *"),
        command="echo hi",
    )

    await create_routine(
        bot_id="bot-x",
        body=body,
        owner_id="u1",
        factory=service,
        request=_request_without_trace(),
    )

    # the addressed bot flows to factory.create_cron; owner comes from principal
    assert service.last_call_kwargs["bot_id"] == "bot-x"
    assert service.last_call_kwargs["user_id"] == "u1"
    assert service.last_call_kwargs["nick_name"] == "u1"


@pytest.mark.asyncio
async def test_create_routine_passes_schedule_as_cron_string():
    # The adapter accepts schedule as the raw cron string; the openapi
    # RoutineSpec carries it nested under trigger.cron. Verify the
    # translation does NOT forward a {kind,expr,tz} dict to the service.
    service = _StubCronCreateService(_adapter_dict())
    body = RoutineSpec(
                name="cron1",
        trigger=ScheduleTrigger(cron="0 9 * * *"),
        command="echo hi",
        timezone="Asia/Shanghai",
        enabled=True,
    )

    await create_routine(
        bot_id="bot-x",
        body=body,
        owner_id="u1",
        factory=service,
        request=_request_without_trace(),
    )

    sent_body = service.last_call_kwargs["body"]
    assert sent_body["name"] == "cron1"
    assert sent_body["schedule"] == "0 9 * * *"  # string, NOT a dict
    assert sent_body["command"] == "echo hi"
    assert sent_body["timezone"] == "Asia/Shanghai"
    assert sent_body["enabled"] is True


@pytest.mark.asyncio
async def test_create_routine_defaults_timezone_when_null():
    service = _StubCronCreateService(_adapter_dict())
    body = RoutineSpec(
                name="cron1",
        trigger=ScheduleTrigger(cron="0 9 * * *"),
        command="echo hi",
        timezone=None,
    )

    await create_routine(
        bot_id="bot-x",
        body=body,
        owner_id="u1",
        factory=service,
        request=_request_without_trace(),
    )

    assert service.last_call_kwargs["body"]["timezone"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_create_routine_reads_x_trace_id_from_request():
    service = _StubCronCreateService(_adapter_dict())
    request = _request_with_trace("trace-create-1")
    body = RoutineSpec(
                name="cron1",
        trigger=ScheduleTrigger(cron="0 9 * * *"),
        command="echo hi",
    )

    env = await create_routine(
        bot_id="bot-x",
        body=body,
        owner_id="u1",
        factory=service,
        request=request,
    )

    assert env.request_id == "trace-create-1"


@pytest.mark.asyncio
async def test_create_routine_500_when_service_returns_no_data():
    service = _StubCronCreateService(None)  # data is None
    body = RoutineSpec(
                name="cron1",
        trigger=ScheduleTrigger(cron="0 9 * * *"),
        command="echo hi",
    )

    with pytest.raises(HTTPException) as exc:
        await create_routine(
            bot_id="bot-x",
            body=body,
            owner_id="u1",
            factory=service,
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 500


# ── get_routine handler wiring (Phase 1 Task 4) ────────────────────────
#
# C3: path carries only routine_id, so bot_id comes via a required query.
# get_cron_detail returns {"success":..,"data":<adapter cron dict>}; a
# missing/non-dict data collapses to 404 (the engine has no row).


class _StubCronDetailService:
    """Minimal stub satisfying the CronRelayServiceProtocol.get_cron_detail seam."""

    def __init__(self, payload):
        # payload is the inner ``data`` value (adapter cron dict or None).
        self._payload = payload
        self.last_call_kwargs: dict = {}

    async def get_cron_detail(
        self, *, bot_id, user_id, nick_name, task_id, runtime_stage="DRAFT"
    ):
        self.last_call_kwargs = {
            "bot_id": bot_id,
            "user_id": user_id,
            "nick_name": nick_name,
            "task_id": task_id,
            "runtime_stage": runtime_stage,
        }
        return {"success": True, "data": self._payload}


@pytest.mark.asyncio
async def test_get_routine_returns_envelope_routine():
    service = _StubCronDetailService(_adapter_dict())

    env = await get_routine(
        routine_id="t1",
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.request_id == ""
    assert isinstance(env.data, Routine)
    assert env.data.routine_id == "t1"
    assert env.data.bot_id == "bot-x"
    assert env.data.trigger.cron == "0 9 * * *"
    assert env.data.command == "echo hi"
    # owner fallback threaded through to get_cron_detail
    assert service.last_call_kwargs.get("bot_id") == "bot-x"
    assert service.last_call_kwargs.get("user_id") == "u1"
    assert service.last_call_kwargs.get("nick_name") == "u1"
    assert service.last_call_kwargs.get("task_id") == "t1"


@pytest.mark.asyncio
async def test_get_routine_404_when_data_missing():
    service = _StubCronDetailService(None)  # data is None → not a dict

    with pytest.raises(HTTPException) as exc:
        await get_routine(
            routine_id="t1",
            owner_id="u1",
            bot_id="bot-x",
            factory=service,
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 404
    assert "not found" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_get_routine_reads_x_trace_id_from_request():
    service = _StubCronDetailService(_adapter_dict())
    request = _request_with_trace("trace-get-1")

    env = await get_routine(
        routine_id="t1",
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=request,
    )

    assert env.request_id == "trace-get-1"


# ── update_routine handler wiring (Phase 1 Task 4) ─────────────────────
#
# adapter body shape (mirrors create): ``schedule`` is the raw cron STRING,
# not a dict. Partial update → only set fields are forwarded. Missing data
# on the response collapses to 404.


class _StubCronUpdateService:
    """Minimal stub satisfying the CronRelayServiceProtocol.update_cron seam."""

    def __init__(self, payload):
        # payload is the inner ``data`` value (adapter cron dict or None).
        self._payload = payload
        self.last_call_kwargs: dict = {}

    async def update_cron(
        self, *, bot_id, user_id, nick_name, task_id, body, runtime_stage="DRAFT"
    ):
        self.last_call_kwargs = {
            "bot_id": bot_id,
            "user_id": user_id,
            "nick_name": nick_name,
            "task_id": task_id,
            "body": body,
            "runtime_stage": runtime_stage,
        }
        return {"success": True, "data": self._payload}


@pytest.mark.asyncio
async def test_update_routine_returns_envelope_routine():
    service = _StubCronUpdateService(_adapter_dict())
    body = RoutineUpdate(name="cron-renamed")

    env = await update_routine(
        routine_id="t1",
        body=body,
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert isinstance(env.data, Routine)
    assert env.data.routine_id == "t1"
    assert env.data.bot_id == "bot-x"


@pytest.mark.asyncio
async def test_update_routine_passes_partial_body_and_schedule_string():
    # Only set fields flow to the adapter; trigger.cron becomes a raw
    # ``schedule`` string (NOT a {kind,expr,tz} dict — Task 3 contract).
    service = _StubCronUpdateService(_adapter_dict())
    body = RoutineUpdate(
        name="cron-renamed",
        trigger=ScheduleTrigger(cron="0 10 * * *"),
        command="echo new",
        timezone="UTC",
        enabled=False,
    )

    await update_routine(
        routine_id="t1",
        body=body,
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    sent_body = service.last_call_kwargs["body"]
    assert sent_body["name"] == "cron-renamed"
    assert sent_body["schedule"] == "0 10 * * *"  # string, NOT a dict
    assert sent_body["command"] == "echo new"
    assert sent_body["timezone"] == "UTC"
    assert sent_body["enabled"] is False
    # task_id + owner fallback threaded through
    assert service.last_call_kwargs["task_id"] == "t1"
    assert service.last_call_kwargs["bot_id"] == "bot-x"
    assert service.last_call_kwargs["user_id"] == "u1"
    assert service.last_call_kwargs["nick_name"] == "u1"


@pytest.mark.asyncio
async def test_update_routine_omits_unset_fields_from_body():
    # Partial update: fields left None must NOT appear in the adapter body
    # (the engine treats presence as "set this field").
    service = _StubCronUpdateService(_adapter_dict())
    body = RoutineUpdate(name="only-name")

    await update_routine(
        routine_id="t1",
        body=body,
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    sent_body = service.last_call_kwargs["body"]
    assert sent_body == {"name": "only-name"}
    # nothing else leaked
    assert "schedule" not in sent_body
    assert "command" not in sent_body
    assert "timezone" not in sent_body
    assert "enabled" not in sent_body


@pytest.mark.asyncio
async def test_update_routine_404_when_data_missing():
    service = _StubCronUpdateService(None)  # data is None
    body = RoutineUpdate(name="cron-renamed")

    with pytest.raises(HTTPException) as exc:
        await update_routine(
            routine_id="t1",
            body=body,
            owner_id="u1",
            bot_id="bot-x",
            factory=service,
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_routine_reads_x_trace_id_from_request():
    service = _StubCronUpdateService(_adapter_dict())
    request = _request_with_trace("trace-upd-1")
    body = RoutineUpdate(name="cron-renamed")

    env = await update_routine(
        routine_id="t1",
        body=body,
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=request,
    )

    assert env.request_id == "trace-upd-1"


# ── _map_run: adapter run dict → openapi RoutineRun (Phase 1 Task 5) ────


def _run_dict(**overrides):
    base = {
        "job_id": "run_001",
        "started_at_ms": 1722165600000,
        "finished_at_ms": 1722165601500,
        "status": "succeeded",
        "error": "",
        "duration_ms": 1500,
    }
    base.update(overrides)
    return base


def test_map_run_flattens_adapter_run_dict():
    r = _map_run(_run_dict(), routine_id="t1")
    assert isinstance(r, RoutineRun)
    assert r.run_id == "run_001"
    assert r.routine_id == "t1"
    assert r.status == "succeeded"
    assert r.started_at and r.started_at.startswith("2024-07-28")
    assert r.finished_at and r.finished_at.startswith("2024-07-28")


def test_map_run_handles_missing_fields():
    r = _map_run({}, routine_id="t1")
    assert r.run_id == ""
    assert r.routine_id == "t1"
    assert r.status == ""
    assert r.started_at is None
    assert r.finished_at is None


def test_map_run_accepts_run_id_alias():
    # Test stubs sometimes use ``run_id`` instead of ``job_id`` — accept both.
    r = _map_run({"run_id": "r-x"}, routine_id="t1")
    assert r.run_id == "r-x"


def test_map_run_coerces_empty_ms_to_none():
    r = _map_run(_run_dict(started_at_ms=None, finished_at_ms=0), routine_id="t1")
    assert r.started_at is None
    assert r.finished_at is None


# ── delete_routine handler wiring (Phase 1 Task 5) ──────────────────────


class _StubCronDeleteService:
    """Minimal stub satisfying the CronRelayServiceProtocol.delete_cron seam."""

    def __init__(self, success=True, payload=None):
        self._success = success
        self._payload = payload if payload is not None else {"deleted": True}
        self.last_call_kwargs: dict = {}

    async def delete_cron(
        self, *, bot_id, user_id, nick_name, task_id, runtime_stage="DRAFT"
    ):
        self.last_call_kwargs = {
            "bot_id": bot_id,
            "user_id": user_id,
            "nick_name": nick_name,
            "task_id": task_id,
            "runtime_stage": runtime_stage,
        }
        return {"success": self._success, "data": self._payload}


@pytest.mark.asyncio
async def test_delete_routine_returns_envelope_deleted_true():
    service = _StubCronDeleteService(success=True)

    env = await delete_routine(
        routine_id="t1",
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.request_id == ""
    assert env.data is not None
    assert env.data.deleted is True
    # owner fallback threaded through to delete_cron
    assert service.last_call_kwargs.get("bot_id") == "bot-x"
    assert service.last_call_kwargs.get("user_id") == "u1"
    assert service.last_call_kwargs.get("nick_name") == "u1"
    assert service.last_call_kwargs.get("task_id") == "t1"


@pytest.mark.asyncio
async def test_delete_routine_returns_404_when_success_false():
    # Engine signals failure without raising → map to 404 (not 200
    # {deleted:false}, per totalfrank #5: clients must distinguish failure from
    # success by status code).
    service = _StubCronDeleteService(success=False, payload={})

    with pytest.raises(HTTPException) as exc:
        await delete_routine(
            routine_id="t1",
            owner_id="u1",
            bot_id="bot-x",
            factory=service,
            request=_request_without_trace(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_routine_reads_x_trace_id_from_request():
    service = _StubCronDeleteService()
    request = _request_with_trace("trace-del-1")

    env = await delete_routine(
        routine_id="t1",
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=request,
    )

    assert env.request_id == "trace-del-1"


# ── run_routine handler wiring (Phase 1 Task 5) ─────────────────────────
#
# run_cron returns {success, data:{ok, ran, reason}} — no run_id. The
# handler synthesizes run_id from routine_id + UTC trigger timestamp, and
# derives status from ran/reason: completed | failed | unknown.


class _StubCronRunService:
    """Minimal stub satisfying the CronRelayServiceProtocol.run_cron seam."""

    def __init__(self, ran=True, reason="", *, status="", success=True, run_id=None):
        self._ran = ran
        self._reason = reason
        self._status = status
        self._success = success
        self._run_id = run_id
        self.last_call_kwargs: dict = {}

    async def run_cron(
        self, *, bot_id, user_id, nick_name, task_id, force=False, runtime_stage="DRAFT"
    ):
        self.last_call_kwargs = {
            "bot_id": bot_id,
            "user_id": user_id,
            "nick_name": nick_name,
            "task_id": task_id,
            "force": force,
            "runtime_stage": runtime_stage,
        }
        data = {
            "ok": self._ran,
            "ran": self._ran,
            "reason": self._reason,
        }
        if self._status:
            data["status"] = self._status
        if self._run_id:
            data["runId"] = self._run_id
        return {"success": self._success, "data": data}


@pytest.mark.asyncio
async def test_run_routine_returns_completed_status_when_ran():
    service = _StubCronRunService(ran=True, reason="")

    env = await run_routine(
        routine_id="t1",
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.request_id == ""
    assert isinstance(env.data, RoutineRun)
    assert env.data.routine_id == "t1"
    assert env.data.status == "completed"
    # Synthesized run_id carries the routine_id prefix
    assert env.data.run_id.startswith("t1-")
    # No real timestamps from the adapter
    assert env.data.started_at is None
    assert env.data.finished_at is None
    # owner fallback threaded through
    assert service.last_call_kwargs.get("bot_id") == "bot-x"
    assert service.last_call_kwargs.get("user_id") == "u1"
    assert service.last_call_kwargs.get("nick_name") == "u1"
    assert service.last_call_kwargs.get("task_id") == "t1"
    assert service.last_call_kwargs.get("force") is True


@pytest.mark.asyncio
async def test_run_routine_maps_openclaw_dispatched_response_and_run_id():
    service = _StubCronRunService(ran="t1", status="dispatched", run_id="manual:t1:123")

    env = await run_routine(
        routine_id="t1",
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    assert env.data.status == "completed"
    assert env.data.run_id == "manual:t1:123"
    assert service.last_call_kwargs["force"] is True


@pytest.mark.asyncio
async def test_run_routine_does_not_treat_not_due_routine_id_as_completed():
    service = _StubCronRunService(ran="t1", status="not_due")

    env = await run_routine(
        routine_id="t1",
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    assert env.data.status == "failed"


@pytest.mark.asyncio
async def test_run_routine_returns_502_when_relay_reports_failure():
    service = _StubCronRunService(ran=False, success=False)

    # Failure reaches the caller as a CronRelayError(error_code=502), not a
    # raw HTTPException; the app-level envelope handler maps it to the public
    # 502 envelope via ENVELOPE_ERRORS[CronRelayError] ("Cron relay service
    # error"). Internal adapter detail does not leak.
    with pytest.raises(CronRelayError, match="routine trigger failed") as exc:
        await run_routine(
            routine_id="t1",
            owner_id="u1",
            bot_id="bot-x",
            factory=service,
            request=_request_without_trace(),
        )

    assert exc.value.error_code == 502


@pytest.mark.asyncio
async def test_run_routine_returns_failed_status_when_reason():
    service = _StubCronRunService(ran=False, reason="agent crashed")

    env = await run_routine(
        routine_id="t1",
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    assert env.data.status == "failed"


@pytest.mark.asyncio
async def test_run_routine_returns_unknown_status_when_no_reason():
    # ran=False and no reason → unknown (neither completed nor failed).
    service = _StubCronRunService(ran=False, reason="")

    env = await run_routine(
        routine_id="t1",
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    assert env.data.status == "unknown"


@pytest.mark.asyncio
async def test_run_routine_reads_x_trace_id_from_request():
    service = _StubCronRunService()
    request = _request_with_trace("trace-run-1")

    env = await run_routine(
        routine_id="t1",
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=request,
    )

    assert env.request_id == "trace-run-1"


# ── list_routine_runs handler wiring (Phase 1 Task 5) ───────────────────
#
# get_cron_runs (draft) returns {success, data:{input, runs:[{job_id,
# started_at_ms, finished_at_ms, status, ...}]}}. _decorate_single_result
# only adds bot metadata fields to data; runs stays intact. We map each
# entry via _map_run and paginate client-side.


class _StubCronRunsService:
    """Minimal stub satisfying the CronRelayServiceProtocol.get_cron_runs seam."""

    def __init__(self, runs):
        self._runs = runs
        self.last_call_kwargs: dict = {}

    async def get_cron_runs(
        self,
        *,
        bot_id,
        user_id,
        nick_name,
        task_id,
        limit=20,
        runtime_stage="DRAFT",
        device_uuid=None,
    ):
        self.last_call_kwargs = {
            "bot_id": bot_id,
            "user_id": user_id,
            "nick_name": nick_name,
            "task_id": task_id,
            "limit": limit,
            "runtime_stage": runtime_stage,
        }
        return {
            "success": True,
            "data": {"input": "tick", "runs": self._runs},
        }


@pytest.mark.asyncio
async def test_list_routine_runs_returns_envelope_page_mapped_from_runs():
    runs = [
        _run_dict(job_id="r1", status="succeeded"),
        _run_dict(job_id="r2", status="failed"),
    ]
    service = _StubCronRunsService(runs)

    env = await list_routine_runs(
        routine_id="t1",
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    assert isinstance(env, Envelope)
    assert env.code == CODE_OK
    assert env.message == "OK"
    assert env.request_id == ""
    assert env.data is not None
    assert isinstance(env.data, Page)
    assert env.data.total == 2
    assert len(env.data.items) == 2
    assert all(isinstance(i, RoutineRun) for i in env.data.items)
    assert env.data.items[0].run_id == "r1"
    assert env.data.items[0].routine_id == "t1"
    assert env.data.items[0].status == "succeeded"
    assert env.data.items[1].run_id == "r2"
    assert env.data.items[1].status == "failed"
    # owner fallback threaded through
    assert service.last_call_kwargs.get("bot_id") == "bot-x"
    assert service.last_call_kwargs.get("user_id") == "u1"
    assert service.last_call_kwargs.get("nick_name") == "u1"
    assert service.last_call_kwargs.get("task_id") == "t1"


@pytest.mark.asyncio
async def test_list_routine_runs_paginates_items():
    runs = [_run_dict(job_id=f"r{i}", status="succeeded") for i in range(5)]
    service = _StubCronRunsService(runs)

    env = await list_routine_runs(
        routine_id="t1",
        page=PageParams(page=2, page_size=2),
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    assert env.data.total == 5
    assert [i.run_id for i in env.data.items] == ["r2", "r3"]


@pytest.mark.asyncio
async def test_list_routine_runs_handles_empty_runs():
    service = _StubCronRunsService([])

    env = await list_routine_runs(
        routine_id="t1",
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    assert env.code == CODE_OK
    assert env.data.total == 0
    assert env.data.items == []


@pytest.mark.asyncio
async def test_list_routine_runs_reads_x_trace_id_from_request():
    service = _StubCronRunsService([])
    request = _request_with_trace("trace-runs-1")

    env = await list_routine_runs(
        routine_id="t1",
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=request,
    )

    assert env.request_id == "trace-runs-1"


@pytest.mark.asyncio
async def test_list_routine_runs_handles_bare_data_list_defensively():
    # Defensive: if the engine ever returns data as a bare list (not wrapped
    # in {runs:[...]}), still extract. The handler should not crash.
    class _BareListService:
        async def get_cron_runs(self, **kwargs):
            return {"success": True, "data": [_run_dict(job_id="r1")]}

    service = _BareListService()

    env = await list_routine_runs(
        routine_id="t1",
        page=PageParams(page=1, page_size=20),
        owner_id="u1",
        bot_id="bot-x",
        factory=service,
        request=_request_without_trace(),
    )

    assert env.data.total == 1
    assert env.data.items[0].run_id == "r1"


# ── run_routine, raised CronRelay*/adapter failure paths ───────────────
# ``@envelope_errors`` is a passthrough wrapper; the ENVELOPE_ERRORS mapping is
# applied by the app-level envelope handler. So in a direct unit call, the
# raised domain errors propagate; the assertions below nail that they carry the
# right type / error_code so the app-level map returns the right envelope.
from agentclaw.community.core.cron.errors import (
    CronApiTimeoutError,
    CronRelayError,
)


class _RaisingCronRunService:
    def __init__(self, exc):
        self._exc = exc
        self.last_call_kwargs: dict = {}

    async def run_cron(self, **kwargs):
        self.last_call_kwargs = kwargs
        raise self._exc


@pytest.mark.asyncio
async def test_run_routine_propagates_service_raised_cron_relay_error():
    service = _RaisingCronRunService(
        CronRelayError("Bot has no device binding", error_code=400)
    )
    with pytest.raises(CronRelayError, match="Bot has no device binding") as exc:
        await run_routine(
            routine_id="t1",
            owner_id="u1",
            bot_id="bot-x",
            factory=service,
            request=_request_without_trace(),
        )
    assert exc.value.error_code == 400
    # Pre-existing behaviour retained: handler always forces the knock.
    assert service.last_call_kwargs.get("force") is True


@pytest.mark.asyncio
async def test_run_routine_propagates_cron_api_timeout():
    service = _RaisingCronRunService(
        CronApiTimeoutError("/api/cron/t1/run", 10.0)
    )
    with pytest.raises(CronApiTimeoutError):
        await run_routine(
            routine_id="t1",
            owner_id="u1",
            bot_id="bot-x",
            factory=service,
            request=_request_without_trace(),
        )


@pytest.mark.asyncio
async def test_run_routine_translates_unexpected_adapter_exception():
    # ValueError("Bot has no device binding") from the relay is translated to
    # CronRelayError(502) → ENVELOPE_ERRORS → "Cron relay service error".
    service = _RaisingCronRunService(ValueError("Bot has no device binding"))
    with pytest.raises(CronRelayError, match="routine trigger failed") as exc:
        await run_routine(
            routine_id="t1",
            owner_id="u1",
            bot_id="bot-x",
            factory=service,
            request=_request_without_trace(),
        )
    assert exc.value.error_code == 502
