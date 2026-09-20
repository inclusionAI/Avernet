"""Gate the existing publication pipeline with durable, exact-task approval."""
import copy
from typing import Any
from types import SimpleNamespace
from uuid import uuid4

from injector import inject

from agentclaw.community.core.digital_employee.capabilities import DigitalEmployeeCapabilityReader
from agentclaw.community.core.digital_employee.history import DigitalEmployeeHistoryReader
from agentclaw.community.core.digital_employee.snapshot import capability_digest
from agentclaw.community.core.digital_employee.contracts import (
    DigitalEmployeeError, DigitalEmployeeRepositoryProtocol, DigitalEmployeeSettings,
)
from agentclaw.community.core.digital_employee.events import APPROVAL_RESULT, DigitalEmployeeEvent
from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
from agentclaw.community.plugin_api.digital_employee import DigitalEmployeePlatformPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin




class DigitalEmployeePublicationService:
    @inject
    def __init__(self, settings: DigitalEmployeeSettings,
                 bots: DigitalEmployeeRepositoryProtocol,
                 publications: BotPublishRepositoryProtocol,
                 capabilities: DigitalEmployeeCapabilityReader,
                 platform: DigitalEmployeePlatformPlugin, passport: PassportPlugin,
                 history: DigitalEmployeeHistoryReader) -> None:
        self._settings, self._bots, self._records = settings, bots, publications
        self._capabilities, self._platform, self._passport = capabilities, platform, passport
        self._history = history

    def capture(self, bot: dict[str, Any]) -> dict[str, Any] | None:
        if not (bot.get("ext") or {}).get("digital_employee"):
            return None
        self._enabled()
        return self._capabilities.read(bot)

    def capture_artifact(self, bot: dict[str, Any], artifact_ext: dict[str, Any]) -> dict[str, Any]:
        self._enabled()
        record = SimpleNamespace(source_bot_pk=bot["id"], env=bot["env"], ext=artifact_ext)
        return self._capabilities.read_published(bot, record)

    def _enabled(self) -> None:
        if not self._settings.enabled:
            raise DigitalEmployeeError("已绑定数字员工，请补齐接入配置后再发布")

    def _record(self, publish_id: int):
        record = self._records.get_by_id(publish_id)
        if record is None:
            raise DigitalEmployeeError("Publish record not found")
        return record

    def _binding(self, record):
        bot = self._bots.get_bot(record.source_bot_pk)
        binding = (bot.get("ext") or {}).get("digital_employee")
        if binding:
            self._enabled()
            if binding.get("status") != "ACTIVE":
                raise DigitalEmployeeError("数字员工身份切换尚未完成")
        return bot, binding

    def _save(self, record, approval: dict[str, Any]):
        ext = copy.deepcopy(record.ext or {})
        ext["digital_employee_approval"] = approval
        saved = self._records.compare_and_set_ext(
            publish_id=record.id, expected_ext=record.ext, ext=ext,
        )
        if saved is None:
            raise DigitalEmployeeError("发布状态已变化，请刷新后重试")
        return saved

    def prepare_online(self, publish_id: int, operator: str) -> bool:
        record = self._record(publish_id)
        bot, binding = self._binding(record)
        if not binding:
            return True
        approval = (record.ext or {}).get("digital_employee_approval")
        if approval and approval.get("status") == "APPROVED":
            self.require_approved(publish_id)
            return True
        if approval and approval.get("status") in {"REJECTED", "CANCELED"}:
            raise DigitalEmployeeError("数字员工审批未通过，请修改草稿并重新构建发布版本")
        snapshot = (record.ext or {}).get("digital_employee_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = self._capabilities.read_published(bot, record)
            ext = copy.deepcopy(record.ext or {})
            ext["digital_employee_snapshot"] = snapshot
            saved = self._records.compare_and_set_ext(publish_id=record.id, expected_ext=record.ext, ext=ext)
            if saved is None:
                raise DigitalEmployeeError("发布状态已变化，请刷新后重试")
            record = saved
        if approval is None:
            previous = self._record(record.last_pub_id) if record.last_pub_id else None
            before = {"skills": [], "mcps": [], "clis": []}
            if previous:
                baseline = (previous.ext or {}).get("digital_employee_snapshot")
                if isinstance(baseline, dict):
                    before = baseline["capabilities"]
                else:
                    before = self._history.read(bot, previous)
            approval = {"status": "SUBMITTING", "request_id": f"digital-employee:{record.id}:{uuid4()}",
                        "operator": operator, "work_no": binding["work_no"],
                        "agent_id": binding["agent_id"], "agent_code": snapshot["agent_code"],
                        "snapshot_digest": capability_digest(snapshot), "before": before}
            record = self._save(record, approval)
        if approval.get("task_id"):
            return False
        snapshot = self._capabilities.read_published(bot, record)
        payload = {"operatorId": approval["operator"], "platformCode": self._settings.platform_code,
                   "agentId": binding["agent_id"], "agentCode": snapshot["agent_code"],
                   "changeReason": "服务 Bot 线上发布", "sourceVersion": str(self._record(record.last_pub_id).version) if record.last_pub_id else None,
                   "targetVersion": str(record.version), "before": approval["before"],
                   "after": snapshot["capabilities"]}
        result = self._platform.create_skill_change(binding["work_no"], approval["request_id"], payload)
        task_id = result.get("taskId")
        target = result.get("target") or {}
        if (not isinstance(task_id, str) or not task_id or target.get("agentId") != binding["agent_id"]
                or target.get("employeeWorkNo") != binding["work_no"]
                or target.get("agentCode") != snapshot["agent_code"]
                or target.get("platformCode") != self._settings.platform_code):
            raise DigitalEmployeeError("技能变更任务响应关联信息无效")
        approval = {**approval, "task_id": task_id, "status": "APPROVING",
                    "approval_url": (result.get("approval") or {}).get("detailUrl", "")}
        self._save(record, approval)
        return False

    def require_approved(self, publish_id: int) -> None:
        record = self._record(publish_id)
        bot, binding = self._binding(record)
        if not binding:
            return
        approval = (record.ext or {}).get("digital_employee_approval") or {}
        snapshot = (record.ext or {}).get("digital_employee_snapshot")
        if (approval.get("status") != "APPROVED" or not isinstance(snapshot, dict)
                or approval.get("snapshot_digest") != capability_digest(snapshot)
                or approval.get("work_no") != binding["work_no"]
                or approval.get("agent_id") != binding["agent_id"]):
            raise DigitalEmployeeError("该发布版本尚未通过数字员工审批")

    def handle_approval_result(self, event: dict[str, Any]) -> tuple[int, str] | None:
        self._enabled()
        message = DigitalEmployeeEvent.model_validate(event)
        if message.type != APPROVAL_RESULT:
            raise DigitalEmployeeError("Not an approval event")
        data = message.data
        if data["platformCode"] != self._settings.platform_code:
            return None
        parts = data["requestId"].split(":")
        if len(parts) != 3 or parts[0] != "digital-employee" or not parts[1].isdecimal():
            raise DigitalEmployeeError("Approval request identifier is invalid")
        record = self._record(int(parts[1]))
        _, binding = self._binding(record)
        approval = (record.ext or {}).get("digital_employee_approval") or {}
        if (not binding or data["workNo"] != binding["work_no"]
                or data["requestId"] != approval.get("request_id")
                or data["taskId"] != approval.get("task_id")
                or data["agentId"] != approval.get("agent_id")
                or data["agentCode"] != approval.get("agent_code")):
            raise DigitalEmployeeError("Approval does not match this Bot publication")
        if record.status not in {"validating", "online_pub", "success"}:
            raise DigitalEmployeeError("Approval targets an obsolete publication")
        if approval.get("status") in {"REJECTED", "CANCELED"}:
            return None
        if approval.get("status") == "APPROVED":
            return (record.id, approval["operator"]) if record.status == "validating" else None
        if data["approvalStatus"] == "APPROVED":
            task = self._platform.query_skill_change(data["taskId"])
            target = task.get("target") or {}
            if (target.get("agentId") != binding["agent_id"]
                    or target.get("employeeWorkNo") != binding["work_no"]
                    or target.get("agentCode") != approval["agent_code"]
                    or target.get("platformCode") != self._settings.platform_code
                    or task.get("taskId") != data["taskId"] or task.get("status") != "APPROVED"
                    or (task.get("security") or {}).get("isPassed") is not True):
                raise DigitalEmployeeError("审批通知已收到，技能安全检查尚未全部通过")
        approval = {**approval, "status": data["approvalStatus"], "event_id": str(message.id)}
        self._save(record, approval)
        return (record.id, approval["operator"]) if data["approvalStatus"] == "APPROVED" else None

    def finalize_scope(self, publish_id: int) -> None:
        record = self._record(publish_id)
        bot, binding = self._binding(record)
        if not binding:
            return
        self.require_approved(publish_id)
        capabilities = record.ext["digital_employee_snapshot"]["capabilities"]
        self._passport.update_passport(
            bot_id=bot["bot_id"], user_id=bot["owner_id"],
            resource_scope={
                "mcp_codes": [item["mcpServerCode"] for item in capabilities["mcps"]],
                "mcp_items": [{"mcp_code": item["mcpServerCode"], "mcp_name": item.get("name", ""),
                               "identity_mode": item["identityMode"].lower()}
                              for item in capabilities["mcps"]],
                "cli_items": [{"cli_code": item["cliCode"], "cli_name": item.get("name", ""),
                               "identity_mode": item["identityMode"].lower()}
                              for item in capabilities["clis"]],
                "skill_items": [{"skill_code": item["skillId"], "skill_name": item["name"],
                                 "skill_desc": item.get("description", "")}
                                for item in capabilities["skills"]],
            },
        )
