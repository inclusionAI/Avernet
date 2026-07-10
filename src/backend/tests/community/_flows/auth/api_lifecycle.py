"""Auth business flows shared by in-process E2E and live acceptance."""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep


AUTH_LIFECYCLE_FLOWS: list[FlowCase] = [
    FlowCase(
        name="auth-identity-operator-and-entity-policy",
        covers=["auth"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/v1/access/check",
                expect={
                    "success": True,
                    "data": {"staffNo": "e2e_user"},
                },
            ),
            FlowStep(
                method="POST",
                path="/api/v1/access/allow",
                body={
                    "entity_id": "e2e_user",
                    "entity_type": "staff",
                },
                expect={
                    "success": True,
                    "data": {
                        "entity_id": "e2e_user",
                        "entity_type": "staff",
                    },
                },
            ),
            FlowStep(
                method="GET",
                path="/api/bots",
                query={
                    "entity_id": "e2e_user",
                    "entity_type": "staff",
                },
                expect={"success": True},
            ),
        ],
    ),
    FlowCase(
        name="auth-missing-identity-is-rejected",
        covers=["auth"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/v1/access/check",
                headers={"x-user-id": ""},
                expect_status=401,
            ),
        ],
    ),
]
