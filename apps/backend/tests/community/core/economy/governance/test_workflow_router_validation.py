"""Unit tests for ``_validate_status_filter`` — three-state contract preservation.

守护 router↔service 边界上的 ``statuses`` 三态语义守恒:

  - None  = 缺省  → service 填默认活跃态
  - []    = 显式空过滤 → service 走空结果路径(**不得在 router 归并为 None**)
  - 非空  = 任一非法值 → 400

历史缺陷:router 曾把 ``[]`` 归一化成 ``None``,吞掉 service 层
``list_review_tickets`` 为「空过滤」定义的空结果语义(见
``test_empty_statuses_means_no_result_not_default``)。这几例直接钉死
纯函数行为,与 service 层契约一一对齐。

注:HTTP query 层面 FastAPI ``list[str]`` 无法构造真正的 ``[]`` 实参
(``?statuses=`` 被解析为 ``['']``),故此契约由 router 内部
``_validate_status_filter`` 守护,端到端测试不可达。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.economy.workflow_router import (
    _validate_status_filter,
)


class TestValidateStatusFilter:
    """三态语义:None/[]/非空 的归一化与校验。"""

    @pytest.mark.parametrize("value", [None])
    def test_none_means_default(self, value: list[str] | None) -> None:
        """None 透传 → service 层据此填默认活跃态。"""
        assert _validate_status_filter(value) is None

    def test_empty_list_preserved_not_folded_to_none(self) -> None:
        """[] 原样返回 [] —— 不得回落 None,否则吞掉 service 空过滤语义。

        Regression:旧实现 ``len==0 → return None`` 把 [] 篡改为 None,
        致使 service 回落默认活跃态而非返回空结果。
        """
        result = _validate_status_filter([])
        assert result is not None
        assert result == []

    @pytest.mark.parametrize(
        "value",
        [
            ["open"], ["closed"], ["open", "scheduled"],
            ["waiting_review", "closed"],
            ["observed"], ["open", "observed"], ["observed", "closed"],
        ],
    )
    def test_valid_statuses_passed_through(self, value: list[str]) -> None:
        """合法状态集合原样返回(不过滤、不去重、不改序)。

        observed = 白名单观察态(Task 13 加入合法集),评审可显式筛观察单。
        """
        assert _validate_status_filter(value) == value

    @pytest.mark.parametrize(
        "value",
        [["bogus"], ["open", "bogus"], [""], ["OPEN"], ["closed ", "open"]],
    )
    def test_invalid_statuses_raise_400(self, value: list[str]) -> None:
        """任一非法值(含空串、大小写错、尾空格)→ 400。"""
        with pytest.raises(HTTPException) as exc:
            _validate_status_filter(value)
        assert exc.value.status_code == 400