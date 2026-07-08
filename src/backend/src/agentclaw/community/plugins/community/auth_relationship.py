"""CommunityAuthRelationshipPlugin — community user↔agent relationship registry.

The corp ``AuthRelationshipPlugin`` records advisory authorization edges in the
AceAgent governance system. Every consumer is fire-and-forget (the return value
never gates control flow), and a community deployment's own ownership model
already encodes "who owns what" — so there is no external registry to talk to.

This is a real, deployable implementation (not a ``MockSeam`` test double) that
succeeds vacuously: nothing reads these returns for behavior, so a no-op is the
correct community semantics, not a loss of function.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin


class CommunityAuthRelationshipPlugin(AuthRelationshipPlugin):
    """No external registry: every operation succeeds vacuously."""

    def create_relationship(
        self,
        work_no: str,
        agent_code: str,
        operator_work_no: str,
        operator_name: str,
        source: str = "tcauthmng",
        description: str | None = None,
    ) -> dict[str, Any] | None:
        return {"auth_id": 0}

    def query_relationships(
        self,
        agent_code: str,
        source: str = "tcauthmng",
        work_no: str | None = None,
        page_num: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        return []

    def delete_relationship(
        self,
        auth_id: int,
        operator_work_no: str | None = None,
        operator_name: str | None = None,
    ) -> bool:
        return True

    def delete_relationships_by_agent(
        self,
        agent_code: str,
        source: str = "tcauthmng",
    ) -> int:
        return 0
