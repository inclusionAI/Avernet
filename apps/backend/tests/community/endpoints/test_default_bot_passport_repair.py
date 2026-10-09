"""Endpoint coverage for the default Bot Passport repair operation."""

from agentclaw.community.api.default_bot_passport_repair_service import (
    DefaultBotPassportRepairServiceProtocol,
)
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_PATH = "/api/bots/repair-default-passport-for-others"
_ADMIN = "100000"


class _StubRepairService:
    def repair(self, **kwargs):
        return {
            "target_user_id": kwargs["target_user_id"],
            "bot_id": "default",
            "action": "repaired",
            "target_env": kwargs["target_env"],
            "passport": {
                "status": "ISSUED",
                "agent_code": "agent-172168",
                "credential_id": "credential-172168",
                "token_present": True,
                "source": "applied",
            },
            "owner_relationship": {
                "verified": True,
                "created": True,
                "auth_id": 42,
            },
            "database": {"ext_agent_code_verified": True},
            "runtime": {
                "restart_required": True,
                "restart_environment": kwargs["target_env"],
            },
        }


def _seed_admin_with_repair_service(world):
    make_staff_user(world, user_id=_ADMIN)
    world.injector.binder.bind(
        DefaultBotPassportRepairServiceProtocol,
        to=_StubRepairService(),
        scope=None,
    )


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="repair_prod_default_bot",
    seed=_seed_admin_with_repair_service,
    input=CaseInput(
        headers={"x-user-id": _ADMIN, "X-Request-ID": "repair-test"},
        json_body={"target_user_id": "172168", "target_env": "prod"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "target_user_id": "172168",
                "bot_id": "default",
                "target_env": "prod",
                "passport": {"status": "ISSUED", "token_present": True},
                "owner_relationship": {"verified": True},
                "runtime": {"restart_required": True},
            },
        },
    ),
)
def repair_prod_default_bot():
    """A super admin can repair one prod default Bot from pre deployment."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="reject_invalid_target_env",
    seed=_seed_admin_with_repair_service,
    input=CaseInput(
        headers={"x-user-id": _ADMIN},
        json_body={"target_user_id": "172168", "target_env": "staging"},
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def reject_invalid_target_env():
    """Only the explicit pre and prod targets are accepted."""
