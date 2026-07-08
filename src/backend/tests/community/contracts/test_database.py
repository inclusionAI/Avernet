"""Rule 25 conformance — DatabasePlugin.

Consumer under test: ``UserService`` (core/access). It writes a user
row via ``PolicyRepository`` which opens ``DatabasePlugin.session()``
to persist. Reading the row back through a fresh session proves the
session contract round-trips end-to-end.

The plugin-hit assertion is observable: a row written through the
consumer must be visible via an independent ``DatabasePlugin.session()``
in the same test (separate session checkout against the same in-memory
engine — pins the StaticPool contract).
"""
from __future__ import annotations

from sqlalchemy import text

from agentclaw.community.core.access.services.user_service import UserService
from agentclaw.community.plugin_api.database import DatabasePlugin


def test_upsert_user_writes_through_database_plugin_session(world) -> None:
    svc = world.get(UserService)
    svc.upsert_user(user_id="u_contract", user_type="staff", status="active")

    # Consumer-level assertion: round-trip via the consumer's API.
    record = svc.get_user(user_id="u_contract", user_type="staff")
    assert record.user_id == "u_contract"
    assert record.status == "active"

    # Plugin-hit assertion: the row must be visible via an independent
    # DatabasePlugin.session() checkout — proves the consumer routed its
    # write through the plugin (not in-memory state on the service).
    plugin = world.get(DatabasePlugin)
    with plugin.session() as s:
        row = s.execute(
            text(
                "SELECT user_id, status FROM ac_user_info "
                "WHERE user_id = :uid AND user_type = :ut"
            ),
            {"uid": "u_contract", "ut": "staff"},
        ).first()
    assert row is not None, "DatabasePlugin.session() must surface the consumer's write"
    assert row[0] == "u_contract"
    assert row[1] == "active"


def test_get_user_raises_when_row_absent(world) -> None:
    from agentclaw.community.core.access.errors import UserNotFoundError
    import pytest

    svc = world.get(UserService)
    with pytest.raises(UserNotFoundError):
        svc.get_user(user_id="nobody", user_type="staff")


def test_community_column_binds_contract_shaped_database(community_world) -> None:
    # The community DatabasePlugin is deliberately schema-less (operator-
    # provisioned), so a writing consumer cannot run against the default-config
    # community_world without side effects; the session contract itself is
    # covered by the plugin unit tests. Here we pin that the community column
    # wires the contract-shaped impl exposing the session surface the consumer
    # depends on.
    from agentclaw.community.plugins.community.database import CommunityDatabase

    plugin = community_world.get(DatabasePlugin)
    assert isinstance(plugin, CommunityDatabase)
    assert callable(plugin.session) and callable(plugin.orm_session)
