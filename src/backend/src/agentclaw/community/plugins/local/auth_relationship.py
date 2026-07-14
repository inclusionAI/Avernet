"""Local mock AuthRelationshipPlugin."""
from __future__ import annotations

from typing import Any

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth_relationship import (
    AuthRelationshipPlugin,
    validate_auth_relationship_target_env,
)
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam

logger = get_logger()


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="no I/O",
)
class LocalAuthRelationshipPlugin(MockSeam, AuthRelationshipPlugin):
    """No-op auth relationship for local development."""

    def create_relationship(
        self,
        work_no: str,
        agent_code: str,
        operator_work_no: str,
        operator_name: str,
        source: str = "tcauthmng",
        description: str | None = None,
    ) -> dict[str, Any] | None:
        logger.info(
            "[LocalAuthRelationship] create_relationship: work_no=%s, agent_code=%s, source=%s",
            work_no, agent_code, source,
        )
        return {"auth_id": 0}

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
        validate_auth_relationship_target_env(target_env)
        return self.create_relationship(
            work_no=work_no,
            agent_code=agent_code,
            operator_work_no=operator_work_no,
            operator_name=operator_name,
            source=source,
            description=description,
        )

    def query_relationships(
        self,
        agent_code: str,
        source: str = "tcauthmng",
        work_no: str | None = None,
        page_num: int = 1,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        logger.info(
            "[LocalAuthRelationship] query_relationships: agent_code=%s, work_no=%s",
            agent_code, work_no,
        )
        return []

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
        validate_auth_relationship_target_env(target_env)
        return self.query_relationships(
            agent_code=agent_code,
            source=source,
            work_no=work_no,
            page_num=page_num,
            page_size=page_size,
        )

    def delete_relationship(
        self,
        auth_id: int,
        operator_work_no: str | None = None,
        operator_name: str | None = None,
    ) -> bool:
        logger.info(
            "[LocalAuthRelationship] delete_relationship: auth_id=%s",
            auth_id,
        )
        return True

    def delete_relationships_by_agent(
        self,
        agent_code: str,
        source: str = "tcauthmng",
    ) -> int:
        logger.info(
            "[LocalAuthRelationship] delete_relationships_by_agent: agent_code=%s",
            agent_code,
        )
        return 0
