"""Bootstrap-time convergence of the overwrite-style AgentPass resource scope."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import time
from typing import Any

from agentclaw.community.core.mcp.services.cli_capabilities import (
    CliCapabilityManifestResolver,
    merge_cli_scope,
)
from agentclaw.community.core.repository.protocols.identity import (
    CallerIdentityRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import (
    McpScopeItem,
    PassportPlugin,
    extract_cli_items,
)


logger = get_logger()


@dataclass(frozen=True, slots=True)
class CliScopeReconcileResult:
    """Non-sensitive Bootstrap projection consumed by the managed installer."""

    cli_codes: tuple[str, ...]
    manifest_version: str
    manifest_digest: str
    updated: bool


@dataclass(frozen=True, slots=True)
class _CliScopeSnapshot:
    """One complete AgentPass snapshot normalized for an overwrite writer."""

    historical_cli_items: list[dict[str, Any]]
    historical_mcp_items: list[McpScopeItem]
    cli_items: list[dict[str, Any]]
    mcp_items: list[McpScopeItem]
    resource_scope: dict[str, Any]

    @property
    def changed(self) -> bool:
        return (
            self.cli_items != self.historical_cli_items
            or self.mcp_items != self.historical_mcp_items
        )


class CliPassportScopeReconciler:
    """The one Bootstrap writer for YAML Default CLI scope."""

    def __init__(
        self,
        *,
        passport_plugin: PassportPlugin,
        identity_repository: CallerIdentityRepositoryProtocol,
        manifest_resolver: CliCapabilityManifestResolver | None = None,
    ) -> None:
        self._passport_plugin = passport_plugin
        self._identity_repository = identity_repository
        self._manifest_resolver = manifest_resolver or CliCapabilityManifestResolver()

    def current_passport_cli_items(self, *, bot: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Read the validated current CLI scope without mutating AgentPass."""
        return list(self._build_snapshot(bot).historical_cli_items)

    def supports_profile(self, *, bot: Mapping[str, Any]) -> bool:
        """Return whether this Bot is in the phase-one Default CLI profile."""
        engine_type = _required_text(bot, "active_engine")
        template_type = bot.get("template_type")
        if template_type is not None and not isinstance(template_type, str):
            return False
        return self._manifest_resolver.is_supported_profile(engine_type, template_type)

    def reconcile(
        self,
        *,
        bot: Mapping[str, Any],
        force_update: bool = False,
    ) -> CliScopeReconcileResult:
        """Merge history/defaults/overrides and update complete scope only if changed."""
        started = time.monotonic()
        bot_id = _required_text(bot, "bot_id")
        owner_id = _required_text(bot, "owner_id")
        engine_type = _required_text(bot, "active_engine")
        template_type = bot.get("template_type")
        if template_type is not None and not isinstance(template_type, str):
            raise ValueError("Bot template_type is invalid")
        profile_items = self._manifest_resolver.required_cli_items(engine_type, template_type)
        logger.info(
            "cli_passport_reconcile_requested bot_id=%s engine_type=%s template_type=%s required_cli_codes=%s",
            bot_id, engine_type, template_type, [item["cli_code"] for item in profile_items],
        )
        try:
            snapshot = self._build_snapshot(bot)
            updated = snapshot.changed or force_update
            if updated:
                logger.info(
                    "agentpass_cli_scope_update_requested bot_id=%s engine_type=%s mcp_codes=%s cli_codes=%s",
                    bot_id,
                    engine_type,
                    [item["mcp_code"] for item in snapshot.mcp_items],
                    [item["cli_code"] for item in snapshot.cli_items],
                )
                update_started = time.monotonic()
                try:
                    self._passport_plugin.update_passport(
                        bot_id=bot_id,
                        user_id=owner_id,
                        engine_type=engine_type,
                        resource_scope=snapshot.resource_scope,
                    )
                except Exception as exc:
                    logger.error(
                        "agentpass_cli_scope_update_failed bot_id=%s engine_type=%s "
                        "status=failed error_type=%s duration_ms=%s",
                        bot_id,
                        engine_type,
                        type(exc).__name__,
                        int((time.monotonic() - update_started) * 1000),
                    )
                    raise
                logger.info(
                    "agentpass_cli_scope_update_succeeded bot_id=%s engine_type=%s "
                    "status=succeeded mcp_count=%s cli_count=%s duration_ms=%s",
                    bot_id,
                    engine_type,
                    len(snapshot.mcp_items),
                    len(snapshot.cli_items),
                    int((time.monotonic() - update_started) * 1000),
                )
        except Exception as exc:
            logger.error(
                "cli_passport_reconcile_failed bot_id=%s engine_type=%s error_type=%s duration_ms=%s",
                bot_id, engine_type, type(exc).__name__, int((time.monotonic() - started) * 1000),
            )
            raise
        logger.info(
            "cli_passport_reconcile_succeeded bot_id=%s engine_type=%s updated=%s cli_codes=%s duration_ms=%s",
            bot_id,
            engine_type,
            updated,
            [item["cli_code"] for item in snapshot.cli_items],
            int((time.monotonic() - started) * 1000),
        )
        return CliScopeReconcileResult(
            cli_codes=tuple(str(item["cli_code"]) for item in snapshot.cli_items),
            manifest_version=self._manifest_resolver.manifest_version,
            manifest_digest=self._manifest_resolver.manifest_digest,
            updated=updated,
        )

    def _build_snapshot(self, bot: Mapping[str, Any]) -> _CliScopeSnapshot:
        """Build the one full MCP+CLI snapshot shared by Bootstrap and CLI edits."""
        bot_id = _required_text(bot, "bot_id")
        owner_id = _required_text(bot, "owner_id")
        engine_type = _required_text(bot, "active_engine")
        template_type = bot.get("template_type")
        if template_type is not None and not isinstance(template_type, str):
            raise ValueError("Bot template_type is invalid")
        passport = self._passport_plugin.query_agent_passport(bot_id, owner_id)
        historical_cli_items = extract_cli_items(passport)
        historical_mcp_items = _extract_mcp_items(passport)
        mcp_identity_modes = self._identity_repository.list_draft_call_types(
            int(bot["id"]), engine_type
        )
        cli_identity_modes = self._identity_repository.list_draft_cli_call_types(
            int(bot["id"]), engine_type
        )
        resource_scope = build_passport_resource_scope(
            passport,
            desired_mcp_items=historical_mcp_items,
            mcp_identity_modes=mcp_identity_modes,
            additional_cli_items=self._manifest_resolver.required_cli_items(
                engine_type, template_type
            ),
            cli_identity_modes=cli_identity_modes,
        )
        return _CliScopeSnapshot(
            historical_cli_items=historical_cli_items,
            historical_mcp_items=historical_mcp_items,
            cli_items=resource_scope["cli_items"],
            mcp_items=resource_scope["mcp_items"],
            resource_scope=resource_scope,
        )


def _extract_mcp_items(passport: Mapping[str, Any] | None) -> list[McpScopeItem]:
    if not isinstance(passport, Mapping):
        raise ValueError("AgentPass scope is unavailable")
    result: list[McpScopeItem] = []
    seen: set[str] = set()
    for raw in passport.get("mcps") or []:
        if not isinstance(raw, Mapping):
            raise ValueError("AgentPass MCP scope is invalid")
        code = raw.get("mcp_code") or raw.get("server_code")
        if not isinstance(code, str) or not code or code in seen:
            raise ValueError("AgentPass MCP code is invalid")
        identity = str(getattr(raw.get("identity_mode", "owner"), "value", raw.get("identity_mode", "owner"))).strip().lower()
        if identity not in {"owner", "caller"}:
            raise ValueError("AgentPass MCP identity mode is invalid")
        seen.add(code)
        item: McpScopeItem = {"mcp_code": code, "identity_mode": identity}
        mcp_name = _optional_text(raw.get("mcp_name") or raw.get("server_name"))
        mcp_desc = _optional_text(raw.get("mcp_desc") or raw.get("server_description"))
        if mcp_name is not None:
            item["mcp_name"] = mcp_name
        if mcp_desc is not None:
            item["mcp_desc"] = mcp_desc
        result.append(item)
    return result


def build_passport_resource_scope(
    passport: Mapping[str, Any] | None,
    *,
    desired_mcp_items: list[McpScopeItem],
    mcp_identity_modes: Mapping[str, object],
    additional_cli_items: list[Mapping[str, Any]] | None = None,
    cli_identity_modes: Mapping[str, object] | None = None,
    removed_cli_codes: set[str] | None = None,
) -> dict[str, Any]:
    """Build an overwrite-safe scope from AgentPass history and local sparse rows.

    The caller owns desired MCP membership; this pure helper only restores the
    identity for those codes from the complete AgentPass snapshot, then lets a
    local sparse row take precedence. CLI history is retained and merged with
    caller-provided defaults or desired additions in the same snapshot.
    """
    historical_mcp_items = _extract_mcp_items(passport)
    historical_cli_items = extract_cli_items(passport)
    mcp_items = _merge_desired_mcp_identity_modes(
        desired_mcp_items,
        historical_mcp_items,
        mcp_identity_modes,
    )
    merged_cli_items = merge_cli_scope(
        historical_cli_items,
        _normalize_cli_additions(additional_cli_items),
        cli_identity_modes or {},
    )
    removed_codes = removed_cli_codes or set()
    cli_items = [
        item for item in merged_cli_items
        if str(item["cli_code"]) not in removed_codes
    ]
    return {
        "mcp_codes": [item["mcp_code"] for item in mcp_items],
        "mcp_items": mcp_items,
        "cli_items": cli_items,
    }


def _merge_desired_mcp_identity_modes(
    desired_items: list[McpScopeItem],
    historical_items: list[McpScopeItem],
    sparse_identity_modes: Mapping[str, object],
) -> list[McpScopeItem]:
    historical_modes = {
        str(item["mcp_code"]): item["identity_mode"]
        for item in historical_items
    }
    merged: list[McpScopeItem] = []
    seen_codes: set[str] = set()
    for raw_item in desired_items:
        code = raw_item.get("mcp_code")
        if not isinstance(code, str) or not code or code in seen_codes:
            raise ValueError("Desired MCP scope is invalid")
        seen_codes.add(code)
        item = dict(raw_item)
        sparse_mode = sparse_identity_modes.get(code)
        identity = sparse_mode if sparse_mode is not None else historical_modes.get(
            code, item.get("identity_mode", "owner")
        )
        item["identity_mode"] = _normalized_mcp_identity_mode(identity)
        merged.append(item)
    return merged


def _normalize_cli_additions(
    additional_cli_items: list[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not additional_cli_items:
        return []
    return extract_cli_items({"clis": list(additional_cli_items)})


def _normalized_mcp_identity_mode(raw: object) -> str:
    identity = str(getattr(raw, "value", raw)).strip().lower()
    if identity not in {"owner", "caller"}:
        raise ValueError("MCP identity mode is invalid")
    return identity


def _required_text(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Bot {key} is invalid")
    return value


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


__all__ = [
    "CliPassportScopeReconciler",
    "CliScopeReconcileResult",
    "build_passport_resource_scope",
]
