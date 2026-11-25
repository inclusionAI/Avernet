"""Durable recipient notices attached to the existing risk record."""

import json
from datetime import datetime, timedelta
from typing import Callable

from agentclaw.community.core.bot_public_risk.repository_protocol import (
    RiskRepository,
    RiskCheck,
)

REASONS = {
    "REJECT",
    "INPUT_INVALID",
    "WINDOW_ENDED",
    "CREATION_UNKNOWN",
    "CREATION_FAILED",
}


def _next_due(notices: dict) -> datetime | None:
    times = [
        datetime.fromisoformat(item["due_at"])
        for item in notices.values()
        if item["status"] != "sent"
    ]
    return min(times) if times else None


def stage_notice(
    row: RiskCheck, workflow: dict, reason: str, now: datetime, changes: dict
) -> None:
    """Add the event to the *same* CAS as its cause; trusted scope is server-built."""
    if reason not in REASONS:
        raise ValueError("unknown notice reason")
    if row.client_scope != f"openapi:{row.operator_id}":
        return
    notices = workflow.setdefault("notices", {})
    if reason not in notices:
        notices[reason] = {"status": "pending", "due_at": now.isoformat()}
        changes["notification_due_at"] = _next_due(notices)


class RiskNoticeDelivery:
    def __init__(
        self,
        repository: RiskRepository,
        send: Callable,
        clock: Callable[[], datetime],
    ):
        self.repo, self.send, self.clock = repository, send, clock

    def deliver(self, *, env: str, risk_check_id: str) -> None:
        row = self.repo.get(env=env, risk_check_id=risk_check_id)
        if row is None or row.client_scope != f"openapi:{row.operator_id}":
            return
        now = self.clock()
        if row.notification_due_at is None or row.notification_due_at > now:
            return
        workflow = json.loads(row.workflow_json)
        notices = workflow.get("notices", {})
        reason = next(
            (
                key
                for key, value in notices.items()
                if value["status"] != "sent"
                and not (key == "CREATION_UNKNOWN" and row.check_id is not None)
                and datetime.fromisoformat(value["due_at"]) <= now
            ),
            None,
        )
        if reason is None:
            return
        notices[reason] = {
            "status": "sending",
            "due_at": (now + timedelta(minutes=2)).isoformat(),
        }
        if not self.repo.update_notice(
            env=env,
            risk_check_id=risk_check_id,
            row_version=row.row_version,
            workflow_json=json.dumps(workflow),
            notification_due_at=_next_due(notices),
        ):
            return
        try:
            self.send(
                recipient=row.operator_id,
                reason=reason,
                risk_check_id=row.risk_check_id,
                detail_url=row.detail_url,
            )
        except Exception:
            sent = False
        else:
            sent = True
        for _ in range(3):
            latest = self.repo.get(env=env, risk_check_id=risk_check_id)
            if latest is None:
                return
            state = json.loads(latest.workflow_json)
            current = state.get("notices", {})
            if current.get(reason) != notices[reason]:
                return
            if (
                reason == "CREATION_UNKNOWN"
                and latest.check_id is not None
                and not sent
            ):
                del current[reason]
            else:
                current[reason] = {
                    "status": "sent" if sent else "pending",
                    "due_at": (now if sent else now + timedelta(minutes=1)).isoformat(),
                }
            if self.repo.update_notice(
                env=env,
                risk_check_id=risk_check_id,
                row_version=latest.row_version,
                workflow_json=json.dumps(state),
                notification_due_at=_next_due(
                    {
                        k: v
                        for k, v in current.items()
                        if not (k == "CREATION_UNKNOWN" and latest.check_id is not None)
                    }
                ),
            ):
                return


class WorkOrderRiskNoticeSender:
    """Send only an overall status and detail link through the existing inbox."""

    def __init__(self, service):
        self.service = service

    def __call__(
        self, *, recipient: str, reason: str, risk_check_id: str, detail_url: str | None
    ) -> None:
        from agentclaw.community.core.work_orders.models import (
            NotificationCategory,
            WorkOrderEventType,
        )

        descriptions = {
            "REJECT": "Bot 公开风险检测未通过，请查看详情并处理。",
            "INPUT_INVALID": "Bot 公开申请的配置或权限已变化，请重新检测。",
            "WINDOW_ENDED": "Bot 公开风险检测的自动查询已暂停，可手动刷新。",
            "CREATION_UNKNOWN": "Bot 公开风险检测创建结果未知，请联系管理员处理。",
            "CREATION_FAILED": "Bot 公开风险检测创建失败，请联系管理员处理。",
        }
        content = {"text": descriptions[reason]}
        if detail_url:
            content["detail_url"] = detail_url
        self.service.create_work_order_event(
            event_category=NotificationCategory.NOTICE,
            biz_type="bot_public_risk",
            biz_id=f"{risk_check_id}:{reason}",
            event_type=WorkOrderEventType.BOT_PUBLIC_RISK.value,
            applicant_user_id=None,
            approver_user_ids=[],
            recipient_user_ids=[recipient],
            title="Bot 公开风险检测需要关注",
            content=content,
            apply_reason=None,
            biz_data={"risk_check_id": risk_check_id},
            actor_id=recipient,
        )


class RiskNoticeTaskHandler:
    task_type = "bot_public_risk_notice"

    def __init__(self, delivery: RiskNoticeDelivery, env_provider: Callable[[], str]):
        self.delivery, self.env_provider = delivery, env_provider

    def handle(self, payload: dict | None):
        from agentclaw.community.core.task_queue.types import Complete, Reschedule
        from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

        if not isinstance(payload, dict):
            raise ValueError("invalid notification payload")
        tenant, env, risk_id = (
            payload.get(k) for k in ("tenant", "env", "risk_check_id")
        )
        if not all(
            isinstance(v, str) and v and v.strip() == v for v in (tenant, env, risk_id)
        ):
            raise ValueError("invalid notification identity")
        if env != self.env_provider():
            raise ValueError("notification environment differs from worker")
        with avernet_tenant_scope(tenant):
            self.delivery.deliver(env=env, risk_check_id=risk_id)
            row = self.delivery.repo.get(env=env, risk_check_id=risk_id)
            if row is None or row.notification_due_at is None:
                return Complete()
            delay = max(
                0, (row.notification_due_at - self.delivery.clock()).total_seconds()
            )
            return Reschedule(delay_seconds=delay)
