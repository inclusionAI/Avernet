"""Build a current, stable risk input from Backend-owned capability reads."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from typing import Any, Protocol
from uuid import UUID

from agentclaw.community.core.skill_center.public_center_identity import (
    PublicCenterSkillIdentity,
)
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class McpProjectionUnavailable(RuntimeError):
    """The risk-safe projection of necessary MCP configuration is unavailable."""


class McpRiskProjector(Protocol):
    def project(
        self, *, bot: Mapping[str, Any], entry: Mapping[str, Any], call_type: str
    ) -> Mapping[str, Any]:
        """Return complete necessary config and call identity with no credentials.

        Raise if a required parameter cannot be represented safely and faithfully.
        This contract has no default implementation until the external field and
        secret-projection contracts have been verified.
        """
        ...


def _text(bot: Mapping[str, Any], field: str) -> str:
    value = bot.get(field)
    if value is None or not str(value).strip():
        raise ValueError(f"Bot {field} is required")
    return str(value)


def _agent_code(
    bot: Mapping[str, Any], passport, bot_id: str, owner: str, entity: str
) -> str:
    ext = bot.get("ext") or {}
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except json.JSONDecodeError:
            ext = {}
    if not isinstance(ext, dict):
        raise ValueError("Bot ext is invalid")
    local = ext.get("passport") or {}
    code = local.get("agent_code") if isinstance(local, dict) else None
    if not code:
        data = (
            passport.query_agent_passport(bot_id, owner, entity_id=entity)
            if passport
            else None
        )
        code = data.get("agent_code") if isinstance(data, dict) else None
    if not isinstance(code, str) or not code.strip():
        raise ValueError("Passport agent_code is unavailable")
    return code


def _safe_mapping(value: Any) -> dict:
    if not isinstance(value, Mapping):
        raise McpProjectionUnavailable("MCP required config is unavailable")

    # Reject obvious credential fields even when a supplied projector is faulty.
    def check(item):
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if not isinstance(key, str) or any(
                    word in key.lower()
                    for word in (
                        "token",
                        "secret",
                        "password",
                        "credential",
                        "api_key",
                        "apikey",
                        "authorization",
                    )
                ):
                    raise McpProjectionUnavailable(
                        "MCP config contains a credential field"
                    )
                check(nested)
        elif isinstance(item, (tuple, list)):
            for nested in item:
                check(nested)

    check(value)
    return dict(value)


class RiskInputBuilder:
    def __init__(
        self,
        capabilities,
        mcps,
        identities,
        observations,
        passport,
        mcp_projector: McpRiskProjector | None,
    ):
        self.capabilities, self.mcps, self.identities = capabilities, mcps, identities
        self.observations, self.passport, self.mcp_projector = (
            observations,
            passport,
            mcp_projector,
        )

    def build(
        self, *, bot: Mapping[str, Any], target: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        if self.mcp_projector is None:
            raise McpProjectionUnavailable("MCP risk projection is not configured")
        bot_id, entity, env = (
            _text(bot, "bot_id"),
            _text(bot, "entity_id"),
            _text(bot, "env"),
        )
        owner, engine = _text(bot, "owner_id"), _text(bot, "active_engine")
        agent_code = _agent_code(bot, self.passport, bot_id, owner, entity)
        visibility = str(target.get("visibility", "")).upper()
        visibility = "AUTHORIZED" if visibility == "PROTECTED" else visibility
        if visibility not in {"PUBLIC", "AUTHORIZED"}:
            raise ValueError("invalid target visibility")
        modes = self.identities.list_draft_call_types(int(_text(bot, "id")), engine)
        snapshot = self.capabilities.active_capabilities(
            bot_id=bot_id, owner_id=owner, bot=bot
        )
        entries = self.mcps.collect_bot_active_mcps(
            entity_id=entity,
            bot_id=bot_id,
            user_id=snapshot.owner_id,
            capability_snapshot=snapshot,
            entity_type=bot.get("entity_type") or "staff",
            engine_type=engine,
            strict_policy_context=True,
        )
        resources, seen = [], set()
        for entry in entries:
            code = entry.get("server_code") or entry.get("serverCode")
            if not isinstance(code, str) or not code.strip():
                raise ValueError("effective MCP lacks server_code")
            if code in seen:
                continue
            seen.add(code)
            raw_mode = modes.get(code, "owner")
            mode = str(getattr(raw_mode, "value", raw_mode)).upper()
            if mode not in {"OWNER", "CALLER"}:
                raise ValueError("invalid MCP call identity")
            config = _safe_mapping(
                self.mcp_projector.project(bot=bot, entry=entry, call_type=mode)
            )
            if not config:
                raise McpProjectionUnavailable(
                    f"MCP {code} missing required identity/config"
                )
            resources.append(
                {
                    "resourceType": "MCP",
                    "resourcePlatform": "teamclaw",
                    "resourceCode": code,
                    "resourceVersion": "V1.0",
                    "resourceName": entry.get("name") or entry.get("server_name") or code,
                    "resourceConfig": config,
                }
            )
        missing = []
        for asset in snapshot.skills:
            path = asset.git_path or ""
            if path.startswith("local://"):
                if self.observations is None:
                    raise ValueError("Local Skill identity reader is unavailable")
                scope = {
                    "env": env,
                    "bot_id": bot_id,
                    "owner_id": entity,
                    "skill_id": asset.skill_id,
                }
                incarnation = self.observations.get_observed_incarnation_id(**scope)
                identity = (
                    self.observations.get_identity_for_asset(
                        **scope, incarnation_id=incarnation
                    )
                    if incarnation
                    else None
                )
                if identity is None:
                    missing.append(asset.skill_id)
                    continue
                code = identity.provider_skill_id
                try:
                    if not code.startswith("tc:") or str(UUID(code[3:])) != code[3:]:
                        raise ValueError("invalid Local Skill identity")
                except (ValueError, AttributeError) as exc:
                    raise ValueError("invalid Local Skill identity") from exc
                platform, version = "teamclaw", "1.0"
            elif path.startswith("center://"):
                external = path[len("center://") :]
                if not external or not asset.skill_uuid or not asset.sc_version_number:
                    raise ValueError(
                        "Center Skill external identity or active version unavailable"
                    )
                derived = PublicCenterSkillIdentity.derive(
                    tenant=get_current_avernet_tenant(), env=env, skill_code=external
                )
                if asset.skill_uuid not in {external, derived.skill_uuid}:
                    raise ValueError("Center Skill external identity is ambiguous")
                code, platform, version = (
                    external,
                    "skillcenter",
                    asset.sc_version_number,
                )
            elif path.startswith("git://"):
                continue
            else:
                raise ValueError("unrecognized active Skill source")
            resources.append(
                {
                    "resourceType": "SKILL",
                    "resourcePlatform": platform,
                    "resourceCode": code,
                    "resourceVersion": version,
                    "resourceName": asset.name,
                }
            )
        resources.sort(
            key=lambda item: (
                item["resourceType"],
                item["resourcePlatform"],
                item["resourceCode"],
                item["resourceVersion"],
            )
        )
        modifier = bot.get("modifier_id") or bot.get("creator_id")
        if not modifier:
            raise ValueError("Bot modifier or creator is required")
        agent = {
            "platformId": "teamclaw",
            "platformAgentId": _text(bot, "id"),
            "platformAgentCode": f"{bot_id}:{entity}:{env}",
            "agentCode": agent_code,
            "agentName": _text(bot, "bot_name"),
            "agentDesc": bot.get("bot_desc") or "",
            "agentOwner": owner,
            "agentModifier": str(modifier),
            "version": "V1.0",
            "developmentMode": "AGENTIC",
            "status": "ONLINE",
            "visibility": visibility,
            "resourceManifest": resources,
        }
        canonical_target = dict(target)
        if "view_depts" in canonical_target:
            departments = canonical_target["view_depts"] or []
            canonical_target["view_depts"] = sorted(
                {str(x["deptNo"]) for x in departments}
            )
        data = {"agent_info": agent, "missing_local_skill_ids": sorted(missing)}
        payload = json.dumps(
            {"input": data, "target": canonical_target},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return data, sha256(payload.encode()).hexdigest()
