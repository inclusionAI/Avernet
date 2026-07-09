"""ContentScanner — LLM-powered semantic diagnostic engine for Bot MD/JSON files.

Each diagnostic check item is an independent class that gathers its own data
and calls the LLM with its own system prompt. Diagnostics run in parallel.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agentclaw.community.core.harness.diagnostics import (
    Diagnostic,
    DiagnosticContext,
    register_all_diagnostics,
)
from agentclaw.community.core.harness.models import (
    FindingsReport,
    Finding,
    Layer,
    Severity,
)
from agentclaw.community.core.harness.services.bot_profile import BotProfile
from agentclaw.community.core.harness.services.llm import LLM
from agentclaw.community.utils.env_utils import get_current_env

logger = logging.getLogger(__name__)


class ContentScanner:
    """LLM-powered semantic diagnostic engine.

    Flow:
        1. List Bot files via BotProfile
        2. Build DiagnosticContext with LLM and BotProfile
        3. Run all diagnostics in parallel via asyncio.gather
        4. Aggregate findings → health score → FindingsReport
    """

    def __init__(
        self,
        bot_profile: BotProfile,
        llm: LLM,
        *,
        patch_library: Any,
        mcp_center: Any,
        kb_config: Any,
        diagnostics: list[Diagnostic] | None = None,
        bcsfuse_base_url: str = "",
    ):
        self._bot_profile = bot_profile
        self._llm = llm
        self._patch_library = patch_library
        self._mcp_center = mcp_center
        self._bcsfuse_base_url = bcsfuse_base_url
        self._kb_config = kb_config
        self._diagnostics = diagnostics if diagnostics is not None else register_all_diagnostics()

    async def scan(
        self,
        entity_type: str,
        entity_id: str,
        bot_id: str,
        operator_id: str = "",
        bot_meta: dict[str, Any] | None = None,
        bot_publish_id: str | None = None,
    ) -> FindingsReport:
        """Run all diagnostic checks via LLM analysis.

        Each diagnostic independently gathers data and calls the LLM.
        Diagnostics run in parallel. Individual failures are caught and
        recorded as SYS01/SYS02 findings without aborting the scan.

        If bot_publish_id is provided, file reads will use that publish version.
        """
        start = time.time()
        report = FindingsReport(
            bot_id=bot_id,
            entity_id=entity_id,
            scan_type="full",
            layer=Layer.L1,
            trigger_source="api",
            env=get_current_env(),
        )

        all_findings: list[Finding] = []

        ctx = DiagnosticContext(
            llm=self._llm,
            bot_profile=self._bot_profile,
            entity_type=entity_type,
            entity_id=entity_id,
            bot_id=bot_id,
            operator_id=operator_id,
            bot_meta=bot_meta or {},
            bot_publish_id=bot_publish_id,
            mcp_center=self._mcp_center,
            bcsfuse_base_url=self._bcsfuse_base_url,
            kb_config=self._kb_config,
        )

        # Run all diagnostics in parallel
        logger.info(
            "[ContentScanner] Starting scan bot=%s diagnostics=%d",
            bot_id, len(self._diagnostics),
        )
        results = await asyncio.gather(
            *[diag.analyze(ctx) for diag in self._diagnostics],
            return_exceptions=True,
        )

        for diag, result in zip(self._diagnostics, results):
            if isinstance(result, Exception):
                logger.error(
                    "[ContentScanner] Diagnostic %s (%s) FAILED: %s",
                    diag.id, diag.name, result,
                )
                all_findings.append(Finding(
                    rule_id="SYS02",
                    rule_name=f"诊断项执行失败: {diag.name}",
                    severity=Severity.CRITICAL,
                    file_type="",
                    message=f"诊断项 {diag.id} ({diag.name}) 执行时发生错误: {result}",
                    suggested_template_ids=[],
                ))
            else:
                logger.info(
                    "[ContentScanner] Diagnostic %s (%s) returned %d findings",
                    diag.id, diag.name, len(result),
                )
                all_findings.extend(result)

        # Enrich findings with PatchLibrary template suggestions
        all_findings = self._enrich_with_templates(all_findings, bot_meta)

        # Build report
        report.findings = all_findings
        all_file_types = sorted({d.file_type for d in self._diagnostics if d.file_type})
        report.findings_summary = self._summarize(all_findings, file_types=all_file_types)
        report.health_score = self._compute_score(all_findings, file_types=all_file_types)
        report.score_grade = report.compute_grade()
        report.duration_ms = int((time.time() - start) * 1000)
        report.check_items = self._build_check_items()
        report.status = "completed"

        logger.info(
            "[ContentScanner] Scan completed bot=%s findings=%d score=%d grade=%s duration=%dms summary=%s",
            bot_id, len(all_findings), report.health_score, report.score_grade,
            report.duration_ms, report.findings_summary,
        )

        return report

    def _enrich_with_templates(
        self,
        findings: list[Finding],
        bot_meta: dict[str, Any] | None,
    ) -> list[Finding]:
        """Enrich findings with suggested_template_ids from PatchLibrary.

        Only validates existing suggested_template_ids against PatchLibrary.
        No fallback — if template_ids were cleared (score >= 80 or 0),
        they stay empty to avoid generating patches.
        """
        for finding in findings:
            # Validate diagnostic-specified IDs against PatchLibrary
            if finding.suggested_template_ids:
                valid_ids: list[int] = []
                for tpl_id in finding.suggested_template_ids:
                    try:
                        tpl = self._patch_library.get_template_by_id(tpl_id)
                        if tpl is not None:
                            valid_ids.append(tpl_id)
                    except Exception:
                        logger.warning(
                            "PatchLibrary get_template_by_id(%s) failed, skipping",
                            tpl_id,
                        )
                finding.suggested_template_ids = valid_ids
        return findings

    def _build_check_items(self) -> list[dict[str, Any]]:
        """Build check_items from the registered diagnostics."""
        return [
            {
                "rule_id": diag.id,
                "rule_name": diag.name,
                "severity": diag.severity,
            }
            for diag in self._diagnostics
        ]

    @staticmethod
    def _summarize(findings: list[Finding], file_types: list[str] | None = None) -> dict[str, int]:
        """Summarize findings by file-type-level result.

        Groups findings by file_type, computes each file type's result
        (pass/warning/fail/error), and counts how many file types fall into each bucket.
        score=0 maps to "error" (LLM disabled or check failed).

        Args:
            findings: List of Finding objects from diagnostics.
            file_types: All file types that were checked. File types with no
                findings are counted as "pass".
        """
        from agentclaw.community.core.harness.models import score_to_result

        summary: dict[str, int] = {"pass": 0, "warning": 0, "fail": 0, "error": 0}
        if not findings and not file_types:
            return summary

        # Group findings by file type
        file_groups: dict[str, list[Finding]] = {}
        for f in findings:
            ft = f.file_type or "_unknown"
            file_groups.setdefault(ft, []).append(f)

        for ft, flist in file_groups.items():
            # Compute file-level result: prefer score-based (exclude score==0 from avg)
            positive_scores = [f.score for f in flist if f.score > 0]

            if positive_scores:
                avg = sum(positive_scores) / len(positive_scores)
                result = score_to_result(avg)
            else:
                # All scores are 0 → error (LLM disabled or check failed)
                result = "error"

            if result in summary:
                summary[result] += 1

        # File types with no findings count as "pass"
        if file_types:
            seen = set(file_groups.keys())
            for ft in file_types:
                if ft not in seen:
                    summary["pass"] += 1

        return summary

    @staticmethod
    def _compute_score(findings: list[Finding], file_types: list[str] | None = None) -> int:
        """Compute health score based on per-file-type LLM score average.

        When findings have LLM-provided scores (score > 0), the health score
        is the average of per-file-type average scores.
        Falls back to penalty-based scoring when no scores are available.

        Args:
            findings: List of Finding objects from diagnostics.
            file_types: All file types that were checked. File types with no
                findings are scored 100 (all diagnostics passed).
        """
        if not findings and not file_types:
            return 100

        # Try LLM score-based computation
        file_groups: dict[str, list[Finding]] = {}
        for f in findings:
            ft = f.file_type or "_unknown"
            file_groups.setdefault(ft, []).append(f)

        file_avg_scores: list[float] = []
        for ft, flist in file_groups.items():
            scores = [f.score for f in flist if f.score > 0]
            if scores:
                file_avg_scores.append(sum(scores) / len(scores))

        # File types with no findings score 100 (all diagnostics passed)
        if file_types:
            seen = set(file_groups.keys())
            for ft in file_types:
                if ft not in seen:
                    file_avg_scores.append(100.0)

        if file_avg_scores:
            return int(sum(file_avg_scores) / len(file_avg_scores))

        # Fallback: penalty-based scoring
        penalty = 0
        for f in findings:
            if f.severity == Severity.CRITICAL:
                penalty += 20
            elif f.severity == Severity.WARNING:
                penalty += 10
            elif f.severity == Severity.INFO:
                penalty += 2
        return max(0, 100 - penalty)
