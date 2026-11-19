"""The conformance kit (03-strategy.zh-CN.md §8.4).

Run by an author in their own tests, and by the Strategy Registry before a version may
be bound outside dev. It drives the strategy through the local platform and checks the
six properties §8.4 lists; a check that needs a capability provider we do not have
locally is reported as skipped, never as passed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from .catalog import DEFAULT_CATALOG, Catalog
from .errors import BudgetExhausted, CapabilityNotGranted, RunCancelled
from .local import (
    LocalCandidates,
    LocalContext,
    LocalWorkspaceFactory,
    StaticRevision,
    build_context,
    candidate_id_of,
)
from .models import Budget, Candidate
from .registration import (
    ConformanceState,
    ConformanceStatus,
    StrategyRegistration,
    check_allowed_genes,
)

KIT_VERSION = "1.0.0"

StrategyFactory = Callable[[], object]


@dataclass(frozen=True)
class ConformanceCheck:
    name: str
    passed: bool
    detail: str
    skipped: bool = False


@dataclass(frozen=True)
class ConformanceReport:
    checks: tuple[ConformanceCheck, ...]
    kit_version: str = KIT_VERSION

    @property
    def status(self) -> ConformanceStatus:
        if any(check.skipped for check in self.checks):
            return ConformanceStatus(status=ConformanceState.PENDING, kit_version=self.kit_version)
        if all(check.passed for check in self.checks):
            return ConformanceStatus(status=ConformanceState.PASSED, kit_version=self.kit_version)
        return ConformanceStatus(status=ConformanceState.FAILED, kit_version=self.kit_version)

    def failures(self) -> list[ConformanceCheck]:
        return [check for check in self.checks if not check.passed and not check.skipped]


class _Skipped(Exception):
    """A check could not be evaluated locally (no provider for a declared capability)."""


class _Fixture:
    """One isolated local run: strategy instance, context, and what it produced."""

    def __init__(self, strategy_factory: StrategyFactory, ctx: LocalContext, timeout_s: float) -> None:
        self._factory = strategy_factory
        self.ctx = ctx
        self.timeout_s = timeout_s
        self.summary = None
        self.error: BaseException | None = None

    def run(self) -> None:
        strategy = self._factory()
        try:
            self.summary = asyncio.run(asyncio.wait_for(strategy.run(self.ctx), timeout=self.timeout_s))
        except BaseException as exc:  # noqa: BLE001 - the kit records what the strategy did
            self.error = exc


def _context(
    registration: StrategyRegistration,
    parent: StaticRevision,
    *,
    catalog: Catalog,
    params: dict | None,
    attempt: int,
    budget: Budget | None = None,
    candidates: LocalCandidates | None = None,
    workspaces: LocalWorkspaceFactory | None = None,
    cancelled: bool = False,
) -> LocalContext:
    ctx = build_context(
        parent=parent,
        params=params if params is not None else {},
        attempt=attempt,
        granted=catalog.granted_for(registration.needs),
        budget=budget,
    )
    if candidates is not None:
        ctx.candidates = candidates
    if workspaces is not None:
        ctx.workspace = workspaces
    if cancelled:
        ctx.cancelled.cancel()
    return ctx


def _raise_skipped_if_unprovided(error: BaseException | None) -> None:
    if isinstance(error, NotImplementedError):
        raise _Skipped(str(error))


def _submitted(context: LocalContext) -> list[Candidate]:
    return list(context.candidates.submitted.values())


def run_conformance(
    strategy_factory: StrategyFactory,
    registration: StrategyRegistration,
    *,
    parent: StaticRevision,
    catalog: Catalog = DEFAULT_CATALOG,
    params: dict | None = None,
    mutable_genes: list[str] | None = None,
    pins: list[str] | None = None,
    timeout_s: float = 60.0,
) -> ConformanceReport:
    """Run every §8.4 strategy-side check against the local platform."""

    checks = [
        _check(
            "candidate_validity",
            lambda: _candidate_validity(
                strategy_factory,
                registration,
                parent,
                catalog,
                params,
                mutable_genes,
                pins,
                timeout_s,
            ),
        ),
        _check(
            "granted_capabilities_only",
            lambda: _granted_only(strategy_factory, registration, parent, catalog, params, timeout_s),
        ),
        _check(
            "stops_on_cancel",
            lambda: _stops_on_cancel(strategy_factory, registration, parent, catalog, params, timeout_s),
        ),
        _check(
            "stops_on_budget_exhausted",
            lambda: _stops_on_budget(strategy_factory, registration, parent, catalog, params, timeout_s),
        ),
        _check(
            "idempotent_resubmit",
            lambda: _idempotent_resubmit(strategy_factory, registration, parent, catalog, params, timeout_s),
        ),
        _check(
            "redispatch_stable",
            lambda: _redispatch_stable(strategy_factory, registration, parent, catalog, params, timeout_s),
        ),
    ]
    return ConformanceReport(checks=tuple(checks))


def _check(name: str, body: Callable[[], str]) -> ConformanceCheck:
    try:
        detail = body()
    except _Skipped as skipped:
        return ConformanceCheck(name=name, passed=True, detail=str(skipped), skipped=True)
    except Exception as exc:  # noqa: BLE001 - a raised check is a failed check
        return ConformanceCheck(name=name, passed=False, detail=f"{type(exc).__name__}: {exc}")
    return ConformanceCheck(name=name, passed=True, detail=detail)


def _candidate_validity(
    strategy_factory: StrategyFactory,
    registration: StrategyRegistration,
    parent: StaticRevision,
    catalog: Catalog,
    params: dict | None,
    mutable_genes: list[str] | None,
    pins: list[str] | None,
    timeout_s: float,
) -> str:
    fixture = _Fixture(
        strategy_factory,
        _context(registration, parent, catalog=catalog, params=params, attempt=1),
        timeout_s,
    )
    fixture.run()
    _raise_skipped_if_unprovided(fixture.error)
    if fixture.error is not None and not isinstance(fixture.error, (RunCancelled, BudgetExhausted)):
        raise fixture.error

    candidates = _submitted(fixture.ctx)
    if not candidates:
        return "no candidate submitted; nothing to validate"

    problems: list[str] = []
    for candidate in candidates:
        problems.extend(
            check_allowed_genes(candidate.patch, mutable_genes, pins) if mutable_genes is not None else []
        )
        if candidate.rationale != candidate.patch.rationale:
            problems.append("candidate rationale diverged from its patch")
    if problems:
        raise ValueError("; ".join(problems))
    scope = "within allowed_genes" if mutable_genes is not None else "patch schema accepted"
    return f"{len(candidates)} candidate(s), {scope}"


def _granted_only(
    strategy_factory: StrategyFactory,
    registration: StrategyRegistration,
    parent: StaticRevision,
    catalog: Catalog,
    params: dict | None,
    timeout_s: float,
) -> str:
    fixture = _Fixture(
        strategy_factory,
        _context(registration, parent, catalog=catalog, params=params, attempt=1),
        timeout_s,
    )
    fixture.run()
    if isinstance(fixture.error, CapabilityNotGranted):
        raise fixture.error
    _raise_skipped_if_unprovided(fixture.error)
    if isinstance(fixture.error, (RunCancelled, BudgetExhausted)):
        return f"stopped early with {type(fixture.error).__name__}; grants respected"
    if fixture.error is not None:
        raise fixture.error
    return "declared capabilities suffice"


def _stops_on_cancel(
    strategy_factory: StrategyFactory,
    registration: StrategyRegistration,
    parent: StaticRevision,
    catalog: Catalog,
    params: dict | None,
    timeout_s: float,
) -> str:
    fixture = _Fixture(
        strategy_factory,
        _context(
            registration,
            parent,
            catalog=catalog,
            params=params,
            attempt=1,
            cancelled=True,
        ),
        timeout_s,
    )
    fixture.run()
    if isinstance(fixture.error, asyncio.TimeoutError):
        raise ValueError(f"strategy ignored cancellation for {timeout_s}s")  # noqa: TRY004 - preserve public ValueError validation contract
    _raise_skipped_if_unprovided(fixture.error)
    if fixture.error is not None and not isinstance(fixture.error, RunCancelled):
        raise fixture.error
    return "returned on a pre-set cancellation token"


def _stops_on_budget(
    strategy_factory: StrategyFactory,
    registration: StrategyRegistration,
    parent: StaticRevision,
    catalog: Catalog,
    params: dict | None,
    timeout_s: float,
) -> str:
    fixture = _Fixture(
        strategy_factory,
        _context(
            registration,
            parent,
            catalog=catalog,
            params=params,
            attempt=1,
            budget=Budget(max_usd=0.0, max_wall_clock_s=0),
        ),
        timeout_s,
    )
    fixture.run()
    if isinstance(fixture.error, asyncio.TimeoutError):
        raise ValueError(f"strategy ignored an exhausted budget for {timeout_s}s")  # noqa: TRY004 - preserve public ValueError validation contract
    _raise_skipped_if_unprovided(fixture.error)
    if fixture.error is not None and not isinstance(fixture.error, (BudgetExhausted, RunCancelled)):
        raise fixture.error
    return "stopped with a zero budget"


def _idempotent_resubmit(
    strategy_factory: StrategyFactory,
    registration: StrategyRegistration,
    parent: StaticRevision,
    catalog: Catalog,
    params: dict | None,
    timeout_s: float,
) -> str:
    fixture = _Fixture(
        strategy_factory,
        _context(registration, parent, catalog=catalog, params=params, attempt=1),
        timeout_s,
    )
    fixture.run()
    _raise_skipped_if_unprovided(fixture.error)
    if fixture.error is not None and not isinstance(fixture.error, (RunCancelled, BudgetExhausted)):
        raise fixture.error

    candidates = _submitted(fixture.ctx)
    if not candidates:
        return "no candidate submitted; nothing to resubmit"
    for candidate in candidates:
        again = asyncio.run(fixture.ctx.candidates.submit(candidate))
        if again != candidate_id_of(candidate):
            raise ValueError(f"resubmitting returned {again}, expected {candidate_id_of(candidate)}")
    return f"{len(candidates)} candidate(s) resubmit to the same id"


def _redispatch_stable(
    strategy_factory: StrategyFactory,
    registration: StrategyRegistration,
    parent: StaticRevision,
    catalog: Catalog,
    params: dict | None,
    timeout_s: float,
) -> str:
    candidates = LocalCandidates()
    workspaces = LocalWorkspaceFactory(parent)
    budget = Budget(max_usd=100.0, max_wall_clock_s=7200)

    first = _Fixture(
        strategy_factory,
        _context(
            registration,
            parent,
            catalog=catalog,
            params=params,
            attempt=1,
            budget=budget,
            candidates=candidates,
            workspaces=workspaces,
        ),
        timeout_s,
    )
    first.run()
    _raise_skipped_if_unprovided(first.error)
    if first.error is not None and not isinstance(first.error, (RunCancelled, BudgetExhausted)):
        raise first.error
    after_first = set(candidates.submitted)

    second = _Fixture(
        strategy_factory,
        _context(
            registration,
            parent,
            catalog=catalog,
            params=params,
            attempt=2,
            budget=budget,
            candidates=candidates,
            workspaces=workspaces,
        ),
        timeout_s,
    )
    second.run()
    if isinstance(second.error, CapabilityNotGranted):
        raise second.error
    after_second = set(candidates.submitted)
    if after_second != after_first:
        extra = sorted(after_second - after_first)
        raise ValueError(f"re-dispatch produced new candidates: {extra}")
    return f"re-dispatch produced no new candidate ({len(after_first)} stable)"


__all__ = [
    "KIT_VERSION",
    "ConformanceCheck",
    "ConformanceReport",
    "run_conformance",
]
