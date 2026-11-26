"""L1 操作封装对象(ACCS-001 §6 逐字)。

``AccsOperationEnvelope`` 是跨边界上层操作的统一**语义封装**,不是新 wire
protocol、不要求同名 RPC;经 A2A 承载。``AccsOperationResult.status`` 是
上层语义状态,**A2A 对象终态不得替代**(ACCS-101 §4/§10-3)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.common import (

    AccsError,
    ObjectRef,
    OperationContext,
    _enum_str,
    _dict_of,
    _list_of,
    _optional_str,
    _required_keys,
    _required_str,
    _refs_tuple,
)

_LAYERS = frozenset({"L2", "L3", "L4", "L5", "L6"})
_MODES = frozenset({"COMMAND", "QUERY", "EVENT"})
_RESULT_STATUSES = frozenset({"ACCEPTED", "SUCCEEDED", "REJECTED", "FAILED"})


@dataclass(frozen=True)
class AccsOperationEnvelope:
    """ACCS-001 §6:操作属于哪层/哪族/作用于哪个权威对象,谁在什么上下文
    里执行,是命令、无副作用查询还是已发生事实。"""

    operation_id: str
    layer: str            # L2 | L3 | L4 | L5 | L6
    operation_family: str
    action: str
    mode: str             # COMMAND | QUERY | EVENT
    context: OperationContext
    target_ref: ObjectRef | None
    expected_revision: int | None
    input_refs: tuple[ObjectRef, ...]
    payload: Any | None

    @classmethod
    def from_dict(cls, data: dict) -> "AccsOperationEnvelope":
        _required_keys(data, "AccsOperationEnvelope", frozenset({
            "operation_id", "layer", "operation_family", "action", "mode",
            "context", "target_ref", "expected_revision", "input_refs",
            "payload",
        }))
        revision = data.get("expected_revision")
        if revision is not None and (
            isinstance(revision, bool) or not isinstance(revision, int)
        ):
            raise ContextCollabValidationError(
                "AccsOperationEnvelope.expected_revision must be an integer "
                f"or null, got: {revision!r}"
            )
        target = data.get("target_ref")
        return cls(
            operation_id=_required_str(
                data, "operation_id", "AccsOperationEnvelope"
            ),
            layer=_enum_str(data, "layer", _LAYERS, "AccsOperationEnvelope"),
            operation_family=_required_str(
                data, "operation_family", "AccsOperationEnvelope"
            ),
            action=_required_str(data, "action", "AccsOperationEnvelope"),
            mode=_enum_str(data, "mode", _MODES, "AccsOperationEnvelope"),
            context=OperationContext.from_dict(
                _dict_of(data, "context", "AccsOperationEnvelope")
            ),
            target_ref=(
                ObjectRef.from_dict(target) if target is not None else None
            ),
            expected_revision=revision,
            input_refs=_refs_tuple(
                data, "input_refs", "AccsOperationEnvelope"
            ),
            payload=data.get("payload"),
        )

    def to_dict(self) -> dict:
        return {
            "operation_id": self.operation_id,
            "layer": self.layer,
            "operation_family": self.operation_family,
            "action": self.action,
            "mode": self.mode,
            "context": self.context.to_dict(),
            "target_ref": (
                self.target_ref.to_dict()
                if self.target_ref is not None
                else None
            ),
            "expected_revision": self.expected_revision,
            "input_refs": [ref.to_dict() for ref in self.input_refs],
            "payload": self.payload,
        }


@dataclass(frozen=True)
class AccsOperationResult:
    """ACCS-001 §6:操作结果——上层语义状态,不等于 A2A 载体终态。"""

    operation_ref: ObjectRef
    status: str            # ACCEPTED | SUCCEEDED | REJECTED | FAILED
    result_refs: tuple[ObjectRef, ...]
    event_refs: tuple[ObjectRef, ...]
    error: AccsError | None

    @classmethod
    def from_dict(cls, data: dict) -> "AccsOperationResult":
        _required_keys(data, "AccsOperationResult", frozenset({
            "operation_ref", "status", "result_refs", "event_refs", "error",
        }))
        error = data.get("error")
        return cls(
            operation_ref=ObjectRef.from_dict(
                _dict_of(data, "operation_ref", "AccsOperationResult")
            ),
            status=_enum_str(
                data, "status", _RESULT_STATUSES, "AccsOperationResult"
            ),
            result_refs=_refs_tuple(data, "result_refs", "AccsOperationResult"),
            event_refs=_refs_tuple(data, "event_refs", "AccsOperationResult"),
            error=AccsError.from_dict(error) if error is not None else None,
        )

    def to_dict(self) -> dict:
        return {
            "operation_ref": self.operation_ref.to_dict(),
            "status": self.status,
            "result_refs": [ref.to_dict() for ref in self.result_refs],
            "event_refs": [ref.to_dict() for ref in self.event_refs],
            "error": self.error.to_dict() if self.error is not None else None,
        }
