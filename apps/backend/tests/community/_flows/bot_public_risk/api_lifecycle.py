"""Original-check risk lifecycle shared by in-process and live HTTP runners."""

from tests.community.framework.flow import FlowCase, FlowStep

RISK_FLOWS = [
    FlowCase(
        name="risk-original-report-pass",
        covers=["bot_public_risk"],
        steps=[
            FlowStep(
                method="POST",
                path="/api/bots/risk-flow/public/risk-checks",
                headers={"x-user-id": "risk-owner"},
                body={"client_request_id": "risk-request", "public": "1", "permission_owner": "owner", "friend_approval": "0"},
                expect={"success": True},
                extract={"risk_id": "data.risk_check_id"},
            ),
            FlowStep(
                method="POST",
                path="/api/bots/risk-flow/public/risk-checks",
                headers={"x-user-id": "risk-owner"},
                body={"client_request_id": "risk-request", "public": "1", "permission_owner": "owner", "friend_approval": "0"},
                expect={"success": True},
                extract={"replayed_id": "data.risk_check_id"},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/risk-flow/public/risk-checks/current",
                headers={"x-user-id": "risk-owner"},
                expect={"success": True, "data": {"phase": "CHECKING"}},
                extract={"current_id": "data.risk_check_id"},
                poll_timeout_sec=10,
                poll_interval_sec=0.1,
            ),
            FlowStep(
                method="POST",
                path="/api/bots/risk-flow/public/risk-checks/{risk_id}/refresh",
                headers={"x-user-id": "risk-owner"},
                expect={"success": True, "data": {"outcome": "UPDATED", "record": {"security_status": "PASS", "phase": "ADVANCING"}}},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/risk-flow/public/risk-checks/{risk_id}",
                headers={"x-user-id": "risk-owner"},
                expect={"success": True, "data": {"security_status": "PASS"}},
                extract={"read_id": "data.risk_check_id"},
            ),
        ],
    )
]
