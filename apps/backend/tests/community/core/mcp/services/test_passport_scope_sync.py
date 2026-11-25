"""Tests for the complete Passport scope update in legacy MCP delivery."""

from contextlib import contextmanager
from unittest.mock import MagicMock

from agentclaw.community.core.mcp.services.cli_passport_scope import build_passport_resource_scope
from agentclaw.community.core.mcp.services.passport_scope_sync import update_mcp_passport_scope


def test_legacy_mcp_sync_holds_employee_scope_guard_across_read_and_write():
    passport = MagicMock()
    bot_repository = MagicMock()
    caller_identity_repository = MagicMock()
    caller_identity_repository.list_draft_call_types.return_value = {}
    bot = {"id": 42, "ext": {"digital_employee": {"status": "ACTIVE"}}}
    bot_repository.get_by_id_and_owner.return_value = bot
    employee = MagicMock()
    historical_mcp = {"mcp_code": "published-tool", "identity_mode": "owner"}
    operations = []

    @contextmanager
    def guard(current_bot):
        assert current_bot is bot
        operations.append("lock")
        try:
            yield
        finally:
            operations.append("unlock")

    def query(*_args):
        operations.append("read")
        return {"mcps": [historical_mcp], "clis": []}

    def write(*, resource_scope, **_kwargs):
        assert resource_scope["mcp_codes"] == ["published-tool"]
        operations.append("write")

    employee.serialize_passport_scope.side_effect = guard
    employee.prepare_mcp_change.return_value = [historical_mcp]
    passport.query_agent_passport.side_effect = query
    passport.update_passport.side_effect = write

    result = update_mcp_passport_scope(
        passport_plugin=passport,
        bot_repository=bot_repository,
        caller_identity_repository=caller_identity_repository,
        bot_id="bot1",
        user_id="u1",
        owner_id="u1",
        synced_mcps=[],
        engine_type="openclaw",
        scope_builder=build_passport_resource_scope,
        employee_service_provider=lambda: employee,
    )

    assert result == {"success": True}
    assert operations == ["lock", "read", "write", "unlock"]
