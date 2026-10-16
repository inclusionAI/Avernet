"""评测门禁 seam（EvaluationGatePlugin）在 PublishFlowService.process() 的
拦截行为测试（v5.3 方案 B2 / D7 / D10）：

- gate seam 未绑定（None）→ 原发布语义（DRAFT→BUILDING 照常推进）。
- gate seam 存在但决策放行 → 照常推进。
- 门禁阻断（正式模式不通过 / 评测中）→ 不做 CAS 推进，status 保持 draft，
  决策摘要写入发布单 ext.gate，结果 message 带阻断文案。
- gate 实现异常 → fail-closed（不默认放行，PRD §14）。
"""

from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.plugin_api.evaluation_gate import (
    GATE_ERROR,
    GATE_EVAL_FAILED_ENFORCE,
    GatePublishDecision,
)

from tests.community.core.service_bot.services.test_publish_flow_service import (
    _arca_router,
    _make_publish_record,
    _pf,
)


class _FakeGate:
    """可编程 gate seam：固定返回给定决策或抛异常。"""

    def __init__(self, decision: GatePublishDecision | None = None, error: Exception | None = None):
        self._decision = decision
        self._error = error
        self.calls: list[dict] = []

    async def check_publish_gate(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        assert self._decision is not None
        return self._decision

    async def query_gate_policy(self, **kwargs):  # pragma: no cover - seam 未被本测试触达
        raise NotImplementedError


def _mk_service(gate, *, record=None):
    """构造带 gate seam 的 PublishFlowService（复用 _pf 的 Mock 默认集）。"""
    record = record or _make_publish_record(status=PublishStatus.DRAFT.value)
    publish_service = Mock()
    publish_service.get_publish_by_id.return_value = record
    publish_service.mutate_publish_ext.return_value = record

    bot_service = Mock()
    bot_service.get_bot.return_value = {"bot_id": "bot-source", "space_id": "sp-1"}

    svc = _pf(
        publish_service,
        Mock(),  # bot_build_service
        Mock(),  # baas_service
        bot_service,
        _arca_router(),
        evaluation_gate=gate,
    )
    svc._advance_status = Mock(return_value=True)
    return svc, publish_service, record


@pytest.mark.asyncio
async def test_no_gate_seam_keeps_original_draft_flow():
    """D7：未绑定 gate seam 的部署（其它消费者）发布字节不变。"""
    record = _make_publish_record(status=PublishStatus.DRAFT.value)
    publish_service = Mock()
    publish_service.get_publish_by_id.return_value = record

    svc = _pf(
        publish_service,
        Mock(),
        Mock(),
        Mock(),
        _arca_router(),
    )
    svc._advance_status = Mock(return_value=True)

    result = await svc.process(publish_id=1, operator="u1")

    assert result.status == PublishStatus.BUILDING.value
    svc._advance_status.assert_called_once()


@pytest.mark.asyncio
async def test_gate_allowed_decision_advances():
    """gate 命中名单但决策放行（通过/观察模式）→ 照常推进。"""
    gate = _FakeGate(GatePublishDecision(allowed=True, status="eval_pass", hit=True))
    svc, publish_service, record = _mk_service(gate)

    result = await svc.process(publish_id=1, operator="u1")

    assert result.status == PublishStatus.BUILDING.value
    svc._advance_status.assert_called_once()
    publish_service.mutate_publish_ext.assert_not_called()
    # gate 拿到 space/bot 上下文
    assert gate.calls[0]["space_id"] == "sp-1"
    assert gate.calls[0]["bot_id"] == "bot-source"
    assert gate.calls[0]["platform"] == "TC"


@pytest.mark.asyncio
async def test_gate_blocked_enforce_stops_draft_advance():
    """正式模式门禁不通过 → CAS 不推进、ext.gate 落摘要、返回 draft 结果。"""
    gate = _FakeGate(
        GatePublishDecision(
            allowed=False,
            status=GATE_EVAL_FAILED_ENFORCE,
            reason="预发发布已被评测门禁阻断，如需继续请申请放行",
            hit=True,
            hit_type="space",
            mode="enforce",
            policy_name="风控团队预发门禁策略",
            failure_summary="P0 用例失败 2 条",
            report_url="https://gate.example/report/1",
        )
    )
    svc, publish_service, record = _mk_service(gate)

    result = await svc.process(publish_id=1, operator="u1")

    svc._advance_status.assert_not_called()
    assert result.status == PublishStatus.DRAFT.value
    assert "评测门禁" in result.message
    assert result.data["gate"]["status"] == GATE_EVAL_FAILED_ENFORCE
    assert result.data["gate"]["mode"] == "enforce"
    assert result.data["gate"]["report_url"] == "https://gate.example/report/1"

    publish_service.mutate_publish_ext.assert_called_once()
    publish_id, mutator = publish_service.mutate_publish_ext.call_args[0]
    assert publish_id == record.id
    ext: dict = {}
    mutator(ext)
    assert ext["gate"]["status"] == GATE_EVAL_FAILED_ENFORCE
    assert ext["gate"]["failure_summary"] == "P0 用例失败 2 条"
    assert ext["gate"]["checked_at"]


@pytest.mark.asyncio
async def test_gate_crash_fails_closed():
    """seam 实现异常 → fail-closed（PRD §14：不建议默认放行）。"""
    gate = _FakeGate(error=RuntimeError("gate platform 500"))
    svc, publish_service, record = _mk_service(gate)

    result = await svc.process(publish_id=1, operator="u1")

    svc._advance_status.assert_not_called()
    assert result.status == PublishStatus.DRAFT.value
    assert result.data["gate"]["status"] == GATE_ERROR
    publish_service.mutate_publish_ext.assert_called_once()


@pytest.mark.asyncio
async def test_gate_space_resolve_failure_does_not_skip_gate():
    """space 解析失败（bot_service 异常）不影响门禁判定继续执行。"""
    record = _make_publish_record(status=PublishStatus.DRAFT.value)
    publish_service = Mock()
    publish_service.get_publish_by_id.return_value = record

    bot_service = Mock()
    bot_service.get_bot.side_effect = RuntimeError("bot gone")

    gate = _FakeGate(GatePublishDecision(allowed=False, status="evaluating", reason="评测中，请等待结果"))
    svc = _pf(
        publish_service,
        Mock(),
        Mock(),
        bot_service,
        _arca_router(),
        evaluation_gate=gate,
    )
    svc._advance_status = Mock(return_value=True)

    result = await svc.process(publish_id=1, operator="u1")

    svc._advance_status.assert_not_called()
    assert result.data["gate"]["status"] == "evaluating"
    assert gate.calls[0]["space_id"] == ""