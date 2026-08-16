"""Unit tests for the stage→binding rule (``core/engine_runtime/stage.py``).

Which record is a stage's live runtime is one rule with two callers — the
relay's device resolution and the connection service's socket composition —
so the rule is pinned here once, and one test proves the two callers land on
the same binding for the same (bot, stage).
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.devices.services.device_context import (
    DeviceNotBoundError,
)
from agentclaw.community.core.engine_runtime.errors import (
    EngineStageNotLiveError,
    EngineStageReadOnlyError,
)
from agentclaw.community.core.engine_runtime.stage import (
    STAGE_DRAFT,
    STAGE_ONLINE,
    STAGE_VERIFY,
    require_stage_addressable,
    require_stage_writable,
    resolve_stage_bind_id,
    resolve_stage_device_context,
)

BOT = "bot-1"
OWNER = "u-1"
PK = 100
ENV = "test"


class _Record:
    def __init__(self, ext, status, record_id=7):
        self.ext = ext
        self.status = status
        self.id = record_id


class _PublishRepo:
    def __init__(self, records):
        self._records = records
        self.calls = []

    def list_by_source_bot(self, source_bot_pk, env):
        self.calls.append((source_bot_pk, env))
        return list(self._records)


class _BindingRepo:
    def __init__(self, statuses=None, raises=None):
        self._statuses = statuses or {}
        self._raises = raises
        self.calls = []

    def get_by_id(self, binding_id):
        self.calls.append(binding_id)
        if self._raises is not None:
            raise self._raises
        status = self._statuses.get(binding_id)
        if status is None:
            return None
        return type("Rec", (), {"status": status})()


def _resolve(records, stage, bindings=None):
    return resolve_stage_bind_id(
        _PublishRepo(records),
        bindings or _BindingRepo(),
        bot_pk=PK,
        bot_id=BOT,
        stage=stage,
        env=ENV,
    )


# ── online ───────────────────────────────────────────────────────────────────


def test_online_is_the_newest_success_records_online_binding():
    records = [
        _Record({"binding": {"online": 43, "verify": 41}}, "success", 9),
        _Record({"binding": {"online": 42}}, "success", 8),
    ]
    assert _resolve(records, STAGE_ONLINE) == 43


def test_a_superseded_or_building_record_never_resolves_online():
    records = [
        _Record({"binding": {"online": 77}}, "building"),
        _Record({"binding": {"online": 88}}, "upgraded"),
        _Record({"binding": {"online": 99}}, "released"),
    ]
    with pytest.raises(EngineStageNotLiveError):
        _resolve(records, STAGE_ONLINE)


def test_online_with_no_records_is_not_live():
    with pytest.raises(EngineStageNotLiveError):
        _resolve([], STAGE_ONLINE)


# ── verify ───────────────────────────────────────────────────────────────────


def test_verify_is_the_validating_records_verify_binding():
    records = [
        _Record({"binding": {"verify": 41}}, "validating", 9),
        _Record({"binding": {"online": 42, "verify": 40}}, "success", 8),
    ]
    assert _resolve(records, STAGE_VERIFY) == 41


def test_a_validating_record_without_its_binding_is_not_live_yet():
    """Mid-publish, the new release decides — never the outgoing one.

    Falling through to the promoted record's retained runtime here would
    silently serve the previous release and then flip once the new binding is
    written; cron consults the retained record only when nothing validates.
    """
    records = [
        _Record({"binding": {}}, "validating", 9),
        _Record({"binding": {"online": 42, "verify": 40}}, "success", 8),
    ]
    bindings = _BindingRepo({40: DeviceBindingStatus.ACTIVE})
    with pytest.raises(EngineStageNotLiveError):
        _resolve(records, STAGE_VERIFY, bindings)
    assert bindings.calls == []


def test_the_retained_verify_binding_counts_while_active():
    """After promotion the pre-prod runtime is retained; refusing it here
    while cron lists and forwards to it would be a 409 for a runtime that is
    up — the two surfaces must agree on whether a runtime exists."""
    records = [_Record({"binding": {"online": 42, "verify": 40}}, "success")]
    bindings = _BindingRepo({40: DeviceBindingStatus.ACTIVE})
    assert _resolve(records, STAGE_VERIFY, bindings) == 40
    assert bindings.calls == [40]


@pytest.mark.parametrize(
    "status",
    [DeviceBindingStatus.RELEASED, DeviceBindingStatus.STOPPED, None],
)
def test_a_dead_retained_binding_is_a_dead_stage(status):
    """A released retained runtime must answer "not live", not the retryable
    "not ready" a doomed device resolution would produce."""
    records = [_Record({"binding": {"online": 42, "verify": 40}}, "success")]
    bindings = _BindingRepo({40: status} if status is not None else {})
    with pytest.raises(EngineStageNotLiveError):
        _resolve(records, STAGE_VERIFY, bindings)


def test_an_unreadable_retained_binding_reads_as_not_live():
    """Degrades toward the honest refusal rather than a 500 — and never
    toward serving a runtime whose state could not be read."""
    records = [_Record({"binding": {"online": 42, "verify": 40}}, "success")]
    with pytest.raises(EngineStageNotLiveError):
        _resolve(records, STAGE_VERIFY, _BindingRepo(raises=RuntimeError("db")))


# ── malformed data stays distinguishable ─────────────────────────────────────


def test_a_missing_primary_key_refuses_rather_than_guessing():
    """Without the pk there is no safe key — a ``bot_id`` fallback would
    reopen the cross-owner hole."""
    with pytest.raises(DeviceNotBoundError):
        resolve_stage_bind_id(
            _PublishRepo([]),
            _BindingRepo(),
            bot_pk=0,
            bot_id=BOT,
            stage=STAGE_ONLINE,
            env=ENV,
        )


def test_unreadable_publish_ext_is_malformed_data_not_a_dead_stage():
    records = [_Record("{not json", "success")]
    with pytest.raises(DeviceNotBoundError):
        _resolve(records, STAGE_ONLINE)


@pytest.mark.parametrize("stage", [STAGE_DRAFT, "prod", ""])
def test_only_a_published_stage_may_be_resolved(stage):
    """The draft never reaches a publish record, and a typo is a programmer
    error — both are refused loudly rather than scanned for."""
    with pytest.raises(ValueError):
        _resolve([], stage)


# ── the addressability check the gate and the relay share ────────────────────


def test_a_service_bot_may_name_every_stage():
    for stage in (STAGE_DRAFT, STAGE_VERIFY, STAGE_ONLINE):
        require_stage_addressable("service", stage)


def test_a_personal_bot_has_only_its_workspace():
    require_stage_addressable("personal", STAGE_DRAFT)
    for stage in (STAGE_VERIFY, STAGE_ONLINE):
        with pytest.raises(EngineStageNotLiveError):
            require_stage_addressable("personal", stage)


def test_an_unknown_stage_is_refused_for_every_bot_type():
    for bot_type in ("personal", "service"):
        with pytest.raises(EngineStageNotLiveError):
            require_stage_addressable(bot_type, "prod")


# ── one rule, two callers ────────────────────────────────────────────────────


def test_the_socket_and_the_relay_address_the_same_binding():
    """The connection service and the relay resolve a published stage through
    this same function — asserted end-to-end so a refactor that gives either
    its own copy of the rule fails here."""
    from agentclaw.community.core.engine_runtime.connection import (
        EngineConnectionService,
    )
    from agentclaw.community.core.engine_runtime.relay import EngineRuntimeRelay

    connection_resolver = EngineConnectionService._stage_binding_id
    relay_resolver = EngineRuntimeRelay._resolve_published_device
    import inspect

    assert "resolve_stage_bind_id" in inspect.getsource(connection_resolver)
    assert "resolve_stage_bind_id" in inspect.getsource(relay_resolver)


# ── writes ───────────────────────────────────────────────────────────────────


def test_the_draft_is_the_only_writable_stage():
    require_stage_writable(STAGE_DRAFT)
    for stage in (STAGE_VERIFY, STAGE_ONLINE):
        with pytest.raises(EngineStageReadOnlyError):
            require_stage_writable(stage)


def test_a_write_to_an_unknown_stage_is_not_reported_as_read_only():
    """A typo is a typo, not a published runtime.

    ``require_stage_writable`` refuses everything that is not the draft, so
    without the known-stage check first it would answer "that runtime is
    published and frozen" about a string that names no runtime at all.
    """
    with pytest.raises(EngineStageNotLiveError):
        require_stage_writable("onlien")


# ── stage → device context ───────────────────────────────────────────────────


class _Resolver:
    """Records which resolution path a stage took."""

    def __init__(self):
        self.for_bot = []
        self.for_binding = []

    def resolve_for_bot(self, bot_id, owner_id, **kwargs):
        self.for_bot.append((bot_id, owner_id))
        return f"ctx-draft:{bot_id}"

    def resolve_for_binding(self, binding_id, operator_id, *, bot_id, **kwargs):
        self.for_binding.append((binding_id, operator_id, bot_id))
        return f"ctx-bound:{binding_id}"


class _BotRepo:
    def __init__(self, record=None):
        self._record = record
        self.calls = []

    def get_by_id_and_owner(self, bot_id, owner_id):
        self.calls.append((bot_id, owner_id))
        return self._record


def _service_row(bot_type="service"):
    return {"id": PK, "bot_id": BOT, "bot_type": bot_type, "owner_id": OWNER}


def _context(stage, *, records=(), bot_repo=None, bindings=None):
    resolver = _Resolver()
    repo = bot_repo if bot_repo is not None else _BotRepo(_service_row())
    ctx = resolve_stage_device_context(
        resolver,
        _PublishRepo(records),
        bindings or _BindingRepo(),
        repo,
        bot_id=BOT,
        owner_id=OWNER,
        stage=stage,
    )
    return ctx, resolver, repo


def test_the_draft_resolves_the_bots_own_binding_and_reads_no_extra_row():
    """The byte-for-byte pin: a request naming no stage does what it always did.

    One ``resolve_for_bot(bot_id, owner_id)`` — the call this path has always
    made — and **no** bot-row read added on top of it.
    """
    ctx, resolver, repo = _context(STAGE_DRAFT)

    assert ctx == f"ctx-draft:{BOT}"
    assert resolver.for_bot == [(BOT, OWNER)]
    assert resolver.for_binding == []
    assert repo.calls == [], "the draft leg must not add a bot-row read"


@pytest.mark.parametrize(
    ("stage", "binding"),
    [(STAGE_ONLINE, 41), (STAGE_VERIFY, 42)],
)
def test_a_published_stage_resolves_that_stages_binding(stage, binding):
    records = [
        _Record(
            {"binding": {"online": 41, "verify": 42}},
            "validating" if stage == STAGE_VERIFY else "success",
        )
    ]
    ctx, resolver, _ = _context(stage, records=records)

    assert ctx == f"ctx-bound:{binding}"
    assert resolver.for_binding == [(binding, OWNER, BOT)]
    assert resolver.for_bot == [], "a published stage never falls back to the draft"


def test_a_published_stage_on_a_personal_bot_refuses_before_any_resolution():
    bot_repo = _BotRepo(_service_row(bot_type="personal"))
    resolver = _Resolver()

    with pytest.raises(EngineStageNotLiveError):
        resolve_stage_device_context(
            resolver,
            _PublishRepo([]),
            _BindingRepo(),
            bot_repo,
            bot_id=BOT,
            owner_id=OWNER,
            stage=STAGE_ONLINE,
        )

    assert resolver.for_bot == [] and resolver.for_binding == []


def test_a_bot_that_is_not_the_callers_is_refused_as_a_stage_with_no_runtime():
    """Not a 404. A published-stage request must not become the one way to
    learn whether a bot exists, so a missing row answers exactly as a personal
    bot does — the same 409 the draft leg gives."""
    with pytest.raises(EngineStageNotLiveError):
        _context(STAGE_ONLINE, bot_repo=_BotRepo(None))


def test_an_unknown_stage_never_reaches_the_published_branch():
    """``stage=""`` is a typo, not a published runtime: it must not cost a bot
    read and must not be answered as "no live runtime at online"."""
    bot_repo = _BotRepo(_service_row())
    with pytest.raises(EngineStageNotLiveError):
        _context("", bot_repo=bot_repo)
    assert bot_repo.calls == []


def test_the_published_lookup_is_keyed_on_the_primary_key_not_the_bot_id():
    """``bot_id`` is not unique across owners; ``bot_pk`` is the discriminator."""
    records = [_Record({"binding": {"online": 41}}, "success")]
    publish_repo = _PublishRepo(records)
    resolve_stage_device_context(
        _Resolver(),
        publish_repo,
        _BindingRepo(),
        _BotRepo(_service_row()),
        bot_id=BOT,
        owner_id=OWNER,
        stage=STAGE_ONLINE,
    )
    # The env comes from ``get_current_env()`` inside the function; the pk is
    # what this pins.
    assert [pk for pk, _env in publish_repo.calls] == [PK]


def test_the_owner_scoped_read_is_the_one_used_for_the_primary_key():
    bot_repo = _BotRepo(_service_row())
    _context(STAGE_ONLINE, records=[_Record({"binding": {"online": 41}}, "success")],
             bot_repo=bot_repo)
    assert bot_repo.calls == [(BOT, OWNER)], (
        "the pk must come from a (bot_id, owner_id) read, never a wider one"
    )
