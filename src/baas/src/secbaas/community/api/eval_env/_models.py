"""评测环境数据模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalBindingInfo:
    """评测绑定信息。"""

    binding_id: int | None
    default_tag: str
    eval_id: str | None = None
