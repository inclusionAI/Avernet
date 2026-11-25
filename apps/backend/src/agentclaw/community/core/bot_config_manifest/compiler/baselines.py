"""The git baselines a compile starts from: each source's SHA as the last apply resolved it (W7)."""

from __future__ import annotations

from typing import Iterable

from agentclaw.community.core.apply_engine.outcomes import ApplyReport, SourceMode

__all__ = ["BASELINE_HISTORY_APPLIES", "last_resolutions"]

#: How many of the bot's most recent reports the baseline read walks.
BASELINE_HISTORY_APPLIES = 10


def last_resolutions(reports: Iterable[ApplyReport]) -> dict[tuple[str, str, str], str]:
    """Each ``(url, ref, mode)``'s SHA as the last apply that RESOLVED it, never the row's ``name``.

    The reports are where "what did we resolve" already lives
    (``ApplyReport.sources``), so strict mode reads them back rather than
    keeping a second table the two could drift apart on. The read walks a
    bounded history, newest report first, **not just the newest report**: the
    newest apply may have failed to fetch a source (a failed fetch or a strict
    refusal adopts nothing), and reading only that row would wipe the
    baseline, disarming strict mode and ``keep_last`` after one outage. Per
    key the newest report wins, and within one report its last row — a
    creation folds two applies (``carry_forward``), carried phase first, so
    the later row is the one its delivery stood behind; the walk reads each
    report backwards. A row with no ``ref`` reads ``"HEAD"``; one missing
    ``url`` or ``mode`` yields no opinion, nor do empty reports.
    """
    baselines: dict[tuple[str, str, SourceMode], str] = {}
    for report in reports:
        for source in reversed(report.sources):
            if source.url is None or source.mode is None or source.resolved_sha is None:
                continue
            key = (source.url, source.ref or "HEAD", source.mode)
            baselines.setdefault(key, source.resolved_sha)
    return baselines
