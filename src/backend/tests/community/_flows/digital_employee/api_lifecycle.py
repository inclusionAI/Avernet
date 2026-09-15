"""Public-local compatibility when the enterprise integration is disabled.

This flow does not claim employee-platform or message-consumer coverage.
"""
from tests.community.framework.flow import FlowCase, FlowStep

DIGITAL_EMPLOYEE_FLOWS = [FlowCase(
    name="digital-employee-disabled-ui-and-auth-boundary", covers=["digital_employee"],
    steps=[
        FlowStep(method="GET", path="/api/digital-employee/settings",
                 expect={"success": True, "data": {"enabled": False, "site_url": ""}}),
        FlowStep(method="GET", path="/openapi/v1/bots/metadata/digital-employees",
                 query={"creatorNo": "e2e_user"}, expect_status=401),
    ],
)]
