"""Rule 25 conformance — AuthRelationshipPlugin.

Consumer under test: ``BotPublicService._rebuild_auth_relationships``
(core/bot_public/services/bot_public_service.py:208). Seeding a
``BotPublicService`` flow that triggers ``_rebuild_auth_relationships``
requires substantial bot + binding fixtures; the plugin's contract
is observed through the DI-bound instance the service holds.

The local ``LocalAuthRelationshipPlugin`` returns a fixed mock
envelope ``{"auth_id": 0}`` for ``create_relationship`` and an empty
list for ``query_relationships``. Both are no-op contracts the
consumer treats as success / no-existing-relationships.

Plugin-hit assertion: the DI-resolved instance's ``create_relationship``
must return ``{"auth_id": 0}`` — only the local impl produces that
exact shape.
"""
from __future__ import annotations

from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin


def test_local_auth_relationship_create_returns_mock_envelope(world) -> None:
    plugin = world.get(AuthRelationshipPlugin)
    result = plugin.create_relationship(
        work_no="alice",
        agent_code="bot_x",
        operator_work_no="alice",
        operator_name="alice",
    )
    assert result == {"auth_id": 0}


def test_local_auth_relationship_query_returns_empty(world) -> None:
    plugin = world.get(AuthRelationshipPlugin)
    assert plugin.query_relationships(agent_code="bot_x") == []


# ── community impl (B4) — no external registry, succeeds vacuously ──


def test_community_auth_relationship_create_succeeds_vacuously(
    community_world,
) -> None:
    plugin = community_world.get(AuthRelationshipPlugin)
    result = plugin.create_relationship(
        work_no="alice",
        agent_code="bot_x",
        operator_work_no="alice",
        operator_name="alice",
    )
    assert result == {"auth_id": 0}
    assert plugin.query_relationships(agent_code="bot_x") == []
    assert plugin.delete_relationships_by_agent("bot_x") == 0
