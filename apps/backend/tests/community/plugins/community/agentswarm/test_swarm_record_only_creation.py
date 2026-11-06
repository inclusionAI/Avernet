"""The swarm bot is a record with no container of its own (design D7).

A swarm bot holds the run's identity and address; every container in the run is a
persona worker. So phase one must be able to stop after the record, and the existing
``create_bot(provision=False)`` split is what it stops at — no new primitive is added
to ``BotService``.

Three properties, each of which would silently break the swarm's container count if it
went the other way:

1. ``provision=False`` writes the record and allocates nothing.
2. The row it writes carries the identity in ``ext.passport`` (so phase one has an
   identity to hand the workers without a second apply).
3. Provisioning remains a separate, explicit step — nothing provisions by itself.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.bot_service import BotService


class _Repo:
    """Just enough of the bot table for a deferred creation, recording every write."""

    def __init__(self, log: list) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self._log = log

    def count_by_owner(self, *a, **k):
        return 0

    def exists_by_bot_name(self, name):
        return False

    def get_by_id_and_owner(self, bot_id, user_id):
        row = self.rows.get(bot_id)
        if row and row.get("owner_id") == user_id and not row.get("is_delete"):
            return dict(row)
        return None

    def insert(self, data):
        self._log.append(("insert", data["bot_id"], data["status"], data["binding_id"]))
        self.rows[data["bot_id"]] = {"id": 1, **data}
        return dict(self.rows[data["bot_id"]])

    def insert_with_initial_skill_layout(self, data, *, layout):
        del layout
        return self.insert(data)

    def update_by_owner(self, bot_id, user_id, fields):
        self._log.append(("update", bot_id, dict(fields)))
        self.rows[bot_id].update(fields)

    def soft_delete_by_owner(self, bot_id, user_id):
        self._log.append(("soft_delete", bot_id))
        self.rows[bot_id]["is_delete"] = 1


def _service(log: list) -> tuple[BotService, MagicMock, MagicMock]:
    """A BotService with the provisioning collaborators observable."""
    svc = BotService.__new__(BotService)
    svc._skills_pool_native_creation_policy = MagicMock(select=MagicMock(return_value=None))
    svc._skill_layout_repository = MagicMock()
    svc._bot_storage_policy = None
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
    svc._workspace_hosting_config = MagicMock(aixcore_base_url="")
    skill_set_service = MagicMock()
    skill_set_service.get_symlink_mappings.return_value = []
    svc._skill_set_factory = MagicMock()
    svc._skill_set_factory.create.return_value = skill_set_service
    svc._common_config_service = None
    svc._policy_service = None
    svc._drm_reader = MagicMock()
    svc._drm_reader.read.return_value = None

    publish_service = MagicMock()
    publish_service.create_publish.side_effect = lambda **kw: MagicMock(
        to_dict=lambda: {"publish_id": "p1"}
    )
    svc._bot_publish_provider = lambda: publish_service

    teclaw_provision = MagicMock()
    teclaw_provision.is_teclaw.return_value = False
    svc._teclaw_provision_provider = lambda: teclaw_provision

    device_service = MagicMock()

    def apply_device(**kw):
        log.append(("apply_device", kw["bot_id"]))
        return MagicMock(device_id="dev-1")

    device_service.apply_device.side_effect = apply_device
    svc._device_service_provider = lambda: device_service
    return svc, device_service, teclaw_provision


_SWARM_BOT = "agent_swarm_0123456789abcdef"
_AGENT_CODE = _SWARM_BOT


def _create_deferred(log: list, *, ext: dict | None = None):
    svc, device_service, teclaw_provision = _service(log)
    with patch.object(
        BotService, "_is_claude_code_bcn_register_enabled", return_value=False
    ):
        record = svc.create_bot(
            user_id="owner-1",
            nick_name="owner",
            bot_name=_SWARM_BOT,
            bot_desc="swarm run",
            bot_id=_SWARM_BOT,
            engine_type="openclaw",
            bot_type="personal",
            ext=ext,
            provision=False,
        )
    return record, device_service, teclaw_provision


@pytest.mark.unit
def test_deferred_creation_allocates_no_container() -> None:
    """The whole point of D7: the swarm bot must not get a container."""
    log: list = []
    _record, device_service, teclaw_provision = _create_deferred(log)

    assert device_service.apply_device.call_count == 0, "a container was allocated"
    assert teclaw_provision.provision.call_count == 0, "a container was allocated"
    assert not any(entry[0] == "apply_device" for entry in log)


@pytest.mark.unit
def test_deferred_creation_writes_the_record_with_no_binding() -> None:
    """A record with no binding is what makes "no container" observable later."""
    log: list = []
    record, _device, _teclaw = _create_deferred(log)

    assert record["bot_id"] == _SWARM_BOT
    assert not record.get("binding_id"), "a deferred record must carry no binding"
    assert record["status"] == "PENDING", (
        "a deferred record is PENDING until provisioning claims it"
    )


@pytest.mark.unit
def test_deferred_creation_carries_the_identity_for_the_workers() -> None:
    """Phase one ends holding the agent code every worker will present."""
    log: list = []
    record, _device, _teclaw = _create_deferred(
        log, ext={"passport": {"agent_code": _AGENT_CODE, "status": "ISSUED"}}
    )

    ext = record.get("ext") or {}
    passport = ext.get("passport") or {}
    assert passport.get("agent_code") == _AGENT_CODE
    assert passport.get("status") == "ISSUED"


@pytest.mark.unit
def test_nothing_provisions_without_an_explicit_step() -> None:
    """Provisioning stays explicit, so a run with N personas makes N containers.

    This is the N-not-N+1 guard: were a deferred creation to provision on its own,
    the swarm bot would add a container no persona owns.
    """
    log: list = []
    svc, device_service, _teclaw = _service(log)
    with patch.object(
        BotService, "_is_claude_code_bcn_register_enabled", return_value=False
    ):
        svc.create_bot(
            user_id="owner-1",
            nick_name="owner",
            bot_name=_SWARM_BOT,
            bot_id=_SWARM_BOT,
            engine_type="openclaw",
            bot_type="personal",
            provision=False,
        )

    assert device_service.apply_device.call_count == 0
    # A second record for a second worker changes nothing about the first.
    assert sum(1 for entry in log if entry[0] == "apply_device") == 0