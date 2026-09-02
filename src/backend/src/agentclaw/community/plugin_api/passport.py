"""Passport update plugin protocol.

Abstracts the notification of MCP list changes to external passport/auth
systems (e.g. tcauthmng).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any, Protocol, TypedDict, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


_CLI_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_CLI_IDENTITY_MODES = {"owner", "caller"}


class PassportError(Exception):
    """Passport service call exception."""
    pass


def validate_passport_target_env(target_env: str | None) -> None:
    """Validate an explicitly selected Passport environment."""
    if target_env is not None and target_env not in {"pre", "prod"}:
        raise ValueError("target_env must be 'pre' or 'prod'")


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
    identity_mode: str


class McpScopeItem(TypedDict, total=False):
    """Provider-neutral MCP resource item for Agent Principal updates."""

    mcp_code: str
    mcp_name: str | None
    mcp_desc: str | None
    identity_mode: str


class PassportResourceScope(TypedDict, total=False):
    """Complete resourceManifest scope for overwrite-style updatePassport calls.

    AgentPass/tcauthmng treats each resource list in updatePassport as a full
    replacement. Callers that update MCP or CLI scope must pass both lists here
    so one resource type does not accidentally clear the other.
    """

    mcp_codes: list[str]
    mcp_items: list[McpScopeItem]
    cli_items: list[CliItem]


def extract_cli_items(passport: Mapping[str, Any] | None) -> list[CliItem]:
    """Extract normalized CLI scope items from a queryAgentPassport result."""
    if not isinstance(passport, Mapping):
        return []
    raw_clis = passport.get("clis", [])
    if raw_clis is None:
        raw_clis = []
    return _normalize_cli_items(raw_clis, allow_missing_identity_mode=True)


def _normalize_cli_items(
    raw_items: object,
    *,
    allow_missing_identity_mode: bool,
) -> list[CliItem]:
    """Validate the complete CLI scope before any overwrite-style update."""
    if not isinstance(raw_items, list):
        raise ValueError("CLI scope items must be a list")
    normalized: list[CliItem] = []
    seen_codes: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            raise ValueError("CLI scope item must be a mapping")
        cli_code = raw_item.get("cli_code")
        if not isinstance(cli_code, str) or not _CLI_CODE_RE.fullmatch(cli_code):
            raise ValueError("CLI scope item code is invalid")
        if cli_code in seen_codes:
            raise ValueError("CLI scope item code is duplicated")
        if "identity_mode" not in raw_item and not allow_missing_identity_mode:
            raise ValueError("CLI scope item identity mode is required")
        raw_identity = raw_item.get("identity_mode", "owner")
        identity_mode = str(getattr(raw_identity, "value", raw_identity)).strip().lower()
        if identity_mode not in _CLI_IDENTITY_MODES:
            raise ValueError("CLI identity mode must be owner or caller")
        cli_name = raw_item.get("cli_name")
        cli_desc = raw_item.get("cli_desc")
        if cli_name is not None and not isinstance(cli_name, str):
            raise ValueError("CLI scope item name is invalid")
        if cli_desc is not None and not isinstance(cli_desc, str):
            raise ValueError("CLI scope item description is invalid")
        seen_codes.add(cli_code)
        normalized.append({
            "cli_code": cli_code,
            "cli_name": cli_name,
            "cli_desc": cli_desc,
            "identity_mode": identity_mode,
        })
    return normalized


def unpack_resource_scope(
    resource_scope: PassportResourceScope | None,
) -> tuple[list[McpScopeItem] | None, list[CliItem] | None]:
    """Return DTO-ready MCP/CLI lists, or None pair for non-resource updates.

    Non-resource updates, such as admins or metadata, intentionally omit
    resource_scope so existing MCP/CLI grants are left untouched.

    A scope that grants MCPs must say under whose identity each one runs.
    ``mcp_codes`` alone cannot: the port fills the missing ``identity_mode``
    with ``"owner"``, so a code-only scope does not leave identity alone — it
    asserts Owner for every MCP and discards the Caller grants
    ``update_mcp_identity_to_agent_principal`` wrote through the same field.
    Because ``updatePassport`` replaces each resource list wholesale, that
    demotion is silent and total. So a non-empty ``mcp_codes`` without
    ``mcp_items`` is rejected here rather than accepted and quietly widened:
    the caller holds the Bot and can read the identities; this function does
    not. An empty list stays legal — it grants nothing, so there is no
    identity to lose — which is what lets a caller clear MCP scope without
    assembling items for it.
    """
    if resource_scope is None:
        return None, None
    if not isinstance(resource_scope, Mapping):
        raise ValueError("resource_scope must be a mapping")
    try:
        cli_items = _normalize_cli_items(
            resource_scope["cli_items"],
            allow_missing_identity_mode=False,
        )
    except KeyError as e:
        raise ValueError(
            "resource_scope must include mcp_codes and cli_items"
        ) from e
    if "mcp_items" in resource_scope:
        return resource_scope["mcp_items"], cli_items
    try:
        mcp_codes = resource_scope["mcp_codes"]
    except KeyError as e:
        raise ValueError(
            "resource_scope must include mcp_codes and cli_items"
        ) from e
    if mcp_codes:
        raise ValueError(
            "resource_scope granting MCPs must include mcp_items: bare "
            "mcp_codes would assert identity_mode=owner for every MCP and "
            "drop the bot's caller grants"
        )
    return [], cli_items


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

    def update_mcp_identity_to_agent_principal(
        self,
        *,
        bot_id: str,
        user_id: str,
        mcp_items: list[McpScopeItem],
    ) -> None:
        """Replace the Agent Principal MCP scope while preserving other resources."""
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
        *,
        target_env: str | None = None,
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

    def query_auth_status(
        self,
        bot_id: str,
        owner_workno: str,
        *,
        target_env: str | None = None,
    ) -> dict[str, Any] | None:
        """Query the current passport auth status."""
        ...

    def query_token(
        self,
        bot_id: str,
        owner_workno: str,
        *,
        target_env: str | None = None,
    ) -> str | None:
        """Query the current passport token."""
        ...

    def query_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        *,
        target_env: str | None = None,
    ) -> dict[str, Any] | None:
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
        """Bring the credential online and make a runtime token queryable.

        Implementations must return only after ``query_token`` can provide the
        token required by device bootstrap, or raise when that postcondition
        cannot be established.
        """
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
