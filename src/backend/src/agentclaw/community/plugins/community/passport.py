"""SelfIssuedPassportPlugin — community bot-credential broker.

The corp ``PassportPlugin`` brokers per-bot "agent passport" credentials through
tcauthmng/AgentPass. A community deployment is the sole authority over its own
bots, so it **self-issues** those credentials locally: a deterministic, stateless
function of ``bot_id`` — no external approval system, no consent step, no I/O.

This is a real, deployable implementation (not a ``MockSeam`` test double). The
issued ``token`` / ``agent_code`` are stable per ``bot_id`` because they are
persisted (``bot.ext.passport``) and re-read; the token is an inert bearer
placeholder (the community sandbox runs ``auth.mode=none`` and the corp egress
gateways it would target do not exist), so any stable opaque value suffices.
"""
from __future__ import annotations

from typing import Any, Optional

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import (
    CliItem,
    McpScopeItem,
    PassportPlugin,
    PassportResourceScope,
    SubResourceItem,
    extract_cli_items,
    unpack_resource_scope,
    validate_passport_target_env,
)


logger = get_logger()


def _token_for(bot_id: str) -> str:
    return f"community-passport-{bot_id}"


class SelfIssuedPassportPlugin(PassportPlugin):
    """Self-issuing passport broker: always ISSUED, allow-all, deterministic."""

    def update_passport(
        self,
        bot_id: str,
        user_id: str,
        bot_name: Optional[str] = None,
        bot_desc: Optional[str] = None,
        bot_llm: Optional[str] = None,
        engine_type: Optional[str] = None,
        access_mode: Optional[str] = None,
        admins: Optional[list[str]] = None,
        resource_scope: Optional[PassportResourceScope] = None,
    ) -> None:
        # No external registry to notify — scope/admin changes are a no-op.
        # ``unpack_resource_scope`` is still called so a malformed scope raises
        # the same ValueError the corp impl would, keeping callers honest.
        unpack_resource_scope(resource_scope)

    def update_mcp_identity_to_agent_principal(
        self,
        *,
        bot_id: str,
        user_id: str,
        mcp_items: list[McpScopeItem],
    ) -> None:
        self._validate_mcp_identity_items(mcp_items)

    def apply_first_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        mcp_codes: list[str],
        bot_name: Optional[str] = None,
        bot_desc: Optional[str] = None,
        bot_llm: Optional[str] = None,
        engine_type: Optional[str] = None,
        access_mode: Optional[str] = None,
        device_token: Optional[str] = None,
        workspace_path: Optional[str] = None,
        admins: Optional[list[str]] = None,
        cli_items: Optional[list[CliItem]] = None,
        *,
        target_env: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        validate_passport_target_env(target_env)
        return self._issue(bot_id)

    def apply_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        mcp_codes: list[str],
        bot_name: Optional[str] = None,
        bot_desc: Optional[str] = None,
        bot_llm: Optional[str] = None,
        engine_type: Optional[str] = None,
        access_mode: Optional[str] = None,
        device_token: Optional[str] = None,
        workspace_path: Optional[str] = None,
        admins: Optional[list[str]] = None,
        cli_items: Optional[list[CliItem]] = None,
    ) -> Optional[dict[str, Any]]:
        return self._issue(bot_id)

    def destroy_passport(self, bot_id: str, owner_workno: str) -> None:
        # Nothing to revoke — credentials are derived, not stored externally.
        return None

    def query_auth_status(
        self,
        bot_id: str,
        owner_workno: str,
        *,
        target_env: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        validate_passport_target_env(target_env)
        return {"status": "ISSUED", "token": _token_for(bot_id)}

    def query_token(
        self,
        bot_id: str,
        owner_workno: str,
        *,
        target_env: Optional[str] = None,
    ) -> Optional[str]:
        validate_passport_target_env(target_env)
        return _token_for(bot_id)

    def query_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        *,
        target_env: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        validate_passport_target_env(target_env)
        return {
            "agent_id": bot_id,
            "agent_code": bot_id,
            "status": "ISSUED",
            "access_mode": None,
            "mcps": [],
            "clis": [],
        }

    def query_passport_clis(
        self,
        bot_id: str,
        owner_workno: str,
    ) -> list[CliItem]:
        return extract_cli_items(self.query_agent_passport(bot_id, owner_workno))

    def save_sub_resources(
        self,
        bot_id: str,
        owner_workno: str,
        sub_resources: list[SubResourceItem],
    ) -> bool:
        # No external permission backend in the community build — no-op.
        logger.debug(
            "[SelfIssuedPassportPlugin] save_sub_resources → no-op "
            "(bot_id=%s, count=%d)",
            bot_id, len(sub_resources),
        )
        return True

    # -- internal --------------------------------------------------------------

    @staticmethod
    def _validate_mcp_identity_items(mcp_items: list[McpScopeItem]) -> None:
        if any(
            not item.get("mcp_code")
            or item.get("identity_mode") not in {"owner", "caller"}
            for item in mcp_items
        ):
            raise ValueError("invalid MCP identity scope")

    @staticmethod
    def _issue(bot_id: str) -> dict[str, Any]:
        # No consent step: a non-empty token + agent_code so create_bot never
        # enters the "need authorization" branch and device bootstrap succeeds.
        return {
            "token": _token_for(bot_id),
            "agent_code": bot_id,
            "iframe_url": None,
            "redirect_url": None,
        }
