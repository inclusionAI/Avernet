"""User-list API lifecycle flows for LOCAL SQLite."""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep


USER_LIST_FLOWS: list[FlowCase] = [
    FlowCase(
        name="user-list-correction-and-membership-lifecycle",
        covers=["user_list"],
        steps=[
            FlowStep(
                method="PUT",
                path="/api/v1/user-lists/correct",
                body={
                    "entity_id": "e2e_user",
                    "user_list_type": "caller_identity",
                    "in_whitelist": True,
                },
                expect={"success": True, "data": {"in_whitelist": True}},
            ),
            FlowStep(
                method="GET",
                path="/api/v1/user-lists/check",
                query={
                    "entity_id": "e2e_user",
                    "user_list_type": "caller_identity",
                },
                expect={"success": True, "data": {"in_whitelist": True}},
            ),
            FlowStep(
                method="PUT",
                path="/api/v1/user-lists/correct",
                body={
                    "entity_id": "e2e_user",
                    "user_list_type": "caller_identity",
                    "in_whitelist": False,
                },
                expect={"success": True, "data": {"in_whitelist": False}},
            ),
            FlowStep(
                method="GET",
                path="/api/v1/user-lists/check",
                query={
                    "entity_id": "e2e_user",
                    "user_list_type": "caller_identity",
                },
                expect={"success": True, "data": {"in_whitelist": False}},
            ),
        ],
    ),
]
