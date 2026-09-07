"""``create_bot(provision=False)`` + ``provision_bot()`` == ``create_bot()`` (W8).

The golden property: the record written and the collaborator calls made, in
order, are the same whether provisioning runs inline or is deferred. Proved on
recorded call order against the real ``BotService`` code, with the repository
and the device / publish collaborators stood in.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotService,
    BotServiceError,
)
from agentclaw.community.core.devices.models import DeviceBindingStatus
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord


class _Repo:
    """The bot table, as much of it as creation touches, recording writes."""

    def __init__(self, log: list) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self._log = log
        self.stale_claims: set[str] = set()

    def count_by_owner(self, *a, **k):
        return 0

    def exists_by_bot_name(self, name):
        return False

    def get_by_id_and_owner(self, bot_id, user_id):
        row = self.rows.get(bot_id)
        return dict(row) if row and row.get("owner_id") == user_id and not row.get("is_delete") else None

    def insert(self, data):
        self._log.append(("insert", data["bot_id"], data["status"], data["binding_id"]))
        self.rows[data["bot_id"]] = {"id": 1, **data}
        return dict(self.rows[data["bot_id"]])

    def update_by_owner(self, bot_id, user_id, fields):
        self._log.append(("update", bot_id, dict(fields)))
        self.rows[bot_id].update(fields)

    def soft_delete_by_owner(self, bot_id, user_id):
        self._log.append(("soft_delete", bot_id))
        self.rows[bot_id]["is_delete"] = 1

    def claim_provisioning(self, bot_id, user_id, *, reclaim_after_seconds=None):
        row = self.rows.get(bot_id)
        if not row or row.get("owner_id") != user_id or row.get("is_delete"):
            return False
        if row.get("binding_id"):
            return False
        if row.get("status") == "PENDING":
            row["status"] = "PROVISIONING"
            self._log.append(("claim", bot_id))
            return True
        # The real repository judges staleness on the DB clock; the fake is
        # told which claims are abandoned.
        if row.get("status") == "PROVISIONING" and reclaim_after_seconds is not None:
            if bot_id in self.stale_claims:
                self.stale_claims.discard(bot_id)
                self._log.append(("reclaim", bot_id))
                return True
        return False


def _device_result() -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=7, entity_id="u1", entity_type="staff", device_id="dev-7", device_provider="arca",
        env="dev", device_props={}, status=DeviceBindingStatus.ACTIVE.value, apply_reason=None,
        applied_by="u1", release_reason=None, released_by=None, released_at=None,
        last_alive_at=None, gmt_create=None, gmt_modified=None,
    )


def _service(log: list, *, teclaw: bool = False) -> BotService:
    svc = BotService.__new__(BotService)
    svc._bot_app_grant_provider = lambda: MagicMock()
    svc._repository = _Repo(log)
    svc._allocation_config = SimpleNamespace(mode="multi", max_devices_per_entity=10)
    svc._passport_plugin = MagicMock()
    svc._oss_record_repo = MagicMock()
    svc._device_binding_repo = MagicMock()
    svc._device_binding_repo.list_by_owner.return_value = []
    svc._cleanup_service = MagicMock()
    svc._bcn_service = MagicMock()
    svc._bot_publish_repo = MagicMock()
    svc._template_service = MagicMock()
    svc._workspace_hosting_service = MagicMock()
    svc._workspace_hosting_config = MagicMock(aixcore_base_url="", aixcore_base_url_pre="")
    skill_set_service = MagicMock()
    skill_set_service.get_symlink_mappings.return_value = []
    svc._skill_set_factory = MagicMock()
    svc._skill_set_factory.create.return_value = skill_set_service
    svc._common_config_service = None
    svc._policy_service = None
    svc._drm_reader = MagicMock()
    svc._drm_reader.read.return_value = None

    publish_service = MagicMock()

    def create_publish(**kw):
        log.append(("publish", kw["source_bot_id"], kw["owner_id"]))
        return MagicMock(to_dict=lambda: {"publish_id": "p1"})

    publish_service.create_publish.side_effect = create_publish

    def record_draft_artifact(**kw):
        log.append(("draft_artifact", kw["bot_id"], kw["artifact"]))

    publish_service.record_draft_artifact.side_effect = record_draft_artifact
    svc._bot_publish_provider = lambda: publish_service

    teclaw_provision = MagicMock()
    teclaw_provision.is_teclaw.return_value = teclaw

    def provision(*, bot, owner_id):
        log.append(("teclaw_provision", bot["bot_id"], owner_id, bot["status"]))
        return SimpleNamespace(
            binding_id=9, device_id="BOT-9", status="ACTIVE", config_artifact={"schema_version": 4}
        )

    teclaw_provision.provision.side_effect = provision
    svc._teclaw_provision_provider = lambda: teclaw_provision

    device_service = MagicMock()

    def apply_device(**kw):
        log.append(("apply_device", kw["bot_id"], kw["owner_id"], kw["engine"]))
        return _device_result()

    device_service.apply_device.side_effect = apply_device
    svc._device_service_provider = lambda: device_service
    return svc


_ARGS = dict(user_id="u1", nick_name="nick", bot_name="golden", bot_desc="d", bot_id="g-1")


def _inline(**kw) -> tuple[list, dict]:
    log: list = []
    svc = _service(log, **kw)
    with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=False):
        record = svc.create_bot(**_ARGS, engine_type="teclaw" if kw.get("teclaw") else "openclaw", bot_type="service")
    return log, record


def _deferred(**kw) -> tuple[list, dict, dict, BotService]:
    log: list = []
    svc = _service(log, **kw)
    with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=False):
        first = svc.create_bot(
            **_ARGS, engine_type="teclaw" if kw.get("teclaw") else "openclaw", bot_type="service", provision=False
        )
        record = svc.provision_bot("g-1", "u1", "nick")
    return log, first, record, svc


@pytest.mark.unit
@pytest.mark.parametrize("teclaw", [False, True], ids=["device_service", "teclaw_container"])
def test_deferred_provisioning_makes_the_same_writes_in_the_same_order(teclaw: bool) -> None:
    inline_log, inline_record = _inline(teclaw=teclaw)
    deferred_log, first, deferred_record, _ = _deferred(teclaw=teclaw)

    # The record-only step: PENDING, no binding.
    assert first["status"] == "PENDING" and first["binding_id"] is None
    # The golden property, the deferred path's durable claim aside: it is the
    # one write the inline path has no need for (nothing re-enters create_bot
    # mid-provisioning — the record's existence short-circuits it).
    assert [w for w in deferred_log if w[0] != "claim"] == inline_log
    assert ("claim", "g-1") in deferred_log
    assert deferred_record == inline_record
    assert deferred_record["binding_id"] == (9 if teclaw else 7)
    assert deferred_record["status"] == "ACTIVE"
    assert deferred_record["publish"] == {"publish_id": "p1"}


@pytest.mark.unit
def test_provision_bot_is_idempotent_on_a_bound_record() -> None:
    log, _first, record, svc = _deferred()
    writes_before = len(log)
    again = svc.provision_bot("g-1", "u1", "nick")
    assert len(log) == writes_before
    assert again["binding_id"] == record["binding_id"]


@pytest.mark.unit
def test_provision_bot_refuses_an_unknown_bot() -> None:
    svc = _service([])
    with pytest.raises(BotServiceError):
        svc.provision_bot("nope", "u1", "nick")


@pytest.mark.unit
def test_a_second_call_while_the_claim_is_held_does_not_provision_again() -> None:
    """The creation job's queue is at-least-once: a re-claimed task must not
    allocate a second device while the first call is still mid-flight."""
    log: list = []
    svc = _service(log)
    with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=False):
        svc.create_bot(**_ARGS, engine_type="openclaw", bot_type="service", provision=False)
    # Someone else holds the claim: the record reads PROVISIONING and unbound.
    assert svc._repository.claim_provisioning("g-1", "u1") is True
    writes_before = len(log)
    with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=False):
        record = svc.provision_bot("g-1", "u1", "nick")
    assert len(log) == writes_before, "the second call allocated"
    assert record["binding_id"] is None and record["status"] == "PROVISIONING"


@pytest.mark.unit
def test_an_abandoned_claim_is_taken_over_rather_than_stranding_the_record() -> None:
    """A holder that died mid-provisioning leaves PROVISIONING and unbound
    with nothing to release it; a later call takes the claim over."""
    log: list = []
    svc = _service(log)
    with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=False):
        svc.create_bot(**_ARGS, engine_type="openclaw", bot_type="service", provision=False)
    assert svc._repository.claim_provisioning("g-1", "u1") is True
    svc._repository.stale_claims.add("g-1")
    with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=False):
        record = svc.provision_bot("g-1", "u1", "nick")
    assert ("reclaim", "g-1") in log
    assert record["binding_id"] is not None


@pytest.mark.unit
def test_a_failed_provisioning_releases_the_claim_when_the_record_survives() -> None:
    log: list = []
    svc = _service(log)
    with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=False):
        svc.create_bot(**_ARGS, engine_type="openclaw", bot_type="personal", provision=False)
    svc._device_service_provider = lambda: (_ for _ in ()).throw(RuntimeError("no device service"))
    with pytest.raises(BotServiceError):
        with patch.object(BotService, "_is_claude_code_bcn_register_enabled", return_value=False):
            svc.provision_bot("g-1", "u1", "nick")
    # The unexpected error soft-deletes the record inside step 2, so nothing
    # is left to release; a record that survived would read PENDING again.
    assert svc._repository.rows["g-1"]["is_delete"] == 1
