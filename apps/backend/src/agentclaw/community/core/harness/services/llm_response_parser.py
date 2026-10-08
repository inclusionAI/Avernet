"""Parse LLM response text into a single Finding per diagnostic.

Each diagnostic produces at most one Finding:
- rule_id, rule_name, severity, file_type are all fixed by the Diagnostic class
- LLM returns a short summary line followed by detailed message
- LLM returns a score tag [SCORE:XX] (0-100) at the end
- If LLM returns empty or "[llm disabled]", an LLM01 finding is produced
- If LLM returns a no-issue marker (e.g. "无问题"), no finding is produced
"""
from __future__ import annotations

import logging
import re

from agentclaw.community.core.harness.models import Finding, Severity

logger = logging.getLogger(__name__)

_LLM_DISABLED_MARKER = "[llm disabled]"

_NO_ISSUE_MARKERS = frozenset({
    "无问题", "没有问题", "未发现问题", "无", "无发现",
    "no issues", "no issues found", "none", "n/a", "na",
    "pass", "ok", "good", "clear", "clean",
})

_SCORE_PATTERN = re.compile(r"\[SCORE[:：]\s*(\d{1,3})\s*\]", re.IGNORECASE)


def _extract_score(text: str) -> tuple[int, str]:
    """Extract [SCORE:XX] from text, returning (score, cleaned_text).

    Score is clamped to 0-100. If no score tag found, defaults to 0.
    The score tag is removed from the returned text.
    """
    match = _SCORE_PATTERN.search(text)
    if match:
        score = int(match.group(1))
        score = max(0, min(100, score))
        cleaned = text[:match.start()] + text[match.end():]
        return score, cleaned.strip()
    return 0, text


def _extract_summary_and_message(text: str) -> tuple[str, str]:
    """Extract short summary (first line) and remaining message from LLM response.

    Expected format:
        第一行：5-7字简短总结
        后续行：详细问题描述和修复建议

    If the text is a single line, it is used as both summary and message.
    """
    lines = text.strip().split("\n", 1)
    summary = lines[0].strip()
    message = lines[1].strip() if len(lines) > 1 else summary

    # Truncate summary to 20 chars for safety
    if len(summary) > 20:
        summary = summary[:20]

    return summary, message


def parse_diagnostic_response(
    rule_id: str,
    rule_name: str,
    severity: str,
    file_type: str,
    suggested_template_ids: list[int] | None,
    bot_id: str,
    raw_text: str,
) -> list[Finding]:
    """Parse LLM response for a single diagnostic into 0 or 1 Finding.

    Args:
        rule_id: Diagnostic rule ID (e.g. "D-AGENTS-001").
        rule_name: Diagnostic rule name (e.g. "AGENTS.md 角色定义诊断").
        severity: Diagnostic severity ("critical" | "warning" | "info").
        file_type: Target file type (e.g. "AGENTS.md").
        suggested_template_ids: Template IDs suggested for this diagnostic.
        bot_id: The bot ID for context.
        raw_text: Raw response text from LLM.chat().

    Returns:
        List with 0 or 1 Finding. Never raises.
    """
    message = raw_text.strip() if raw_text else ""

    # Strip surrounding quotes (LLM sometimes returns "" as a literal response)
    if len(message) >= 2 and message[0] == '"' and message[-1] == '"':
        message = message[1:-1].strip()

    # Use a copy to avoid mutating the shared class-level list
    template_ids = list(suggested_template_ids) if suggested_template_ids else []

    # LLM disabled or empty response → score 0
    if not message or message == _LLM_DISABLED_MARKER:
        return [Finding(
            rule_id="LLM01",
            rule_name="LLM 服务未启用",
            severity=Severity.INFO,
            file_type=file_type,
            message=f"{file_type} 的语义诊断被跳过：LLM 服务未启用",
            short_summary="LLM执行异常(请重试)",
            score=0,
            suggested_template_ids=[],
        )]

    # No issue found → score 100
    if message.lower() in _NO_ISSUE_MARKERS:
        return []

    # Extract score from [SCORE:XX] tag
    score, message = _extract_score(message)

    # Score >= 80 means "pass" — no patch needed
    if score >= 80:
        logger.info(
            "[parse_diagnostic_response] %s score=%d (>=80, pass), clearing suggested_template_ids",
            rule_id, score,
        )
        template_ids = []

    # Score 0 means "check failed" (e.g., LLM disabled) — no patch should be generated
    if score == 0:
        logger.info(
            "[parse_diagnostic_response] %s score=0 (check failed), clearing suggested_template_ids",
            rule_id,
        )
        template_ids = []

    # Ensure severity is valid
    if severity not in ("critical", "warning", "info"):
        logger.warning(
            "[parse_diagnostic_response] Invalid severity '%s', defaulting to 'info'",
            severity,
        )
        severity = "info"

    short_summary, full_message = _extract_summary_and_message(message)

    # Derive result from score: >0 uses score_to_result, ==0 means LLM failed → "error"
    if score > 0:
        from agentclaw.community.core.harness.models import score_to_result
        result = score_to_result(score)
    else:
        result = "error"

    return [Finding(
        rule_id=rule_id,
        rule_name=rule_name,
        severity=Severity(severity),
        file_type=file_type,
        message=full_message,
        short_summary=short_summary,
        score=score,
        result=result,
        suggested_template_ids=template_ids,
    )]
