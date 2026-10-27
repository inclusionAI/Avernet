"""Discover a persisted expert through the public contract."""

from tests.community.framework.flow import FlowCase, FlowStep

EXPERT_MARKET_FLOWS = [
    FlowCase(
        name="expert-market-discovery",
        covers=["expert_market"],
        steps=[
            FlowStep(
                method="GET",
                path="/openapi/v1/experts",
                query={"keyword": "PRD"},
                expect={"data": {"total": 1}},
                extract={"expert_id": "data.items.0.expert_id"},
            ),
            FlowStep(
                method="GET",
                path="/openapi/v1/experts/categories",
                expect={"data": ["内容"]},
            ),
            FlowStep(
                method="GET",
                path="/openapi/v1/experts/{expert_id}",
                expect={
                    "data": {
                        "identity": [{"type": "AGENTS.md", "content": "原文正文"}],
                        "creation_count": 0,
                    }
                },
            ),
            FlowStep(
                method="GET",
                path="/openapi/v1/experts",
                query={"keyword": "no-matching-expert"},
                expect={"data": {"total": 0, "items": []}},
            ),
        ],
    )
]
