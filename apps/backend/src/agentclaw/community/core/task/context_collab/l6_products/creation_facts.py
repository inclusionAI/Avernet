"""Composition 创建事实 → 初始事件 payload 的唯一装配点。

事件流 append-only,创建事实今天不写则存量行永远缺失(不可回填性,
spec 2026-11-12-context-collab-l4-creation-event-facts):本函数是该
payload 形状的唯一定义点——通用服务与产品层(products/,乙案依赖矩阵)
共用,消除自持编排的漂移。``space`` / ``principal_ref`` 由聚合六字段
(``scope`` / ``initiator_ref``)持久,不进事件不重复记录。
"""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.l6_products.context_spec import (
    ContextSpec,
)


def creation_payload(spec: ContextSpec, objective: str) -> dict:
    """六键全量创建事实(与 spec §2 逐字):恒带全部 key,None 也写 null。"""
    return {
        "objective": objective,
        "resource_refs": [ref.to_dict() for ref in spec.resource_refs],
        "method_kind": spec.method_kind,
        "executor_ref": (spec.executor_ref.to_dict() if spec.executor_ref else None),
        "product_kind": spec.product_kind,
        "workflow_kind": spec.workflow_kind,
    }
