"""Real operations policy/repository/repair; only queue admission is recorded.

A queued receipt does not prove external delivery. Live Singlebox stories verify
that the same HTTP request reaches real BCS and refreshes its authorization.
"""

from agentclaw.community.core.bot_management.ownership_sync.repair import (
    OwnershipSyncRepairService,
)
from agentclaw.community.core.repository.protocols.bot.ownership_sync import (
    OwnershipSyncReadRepository,
)
from agentclaw.community.di.bot_manager_sync_config import ManagerSyncConfig
from agentclaw.community.kernel.bot_manager_sync import SubmissionResult, SyncReason
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


class RecordingAdmission:
    def __init__(self):
        self.calls = []

    def request_ownership_sync(self, **kwargs):
        self.calls.append(kwargs)
        return SubmissionResult.QUEUED


def _register(forbidden):
    body = {"bot_pk": 1}

    def seed(world):
        bot = make_bot(world, bot_id="repair-service", owner_id="original-owner")
        body["bot_pk"] = bot["id"]
        admission = RecordingAdmission()
        repair = OwnershipSyncRepairService(
            world.get(OwnershipSyncReadRepository), admission
        )
        # Keep the real composition provider, configured admin scope, HTTP
        # policy and database. Only async queue admission is a recording edge.
        world.injector.binder.bind(OwnershipSyncRepairService, to=repair, scope=None)
        world.injector.binder.bind(
            ManagerSyncConfig,
            to=ManagerSyncConfig(env=get_current_env()),
            scope=None,
        )
        world.injector.binder.bind(RecordingAdmission, to=admission, scope=None)

    def assert_admission(response, world):
        calls = world.get(RecordingAdmission).calls
        if forbidden:
            assert calls == []
        else:
            assert len(calls) == 1
            assert calls[0] == {
                "bot_pk": body["bot_pk"],
                "env": get_current_env(),
                "avernet_tenant": "teamclaw",
                "reason": SyncReason.REPAIR,
            }
            assert response.json()["data"]["delivery_confirmed"] is False

    endpoint_test(
        method="POST",
        path="/api/bots/owner-handoffs/bcn-repair",
        scenario="non_admin_forbidden" if forbidden else "admin_admitted",
        input=CaseInput(
            json_body=body,
            headers={"x-user-id": "original-owner" if forbidden else "100000"},
        ),
        seed=seed,
        expect=ExpectError(status=403, json_contains={"detail": "ADMIN_REQUIRED"})
        if forbidden
        else ExpectSuccess(
            status=202,
            json_contains={
                "success": True,
                "data": {
                    "bcn_bot_id": "repair-service:original-owner",
                    "submission": "queued",
                    "delivery_confirmed": False,
                },
            },
        ),
        extra_assertions=(assert_admission,),
    )(lambda: None)


_register(False)
_register(True)
