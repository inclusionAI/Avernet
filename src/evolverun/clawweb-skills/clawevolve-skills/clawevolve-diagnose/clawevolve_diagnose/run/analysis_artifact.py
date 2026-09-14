from __future__ import annotations

from pathlib import Path
from typing import Any

from ..artifacts.reporting import AnalysisReportBuilder, RelatedFileLocator
from ..models import CasePreference, Diagnosis, SessionRow


def write_analysis_report(
    out_dir: Path,
    safe_bot: str,
    bot_id: str,
    selected: list[Diagnosis],
    selection_report: dict[str, Any],
    layout: dict[str, Any],
    *,
    rows: list[SessionRow] | None = None,
    preference: CasePreference | None = None,
    product_outcome: dict[str, Any] | None = None,
) -> Path:
    report_path = out_dir / f"{safe_bot}_analysis_report.md"
    builder = AnalysisReportBuilder(RelatedFileLocator(layout))
    report_path.write_text(
        builder.build(
            bot_id,
            selected,
            selection_report,
            rows=rows or [],
            preference=preference or CasePreference(),
            product_outcome=product_outcome or {},
        ),
        encoding="utf-8",
    )
    return report_path
