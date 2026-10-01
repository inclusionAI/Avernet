"""Read real active capabilities and exact package references for approval."""
import copy
import os
from pathlib import Path, PurePosixPath
from typing import Any

from injector import inject

from agentclaw.community.core.mcp.services.cli_passport_scope import extract_passport_mcp_items
from agentclaw.community.core.mcp.services.repositories import BotMCPProvider
from agentclaw.community.core.digital_employee.history import DigitalEmployeeHistoryReader
from agentclaw.community.kernel.bot_config import BotConfigArtifact
from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry
from agentclaw.community.core.digital_employee.packages import DigitalEmployeePackageReader
from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeError
from agentclaw.community.core.repository.protocols.identity import CallerIdentityRepositoryProtocol
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.capability_state_contract import BotCapabilityStateReaderProtocol
from agentclaw.community.core.skill_center.canonical_center_store import CanonicalCenterVersionIdentity
from agentclaw.community.core.skill_center.center_content_distribution import (
    CenterContentDistribution, CenterContentReadyPackage,
)
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin, extract_cli_items


class DigitalEmployeeCapabilityReader:
    @inject
    def __init__(self, reader: BotCapabilityStateReaderProtocol,
                 skills: SkillRepository, packages: CenterContentDistribution,
                 identities: CallerIdentityRepositoryProtocol,
                 mcps: MCPCenterPlugin, passport: PassportPlugin, scan_packages: DigitalEmployeePackageReader,
                 history: DigitalEmployeeHistoryReader, engines: EngineSandboxRegistry, mcp_provider: BotMCPProvider) -> None:
        self._reader, self._skills, self._packages = reader, skills, packages
        self._identities, self._mcps, self._passport = identities, mcps, passport
        self._scan_packages, self._history, self._engines = scan_packages, history, engines
        self._mcp_provider = mcp_provider

    def read(self, bot: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._reader.active_capabilities(
            bot_id=bot["bot_id"], owner_id=bot["owner_id"], bot=bot,
        )
        skill_items = []
        package_hashes = {}
        package_keys = {}
        skill_sources = {}
        for asset in snapshot.skills:
            skill = self._skills.get_by_id(str(asset.skill_id))
            if not skill:
                raise DigitalEmployeeError("无法读取已激活 Skill 元数据")
            exact_center = bool(asset.skill_uuid and asset.sc_version_number)
            code = asset.skill_uuid if exact_center else str(asset.skill_id)
            if exact_center:
                package = self._packages.prepare(CanonicalCenterVersionIdentity(
                    asset.skill_uuid, asset.sc_version_number
                ))
                if not isinstance(package, CenterContentReadyPackage):
                    raise DigitalEmployeeError("Skill 精确版本扫描包尚未就绪")
                package_url = package.signed_url
                version = asset.sc_version_number
                package_hash = package.package_sha256
            else:
                package = self._scan_packages.read(bot, asset)
                package_url, package_hash = package["url"], package["sha256"]
                version = package_hash
                package_keys[str(asset.skill_id)] = package["key"]
            if not isinstance(package_hash, str) or len(package_hash) != 64:
                raise DigitalEmployeeError(f"Skill {asset.name} 缺少精确包摘要，不能审批未确认的内容")
            package_hashes[code] = package_hash
            skill_sources[code] = "antmcp" if asset.skill_uuid and asset.sc_version_number else "custom"
            skill_items.append({"skillId": code,
                                "name": asset.name, "ossAddress": package_url,
                                "description": skill.get("description") or "",
                                "version": version, "owner": str(skill.get("user_id") or bot["owner_id"]),
                                "modifier": str(skill.get("modifier") or bot["owner_id"])})
        passport = self._passport.query_agent_passport(
            bot["bot_id"], bot["owner_id"], entity_id=bot["entity_id"]
        )
        if not passport or not passport.get("agent_code"):
            raise DigitalEmployeeError("无法读取 passport 能力")
        historical_modes = {item["mcp_code"]: item["identity_mode"] for item in extract_passport_mcp_items(passport)}
        call_types = self._identities.list_draft_call_types(bot["id"], bot["active_engine"])
        mcp_items = []
        effective_mcps = self._mcp_provider.collect_bot_active_mcps(
            entity_id=bot["entity_id"], bot_id=bot["bot_id"], user_id=bot["owner_id"],
            entity_type=bot.get("entity_type") or "staff", engine_type=bot["active_engine"],
        )
        for code in sorted({item["server_code"] for item in effective_mcps}):
            detail = self._mcps.get_mcp_detail(code)
            if not isinstance(detail, dict):
                raise DigitalEmployeeError("无法读取 MCP 元数据")
            if detail.get("accessLevel") == "LOCAL":
                continue
            mode = str(call_types.get(code, historical_modes.get(code, "owner"))).upper()
            if mode not in {"OWNER", "CALLER"}:
                raise DigitalEmployeeError("MCP 执行模式无效")
            mcp_items.append({"mcpServerCode": code, "name": detail.get("name") or code,
                              "description": detail.get("description") or "", "identityMode": mode})
        cli_items = [{"cliCode": item["cli_code"], "name": item.get("cli_name") or item["cli_code"],
                      "description": item.get("cli_desc") or "",
                      "identityMode": str(item.get("identity_mode", "owner")).upper()}
                     for item in extract_cli_items(passport)]
        return {"agent_code": passport["agent_code"], "package_hashes": package_hashes, "package_keys": package_keys, "skill_sources": skill_sources,
                "capabilities": {"skills": skill_items, "mcps": mcp_items, "clis": cli_items}}

    def read_published(self, bot: dict[str, Any], record) -> dict[str, Any]:
        if record.source_bot_pk != bot["id"] or record.env != bot["env"]:
            raise DigitalEmployeeError("发布记录与 Bot 不匹配")
        snapshot = copy.deepcopy((record.ext or {}).get("digital_employee_snapshot"))
        if not isinstance(snapshot, dict):
            snapshot = self._restore_published(bot, record)
        for skill in snapshot["capabilities"]["skills"]:
            code = skill["skillId"]
            key = snapshot.get("package_keys", {}).get(code)
            if key:
                skill["ossAddress"] = self._scan_packages.sign(key)
            elif snapshot.get("skill_sources", {}).get(code) == "antmcp":
                package = self._packages.prepare(CanonicalCenterVersionIdentity(code, skill["version"]))
                if not isinstance(package, CenterContentReadyPackage):
                    raise DigitalEmployeeError("发布版本 Skill 扫描包不可用")
                if package.package_sha256 != snapshot["package_hashes"][code]:
                    raise DigitalEmployeeError("发布版本 Skill 内容摘要不匹配")
                skill["ossAddress"] = package.signed_url
        return snapshot

    def _restore_published(self, bot: dict[str, Any], record) -> dict[str, Any]:
        capabilities = self._history.read(bot, record)
        passport = self._passport.query_agent_passport(bot["bot_id"], bot["owner_id"], entity_id=bot["entity_id"])
        if not passport or not passport.get("agent_code"):
            raise DigitalEmployeeError("无法读取 passport 编码")
        ext = record.ext or {}
        artifact = BotConfigArtifact.from_dict(ext["config_artifact"]) if ext.get("config_artifact") else None
        center = {item["runtime_name"]: item for item in (ext.get("skills_manifest") or {}).get("center_skills", [])}
        refs = {item.name: item for item in artifact.skills} if artifact else {}
        if artifact and len(refs) != len(artifact.skills):
            raise DigitalEmployeeError("发布产物存在同名 Skill，不能确定扫描对象")
        hashes, keys, sources, skills = {}, {}, {}, []
        for previous in capabilities["skills"]:
            name = previous["name"]
            ref = refs.get(name)
            exact = center.get(name)
            if ref and ref.store == "skill-center":
                parts = ref.path.split("/")
                if len(parts) != 2:
                    raise DigitalEmployeeError("发布产物的 Skill Center 版本引用无效")
                exact = {"skill_uuid": parts[0], "sc_version_number": parts[1]}
            if exact:
                code, version = exact["skill_uuid"], exact["sc_version_number"]
                package = self._packages.prepare(CanonicalCenterVersionIdentity(code, version))
                if not isinstance(package, CenterContentReadyPackage):
                    raise DigitalEmployeeError("原发布 Skill Center 精确版本包不可用")
                url, digest = package.signed_url, package.package_sha256
                metadata = self._skills.get_by_uuid(code) or {}
                source = "antmcp"
            else:
                if ref and ref.store == "skill-repo":
                    metadata = self._skills.get_by_git_path(f"git://{ref.path}")
                else:
                    metadata = self._skills.get_bot_local_by_name(bot_id=bot["bot_id"], name=name, user_id=bot["owner_id"])
                    if not metadata:
                        metadata = self._skills.get_by_link_name(name, bot["bot_id"])
                if not metadata:
                    raise DigitalEmployeeError(f"原发布 Skill {name} 的身份元数据不可用")
                code = str(metadata["id"])
                if ref:
                    store = artifact.stores.get(ref.store)
                    if store is None:
                        raise DigitalEmployeeError("发布 Skill 缺少内容存储坐标")
                    package = self._scan_packages.from_store(bot, code, store, ref.path)
                else:
                    root = Path(ext["build_target_path"]).resolve(strict=True)
                    plan = self._engines.resolve(bot["active_engine"]).get_build_plan(bot=bot)
                    directory = self._history.skill_root(bot, record) / name
                    package = None
                    if directory.is_symlink():
                        target = PurePosixPath(os.readlink(directory))
                        if target.is_absolute():
                            for delivery in (ext.get("skills_manifest") or {}).get("shared_corpora", []):
                                if delivery["corpus"] != "repo" or not target.is_relative_to(delivery["runtime_path"]):
                                    continue
                                relative = target.relative_to(delivery["runtime_path"]).as_posix()
                                if metadata.get("git_path") != f"git://{relative}":
                                    raise DigitalEmployeeError("原发布 Skill 的共享内容引用与身份不一致")
                                package = self._scan_packages.from_shared_store(bot, code, delivery["store_prefix"], relative)
                                break
                            if package is None:
                                directory = self._history.map_runtime_path(root, target, plan)
                    if package is None:
                        if not directory.resolve(strict=True).is_relative_to(root):
                            raise DigitalEmployeeError("原发布 Skill 内容超出其发布产物")
                        package = self._scan_packages.from_directory(bot, code, directory)
                url, digest, version = package["url"], package["sha256"], package["sha256"]
                keys[code], source = package["key"], "custom"
            hashes[code], sources[code] = digest, source
            skills.append({"skillId": code, "name": name, "version": version,
                           "ossAddress": url, "description": metadata.get("description") or ""})
        capabilities["skills"] = skills
        # Historical MCP entries without identity metadata use the established
        # legacy owner mode, the same default as Passport's sparse mode reader.
        for item in capabilities["mcps"]:
            item.setdefault("identityMode", "OWNER")
        return {"agent_code": passport["agent_code"], "capabilities": capabilities,
                "package_hashes": hashes, "package_keys": keys, "skill_sources": sources}
