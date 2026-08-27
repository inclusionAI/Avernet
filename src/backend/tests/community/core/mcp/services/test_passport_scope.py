"""The identity half of the Passport scope contract.

``updatePassport`` replaces each resource list wholesale and the Passport
port backfills a missing ``identity_mode`` with Owner, so how these builders
resolve identity decides whether a Caller MCP survives a projection. Nothing
covered this module before; these pin the parts a "simplification" would
otherwise be free to change.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.caller_identity.models import McpCallType
from agentclaw.community.core.mcp.errors import McpIdentityUnresolvedError
from agentclaw.community.core.mcp.services.passport_scope import (
    passport_mcp_items_from_codes,
    passport_mcp_items_from_entries,
    resolve_mcp_identity_modes,
)


class TestIdentityModeResolution:
    def test_missing_entry_defaults_to_owner(self):
        """The call-config table is sparse: no row means Owner."""
        items = passport_mcp_items_from_codes(["mcp.a"], identity_modes={})

        assert items == [{"mcp_code": "mcp.a", "identity_mode": "owner"}]

    def test_caller_mode_survives_as_caller(self):
        items = passport_mcp_items_from_codes(
            ["mcp.a"], identity_modes={"mcp.a": McpCallType.CALLER}
        )

        assert items == [{"mcp_code": "mcp.a", "identity_mode": "caller"}]

    def test_enum_and_raw_string_resolve_identically(self):
        """Callers pass McpCallType; stored rows can be plain strings."""
        as_enum = passport_mcp_items_from_codes(
            ["mcp.a"], identity_modes={"mcp.a": McpCallType.CALLER}
        )
        as_text = passport_mcp_items_from_codes(
            ["mcp.a"], identity_modes={"mcp.a": "  CALLER "}
        )

        assert as_enum == as_text

    @pytest.mark.parametrize("mode", ["admin", "", "OWNER_", "delegate"])
    def test_unrecognised_mode_is_rejected_not_coerced(self, mode: str):
        """Coercing an unknown mode to Owner is the demotion this prevents.

        It must fail loudly at the builder rather than reach the Passport
        port, which would write Owner explicitly and silently move the MCP
        onto the bot owner's credential.
        """
        with pytest.raises(ValueError, match="identity mode must be owner or caller"):
            passport_mcp_items_from_codes(["mcp.a"], identity_modes={"mcp.a": mode})

    def test_both_builders_agree_on_one_identity_contract(self):
        """The codes and entries builders must not drift apart.

        They were separate copies once, and the copies disagreed; the entries
        variant adds name/desc but must resolve identity identically.
        """
        modes = {"mcp.a": McpCallType.CALLER}
        from_codes = passport_mcp_items_from_codes(["mcp.a"], identity_modes=modes)
        from_entries = passport_mcp_items_from_entries(
            [{"server_code": "mcp.a"}],
            identity_modes=modes,
            local_registry=_EmptyRegistry(),
        )

        assert [item["identity_mode"] for item in from_codes] == [
            item["identity_mode"] for item in from_entries
        ]


class TestCodesBuilderScope:
    def test_scope_follows_the_codes_not_the_config_rows(self):
        """A row for an MCP the Bot no longer holds must not re-grant it."""
        items = passport_mcp_items_from_codes(
            ["mcp.a"],
            identity_modes={
                "mcp.a": McpCallType.OWNER,
                "mcp.retired": McpCallType.CALLER,
            },
        )

        assert [item["mcp_code"] for item in items] == ["mcp.a"]

    def test_order_is_preserved_for_a_deterministic_manifest(self):
        items = passport_mcp_items_from_codes(
            ["mcp.b", "mcp.a"], identity_modes={}
        )

        assert [item["mcp_code"] for item in items] == ["mcp.b", "mcp.a"]

    def test_name_and_desc_are_omitted_so_no_catalogue_lookup_is_needed(self):
        """The port leaves absent optional fields off the wire.

        Identity can therefore be declared without one MCP-Center round trip
        per code, which is what keeps a single-MCP change a single call.
        """
        (item,) = passport_mcp_items_from_codes(["mcp.a"], identity_modes={})

        assert set(item) == {"mcp_code", "identity_mode"}

    def test_no_codes_yields_no_items(self):
        assert passport_mcp_items_from_codes([], identity_modes={}) == []


class _EmptyRegistry:
    """No LOCAL/stdio servers declared, so nothing is filtered as local."""

    def list_mcp_details(self, **kwargs) -> list[dict]:
        return []


class _RecordingIdentityRepo:
    """Answers one identity query, recording how it was keyed."""

    def __init__(self, modes=None, error: Exception | None = None) -> None:
        self._modes = modes or {}
        self._error = error
        self.calls: list[tuple[int, str]] = []

    def list_draft_call_types(self, bot_pk: int, engine_type: str):
        self.calls.append((bot_pk, engine_type))
        if self._error is not None:
            raise self._error
        return self._modes


class TestResolveMcpIdentityModes:
    """The one definition of "fail closed on identity".

    Three callers assemble an overwrite-style Passport MCP scope — the runtime
    projector, the CLI-removal endpoint, and the aicoding restart refresh. Each
    would demote every Caller MCP on the bot if it defaulted, so the read that
    would have to default is shared and refuses instead.
    """

    def test_the_stored_modes_are_returned_for_the_bot_and_engine(self):
        repo = _RecordingIdentityRepo({"mcp.a": McpCallType.CALLER})

        modes = resolve_mcp_identity_modes(
            repo, bot_pk=42, engine_type="openclaw", bot_id="bot-1"
        )

        assert modes == {"mcp.a": McpCallType.CALLER}
        assert repo.calls == [(42, "openclaw")]

    def test_a_string_primary_key_is_coerced(self):
        """Bot records reach here from several readers; not all type the pk."""
        repo = _RecordingIdentityRepo()

        resolve_mcp_identity_modes(
            repo, bot_pk="42", engine_type="openclaw", bot_id="bot-1"
        )

        assert repo.calls == [(42, "openclaw")]

    def test_a_missing_primary_key_fails_rather_than_defaulting(self):
        """No pk means the record is not what the caller assumes — and that is
        exactly when guessing Owner is least safe."""
        repo = _RecordingIdentityRepo()

        with pytest.raises(McpIdentityUnresolvedError):
            resolve_mcp_identity_modes(
                repo, bot_pk=None, engine_type="openclaw", bot_id="bot-1"
            )

        assert repo.calls == [], "must not query with an unknown key"

    def test_an_unreadable_row_fails_rather_than_defaulting(self):
        repo = _RecordingIdentityRepo(error=RuntimeError("identity store down"))

        with pytest.raises(McpIdentityUnresolvedError) as excinfo:
            resolve_mcp_identity_modes(
                repo, bot_pk=42, engine_type="openclaw", bot_id="bot-1"
            )

        # The cause is kept so the operator sees what actually broke.
        assert isinstance(excinfo.value.__cause__, RuntimeError)

    def test_no_stored_overrides_is_a_success_not_a_failure(self):
        """An empty map is a real answer: every MCP runs as Owner."""
        assert resolve_mcp_identity_modes(
            _RecordingIdentityRepo({}), bot_pk=42, engine_type="openclaw", bot_id="b"
        ) == {}
