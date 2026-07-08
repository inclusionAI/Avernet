"""Passport update plugin protocol.

Abstracts the notification of MCP list changes to external passport/auth
systems (e.g. tcauthmng).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


class PassportError(Exception):
    """Passport service call exception."""
    pass


@dataclass
class SubResourceItem:
    """Neutral second-level resource item for :meth:`PassportPlugin.save_sub_resources`.

    Provider-agnostic: the corp ``ProdPassportPlugin`` maps this to its TCAuth
    Hessian request DTO internally; community/local impls treat the call as a
    no-op. Keeps ``plugin_api`` free of any corp serialization.
    """

    resource_type: str
    sub_resource_type: str
    sub_resource_code: str
    detail_config: dict[str, Any] | None = None


class CliItem(TypedDict, total=False):
    """CLI scope item supplied by AgentPass query responses."""

    cli_code: str | None
    cli_name: str | None
    cli_desc: str | None


class PassportResourceScope(TypedDict):
    """Complete resourceManifest scope for overwrite-style updatePassport calls.

    AgentPass/tcauthmng treats each resource list in updatePassport as a full
    replacement. Callers that update MCP or CLI scope must pass both lists here
    so one resource type does not accidentally clear the other.
    """

    mcp_codes: list[str]
    cli_items: list[CliItem]


def extract_cli_items(passport: Mapping[str, Any] | None) -> list[CliItem]:
    """Extract normalized CLI scope items from a queryAgentPassport result."""
    if not isinstance(passport, Mapping):
        return []
    clis = []
    for item in passport.get("clis") or []:
        if not isinstance(item, Mapping):
            continue
        cli_code = item.get("cli_code")
        if not cli_code:
            continue
        clis.append({
            "cli_code": cli_code,
            "cli_name": item.get("cli_name"),
            "cli_desc": item.get("cli_desc"),
        })
    return clis


def unpack_resource_scope(
    resource_scope: PassportResourceScope | None,
) -> tuple[list[str] | None, list[CliItem] | None]:
    """Return DTO-ready MCP/CLI lists, or None pair for non-resource updates.

    Non-resource updates, such as admins or metadata, intentionally omit
    resource_scope so existing MCP/CLI grants are left untouched.
    """
    if resource_scope is None:
        return None, None
    try:
        return resource_scope["mcp_codes"], resource_scope["cli_items"]
    except KeyError as e:
        raise ValueError("resource_scope must include mcp_codes and cli_items") from e


@runtime_checkable
class PassportPlugin(Plugin, Protocol):
    """Plugin for passport lifecycle management (tcauthmng facade)."""

    def update_passport(
        self,
        bot_id: str,
        user_id: str,
        bot_name: str | None = None,
        bot_desc: str | None = None,
        bot_llm: str | None = None,
        engine_type: str | None = None,
        access_mode: str | None = None,
        admins: list[str] | None = None,
        resource_scope: PassportResourceScope | None = None,
    ) -> None:
        """Notify passport of metadata/admin updates or a complete MCP+CLI resource scope.

        Passing resource_scope means the caller is updating resourceManifest and
        has already collected the complete MCP and CLI scope for this bot.
        """
        ...

    def apply_first_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        mcp_codes: list[str],
        bot_name: str | None = None,
        bot_desc: str | None = None,
        bot_llm: str | None = None,
        engine_type: str | None = None,
        access_mode: str | None = None,
        device_token: str | None = None,
        workspace_path: str | None = None,
        admins: list[str] | None = None,
        cli_items: list[CliItem] | None = None,
    ) -> dict[str, Any] | None:
        """Apply for an agent passport (first time)."""
        ...

    def apply_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        mcp_codes: list[str],
        bot_name: str | None = None,
        bot_desc: str | None = None,
        bot_llm: str | None = None,
        engine_type: str | None = None,
        access_mode: str | None = None,
        device_token: str | None = None,
        workspace_path: str | None = None,
        admins: list[str] | None = None,
        cli_items: list[CliItem] | None = None,
    ) -> dict[str, Any] | None:
        """Apply for an agent passport (non-first time)."""
        ...

    def destroy_passport(self, bot_id: str, owner_workno: str) -> None:
        """Destroy the passport for a bot."""
        ...

    def query_auth_status(self, bot_id: str, owner_workno: str) -> dict[str, Any] | None:
        """Query the current passport auth status."""
        ...

    def query_token(self, bot_id: str, owner_workno: str) -> str | None:
        """Query the current passport token."""
        ...

    def query_agent_passport(self, bot_id: str, owner_workno: str) -> dict[str, Any] | None:
        """Query the full agent passport details."""
        ...

    def freeze_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        reason: str,
    ) -> None:
        """Set the bot's agent identity credential offline."""
        ...

    def unfreeze_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        reason: str,
    ) -> None:
        """Set the bot's agent identity credential online."""
        ...

    def offline_agent_identity_credential(
        self,
        bot_id: str,
        owner_workno: str,
        reason: str,
    ) -> dict[str, Any] | None:
        """Set a bot's agent identity credential offline."""
        ...

    def online_agent_identity_credential(
        self,
        bot_id: str,
        owner_workno: str,
        reason: str,
    ) -> dict[str, Any] | None:
        """Set a bot's agent identity credential online."""
        ...

    def query_passport_clis(
        self,
        bot_id: str,
        owner_workno: str,
    ) -> list[CliItem]:
        """Query the current CLI resource scope."""
        ...

    def save_sub_resources(
        self,
        bot_id: str,
        owner_workno: str,
        sub_resources: list[SubResourceItem],
    ) -> bool:
        """Full (overwrite) sync of a bot's second-level resource permissions.

        Corp maps ``sub_resources`` to the TCAuth ``saveSubResources`` facade;
        community/local impls are no-ops that return ``True``. Never raises —
        failure logs and returns ``False``.
        """
        ...
