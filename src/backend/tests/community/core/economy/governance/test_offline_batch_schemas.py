"""Tests for offline-batch schemas — GovernanceRecordInput 边界校验 + to_record 转换。

验证:分层必填(必填缺失/类型错 → ValidationError)、可选缺 → None、to_record 字段映射、
effective_worker_key 一致。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentclaw.community.adapters.http.economy.schemas import (
    GovernanceRecordInput,
    OfflineBatchRequest,
)
from agentclaw.community.core.economy.governance.domain.domain import (
    GovernanceRecord,
)


def _full_input(**overrides) -> GovernanceRecordInput:
    """构造完整合规的 GovernanceRecordInput。"""
    base = dict(
        owner_id="o-1",
        bot_id="b-1",
        governance_decision="actionable",
        dt_version="20260711",
        bot_name="TestBot",
        hit_dimensions="token_usage",
        hit_dimensions_count=2,
        governance_max_priority="P1",
        expected_token_saving=5000,
        saving_ratio=0.5,
        task_summary="cost high",
        notification_structured='{"dims":["cost"]}',
        analysis_status="done",
    )
    base.update(overrides)
    return GovernanceRecordInput(**base)


class TestGovernanceRecordInputValidation:
    def test_full_record_valid(self) -> None:
        rec = _full_input()
        assert rec.owner_id == "o-1"
        assert rec.hit_dimensions_count == 2

    @pytest.mark.parametrize("missing", ["owner_id", "bot_id", "governance_decision", "dt_version"])
    def test_required_field_missing_rejected(self, missing: str) -> None:
        """必填缺失 → ValidationError。"""
        kwargs = dict(
            owner_id="o-1", bot_id="b-1",
            governance_decision="actionable", dt_version="20260711",
        )
        kwargs.pop(missing)
        with pytest.raises(ValidationError):
            GovernanceRecordInput(**kwargs)

    @pytest.mark.parametrize("empty", ["owner_id", "bot_id", "governance_decision", "dt_version"])
    def test_required_field_empty_rejected(self, empty: str) -> None:
        """必填空串(min_length=1)→ ValidationError。"""
        with pytest.raises(ValidationError):
            _full_input(**{empty: ""})

    def test_optional_fields_default_none(self) -> None:
        """可选字段缺省 None,必填齐全即合法。"""
        rec = GovernanceRecordInput(
            owner_id="o-1", bot_id="b-1",
            governance_decision="actionable", dt_version="20260711",
        )
        assert rec.worker_id is None
        assert rec.bot_name is None
        assert rec.hit_dimensions is None
        assert rec.saving_ratio is None

    def test_wrong_type_rejected(self) -> None:
        """类型错(hit_dimensions_count 应 int)→ ValidationError。"""
        with pytest.raises(ValidationError):
            _full_input(hit_dimensions_count="not-an-int")  # type: ignore[arg-type]


class TestToRecord:
    def test_to_record_field_mapping(self) -> None:
        inp = _full_input()
        rec = inp.to_record()
        assert isinstance(rec, GovernanceRecord)
        # 身份字段
        assert rec.owner_id == "o-1"
        assert rec.bot_id == "b-1"
        assert rec.governance_decision == "actionable"
        assert rec.dt_version == "20260711"
        # 数据字段透传
        assert rec.bot_name == "TestBot"
        assert rec.hit_dimensions == "token_usage"
        assert rec.hit_dimensions_count == 2
        assert rec.governance_max_priority == "P1"
        assert rec.expected_token_saving == 5000
        assert rec.saving_ratio == 0.5
        assert rec.task_summary == "cost high"
        assert rec.notification_structured == '{"dims":["cost"]}'

    def test_to_record_effective_worker_key_synthesizes(self) -> None:
        """worker_id 缺 → to_record 后 effective_worker_key 合成 owner_id:bot_id。"""
        rec = _full_input().to_record()
        assert rec.effective_worker_key == "o-1:b-1"

    def test_to_record_effective_worker_key_uses_worker_id(self) -> None:
        """worker_id 有 → effective_worker_key 用之。"""
        rec = _full_input(worker_id="o-1:b-1").to_record()
        assert rec.effective_worker_key == "o-1:b-1"

    def test_to_record_optional_none_preserved(self) -> None:
        """可选缺 → to_record 后字段 None。"""
        rec = GovernanceRecordInput(
            owner_id="o-1", bot_id="b-1",
            governance_decision="actionable", dt_version="20260711",
        ).to_record()
        assert rec.worker_id is None
        assert rec.bot_name is None
        assert rec.saving_ratio is None


class TestOfflineBatchRequestRecordsType:
    def test_records_accepts_governance_record_input(self) -> None:
        req = OfflineBatchRequest(
            records=[_full_input(), _full_input(owner_id="o-2")],
            batch_id="b-1",
            dt_version="20260711",
            total_count=2,
        )
        assert len(req.records) == 2
        assert all(isinstance(r, GovernanceRecordInput) for r in req.records)

    def test_records_empty_rejected(self) -> None:
        """records min_length=1 → 空列表被拒。"""
        with pytest.raises(ValidationError):
            OfflineBatchRequest(records=[], batch_id="b-1")

    def test_records_incomplete_dict_rejected(self) -> None:
        """records 收口为 GovernanceRecordInput:缺必填字段的 dict → ValidationError。"""
        with pytest.raises(ValidationError):
            OfflineBatchRequest(
                records=[{"owner_id": "o-1", "bot_id": "b-1"}],  # type: ignore[list-item]
                batch_id="b-1",
            )

    def test_records_full_dict_coerced_accepted(self) -> None:
        """完整 dict(含全部必填)被 Pydantic lax 模式强制转换为 GovernanceRecordInput 接受。

        向后兼容:ODPS 调用方过渡期仍可发 dict,只要字段齐全。to_record 仍可用于转换。
        """
        req = OfflineBatchRequest(
            records=[{
                "owner_id": "o-1", "bot_id": "b-1",
                "governance_decision": "actionable", "dt_version": "20260711",
            }],
            batch_id="b-1",
        )
        assert isinstance(req.records[0], GovernanceRecordInput)
        rec = req.records[0].to_record()
        assert rec.owner_id == "o-1"
        assert rec.effective_worker_key == "o-1:b-1"

    def test_records_to_record_end_to_end(self) -> None:
        """请求 records 可逐条 to_record 转领域模型(service 接的就是这个)。"""
        req = OfflineBatchRequest(records=[_full_input()], batch_id="b-1", total_count=1)
        domain_records = [r.to_record() for r in req.records]
        assert all(isinstance(r, GovernanceRecord) for r in domain_records)
        assert domain_records[0].owner_id == "o-1"