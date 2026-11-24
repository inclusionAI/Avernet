"""Bounded, best-effort Server permission observations for an Apply report."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import BoundedSemaphore
from typing import Callable, Iterable

from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.outcomes import ApplyReport, EntryOutcome


ServerCheck = Callable[[str, str, float], bool | None]
_PER_APPLY = 4
_BUDGET_SECONDS = 2.0
_PROCESS_LIMIT = 16
_slots = BoundedSemaphore(_PROCESS_LIMIT)
_executor = ThreadPoolExecutor(max_workers=_PROCESS_LIMIT, thread_name_prefix="mcp-permission")


def _check(check: ServerCheck, user: str, code: str, deadline: float) -> tuple[bool | None, float]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, time.monotonic()
    try:
        result = check(user, code, remaining)
    except Exception:  # permission lookup is advisory
        result = None
    return result, time.monotonic()


def observation_deadline() -> float:
    return time.monotonic() + _BUDGET_SECONDS


async def permission_notes(
    check: ServerCheck, user: str, codes: Iterable[str], *, deadline: float | None = None,
) -> tuple[str, ...]:
    """Collect only explicit denials completed in the two-second batch budget.

    Global slots are acquired before dispatch, held until the actual blocking
    request returns, and shared across every event loop in the process. A late
    request can never modify the returned notes.
    """
    remaining_codes = iter(dict.fromkeys(codes))
    deadline = observation_deadline() if deadline is None else deadline
    pending: dict[asyncio.Future, str] = {}
    denied: set[str] = set()
    exhausted = False
    while time.monotonic() < deadline:
        while (not exhausted and len(pending) < _PER_APPLY
               and time.monotonic() < deadline and _slots.acquire(blocking=False)):
            try:
                code = next(remaining_codes)
            except StopIteration:
                _slots.release()
                exhausted = True
                break
            try:
                future = _executor.submit(_check, check, user, code, deadline)
            except BaseException:
                _slots.release()
                raise
            future.add_done_callback(lambda _: _slots.release())
            pending[asyncio.wrap_future(future)] = code
        if not pending:
            if exhausted:
                break
            await asyncio.sleep(min(0.01, max(0, deadline - time.monotonic())))
            continue
        timeout = max(0, deadline - time.monotonic())
        # Poll if other Applies hold every slot, allowing us to dispatch as
        # soon as one returns without creating an unbounded executor queue.
        if len(pending) < _PER_APPLY and not exhausted:
            timeout = min(timeout, 0.01)
        done, _ = await asyncio.wait(pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        for future in done:
            code = pending.pop(future)
            result, finished_at = future.result()
            if finished_at <= deadline and result is False:
                denied.add(code)
    # A result may have completed inside the budget just as the event loop
    # woke at its deadline. Count that result without waiting for late work.
    for future, code in pending.items():
        if future.done() and not future.cancelled():
            result, finished_at = future.result()
            if finished_at <= deadline and result is False:
                denied.add(code)
    return tuple(
        f"{code} MCP 已安装。当前用户尚无该 Server 的调用权限，请前往 MCP Center 申请。"
        for code in sorted(denied)
    )


def successful_candidates(ctx: ApplyContext, report: ApplyReport) -> set[str]:
    successful = {EntryOutcome.CREATED, EntryOutcome.UPDATED, EntryOutcome.UNCHANGED}
    return set().union(*(
        ctx.capability_state.manifest_mcp_candidates.get(entry.identity, frozenset())
        for entry in report.entries if entry.outcome in successful
    ))


async def finish_permission_notes(
    ctx: ApplyContext, report: ApplyReport, check: ServerCheck | None,
) -> ApplyReport:
    subject = getattr(ctx, "permission_subject", None)
    if not subject or check is None:
        return report
    delivered = set(ctx.capability_state.delivered_mcp_codes)
    projection = report.runtime_projection
    if (projection is not None and projection.passport_updated is True
            and projection.passport_mcp_codes is not None):
        delivered.update(projection.delivered_mcp_codes & projection.passport_mcp_codes)
    codes = successful_candidates(ctx, report) & delivered
    if not codes:
        return report
    notes = await permission_notes(check, subject, sorted(codes))
    return replace(report, notes=report.notes + notes)
