"""L1 公共合同值对象(ACCS-001 §3–§5/§9 逐字;strictification 与 collet.py 同级)。

L1 复用 A2A 的互操作与交付模型,本模块只承载跨层操作的**公共合同**
(ID 引用/上下文/操作封装的错误合同),不拥有任何上层权威状态
(ACCS-101 §4)。值对象首居本模块(裁决 D):跨层消费者出现时再评估
提升,同 ``ActorContext`` 归位 G1 先例。wire 侧时间戳一律 ISO 字符串
(裁决 E)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)


def _required_str(data: dict, key: str, owner: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string, got: {value!r}"
        )
    return value


def _optional_str(data: dict, key: str, owner: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a non-empty string or null, got: {value!r}"
        )
    return value


def _required_keys(data: dict, owner: str, keys: frozenset) -> None:
    """严格顶层键集:未知键拒绝(规避 ContextSpec 同居件的宽松滑坡)。"""
    if not isinstance(data, dict):
        raise ContextCollabValidationError(
            f"{owner} must be an object, got: {data!r}"
        )
    unknown = set(data) - keys
    if unknown:
        raise ContextCollabValidationError(
            f"{owner} has unknown top-level keys: {sorted(unknown)}; "
            f"allowed keys: {sorted(keys)}"
        )


def _enum_str(data: dict, key: str, allowed: frozenset, owner: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or value not in allowed:
        raise ContextCollabValidationError(
            f"unknown {owner}.{key} value: {value!r}, allowed: {sorted(allowed)}"
        )
    return value


def _list_of(data: dict, key: str, owner: str) -> list:
    value = data.get(key)
    if not isinstance(value, list):
        raise ContextCollabValidationError(
            f"{owner}.{key} must be a list, got: {value!r}"
        )
    return value


def _dict_of(data: dict, key: str, owner: str) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ContextCollabValidationError(
            f"{owner}.{key} must be an object, got: {value!r}"
        )
    return value


# --------------------------------------------------------------------- 身份与引用


@dataclass(frozen=True)
class IdentityRef:
    """ACCS-001 §3:身份引用。``PRINCIPAL`` 指向 L2 Principal,``AGENT``
    指向 L2 Agent;跨信任域使用必须验证 ``issuer``(P2 签名随真实 A2A 补)。"""

    identity_type: str  # PRINCIPAL | AGENT
    identity_id: str
    issuer: str

    @classmethod
    def from_dict(cls, data: dict) -> "IdentityRef":
        _required_keys(data, "IdentityRef", frozenset(
            {"identity_type", "identity_id", "issuer"}))
        return cls(
            identity_type=_enum_str(
                data, "identity_type",
                frozenset({"PRINCIPAL", "AGENT"}), "IdentityRef",
            ),
            identity_id=_required_str(data, "identity_id", "IdentityRef"),
            issuer=_required_str(data, "issuer", "IdentityRef"),
        )

    def to_dict(self) -> dict:
        return {
            "identity_type": self.identity_type,
            "identity_id": self.identity_id,
            "issuer": self.issuer,
        }


@dataclass(frozen=True)
class ObjectRef:
    """ACCS-001 §4:任意版本化对象的引用;安全敏感操作应固定 version。"""

    object_type: str
    object_id: str
    version: str | None
    issuer: str | None

    @classmethod
    def from_dict(cls, data: dict) -> "ObjectRef":
        _required_keys(data, "ObjectRef", frozenset(
            {"object_type", "object_id", "version", "issuer"}))
        return cls(
            object_type=_required_str(data, "object_type", "ObjectRef"),
            object_id=_required_str(data, "object_id", "ObjectRef"),
            version=_optional_str(data, "version", "ObjectRef"),
            issuer=_optional_str(data, "issuer", "ObjectRef"),
        )

    def to_dict(self) -> dict:
        return {
            "object_type": self.object_type,
            "object_id": self.object_id,
            "version": self.version,
            "issuer": self.issuer,
        }


@dataclass(frozen=True)
class ContextRef:
    """ACCS-001 §4:操作所处上下文引用(八值闭包)。"""

    context_type: str
    context_id: str

    @classmethod
    def from_dict(cls, data: dict) -> "ContextRef":
        _required_keys(data, "ContextRef", frozenset(
            {"context_type", "context_id"}))
        return cls(
            context_type=_enum_str(
                data, "context_type",
                frozenset({
                    "SPACE", "COLLET", "TASK", "ACTIVITY", "METHOD_BINDING",
                    "PRODUCT", "IMPROVEMENT_RUN", "EVALUATION",
                }),
                "ContextRef",
            ),
            context_id=_required_str(data, "context_id", "ContextRef"),
        )

    def to_dict(self) -> dict:
        return {
            "context_type": self.context_type,
            "context_id": self.context_id,
        }


# --------------------------------------------------------------- 操作上下文


def _refs_tuple(data: dict, key: str, owner: str) -> tuple[ObjectRef, ...]:
    return tuple(
        ObjectRef.from_dict(item) for item in _list_of(data, key, owner)
    )


@dataclass(frozen=True)
class OperationContext:
    """ACCS-001 §5:操作上下文。

    ``caller`` 是实际发起调用的身份;Agent 调用同时携带可追溯的
    ``accountable_principal_ref``(规则 1);``space_refs`` 只是上下文提示,
    不等于访问授权(规则 2);``deadline`` 到期后实现必须停止新增副作用
    (规则 3)。
    """

    request_id: str
    idempotency_key: str | None
    caller: IdentityRef
    accountable_principal_ref: IdentityRef | None
    delegation_chain_refs: tuple[ObjectRef, ...]
    space_refs: tuple[ObjectRef, ...]
    collet_ref: ContextRef | None
    method_binding_ref: ObjectRef | None
    product_profile_ref: ObjectRef | None
    trace_id: str
    correlation_id: str | None
    causation_id: str | None
    requested_at: str
    deadline: str | None
    protocol_versions: dict[str, str]
    extensions: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict) -> "OperationContext":
        _required_keys(data, "OperationContext", frozenset({
            "request_id", "idempotency_key", "caller",
            "accountable_principal_ref", "delegation_chain_refs",
            "space_refs", "collet_ref", "method_binding_ref",
            "product_profile_ref", "trace_id", "correlation_id",
            "causation_id", "requested_at", "deadline",
            "protocol_versions", "extensions",
        }))
        accountable = data.get("accountable_principal_ref")
        collet = data.get("collet_ref")
        method_ref = data.get("method_binding_ref")
        product_ref = data.get("product_profile_ref")
        return cls(
            request_id=_required_str(data, "request_id", "OperationContext"),
            idempotency_key=_optional_str(
                data, "idempotency_key", "OperationContext"
            ),
            caller=IdentityRef.from_dict(_dict_of(data, "caller", "OperationContext")),
            accountable_principal_ref=(
                IdentityRef.from_dict(accountable)
                if accountable is not None
                else None
            ),
            delegation_chain_refs=_refs_tuple(
                data, "delegation_chain_refs", "OperationContext"
            ),
            space_refs=_refs_tuple(data, "space_refs", "OperationContext"),
            collet_ref=(
                ContextRef.from_dict(collet) if collet is not None else None
            ),
            method_binding_ref=(
                ObjectRef.from_dict(method_ref)
                if method_ref is not None
                else None
            ),
            product_profile_ref=(
                ObjectRef.from_dict(product_ref)
                if product_ref is not None
                else None
            ),
            trace_id=_required_str(data, "trace_id", "OperationContext"),
            correlation_id=_optional_str(
                data, "correlation_id", "OperationContext"
            ),
            causation_id=_optional_str(
                data, "causation_id", "OperationContext"
            ),
            requested_at=_required_str(
                data, "requested_at", "OperationContext"
            ),
            deadline=_optional_str(data, "deadline", "OperationContext"),
            protocol_versions=dict(_dict_of(
                data, "protocol_versions", "OperationContext"
            )),
            extensions=dict(_dict_of(data, "extensions", "OperationContext")),
        )

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "caller": self.caller.to_dict(),
            "accountable_principal_ref": (
                self.accountable_principal_ref.to_dict()
                if self.accountable_principal_ref is not None
                else None
            ),
            "delegation_chain_refs": [
                ref.to_dict() for ref in self.delegation_chain_refs
            ],
            "space_refs": [ref.to_dict() for ref in self.space_refs],
            "collet_ref": (
                self.collet_ref.to_dict()
                if self.collet_ref is not None
                else None
            ),
            "method_binding_ref": (
                self.method_binding_ref.to_dict()
                if self.method_binding_ref is not None
                else None
            ),
            "product_profile_ref": (
                self.product_profile_ref.to_dict()
                if self.product_profile_ref is not None
                else None
            ),
            "trace_id": self.trace_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "requested_at": self.requested_at,
            "deadline": self.deadline,
            "protocol_versions": dict(self.protocol_versions),
            "extensions": dict(self.extensions),
        }


# ------------------------------------------------------------------- 错误合同


@dataclass(frozen=True)
class AccsError:
    """ACCS-001 §9:错误合同(category 九值闭包逐字;trace_id 必填)。"""

    code: str
    message: str
    retryable: bool
    category: str  # 九值闭包,见 _CATEGORIES
    trace_id: str
    object_ref: ObjectRef | None

    _CATEGORIES = frozenset({
        "VALIDATION", "AUTHENTICATION", "AUTHORIZATION", "CONFLICT",
        "NOT_FOUND", "RATE_LIMIT", "DEPENDENCY", "TIMEOUT", "INTERNAL",
    })

    @classmethod
    def from_dict(cls, data: dict) -> "AccsError":
        _required_keys(data, "AccsError", frozenset({
            "code", "message", "retryable", "category", "trace_id",
            "object_ref",
        }))
        retryable = data.get("retryable")
        if not isinstance(retryable, bool):
            raise ContextCollabValidationError(
                f"AccsError.retryable must be a boolean, got: {retryable!r}"
            )
        object_ref = data.get("object_ref")
        return cls(
            code=_required_str(data, "code", "AccsError"),
            message=_required_str(data, "message", "AccsError"),
            retryable=retryable,
            # _CATEGORIES 在类体引用受限,from_dict 内以模块级闭包校验。
            category=(
                _enum_str(data, "category", _ERROR_CATEGORIES, "AccsError")
            ),
            trace_id=_required_str(data, "trace_id", "AccsError"),
            object_ref=(
                ObjectRef.from_dict(object_ref)
                if object_ref is not None
                else None
            ),
        )

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "category": self.category,
            "trace_id": self.trace_id,
            "object_ref": (
                self.object_ref.to_dict()
                if self.object_ref is not None
                else None
            ),
        }


_ERROR_CATEGORIES = frozenset(AccsError._CATEGORIES)
