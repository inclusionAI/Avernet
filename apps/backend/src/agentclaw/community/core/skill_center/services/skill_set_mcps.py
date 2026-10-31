"""Legacy SkillSet mcps behavior."""

import time
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, List, Mapping, Optional

from agentclaw.community.core.devices.models import SynlinkMappingInfo
from agentclaw.community.core.mcp.services._defaults import (
    get_default_mcp_config,
    get_default_mcp_server_codes,
    get_default_mcp_servers,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilitySnapshot,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
)
from agentclaw.community.core.skill_center.path_resolution import (
    canonical_pool_local_path,
)
from agentclaw.community.core.skill_center.runtime_resolver import (
    RuntimeDesiredState,
    resolve_effective_mcp_server_codes,
)

from .skill_set_context import (
    _T,
    logger,
)

if TYPE_CHECKING:
    from agentclaw.community.core.skills_pool.models import RegisteredSkillAsset

class SkillSetMcpCollection:
    def collect_bot_active_mcps(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
        *,
        strict_policy_context: bool = False,
        capability_snapshot: BotCapabilitySnapshot | None = None,
    ) -> List[dict]:
        """Effective MCPs = default policy ∪ installed ∪ Skill dependencies.

        The Default half keeps its proven projection: static engine/template
        defaults plus Default-Set rows, minus this Bot's exclusions. Ordinary
        Sets no longer speak here — their members reach the Bot as
        Installation rows, which the reader answers after its lazy flush.
        Metadata for an installed code comes from the Bot's own Set membership
        rows when available, else a minimal entry (a direct installation has
        no membership row). No MCP Center round-trip.

        Args:
            engine_type: Engine type for scoping. Defaults to self.engine_type.
            capability_snapshot: Reader output for this same Bot and projection.
                When supplied, reuse its Skills and Installation codes while
                retaining the canonical Default/exclusion/metadata logic below.
                Omit for standalone reads; never retain across commands.
        """
        if self._reader is None:
            raise RuntimeError(
                "SkillSetService.collect_bot_active_mcps requires the "
                "capability state reader; construct through "
                "SkillSetServiceFactory"
            )
        if capability_snapshot is not None and (
            capability_snapshot.bot_id != bot_id
            or capability_snapshot.owner_id != entity_id
        ):
            raise ValueError("Capability snapshot belongs to a different Bot")
        effective_engine = engine_type if engine_type is not None else self.engine_type
        effective_ext_info = self._run_effective_mcp_stage(
            stage="default_ext_info",
            bot_id=bot_id,
            engine_type=effective_engine,
            operation=lambda: self._get_default_capabilities_ext_info(
                effective_engine,
                bot_id,
                strict=strict_policy_context,
            ),
            report_has_value=True,
        )
        effective_template_type = self._run_effective_mcp_stage(
            stage="default_template_type",
            bot_id=bot_id,
            engine_type=effective_engine,
            operation=lambda: self._get_default_capabilities_template_type(
                bot_id,
                entity_id=entity_id,
                strict=strict_policy_context,
            ),
            report_has_value=True,
        )
        active_skill_sets = self._run_effective_mcp_stage(
            stage="active_skill_sets",
            bot_id=bot_id,
            engine_type=effective_engine,
            operation=lambda: self._get_all_active_skill_sets_with_default_fallback(
                user_id=entity_id,
                bolt_id=bot_id,
                engine_type=effective_engine,
            ),
            item_count=len,
        )

        # This Bot's exclusions silence a Default member entirely — the row
        # half too: ``get_set_mcp_servers`` filters only the static default
        # codes, and the flush already treats an exclusion as the Default
        # Set's per-Bot deactivation.
        excluded_codes = self._run_effective_mcp_stage(
            stage="default_mcp_exclusions",
            bot_id=bot_id,
            engine_type=effective_engine,
            operation=lambda: set(
                self.skill_set_repo.get_all_excluded_mcps(entity_id, bot_id)
            ),
            item_count=len,
        )

        # Each phase appends entries and marks their codes in
        # ``seen_server_codes``, so a later phase never duplicates an earlier
        # one; ordering is the union's precedence (rows, policy, installed).
        active_mcps: List[dict] = []
        seen_server_codes: set = set()
        default_set_rows = self._run_effective_mcp_stage(
            stage="default_set_mcp_rows",
            bot_id=bot_id,
            engine_type=effective_engine,
            operation=lambda: self._default_set_mcp_rows(
                active_skill_sets,
                user_id=entity_id,
                bot_id=bot_id,
                engine_type=effective_engine,
                template_type=effective_template_type,
                ext_info=effective_ext_info,
                excluded_codes=excluded_codes,
                seen_server_codes=seen_server_codes,
            ),
            item_count=len,
        )
        active_mcps.extend(default_set_rows)
        default_policy_entries = self._run_effective_mcp_stage(
            stage="default_policy_mcp_entries",
            bot_id=bot_id,
            engine_type=effective_engine,
            operation=lambda: self._default_policy_mcp_entries(
                engine_type=effective_engine,
                template_type=effective_template_type,
                ext_info=effective_ext_info,
                excluded_codes=excluded_codes,
                seen_server_codes=seen_server_codes,
            ),
            item_count=len,
        )
        active_mcps.extend(default_policy_entries)
        active_skill_assets = self._run_effective_mcp_stage(
            stage="active_skill_assets",
            bot_id=bot_id,
            engine_type=effective_engine,
            operation=lambda: (
                capability_snapshot.skills
                if capability_snapshot is not None
                else self._active_skill_assets(
                    entity_id=entity_id, bot_id=bot_id, user_id=user_id
                )
            ),
            item_count=len,
        )
        installed_mcp_codes = self._run_effective_mcp_stage(
            stage="installed_mcp_codes",
            bot_id=bot_id,
            engine_type=effective_engine,
            operation=lambda: (
                capability_snapshot.installed_mcp_server_codes
                if capability_snapshot is not None
                else self._installed_mcp_codes(
                    entity_id=entity_id, bot_id=bot_id, user_id=user_id
                )
            ),
            item_count=len,
        )
        effective_non_default_codes = self._run_effective_mcp_stage(
            stage="resolve_non_default_codes",
            bot_id=bot_id,
            engine_type=effective_engine,
            operation=lambda: resolve_effective_mcp_server_codes(
                RuntimeDesiredState(
                    skills=active_skill_assets,
                    installed_mcp_server_codes=installed_mcp_codes,
                )
            ),
            item_count=len,
        )
        non_default_entries = self._run_effective_mcp_stage(
            stage="non_default_mcp_entries",
            bot_id=bot_id,
            engine_type=effective_engine,
            operation=lambda: self._non_default_effective_mcp_entries(
                effective_codes=effective_non_default_codes,
                active_skill_sets=active_skill_sets,
                seen_server_codes=seen_server_codes,
            ),
            item_count=len,
        )
        active_mcps.extend(non_default_entries)

        logger.info(
            f"[collect_bot_active_mcps] bot_id={bot_id}, engine_type={effective_engine}, "
            f"total_mcps={len(active_mcps)}, codes={[m.get('server_code') for m in active_mcps]}"
        )
        return active_mcps

    def _run_effective_mcp_stage(
        self,
        *,
        stage: str,
        bot_id: str,
        engine_type: str | None,
        operation: Callable[[], _T],
        item_count: Callable[[_T], int] | None = None,
        report_has_value: bool = False,
    ) -> _T:
        started_at = time.perf_counter()
        try:
            result = operation()
        except Exception:
            self._log_effective_mcp_timing(
                stage=stage,
                bot_id=bot_id,
                engine_type=engine_type,
                started_at=started_at,
                outcome="error",
            )
            raise
        self._log_effective_mcp_timing(
            stage=stage,
            bot_id=bot_id,
            engine_type=engine_type,
            started_at=started_at,
            outcome="success",
            item_count=item_count(result) if item_count is not None else None,
            has_value=result is not None if report_has_value else None,
        )
        return result

    @staticmethod
    def _log_effective_mcp_timing(
        *,
        stage: str,
        bot_id: str,
        engine_type: str | None,
        started_at: float,
        outcome: str,
        item_count: int | None = None,
        has_value: bool | None = None,
    ) -> None:
        logger.info(
            "[collect_bot_active_mcps] timing stage=%s bot_id=%s engine_type=%s "
            "duration_ms=%s outcome=%s item_count=%s has_value=%s",
            stage,
            bot_id,
            engine_type,
            round((time.perf_counter() - started_at) * 1000),
            outcome,
            item_count,
            has_value,
        )

    def _default_set_mcp_rows(
        self,
        active_skill_sets: List[dict],
        *,
        user_id: str,
        bot_id: str,
        engine_type: Optional[str],
        template_type: Optional[str],
        ext_info,
        excluded_codes: set,
        seen_server_codes: set,
    ) -> List[dict]:
        """The Default Set's association-row members, minus exclusions."""
        rows: List[dict] = []
        for skill_set in active_skill_sets:
            if not skill_set.get("is_default"):
                continue
            for mcp in self.get_set_mcp_servers(
                str(skill_set.get("id")),
                user_id,
                bot_id,
                engine_type,
                template_type,
                ext_info=ext_info,
            ):
                server_code = mcp.get("server_code")
                if (
                    server_code
                    and server_code not in excluded_codes
                    and server_code not in seen_server_codes
                ):
                    seen_server_codes.add(server_code)
                    rows.append(mcp)
        return rows

    def _default_policy_mcp_entries(
        self,
        *,
        engine_type: Optional[str],
        template_type: Optional[str],
        ext_info,
        excluded_codes: set,
        seen_server_codes: set,
    ) -> List[dict]:
        """The static engine/template default configs, minus exclusions."""
        entries: List[dict] = []
        for config in get_default_mcp_servers(
            engine_type,
            template_type,
            ext_info=ext_info,
        ):
            server_code = config["server_code"]
            if server_code in excluded_codes or server_code in seen_server_codes:
                continue  # Skip user-excluded default MCPs
            entry = {
                "server_code": server_code,
                "name": config.get("name") or server_code,
                "description": config.get("description", "Default MCP"),
                "status": "ONLINE",
            }
            if "icon" in config and config.get("icon"):
                entry["icon"] = config["icon"]
            if "headers" in config:
                entry["headers"] = config["headers"]
            seen_server_codes.add(server_code)
            entries.append(entry)
        return entries

    def _installed_mcp_codes(
        self, *, entity_id: str, bot_id: str, user_id: str
    ) -> frozenset:
        """The installed half, read through the reader (which flushes first)."""
        try:
            return self._reader.active_mcp_server_codes(bot_id=bot_id, owner_id=user_id)
        except LocalSkillNotFoundError:
            # Entity-keyed callers (the device-alive MCP sync, diagnostics)
            # land here when ``user_id`` is the Bot's entity, not its owner —
            # Installation is owner-keyed, so resolve the owner and retry
            # before giving the installed half up.
            bot = self._bot_repo.get_by_id_and_entity(bot_id, entity_id)
            owner = str(bot.get("owner_id") or "") if bot else ""
            if bot is not None and owner and owner != user_id:
                return self._reader.active_mcp_server_codes(
                    bot_id=bot_id, owner_id=owner, bot=bot
                )
            logger.warning(
                "[collect_bot_active_mcps] Bot not found: user_id=%s, bot_id=%s",
                user_id,
                bot_id,
            )
            return frozenset()

    def _active_skill_assets(
        self, *, entity_id: str, bot_id: str, user_id: str
    ) -> tuple["RegisteredSkillAsset", ...]:
        """Installed Skill assets whose declarations supply derived MCP codes."""
        try:
            return self._reader.active_skill_assets(bot_id=bot_id, owner_id=user_id)
        except LocalSkillNotFoundError:
            bot = self._bot_repo.get_by_id_and_entity(bot_id, entity_id)
            owner = str(bot.get("owner_id") or "") if bot else ""
            if bot is not None and owner and owner != user_id:
                return self._reader.active_skill_assets(
                    bot_id=bot_id, owner_id=owner, bot=bot
                )
            logger.warning(
                "[collect_bot_active_mcps] Bot not found while reading Skill "
                "dependencies: user_id=%s, bot_id=%s",
                user_id,
                bot_id,
            )
            return ()

    def _non_default_effective_mcp_entries(
        self,
        *,
        effective_codes,
        active_skill_sets: List[dict],
        seen_server_codes: set,
    ) -> List[dict]:
        """Non-default Effective codes, enriched from membership when possible.

        An ordinary Set's association row is the best metadata an installed
        code can have. Direct Installation and Skill dependency supply have no
        membership row, so their entries are minimal.
        """
        missing_codes = [
            code for code in sorted(effective_codes) if code not in seen_server_codes
        ]
        if not missing_codes:
            return []
        membership_metadata: dict[str, dict] = {}
        for skill_set in active_skill_sets:
            if skill_set.get("is_default"):
                continue
            for assoc in self.skill_set_repo.get_mcp_servers_in_set(
                str(skill_set.get("id"))
            ):
                code = assoc.get("server_code")
                if code and code not in membership_metadata:
                    membership_metadata[code] = assoc
        entries: List[dict] = []
        for code in missing_codes:
            assoc = membership_metadata.get(code)
            # One shape for every union entry, matching
            # ``get_set_mcp_servers``'s normalization.
            entries.append(
                {
                    "id": assoc.get("id") if assoc else None,
                    "server_code": code,
                    "name": (assoc.get("name") if assoc else None) or code,
                    "description": (assoc.get("description") if assoc else None) or "",
                    "icon": assoc.get("icon") if assoc else None,
                    "status": "ONLINE",
                    "is_default": False,
                }
            )
            seen_server_codes.add(code)
        return entries

    def collect_bot_mcps(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
    ) -> List[dict]:
        """收集 Bot 关联的所有 MCP（所有 SkillSet + 默认），不调用 MCP Center 补全数据。

        Args:
            engine_type: Engine type for scoping. Defaults to self.engine_type.
        """
        effective_engine = engine_type if engine_type is not None else self.engine_type
        effective_ext_info = self._get_default_capabilities_ext_info(
            effective_engine,
            bot_id,
        )
        effective_template_type = self._get_default_capabilities_template_type(bot_id)
        all_skill_sets = self.list_skill_sets(
            user_id=entity_id,
            bolt_id=bot_id,
        )

        active_mcps = []
        seen_server_codes = set()
        skill_set_mcps_detail = []
        for skill_set in all_skill_sets:
            skill_set_id = str(skill_set.get("id"))
            skill_set_name = skill_set.get("name", "unnamed")
            mcps_in_set = self.get_set_mcp_servers(
                skill_set_id,
                user_id,
                bot_id,
                effective_engine,
                effective_template_type,
                ext_info=effective_ext_info,
            )
            skill_mcp_codes = []
            for mcp in mcps_in_set:
                server_code = mcp.get("server_code")
                skill_mcp_codes.append(server_code)
                if server_code and server_code not in seen_server_codes:
                    seen_server_codes.add(server_code)
                    active_mcps.append(mcp)
            skill_set_mcps_detail.append(
                f"{skill_set_name}[{len(skill_mcp_codes)}]: {skill_mcp_codes}"
            )
        logger.info(
            f"[collect_bot_mcps] bot_id={bot_id}, engine_type={effective_engine}, skillsets MCPs: {'; '.join(skill_set_mcps_detail)}"
        )

        # Get user-excluded default MCPs (across all default skill sets)
        excluded_codes = set(self.skill_set_repo.get_all_excluded_mcps(user_id, bot_id))

        default_mcp_configs = get_default_mcp_servers(
            effective_engine,
            effective_template_type,
            ext_info=effective_ext_info,
        )
        default_mcps = []
        for config in default_mcp_configs:
            server_code = config["server_code"]
            if server_code in excluded_codes:
                continue  # Skip user-excluded default MCPs
            mcp_entry = {
                "server_code": server_code,
                "name": config.get("name") or server_code,
                "description": config.get("description", "Default MCP"),
                "status": "ONLINE",
            }
            if "icon" in config and config.get("icon"):
                mcp_entry["icon"] = config["icon"]
            if "headers" in config:
                mcp_entry["headers"] = config["headers"]
            default_mcps.append(mcp_entry)

        all_mcps = list(active_mcps)
        for default_mcp in default_mcps:
            if default_mcp["server_code"] not in seen_server_codes:
                all_mcps.append(default_mcp)

        logger.info(
            f"[collect_bot_mcps] bot_id={bot_id}, engine_type={effective_engine}, "
            f"total_mcps={len(all_mcps)}, codes={[m.get('server_code') for m in all_mcps]}"
        )
        return all_mcps

    def get_bot_mcp_codes(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
    ) -> List[str]:
        """获取 Bot 关联的所有 MCP 的 server_code 列表。"""
        mcps = self.collect_bot_active_mcps(
            entity_id, bot_id, user_id, entity_type, engine_type
        )
        return [m.get("server_code") for m in mcps if m.get("server_code")]

    def get_bot_mcp_codes_for_env(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str,
        engine_type: str | None,
        target_env: str,
    ) -> list[str]:
        """Collect Passport MCP scope from explicitly env-scoped skill-set data."""
        if target_env not in {"pre", "prod"}:
            raise ValueError("target_env must be pre or prod")
        effective_engine = engine_type if engine_type is not None else self.engine_type
        active_skill_sets = (
            self._get_all_active_skill_sets_for_env_with_default_fallback(
                user_id=entity_id,
                bolt_id=bot_id,
                engine_type=effective_engine,
                env=target_env,
            )
        )

        codes: list[str] = []
        seen: set[str] = set()
        for skill_set in active_skill_sets:
            associations = self.skill_set_repo.get_mcp_servers_in_set_for_env(
                str(skill_set["id"]), env=target_env
            )
            for association in associations:
                code = association.get("server_code")
                if code and code not in seen:
                    seen.add(code)
                    codes.append(code)

        excluded_codes = set(self.skill_set_repo.get_all_excluded_mcps(user_id, bot_id))
        effective_ext_info = self._get_default_capabilities_ext_info(
            effective_engine,
            bot_id,
        )
        effective_template_type = self._get_default_capabilities_template_type(bot_id)
        for default_mcp in get_default_mcp_servers(
            effective_engine,
            effective_template_type,
            ext_info=effective_ext_info,
        ):
            code = default_mcp["server_code"]
            if code not in excluded_codes and code not in seen:
                seen.add(code)
                codes.append(code)
        return codes

    def get_active_skill_sets_mcp_summary(
        self,
        entity_id: str,
        bot_id: str,
        user_id: str,
        entity_type: str = "staff",
        engine_type: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Return active skill sets info plus duplicate server codes.

        Args:
            engine_type: Engine type for scoping (e.g., 'openclaw', 'aicoding').
        """
        effective_engine = engine_type if engine_type is not None else self.engine_type
        effective_ext_info = self._get_default_capabilities_ext_info(
            effective_engine,
            bot_id,
        )
        effective_template_type = self._get_default_capabilities_template_type(bot_id)
        active_skill_sets = self._get_all_active_skill_sets_with_default_fallback(
            user_id=entity_id,
            bolt_id=bot_id,
            engine_type=effective_engine,
        )
        active_skill_sets_info = []
        seen = set()
        duplicate_server_codes = []
        for skill_set in active_skill_sets:
            skill_set_id = str(skill_set.get("id"))
            skill_set_name = skill_set.get("name", "")
            mcps_in_set = self.get_set_mcp_servers(
                skill_set_id,
                user_id,
                bot_id,
                effective_engine,
                effective_template_type,
                ext_info=effective_ext_info,
            )
            active_skill_sets_info.append(
                {
                    "id": skill_set_id,
                    "name": skill_set_name,
                    "mcp_count": len(mcps_in_set),
                }
            )
            for mcp in mcps_in_set:
                sc = mcp.get("server_code")
                if sc and sc not in seen:
                    seen.add(sc)
                elif sc:
                    duplicate_server_codes.append(sc)
        return active_skill_sets_info, duplicate_server_codes
