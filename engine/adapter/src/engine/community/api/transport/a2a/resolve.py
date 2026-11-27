"""A2A interaction validation and engine dispatch (keeps the legacy full contract)."""
from __future__ import annotations

import asyncio
from typing import Any


class _ParamError(ValueError):
    """Boundary type/shape violation mapped to JSON-RPC -32602."""


# ── E4: ResolveInteraction — HITL same-run resolve closed loop ──────────────

class _ResolveRejected(Exception):
    """Engine deliberately refused the resolve decision.

    ``code`` carries the engine error code (structural classification —
    R17-3: real port contract is ``{success, payload|error}``; ``success``
    is the判定 field, not ``ok``); INTERNAL_ERROR 保持 R14 的"结果不可知"。
    """

    def __init__(self, message: str, *, code: str = "UNKNOWN"):
        super().__init__(message)
        self.code = code


class _ResolveUnsupported(Exception):
    """R7-F3: active engine lacks the resolve-contract method for this kind.

    Legacy full resolve covers three interaction kinds. OpenClaw implements
    exec only and advertises its separate scoped extension.
    Gate per-kind BEFORE dispatch so the failure surfaces as a structured
    ``resolve_unsupported`` JSON-RPC error, never as a bare AttributeError
    leaking through the handler (HTTP 500 with the approval left pending).
    """


# R7-F3：resolve kind → 所需的 ChatService 契约方法（None kind = ask_user
# 缺省分支，与 _forward_resolve 的分发梯子同源）。
_KIND_RESOLVE_METHOD = {
    "exec": "resolve_exec_approval",
    "ask_user": "resolve_interaction",
    None: "resolve_interaction",
    "mode_switch": "resolve_mode_transition",
}


def _engine_resolve_supported() -> bool:
    """P0/stage2 §5：resolve 声明真值——活动引擎 ChatService 实现
    `_KIND_RESOLVE_METHOD.values()` 全部三方法才为 True。

    与 `_forward_resolve` 运行期门控同源（同一词汇表），部分支持=不
    声明，免于 "card 声明≠实际能力"；取插件失败按不声明处理（保守）。
    属性访问抛错（奇异插件）同样降级为不声明——card 端点永可答，
    不因真值检查 500。请求时计算、无缓存——声明与实现不漂移。"""
    try:
        from engine.community.api.transport.a2a.engine_bridge import _chat_plugin
        chat = _chat_plugin()
        return all(getattr(chat, m, None) is not None
                   for m in set(_KIND_RESOLVE_METHOD.values()))
    except Exception:
        return False


def _validated_resolution(resolution: dict[str, Any]) -> dict[str, Any]:
    """resolution 载荷的边界规范化（PR #2743 R3-1）。

    类型不符 = 边界错误（_ParamError → 422），不带病下行。ask_user 的
    结构化答案（answers/values/selected_options|selectedOptions）原样
    保留——与 v3 builder（build_interaction_resolve_request）等价，灰度
    切换不得丢失载荷/改写决策。"""
    decision = resolution.get("decision")
    if not isinstance(decision, str) or not decision:
        raise _ParamError("resolution.decision is required")
    out: dict[str, Any] = {"decision": decision}
    kind = resolution.get("kind")
    if kind is not None and not isinstance(kind, str):
        raise _ParamError("resolution.kind must be a string")
    if kind:
        out["kind"] = kind
    for field in ("answer", "message"):
        value = resolution.get(field)
        if value is not None and not isinstance(value, str):
            raise _ParamError(f"resolution.{field} must be a string")
        if value is not None:
            out[field] = value
    for field in ("answers", "values"):
        value = resolution.get(field)
        if value is not None and not isinstance(value, dict):
            raise _ParamError(f"resolution.{field} must be an object")
        if value is not None:
            out[field] = value
    selected = resolution.get("selected_options")
    if selected is None:
        selected = resolution.get("selectedOptions")   # 容忍 v3 wire 形态
    if selected is not None:
        if not isinstance(selected, list) or any(
                not isinstance(opt, (list, tuple))
                or any(not isinstance(s, str) for s in opt)
                for opt in selected):
            raise _ParamError("resolution.selected_options must be a list "
                              "of string lists")
        out["selected_options"] = [list(opt) for opt in selected]
    return out


async def _forward_resolve(*, session_key: str,
                           interaction_id: str,
                           resolution: dict[str, Any],
                           timeout: float = 30.0) -> None:
    """E4/16-3: 三类决策分发到 engine adapter 三方法。

    转发键 = BaaS 持有的真实 interaction 标识——即 interaction.requested
    帧上行的引擎侧 id（gateway 生成，如 ``int:<uuid>``）；引擎的 pending
    注册表按它查找。kernel rid 是传输层 id、native run id 是执行标识，
    引擎 resolve 注册表一概不认，严禁替换成二者之一。

    resolution 载荷经 _validated_resolution 规范化：decision 与结构化
    答案全量转发（R3-1——cancel 不得被改写成 submit，answers/values/
    selected_options 不得丢）。

    Raises _ResolveRejected（结构化 code）。"""
    from engine.community.api.transport.a2a.engine_bridge import _chat_plugin

    chat = _chat_plugin()
    kind = resolution.get("kind")
    decision = resolution.get("decision")

    # Gate each kind independently: OpenClaw implements exec only; the full
    # Claude contract also supports ask_user and mode_switch.
    required_method = _KIND_RESOLVE_METHOD.get(kind)
    if required_method is None:
        raise _ParamError(f"unsupported resolution kind: {kind!r}")
    if getattr(chat, required_method, None) is None:
        raise _ResolveUnsupported(
            f"active engine ChatService has no {required_method} "
            f"(resolve contract not implemented for this engine)")

    if kind == "exec":
        coro = chat.resolve_exec_approval(
            session_key=session_key, interaction_id=interaction_id,
            decision=decision, message=resolution.get("message"))
    elif kind in ("ask_user", None):
        coro = chat.resolve_interaction(
            session_key=session_key, interaction_id=interaction_id,
            decision=decision,
            answer=resolution.get("answer"),
            message=resolution.get("message"),
            answers=resolution.get("answers"),
            values=resolution.get("values"),
            selected_options=resolution.get("selected_options"))
    elif kind == "mode_switch":
        coro = chat.resolve_mode_transition(
            session_key=session_key, interaction_id=interaction_id,
            decision=decision)
    else:
        raise _ParamError(f"unsupported resolution kind: {kind!r}")

    result = await asyncio.wait_for(coro, timeout)
    # R17-3: engine/port 的真实契约是 {success, payload|error}——不是 {ok}。
    if not result.get("success"):
        err = result.get("error") or {}
        raise _ResolveRejected(
            str(err.get("message", "engine resolve rejected")),
            code=str(err.get("code", "UNKNOWN")))

def exec_resolve_supported() -> bool:
    """Explicit OpenClaw exec-only contract, never inferred from partial methods."""
    try:
        from engine.community.api.transport.a2a.engine_bridge import _chat_plugin
        chat = _chat_plugin()
        return (getattr(chat, "a2a_exec_approvals", False) is True
                and callable(getattr(chat, "resolve_exec_approval", None))
                and callable(getattr(chat, "stream_for_a2a", None)))
    except Exception:
        return False
