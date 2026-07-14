"""Unit tests for the community CommunityAuthRelationshipPlugin (B4 T8)."""
from __future__ import annotations

import pytest

from agentclaw.community.plugins.community.auth_relationship import (
    CommunityAuthRelationshipPlugin,
)


def test_create_returns_vacuous_success():
    plugin = CommunityAuthRelationshipPlugin()
    result = plugin.create_relationship(
        work_no="u1", agent_code="a1", operator_work_no="u1", operator_name="U1"
    )
    assert result == {"auth_id": 0}


def test_query_returns_empty():
    assert CommunityAuthRelationshipPlugin().query_relationships("a1") == []


def test_delete_returns_true():
    assert CommunityAuthRelationshipPlugin().delete_relationship(123) is True


def test_delete_by_agent_returns_zero():
    assert CommunityAuthRelationshipPlugin().delete_relationships_by_agent("a1") == 0


def test_not_a_mock_seam():
    from agentclaw.community.plugins.local._mock_seam import MockSeam

    assert not issubclass(CommunityAuthRelationshipPlugin, MockSeam)


def test_explicit_env_operations_delegate_to_vacuous_contract():
    plugin = CommunityAuthRelationshipPlugin()
    assert plugin.create_relationship_for_env(
        target_env="prod",
        work_no="u1",
        agent_code="a1",
        operator_work_no="admin",
        operator_name="Admin",
    ) == {"auth_id": 0}
    assert plugin.query_relationships_for_env(
        target_env="pre", agent_code="a1", work_no="u1"
    ) == []


def test_explicit_env_operations_reject_unknown_environment():
    with pytest.raises(ValueError, match="target_env"):
        CommunityAuthRelationshipPlugin().query_relationships_for_env(
            target_env="gray", agent_code="a1"
        )
