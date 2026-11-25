"""Acceptance bootstrap must satisfy the production runtime metadata checks."""

import pytest
from sqlalchemy import text

from agentclaw.community.core.bot_management.owner_handoff.contracts import HandoffError
from agentclaw.community.core.bot_management.owner_handoff.preflight import OwnerHandoffPreflight
from tests.community.acceptance.bot_ownership_sync.live_case import OwnershipCase
from tests.community.core.bot_management.test_owner_handoff import world  # noqa: F401
from tests.community.core.bot_management.test_resource_handoff import persisted  # noqa: F401


def test_acceptance_bootstrap_supplies_real_binding_to_preflight(world):
    # Reproduce the old live seed: a service Bot with no runtime binding.
    world.bots.update_by_owner("service", "A", {"binding_id": None, "device_id": None})
    preflight = OwnerHandoffPreflight(world.passport, world.devices, world.publishes)
    bot = world.bots.get_by_pk(world.bot["id"])
    with pytest.raises(HandoffError, match="HANDOFF_RUNTIME_TARGET_REQUIRED"):
        preflight.check(bot)

    case = OwnershipCase.__new__(OwnershipCase)
    case.suffix, case.entity_id, case.old_owner = "bootstrap", "A", "A"
    case.bot_pk = bot["id"]

    def sql(statement, **params):
        with world.db.orm_session() as session:
            session.execute(text(statement), params)

    case.sql = sql
    case.seed_runtime_binding()
    bot = world.bots.get_by_pk(bot["id"])
    binding = world.devices.get_by_id(bot["binding_id"])
    assert binding.entity_id == bot["entity_id"]
    assert binding.device_id == bot["device_id"]
    assert binding.device_provider == "local"
    assert binding.status == "ACTIVE" and binding.env == "dev"
    preflight.check(bot)
    assert not world.passport.issues and not world.runtime.calls

    # A failed runtime remains rejected; the fixture does not bypass preflight.
    with world.db.orm_session() as session:
        session.execute(text(
            "UPDATE ac_entity_device_binding SET status='FAILED' WHERE id=:id"
        ), {"id": binding.id})
    with pytest.raises(HandoffError, match="HANDOFF_RUNTIME_TARGET_INVALID"):
        preflight.check(bot)
