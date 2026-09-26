"""Core result types for the multi-runtime CodeFuse token write orchestration.

Only data: the orchestration itself lives in
``BotService.write_codefuse_token_to_runtimes``; the HTTP adapter and the async
device-refresher both consume the ``CodefuseWriteResult`` and translate it
(HTTP status / log) without re-implementing the fan-out policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CodefuseWriteOutcome(str, Enum):
    """Aggregate outcome of writing a CodeFuse token across all live runtimes."""

    NO_BINDING = "no_binding"   # resolve 出 0 条 binding
    NO_TARGET = "no_target"     # 解到 binding id 但实体都取不到
    ALL_FAILED = "all_failed"   # 有 target 但全部写入失败
    WROTE = "wrote"             # 至少一条 runtime 写入成功


@dataclass
class CodefuseTargetResult:
    """Per-runtime write attempt result."""

    binding_id: int | None
    provider: str
    ok: bool
    status_code: int           # 200 成功，否则失败码（400 缺字段 / 502 exec 失败）
    detail: str = ""


@dataclass
class CodefuseWriteResult:
    """Aggregated result returned by ``BotService.write_codefuse_token_to_runtimes``."""

    resolved_binding_ids: list[int] = field(default_factory=list)
    results: list[CodefuseTargetResult] = field(default_factory=list)
    wrote_any: bool = False
    first_failure: tuple[int, str] | None = None
    outcome: CodefuseWriteOutcome = CodefuseWriteOutcome.NO_BINDING

    @property
    def attempted(self) -> int:
        return len(self.results)

    @property
    def ok_providers(self) -> list[str]:
        return [r.provider for r in self.results if r.ok]


__all__ = ["CodefuseWriteOutcome", "CodefuseTargetResult", "CodefuseWriteResult"]
