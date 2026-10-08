"""Recover pre-integration capability lists from the original release artifacts."""
import copy
import json
from pathlib import Path, PurePosixPath
from typing import Any

from injector import inject

from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeError
from agentclaw.community.core.workspace.skill_layout import pool_paths_for_engine, runtime_layout_engine_for_bot
from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry
from agentclaw.community.kernel.bot_config import BotConfigArtifact
from agentclaw.community.plugin_api.passport import PassportPlugin, extract_cli_items
from agentclaw.community.core.mcp.services.cli_passport_scope import extract_passport_mcp_items


class DigitalEmployeeHistoryReader:
    @inject
    def __init__(self, engines: EngineSandboxRegistry, passport: PassportPlugin) -> None:
        self._engines, self._passport = engines, passport

    def read(self, bot: dict[str, Any], record) -> dict[str, Any]:
        if record.source_bot_pk != bot["id"] or record.env != bot["env"]:
            raise DigitalEmployeeError("发布记录与 Bot 不匹配")
        ext = record.ext or {}
        snapshot = ext.get("digital_employee_snapshot")
        if isinstance(snapshot, dict):
            return copy.deepcopy(snapshot["capabilities"])
        passport = self._passport.query_agent_passport(bot["bot_id"], bot["owner_id"], entity_id=bot["entity_id"])
        if not passport:
            raise DigitalEmployeeError("无法读取共享 passport 的实际授权")
        cli_items = [{"cliCode": item["cli_code"], "name": item.get("cli_name"),
                      "identityMode": item["identity_mode"].upper()} for item in extract_cli_items(passport)]
        modes = {item["mcp_code"]: item["identity_mode"].upper() for item in extract_passport_mcp_items(passport)}
        if isinstance(ext.get("config_artifact"), dict):
            raw = ext["config_artifact"]
            artifact = BotConfigArtifact.from_dict(raw)
            return {
                "skills": [{"name": skill.name} for skill in artifact.skills],
                "mcps": [{"mcpServerCode": server.server_code, "name": server.name, **({"identityMode": modes[server.server_code]} if server.server_code in modes else {})}
                         for server in artifact.mcp.servers if server.transport != "stdio"],
                "clis": cli_items,
            }
        target = ext.get("build_target_path")
        if not isinstance(target, str) or not target:
            raise DigitalEmployeeError("原发布记录缺少可读取的配置或产物")
        root = Path(target).resolve(strict=True)
        plan = self._engines.resolve(bot["active_engine"]).get_build_plan(bot=bot)
        mcp_path = self.artifact_child(root, plan.mcp_config_relpath)
        raw_mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
        servers = raw_mcp.get("mcpServers") if isinstance(raw_mcp, dict) else None
        if not isinstance(servers, dict):
            raise DigitalEmployeeError("原发布产物 MCP 配置格式无法识别")
        skills_root = self.skill_root(bot, record)
        if not skills_root.is_dir():
            raise DigitalEmployeeError("原发布产物的激活 Skill 目录不可读")
        center = {item["runtime_name"]: item for item in (ext.get("skills_manifest") or {}).get("center_skills", [])}
        skills = []
        for item in sorted(skills_root.iterdir()):
            if item.name in {"skills-local", "skills-repo", "skills-center"}:
                continue
            if item.is_symlink() or (item / "SKILL.md").is_file() or item.name in center:
                skills.append({"name": item.name})
        # passport CLI codes are shared grants, not the executable names in
        # BotConfigArtifact.cli_tools. Read the real grant source; do not turn
        # an executable filename into an enterprise CLI capability identifier.
        return {"skills": skills,
                "mcps": [{"mcpServerCode": code, **({"identityMode": modes[code]} if code in modes else {})} for code, value in servers.items()
                         if isinstance(value, dict) and not value.get("command")],
                "clis": copy.deepcopy(cli_items)}

    @staticmethod
    def artifact_child(root: Path, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise DigitalEmployeeError("发布产物包含无效相对路径")
        child = (root / path).resolve(strict=True)
        if not child.is_relative_to(root):
            raise DigitalEmployeeError("发布产物路径超出原产物目录")
        return child

    def skill_root(self, bot: dict[str, Any], record) -> Path:
        ext = record.ext or {}
        root = Path(ext["build_target_path"]).resolve(strict=True)
        observed = ext.get("active_skill_snapshot_path")
        if observed:
            return self.artifact_child(root, observed)
        plan = self._engines.resolve(bot["active_engine"]).get_build_plan(bot=bot)
        # The compatibility layout declares the active root for all supported
        # filesystem engines. Translate using the same source mappings as the
        # existing build plan, including Claude's separately copied directory.
        runtime = PurePosixPath(pool_paths_for_engine(runtime_layout_engine_for_bot(bot)).active)
        return self.map_runtime_path(root, runtime, plan)

    @staticmethod
    def map_runtime_path(root: Path, runtime: PurePosixPath, plan) -> Path:
        mappings = [(PurePosixPath(plan.source_root_name), PurePosixPath("."))]
        if plan.extra_sync_source_relpath and plan.extra_sync_target_relpath:
            mappings.append((PurePosixPath(plan.extra_sync_source_relpath), PurePosixPath(plan.extra_sync_target_relpath)))
        candidates = set()
        for source, destination in mappings:
            for start in range(1, len(runtime.parts) - len(source.parts) + 1):
                if runtime.parts[start:start + len(source.parts)] == source.parts:
                    tail = runtime.parts[start + len(source.parts):]
                    if tail:
                        candidates.add((destination.joinpath(*tail)).as_posix())
        if len(candidates) != 1:
            raise DigitalEmployeeError("原发布产物无法映射 Engine 声明的技能路径")
        return DigitalEmployeeHistoryReader.artifact_child(root, candidates.pop())
