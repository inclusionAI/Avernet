"""Community no-op implementation of the service-Bot evaluation gate."""

from __future__ import annotations

from typing import Any, Sequence

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.service_bot_eval import (
    ServiceBotEvalContext,
    ServiceBotEvalFile,
    ServiceBotEvalPlugin,
)

logger = get_logger()


def skipped_result(message: str = "当前环境未启用统一评测门禁") -> dict[str, Any]:
    return {
        "gate_status": "SKIPPED",
        "gate_result": "GATE_SKIPPED",
        "allowed": True,
        "reason": "EVAL_GATE_DISABLED",
        "display_message": message,
        "next_action": "CONTINUE",
    }


class CommunityServiceBotEval(ServiceBotEvalPlugin):
    """Community has no SecurityAC integration, so releases bypass the gate."""

    async def precheck(
        self,
        context: ServiceBotEvalContext,
        *,
        trigger_source: str = "draft_to_validate",
    ) -> dict[str, Any]:
        return skipped_result()

    async def detail(self, context: ServiceBotEvalContext) -> dict[str, Any]:
        return {
            "bot_id": context.bot_id,
            "bot_version": context.bot_version,
            "owner_permission": {"operable": True, "reason": ""},
            "eval_scope": {
                "hit_gate_whitelist": False,
                "gate_drm_config_version": "",
                "gate_batch_no": "",
            },
            "tabs": {
                "simple_cert_submit": {"enabled": False, "visible": False},
                "restart_eval": {"enabled": False, "visible": False},
                "eval_history": {"enabled": True, "visible": True},
            },
            "cert_state": {
                "gateway_status": "SKIPPED",
                "cert_id": "",
                "upload_id": "",
                "cert_result": None,
                "cert_status": "MISSING",
                "cert_summary": "当前环境未启用统一评测门禁",
                "report_url": "",
            },
        }

    async def submit(
        self,
        context: ServiceBotEvalContext,
        files: Sequence[ServiceBotEvalFile],
        *,
        trigger_source: str,
    ) -> dict[str, Any]:
        logger.info(
            "[ServiceBotEval] skipped submit bot=%s version=%s trigger=%s",
            context.bot_id,
            context.bot_version,
            trigger_source,
        )
        return {
            "submission_id": "",
            "task_type": "SIMPLE_CERT_SUBMIT",
            "task_status": "SKIPPED",
            "next_step": "NONE",
            "message": "当前环境未启用统一评测门禁",
        }

    async def get_submission(
        self,
        context: ServiceBotEvalContext,
        submission_id: str,
    ) -> dict[str, Any]:
        return {
            "submission_id": submission_id,
            "task_type": "SIMPLE_CERT_SUBMIT",
            "task_status": "SKIPPED",
            "message": "当前环境未启用统一评测门禁",
        }

    async def history(
        self,
        context: ServiceBotEvalContext,
        *,
        page_no: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        return {
            "records": [],
            "page": {"page_no": page_no, "page_size": page_size, "total": 0},
        }

    async def sync(self, context: ServiceBotEvalContext) -> dict[str, Any]:
        return skipped_result()
