from __future__ import annotations

from pathlib import Path
from typing import Iterable, Protocol

from ..constants import DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS
from ..models import (
    CasePreference,
    Diagnosis,
    JudgeRuntimeConfig,
    SessionRow,
    normalize_judge_backend,
)
from .ocsa_session_report_analyzer import (
    OcsaSessionReportAnalyzer,
    OcsaSessionReportJudgeConfig,
)
from .keyless_session_analyzer import (
    KeylessSessionJudgeConfig,
    KeylessSubagentSessionAnalyzer,
)


class SessionAnalyzer(Protocol):
    """Common contract for diagnose session judge backends."""

    def analyze(self, rows: Iterable[SessionRow], bot_id: str = "") -> list[Diagnosis]:
        """Analyze session rows and return normalized diagnoses."""
        ...


def create_session_analyzer(
    runtime: JudgeRuntimeConfig,
    preference: CasePreference,
    *,
    max_concurrent_tasks: int,
    artifact_dir: Path | None = None,
) -> SessionAnalyzer:
    """Create the selected local session analyzer."""

    backend = normalize_judge_backend(runtime.backend)
    if backend == "subagent":
        return KeylessSubagentSessionAnalyzer(
            KeylessSessionJudgeConfig(
                runtime=runtime,
                preference=preference,
                timeout_seconds=DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS,
                artifact_dir=artifact_dir,
            )
        )
    return OcsaSessionReportAnalyzer(
        OcsaSessionReportJudgeConfig(
            runtime=runtime,
            preference=preference,
            timeout_seconds=DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS,
            max_concurrent_tasks=max_concurrent_tasks,
        )
    )
