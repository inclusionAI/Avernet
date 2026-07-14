"""AuthRelationship plugin protocol.

Abstracts the authorization relationship management between users and agents
to external auth systems (e.g. AceAgent).
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from agentclaw.community.plugin_api.base import Plugin


class AuthRelationshipError(Exception):
    """AuthRelationship service call exception."""
    pass


def validate_auth_relationship_target_env(target_env: str) -> None:
    """Fail closed before routing an explicit-environment AceAgent call."""
    if target_env not in {"pre", "prod"}:
        raise ValueError("target_env must be pre or prod")


@runtime_checkable
class AuthRelationshipPlugin(Plugin, Protocol):
    """Plugin for user-agent authorization relationship management."""

    def create_relationship(
        self,
        work_no: str,
        agent_code: str,
        operator_work_no: str,
        operator_name: str,
        source: str = "tcauthmng",
        description: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a USER_AUTH relationship between a user and an agent.

        Returns the created auth record ID on success, or None on failure.
        """
        ...

    def create_relationship_for_env(
        self,
        *,
        target_env: str,
        work_no: str,
        agent_code: str,
        operator_work_no: str,
        operator_name: str,
        source: str = "tcauthmng",
        description: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a relationship through the explicitly selected environment."""
        ...

    def query_relationships(
        self,
        agent_code: str,
        source: str = "tcauthmng",
        work_no: str | None = None,
        page_num: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Query authorization relationships for an agent.

        Returns a list of AuthRelationshipResult dicts.
        """
        ...

    def query_relationships_for_env(
        self,
        *,
        target_env: str,
        agent_code: str,
        source: str = "tcauthmng",
        work_no: str | None = None,
        page_num: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Query relationships through the explicitly selected environment."""
        ...

    def delete_relationship(
        self,
        auth_id: int,
        operator_work_no: str | None = None,
        operator_name: str | None = None,
    ) -> bool:
        """Delete an authorization relationship by ID.

        Returns True on success.
        """
        ...

    def delete_relationships_by_agent(
        self,
        agent_code: str,
        source: str = "tcauthmng",
    ) -> int:
        """Delete all user-agent relationships for an agent.

        Returns the number of relationships deleted.
        """
        ...
