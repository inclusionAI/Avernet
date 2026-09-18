"""Public-local compatibility when the enterprise integration is disabled.

These flows do not claim employee-platform or message-consumer coverage.  The
execution-identity case exercises the real HTTP, application-service, and Bot
repository boundary without inventing enterprise AgentPass credentials.
"""
from tests.community.framework.flow import FlowCase, FlowStep

DIGITAL_EMPLOYEE_FLOWS = [
    FlowCase(
        name="digital-employee-disabled-ui-and-auth-boundary",
        covers=["digital_employee"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/digital-employee/settings",
                expect={"success": True, "data": {"enabled": False, "site_url": ""}},
            ),
            FlowStep(
                method="GET",
                path="/openapi/v1/bots/metadata/digital-employees",
                query={"creatorNo": "e2e_user"},
                expect_status=401,
            ),
        ],
    ),
    FlowCase(
        name="execution-identity-rejects-missing-service-bot",
        covers=["execution_identity"],
        steps=[
            FlowStep(
                method="POST",
                path="/api/bots/bot_does_not_exist_e2e/admin/execution-identity",
                body={
                    "owner_id": "e2e_user",
                    "execution_workno": "digital_employee_e2e",
                    "identity_type": "DIGITAL_EMPLOYEE",
                    "action": "reissue",
                },
                expect_status=200,
                expect={"success": False, "error_code": 404, "data": None},
            ),
        ],
    ),
]
