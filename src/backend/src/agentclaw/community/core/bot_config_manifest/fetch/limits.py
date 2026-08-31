"""Limits and deployment allowlists for the guarded fetcher (W2, #1470).

Single source for every number the transport enforces — schema §5 of
``docs/bot-config-manifest/manifest-schema.zh-CN.md``. Fetch-time limits
(remote content sizes) live here and nowhere else: the schema module refuses
at PUT only what it can see at write time, and duplicating fetch numbers
there is how the two drift.

Deployment allowlists are deliberately env-driven and simple: they exist so
an operator can turn a *deployment decision* (corporate HTTP mirror, an
internal content proxy) into an exception without a code change. They widen
nothing else — a host on the allowlist still goes through address
validation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Mapping

#: Address resolution seam: host → resolved IP string list. Real DNS via
#: socket in production; tests inject deterministic answers (including the
#: check-public/connect-private rebinding pair).
Resolver = Callable[[str], list[str]]

#: ``schema §5`` — per-entry fetch caps, in bytes, by category.
FETCH_ENTRY_LIMITS: Mapping[str, int] = {
    "skills": 100 * 1024 * 1024,
    "resources_file": 100 * 1024 * 1024,
    "identity": 1 * 1024 * 1024,
    "cli_tools": 200 * 1024 * 1024,
    "resources_archive": 200 * 1024 * 1024,
    "resources_unpacked": 500 * 1024 * 1024,
}

#: ``schema §5`` — one archive may not explode beyond this many members.
ARCHIVE_MEMBER_LIMIT = 5000

#: ``schema §5`` — totals for a single apply run (W4 threads one
#: ``FetchBudget`` through every entry so these are shared).
APPLY_FETCH_TOTAL_LIMIT = 500 * 1024 * 1024
APPLY_BUDGET_S = 300.0

#: Per network operation (each hop, each read). A multi-hop entry is
#: therefore NOT bounded to one of these — its wall clock is bounded by the
#: apply-level budget W4 threads; that is the schema §5 "单条 60s"的诚实形态。
FETCH_TIMEOUT_S = 60.0

#: Redirect hop budget — deep chains are a redirected-SSF pattern, so the
#: cap is both a robustness and a safety bound.
MAX_REDIRECTS = 5

#: Schemes accepted without a deployment exception (W2 验收: 仅 https)。
SAFE_SCHEMES = frozenset({"https"})


def fetch_transport_allowlist() -> frozenset[str]:
    """Hosts exempted from public-only resolution and, where needed, the
    https-only rule (``BCM_FETCH_TRANSPORT_ALLOW``, comma-separated).

    Matching is exact-host — the DNS-rebinding lesson: a hostname on the
    list is the hostname exempted. Allowlisted hosts keep every other rule:
    redirect budget, byte caps, hop-by-hop re-validation. The list admits
    *destinations*, not behaviors.
    """
    raw = os.environ.get("BCM_FETCH_TRANSPORT_ALLOW", "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class FetchBudget:
    """Transport budget for one fetch request as the orchestrator issues it.

    Per-entry attributes only: the *apply-scope* ledger (shared totals,
    remaining wall clock) is the W4 orchestrator's own state — it decides
    whether to spend the budget, W2 enforces what a single request may
    cost.
    """

    category: str = "resources_file"

    @property
    def entry_limit(self) -> int:
        default = FETCH_ENTRY_LIMITS["resources_file"]
        return FETCH_ENTRY_LIMITS.get(self.category, default)

    @property
    def timeout_s(self) -> float:
        return FETCH_TIMEOUT_S
