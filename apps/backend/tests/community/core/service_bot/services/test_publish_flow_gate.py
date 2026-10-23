"""评测门禁 seam（EvaluationGatePlugin）在 PublishFlowService.process() 的
拦截行为测试（v5.3 方案 B2 / D7 / D10）：

- gate seam 未绑定（None）→ 原发布语义（DRAFT→BUILDING 照常推进）。
- gate seam 存在但决策放行 → 照常推进。
- 门禁阻断（正式模式不通过 / 评测中）→ 不做 CAS 推进，status 保持 draft，
  决策摘要写入发布单 ext.gate，结果 message 带阻断文案。
- gate 实现异常 → fail-open（不阻断主流程，灰度期门禁平台抖动不应卡发布）。
  真正的拦截由 corp DRM 总开关 + 名单命中两层控制；本层只兜底非预期崩溃。
"""

from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.core.service_bot.schemas.publish_schemas import PublishFlowResult
from agentclaw.community.plugin_api.evaluation_gate import (
    GATE_EVAL_FAILED_ENFORCE,
    GATE_EVALUATING,
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
async def test_gate_crash_does_not_block_main_flow():
    """seam 实现异常 → fail-open（不阻断主流程）。

    灰度期门禁平台抖动不应卡住发布；本层只兜底非预期崩溃，放行推进
    DRAFT→BUILDING，不落 ext.gate。真正的拦截由 corp DRM 总开关 + 名单命中
    两层控制（见 apps/backend/specs/2026-10-19-evaluation-gate-seam/spec.md §6）。
    """
    gate = _FakeGate(error=RuntimeError("gate platform 500"))
    svc, publish_service, record = _mk_service(gate)

    result = await svc.process(publish_id=1, operator="u1")

    svc._advance_status.assert_called_once()  # 主流程照常推进
    assert result.status == PublishStatus.BUILDING.value
    publish_service.mutate_publish_ext.assert_not_called()  # 不落门禁摘要


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


@pytest.mark.asyncio
async def test_gate_not_consulted_for_non_draft_status():
    """门禁 seam 只在草稿态推进点生效；非 DRAFT 状态（BUILDING 等）不查门禁。

    防回归：若有人把门禁判定挪到 describe 分支或抬到 process 顶部，
    此用例会捕获（gate.calls 不应为空 → 失败）。
    """
    record = _make_publish_record(status=PublishStatus.BUILDING.value)
    gate = _FakeGate(
        GatePublishDecision(allowed=False, status=GATE_EVAL_FAILED_ENFORCE, reason="不通过")
    )
    svc, publish_service, record = _mk_service(gate, record=record)
    svc._advance_status = Mock(return_value=True)
    # 非草稿态走 describe-only；桩掉以拿到确定性结果（describe 实现不属本用例关注点）。
    svc._describe_publish_status = Mock(
        return_value=PublishFlowResult(
            publish_id=record.id,
            status=PublishStatus.BUILDING.value,
            message="building",
            action="process",
        )
    )

    result = await svc.process(publish_id=record.id, operator="u1")

    assert gate.calls == []  # 门禁未被咨询
    svc._advance_status.assert_not_called()
    publish_service.mutate_publish_ext.assert_not_called()  # 不落门禁摘要
    assert result.status == PublishStatus.BUILDING.value


@pytest.mark.asyncio
async def test_gate_evaluating_blocks_and_records_ext():
    """评测中（evaluationg）→ 不推进、ext.gate 落『评测中』摘要。"""
    gate = _FakeGate(
        GatePublishDecision(
            allowed=False,
            status=GATE_EVALUATING,
            reason="评测任务执行中，评测期间不可预发发布",
            hit=True,
            hit_type="space",
            mode="enforce",
            policy_name="风控预发门禁策略",
        )
    )
    svc, publish_service, record = _mk_service(gate)

    result = await svc.process(publish_id=1, operator="u1")

    svc._advance_status.assert_not_called()
    assert result.status == PublishStatus.DRAFT.value
    assert result.data["gate"]["status"] == GATE_EVALUATING
    assert result.data["gate"]["mode"] == "enforce"
    # 草稿态推进点解析 space_id（bot_service.get_bot 返回 sp-1）
    assert gate.calls[0]["space_id"] == "sp-1"
    assert "评测" in result.message
    publish_service.mutate_publish_ext.assert_called_once()
    _, mutator = publish_service.mutate_publish_ext.call_args[0]
    ext: dict = {}
    mutator(ext)
    assert ext["gate"]["status"] == GATE_EVALUATING
    assert ext["gate"]["policy_name"] == "风控预发门禁策略"


@pytest.mark.asyncio
async def test_gate_ext_record_failure_does_not_unblock():
    """ext.gate 摘要落库失败 → 仍阻断（摘要吞掉 warning，阻断语义不变）。

    PublishFlowService._check_evaluation_gate 对 mutate_publish_ext 做了
    try/except，但阻断结果（result.data.gate）照常返回；本用例锁这一兜底，
    避免有人误把 ext 落库失败回退成放行。
    """
    gate = _FakeGate(
        GatePublishDecision(
            allowed=False,
            status=GATE_EVAL_FAILED_ENFORCE,
            reason="评测门禁未通过，预发发布已阻断",
            hit=True,
            mode="enforce",
        )
    )
    svc, publish_service, record = _mk_service(gate)
    publish_service.mutate_publish_ext.side_effect = RuntimeError("db gone")

    result = await svc.process(publish_id=1, operator="u1")

    svc._advance_status.assert_not_called()  # 仍阻断
    assert result.status == PublishStatus.DRAFT.value
    assert result.data["gate"]["status"] == GATE_EVAL_FAILED_ENFORCE
    publish_service.mutate_publish_ext.assert_called_once()  # 尝试落过摘要


@pytest.mark.asyncio
async def test_gate_block_ext_write_preserves_in_progress_eval():
    """幂等（reviewer L366）：重复 publish_staging 检查不清掉编排已写的
    evaluation_id / eval_env_context / started_at——只刷新决策字段。"""
    gate = _FakeGate(
        GatePublishDecision(
            allowed=False,
            status=GATE_EVALUATING,
            reason="评测任务执行中",
            hit=True,
            hit_type="space",
            mode="enforce",
        )
    )
    svc, publish_service, record = _mk_service(gate)

    await svc.process(publish_id=1, operator="u1")

    publish_service.mutate_publish_ext.assert_called_once()
    _, mutator = publish_service.mutate_publish_ext.call_args[0]
    # 编排此前已落 ext.gate（一次 start_evaluation）——模拟"已在评的评测"
    pre_ext = {
        "gate": {
            "status": "evaluating",
            "evaluation_id": "st-uuid-1",
            "eval_env_context": {"binding_id": "bnd-1", "default_tag": "eval"},
            "started_at": "2026-10-20T00:00:00",
        }
    }
    mutator(pre_ext)
    # 决策面刷新（checked_at 写入、status 仍 evaluating），评测跟踪字段保留
    assert "checked_at" in pre_ext["gate"]
    assert pre_ext["gate"]["status"] == GATE_EVALUATING
    assert pre_ext["gate"]["evaluation_id"] == "st-uuid-1"
    assert pre_ext["gate"]["eval_env_context"]["binding_id"] == "bnd-1"
    assert pre_ext["gate"]["started_at"] == "2026-10-20T00:00:00"