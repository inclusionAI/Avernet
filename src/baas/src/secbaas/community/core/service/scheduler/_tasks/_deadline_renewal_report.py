"""Structured run report dataclasses for DeadlineRenewalScheduler.

Provides RenewalRunReport (per-run summary) and GapDetectionResult
(gap detection phase outcome).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class GapDetectionResult:
    """Outcome of the gap detection step (Step 0 in design doc §8.2)."""

    cold_count: int = 0
    hot_count: int = 0
    # 86-02 (R3): gap = hot - covered. "Covered" counts hot rows matched by
    # ANY cold row (ACTIVE or STOPPED), so terminal rows keep covering their
    # hot rows and can no longer latch the gap alarm at 1.
    gap: int = 0
    records_registered: int = 0
    register_error: int = 0
    anti_join_triggered: bool = False


@dataclass
class RenewalRunReport:
    """Per-run summary produced by DeadlineRenewalScheduler.run().

    Structured analog of DeviceTtlRunReport — provides a single-line
    ``to_log()`` method for monitor scraping.
    """

    run_uuid: str = field(default_factory=lambda: str(uuid4()))
    trigger: str = ""
    duration_seconds: float = 0.0
    gap_detected: bool = False
    gap_records_registered: int = 0
    anti_join_triggered: bool = False
    due_count: int = 0
    orphan_count: int = 0
    success: int = 0
    skipped: int = 0
    failure: int = 0
    stopped: int = 0
    # 86-02 (R3): suppressed-but-hot-ACTIVE rows (hot rows covered by a
    # STOPPED cold row) — the standalone alertable gauge dimension.
    suppressed_terminal_count: int = 0

    def to_log(self) -> str:
        """Single-line comma-separated report for monitor scraping.

        Format:
            uuid,trigger,duration,gapl,duel,orphan,ok,skp,fail,stop
        """
        return (
            f"{self.run_uuid},{self.trigger},{self.duration_seconds:.2f},"
            f"{self.gap_detected},{self.gap_records_registered},"
            f"{self.due_count},{self.orphan_count},"
            f"{self.success},{self.skipped},{self.failure},{self.stopped}"
        )
