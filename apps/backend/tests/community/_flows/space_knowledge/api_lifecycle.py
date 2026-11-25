"""Real SQLite membership + HTTP closed-default flow; no simulated sync success.

This covers admission and the deliberately unavailable producer only. Successful
canonical creation/recovery requires a deployment provider and is verified by
the separately reported internal fixed-combination ASGI suite, not this flow.
"""

from tests.community.framework.flow import FlowCase, FlowStep

SPACE_KNOWLEDGE_FLOWS = [
    FlowCase(
        name="space-knowledge-real-membership-unavailable-sync",
        covers=["space_knowledge"],
        steps=[
            FlowStep(
                method="POST",
                path="/local/sql/execute",
                body={
                    "statements": [
                        {
                            "sql": "INSERT INTO ac_space (id,space_code,space_type,name,env,created_by,updated_by) VALUES (71007,'kb-flow','TEAM','Knowledge flow','dev','founder','founder')"
                        },
                        {
                            "sql": "INSERT INTO ac_space_member (space_id,user_id,role,env,created_by) VALUES (71007,'e2e_user','MEMBER','dev','founder')"
                        },
                    ]
                },
                expect_status=200,
            ),
            FlowStep(
                method="POST",
                path="/api/spaces/71007/knowledge-bases",
                body={"request_id": "stable-flow-intent", "name": "Knowledge"},
                expect_status=503,
                expect={"detail": {"code": "SPACE_SYNC_UNAVAILABLE"}},
            ),
            FlowStep(
                method="GET",
                path="/api/spaces/71007/knowledge-bases/original-kb",
                expect_status=503,
                expect={"detail": {"code": "SPACE_SYNC_UNAVAILABLE"}},
            ),
            FlowStep(
                method="GET",
                path="/api/spaces/71007/knowledge-bases",
                expect_status=503,
                expect={"detail": {"code": "SPACE_SYNC_UNAVAILABLE"}},
            ),
            FlowStep(
                method="POST",
                path="/local/sql/execute",
                body={
                    "statements": [
                        {
                            "sql": "UPDATE ac_space_member SET status='INACTIVE' WHERE space_id=71007 AND user_id='e2e_user'"
                        }
                    ]
                },
                expect_status=200,
            ),
            FlowStep(
                method="POST",
                path="/api/spaces/71007/knowledge-bases",
                body={"request_id": "stable-flow-intent", "name": "Knowledge"},
                expect_status=403,
                expect={"detail": {"code": "SPACE_MEMBER_REQUIRED"}},
            ),
        ],
    ),
]
