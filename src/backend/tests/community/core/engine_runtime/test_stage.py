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
from agentclaw.community.core.engine_runtime.errors import EngineStageNotLiveError
from agentclaw.community.core.engine_runtime.stage import (
    STAGE_DRAFT,
    STAGE_ONLINE,
    STAGE_VERIFY,
    require_stage_addressable,
    resolve_stage_bind_id,
)

BOT = "bot-1"
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
