"""Real HTTP management admission with the deployment capability disabled.

Does not claim remote-provider success: those paths have separate transport,
repository, contract and internal paired suites.
"""

from tests.community.framework.flow import FlowCase, FlowStep

KNOWLEDGE_MANAGEMENT_FLOWS = [
    FlowCase(
        name="knowledge-management-closed-default",
        covers=["knowledge_management"],
        steps=[
            FlowStep(
                method=method,
                path=path,
                expect_status=503,
                expect={"detail": {"code": "KNOWLEDGE_INTEGRATION_UNAVAILABLE"}},
            )
            for method, path in [
                ("GET", "/api/knowledge/spaces/71007/knowledge-bases"),
                ("GET", "/api/knowledge/spaces/71007/knowledge-bases/kb-test/sources"),
            ]
        ],
    ),
]
