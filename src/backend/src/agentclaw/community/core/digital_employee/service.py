"""Recoverable metadata binding driven by verified platform events."""
from typing import Any
import hashlib
import json

from injector import inject

from agentclaw.community.core.execution_identity.protocols import ExecutionIdentityServiceProtocol
from agentclaw.community.core.digital_employee.contracts import (
    DigitalEmployeeError, DigitalEmployeeRepositoryProtocol, DigitalEmployeeSettings,
)
from agentclaw.community.core.digital_employee.events import DigitalEmployeeEvent, ONBOARDED
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.repository.protocols.identity import ExecutionIdentityRepositoryProtocol
from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
from agentclaw.community.plugin_api.digital_employee import DigitalEmployeePlatformPlugin
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin


class DigitalEmployeeService:
    @inject
    def __init__(self, settings: DigitalEmployeeSettings,
                 repository: DigitalEmployeeRepositoryProtocol,
                 platform: DigitalEmployeePlatformPlugin,
                 identities: ExecutionIdentityServiceProtocol,
                 devices: DeviceBindingRepository,
                 publications: BotPublishRepositoryProtocol,
                 identity_bindings: ExecutionIdentityRepositoryProtocol,
                 mcp_center: MCPCenterPlugin, passport: PassportPlugin) -> None:
        self._settings, self._repo, self._platform = settings, repository, platform
        self._identities, self._devices, self._publications = identities, devices, publications
        self._identity_bindings = identity_bindings
        self._mcp_center, self._passport = mcp_center, passport

    def _require_enabled(self) -> None:
        if not self._settings.enabled:
            raise DigitalEmployeeError("数字员工接入未启用，请补齐配置")

    def _runnable(self, bot: dict[str, Any]) -> bool:
        binding_ids = {bot.get("binding_id")}
        for record in self._publications.list_by_source_bot(bot["id"], bot["env"]):
            if record.status in {"success", "validating"}:
                binding_ids.update(((record.ext or {}).get("binding") or {}).values())
        for binding_id in binding_ids - {None}:
            binding = self._devices.get_by_id(binding_id)
            if binding is not None and binding.status == "ACTIVE":
                return True
        return False

    def list_bindable(self, creator_no: str) -> list[dict[str, Any]]:
        self._require_enabled()
        if not creator_no.strip():
            raise DigitalEmployeeError("creatorNo is required")
        result = []
        for bot in self._repo.list_service_bots(creator_no):
            if not self._runnable(bot):
                continue
            binding = bot["ext"].get("digital_employee") or {}
            result.append({"agentId": str(bot["id"]), "platform": self._settings.platform_code,
                           "agentName": bot.get("bot_name"), "description": bot.get("bot_desc"),
                           "creatorNo": bot["creator_id"], "aiWorkNo": binding.get("work_no")})
        return result

    def handle_onboarded(self, event: dict[str, Any]) -> None:
        self._require_enabled()
        message = DigitalEmployeeEvent.model_validate(event)
        if message.type != ONBOARDED:
            raise DigitalEmployeeError("Not an onboarded event")
        data = message.data
        if data.get("platformCode") and data["platformCode"] != self._settings.platform_code:
            return
        detail = self._platform.get_employee(data["workNo"])
        if detail.get("platformCode") != self._settings.platform_code:
            return
        if detail.get("workNo") != data["workNo"] or detail.get("status") != "APPROVED":
            raise DigitalEmployeeError("Employee details do not match approved registration")
        agent_id = detail.get("agentId")
        if not isinstance(agent_id, str) or not agent_id.isdecimal():
            raise DigitalEmployeeError("Employee agentId does not identify Bot metadata")
        if data.get("agentId") and data["agentId"] != agent_id:
            raise DigitalEmployeeError("Event and employee agentId mismatch")
        bot = self._repo.get_bot(int(agent_id))
        if bot["bot_type"] != "service":
            raise DigitalEmployeeError("Only service Bots may bind digital employees")
        binding = bot["ext"].get("digital_employee") or {}
        if binding.get("work_no") == data["workNo"] and binding.get("phase") == "ACTIVE":
            return
        if not self._runnable(bot):
            raise DigitalEmployeeError("服务 Bot 没有可运行实例")
        binding = self._repo.begin_binding(bot["id"], data["workNo"], str(message.id), detail)
        args = {"bot_id": bot["bot_id"], "owner_id": bot["owner_id"],
                "modifier_id": bot["owner_id"]}
        # Claim before the non-idempotent reissue. A retry reconciles the durable
        # pending identity, never blindly repeats a force-reissue after a timeout.
        if self._repo.change_binding_phase(bot["id"], data["workNo"], "DETAIL_READY", "REISSUING"):
            result = self._identities.change_execution_identity(
                **args, action="reissue", execution_workno=data["workNo"],
                identity_type="DIGITAL_EMPLOYEE",
            )
        else:
            active = self._identity_bindings.get_active(bot_pk=bot["id"])
            if (active is not None and active.execution_workno == data["workNo"]
                    and active.identity_type == "DIGITAL_EMPLOYEE"):
                # The identity transaction commits only after runtime injection.
                # Recover a crash between that commit and the metadata projection.
                result = {"status": "ACTIVE", "token_injected": True}
            else:
                pending = self._identity_bindings.get_pending(bot_pk=bot["id"])
                if pending is None:
                    # A crash may occur after claiming the metadata phase but
                    # before begin_pending. The identity repository serializes
                    # begin_pending on the Bot row, so only one retry can issue
                    # credentials. Remote uncertainty always retains a pending
                    # identity and must follow activation instead.
                    result = self._identities.change_execution_identity(
                        **args, action="reissue", execution_workno=data["workNo"],
                        identity_type="DIGITAL_EMPLOYEE",
                    )
                else:
                    result = self._identities.change_execution_identity(**args, action="activate")
        if result.get("status") != "ACTIVE" or result.get("token_injected") is not True:
            raise DigitalEmployeeError("数字员工执行身份尚未完成授权或实例注入，请重试查询")
        if not self._repo.change_binding_phase(bot["id"], data["workNo"], "REISSUING", "ACTIVE"):
            latest = self._repo.get_bot(bot["id"])["ext"].get("digital_employee") or {}
            if latest.get("work_no") != data["workNo"] or latest.get("phase") != "ACTIVE":
                raise DigitalEmployeeError("Employee binding changed concurrently")

    def query_permissions(self, bot_pk: int, mcp_codes: list[str]) -> dict[str, Any]:
        self._require_enabled()
        bot = self._repo.get_bot(bot_pk)
        binding = bot["ext"].get("digital_employee") or {}
        if binding.get("status") != "ACTIVE":
            raise DigitalEmployeeError("Bot has no active employee binding")
        if not 1 <= len(mcp_codes) <= 100:
            raise DigitalEmployeeError("MCP query requires 1 to 100 codes")
        return self._platform.query_mcp_permissions(binding["work_no"], mcp_codes)

    def prepare_mcp_change(self, bot: dict[str, Any], desired: list[dict[str, Any]],
                           historical: list[dict[str, Any]], operator: str) -> list[dict[str, Any]]:
        binding = (bot.get("ext") or {}).get("digital_employee")
        if not binding:
            return desired
        self._require_enabled()
        if binding.get("status") != "ACTIVE":
            raise DigitalEmployeeError("数字员工身份切换尚未完成，暂不能变更 MCP")
        private_codes = []
        for item in desired:
            code = item["mcp_code"]
            detail = self._mcp_center.get_mcp_detail(code)
            if not isinstance(detail, dict) or not detail.get("accessLevel"):
                raise DigitalEmployeeError("无法确认 MCP 公开性，未发起权限申请")
            if detail["accessLevel"] not in {"PUBLIC", "LOCAL"}:
                private_codes.append(code)
        if private_codes:
            # Scope-derived key survives replay of the same desired-state update.
            codes = sorted(set(private_codes))
            key = hashlib.sha256(json.dumps(
                [bot["id"], binding["work_no"], codes], separators=(",", ":")
            ).encode()).hexdigest()
            passport = self._passport.query_agent_passport(
                bot["bot_id"], bot["owner_id"], entity_id=bot["entity_id"]
            )
            if not passport or not passport.get("agent_code"):
                raise DigitalEmployeeError("无法查询 passport 编码")
            result = self._platform.apply_mcp_permissions(key, {
                "workerId": binding["work_no"], "agentId": binding["agent_id"],
                "agentCode": passport["agent_code"], "mcpCodes": codes,
                "reason": "服务 Bot 草稿能力变更",
            })
            if result.get("success") is not True:
                raise DigitalEmployeeError("部分 MCP 权限申请失败，请查询申请进度后重试")
        # The draft and published deployment share credentials. Only a successful
        # final publication may remove historical grants from this union.
        merged = {item["mcp_code"]: item for item in historical}
        merged.update({item["mcp_code"]: item for item in desired})
        return list(merged.values())
