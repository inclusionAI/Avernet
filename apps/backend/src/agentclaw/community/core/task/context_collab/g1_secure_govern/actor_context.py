"""已认证调用者上下文(G1 语义槽位)。

ACCS 一致性条款:"每个有副作用操作必须执行 G1 授权"——实现必须有一个
认证调用者载体。本类型即其权威居所(G1 层);``service_api.py`` 对其做兼容
再导出,既有 ``from …service_api import ActorContext`` 的 ~10 处 import
零破坏(2026-11-13 归位,spec bbs-public-discussion-product §3.2-I)。
由 delivery 从 auth 信息构造,核心不解析 HTTP。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from agentclaw.community.core.errors import Forbidden

from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)


@dataclass(frozen=True)
class ActorContext:
    """已认证调用者上下文;G1 授权与审计的执行主体来源。"""

    actor: PrincipalRef
    source: str  # "api" | "openapi" | "a2a"
    # Trusted authentication provenance (e.g. the signed Bot owner), not JSON.
    # Optional for operations that do not need accountability; a human acts for
    # themselves when omitted. Bot application submission requires this fact.
    accountable_principal_ref: PrincipalRef | None = None


_CURRENT_ACTOR: ContextVar[ActorContext] = ContextVar("context_collab_actor")


def current_actor_context() -> ActorContext:
    """Read a trusted caller bound by delivery or a library entrypoint.

    There is deliberately no anonymous/default-owner fallback. This carrier
    does not authenticate claims; only trusted entrypoints may establish it.
    """
    try:
        return _CURRENT_ACTOR.get()
    except LookupError:
        raise Forbidden("an authenticated actor scope is required") from None


@contextmanager
def actor_context_scope(actor: ActorContext) -> Iterator[None]:
    """Bind one caller task-locally and restore the prior scope on every exit."""
    token = _CURRENT_ACTOR.set(actor)
    try:
        yield
    finally:
        _CURRENT_ACTOR.reset(token)
