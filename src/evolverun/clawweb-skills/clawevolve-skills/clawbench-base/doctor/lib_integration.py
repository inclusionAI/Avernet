"""
Doctor Integration Module for AgentBench.

Lightweight integration that preserves rule-based diagnosis as the foundation,
with optional LLM enhancement.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Optional LLM import
try:
    from lib_llm_analyzer import LLMAnalyzer, LLMInsight
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False

from lib_patterns import PatternDetector, Issue

logger = logging.getLogger(__name__)


@dataclass
class TaskDiagnosis:
    """Diagnosis result for a single task."""
    task_id: str
    score: float
    status: str
    issues: List[Issue] = field(default_factory=list)
    llm_insights: List[LLMInsight] = field(default_factory=list)
    user_query: str = ""
    thinking_count: int = 0
    tool_call_count: int = 0


class TranscriptExtractor:
    """Extract information from OpenClaw transcript."""

    @staticmethod
    def extract_user_query(transcript: List[Dict]) -> str:
        """Extract the first user message as the query."""
        for entry in transcript:
            if entry.get("type") == "message":
                msg = entry.get("message", {})
                if msg.get("role") == "user":
                    content = msg.get("content", [])
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text = item.get("text", "")
                            # Remove timestamp prefix if present
                            if "]" in text and "GMT" in text[:30]:
                                return text.split("]", 1)[1].strip()
                            return text
        return ""

    @staticmethod
    def extract_thinking_count(transcript: List[Dict]) -> int:
        """Count thinking entries in transcript."""
        count = 0
        for entry in transcript:
            if entry.get("type") == "message":
                msg = entry.get("message", {})
                if msg.get("role") == "assistant":
                    content = msg.get("content", [])
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "thinking":
                            count += 1
        return count

    @staticmethod
    def extract_tool_calls(transcript: List[Dict]) -> List[Dict]:
        """Extract tool calls from transcript."""
        tool_calls = []
        tool_results = {}

        # First pass: collect tool calls and results
        for entry in transcript:
            if entry.get("type") != "message":
                continue

            msg = entry.get("message", {})
            role = msg.get("role", "")
            content = msg.get("content", [])

            if role == "assistant":
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "toolCall":
                        tool_calls.append({
                            "tool_name": item.get("name", ""),
                            "tool_call_id": item.get("id", ""),
                            "arguments": item.get("arguments", {}),
                            "timestamp": entry.get("timestamp"),
                            "result": None,
                            "is_error": False,
                        })

            elif role == "toolResult":
                tool_call_id = msg.get("toolCallId", "")
                result_content = msg.get("content", [])
                result_text = ""
                if isinstance(result_content, list):
                    for item in result_content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            result_text = item.get("text", "")
                            break

                tool_results[tool_call_id] = {
                    "result": result_text,
                    "is_error": msg.get("isError", False),
                }

        # Second pass: match results with calls
        for tc in tool_calls:
            tc_id = tc["tool_call_id"]
            if tc_id in tool_results:
                tc["result"] = tool_results[tc_id]["result"]
                tc["is_error"] = tool_results[tc_id]["is_error"]

        return tool_calls


class DoctorAnalyzer:
    """
    Main analyzer combining rule-based and optional LLM diagnosis.

    Rule-based detection is always used as the foundation.
    LLM analysis is optional and only triggered when:
    1. explicitly enabled
    2. rule-based issues are found (to avoid wasting LLM calls)
    """

    def __init__(
        self,
        enable_llm: bool = False,
        llm_api_key: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_base_url: Optional[str] = None,
        llm_only_on_issues: bool = True,
    ):
        self.enable_llm = enable_llm and LLM_AVAILABLE
        self.llm_only_on_issues = llm_only_on_issues
        self.detector = PatternDetector()
        self.extractor = TranscriptExtractor()

        # Initialize LLM analyzer if requested
        self.llm_analyzer = None
        if self.enable_llm:
            try:
                self.llm_analyzer = LLMAnalyzer(
                    api_key=llm_api_key,
                    model=llm_model,
                    base_url=llm_base_url,
                )
                if self.llm_analyzer.is_available():
                    logger.info("✅ LLM analyzer initialized")
                else:
                    logger.warning("⚠️ LLM analyzer not available")
                    self.llm_analyzer = None
            except Exception as e:
                logger.warning("⚠️ Failed to initialize LLM: %s", e)
                self.llm_analyzer = None

    def diagnose(
        self,
        task_id: str,
        transcript: List[Dict],
        score: float,
        status: str = "success",
    ) -> Optional[TaskDiagnosis]:
        """
        Diagnose a task.

        Always runs rule-based detection.
        Optionally runs LLM analysis if enabled and issues found.

        Args:
            task_id: Task identifier
            transcript: OpenClaw transcript
            score: Task score
            status: Task status

        Returns:
            TaskDiagnosis or None if no issues and no LLM insights
        """
        # Extract context
        user_query = self.extractor.extract_user_query(transcript)
        thinking_count = self.extractor.extract_thinking_count(transcript)
        tool_calls = self.extractor.extract_tool_calls(transcript)

        # Create diagnosis object
        diagnosis = TaskDiagnosis(
            task_id=task_id,
            score=score,
            status=status,
            user_query=user_query,
            thinking_count=thinking_count,
            tool_call_count=len(tool_calls),
        )

        # Step 1: Rule-based detection (always run)
        issues = self._detect_issues(transcript, task_id, tool_calls)
        diagnosis.issues = issues

        # Step 2: LLM analysis (optional, only if issues found or forced)
        if self.llm_analyzer and (issues or not self.llm_only_on_issues):
            llm_insights = self._analyze_with_llm(
                task_id, user_query, transcript, tool_calls, issues
            )
            diagnosis.llm_insights = llm_insights

        # Return diagnosis if there are any findings
        if diagnosis.issues or diagnosis.llm_insights:
            return diagnosis
        return None

    def _detect_issues(
        self,
        transcript: List[Dict],
        task_id: str,
        tool_calls: List[Dict],
    ) -> List[Issue]:
        """Run rule-based pattern detection."""
        # Convert transcript to events format expected by detector
        events = []
        for entry in transcript:
            event = self._convert_to_event(entry)
            if event:
                events.append(event)

        # Run detection
        try:
            issues = self.detector.detect(events, task_id, tool_calls)
            return issues
        except Exception as e:
            logger.warning("Pattern detection failed: %s", e)
            return []

    def _convert_to_event(self, entry: Dict) -> Optional[Any]:
        """Convert transcript entry to event format."""
        from lib_doctor import TranscriptEvent

        event_type = entry.get("type", "")

        if event_type == "session":
            return TranscriptEvent(
                event_type="session",
                event_id=entry.get("id", ""),
                timestamp=entry.get("timestamp"),
                raw=entry,
            )

        if event_type == "message":
            msg = entry.get("message", {})
            role = msg.get("role", "")
            content = msg.get("content", [])

            # Extract thinking
            thinking = None
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "thinking":
                        thinking = item.get("thinking", "")
                        break

            if role == "assistant":
                # Check for tool calls
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "toolCall":
                        return TranscriptEvent(
                            event_type="toolCall",
                            event_id=entry.get("id", ""),
                            timestamp=entry.get("timestamp"),
                            role=role,
                            tool_name=item.get("name", ""),
                            tool_call_id=item.get("id", ""),
                            arguments=item.get("arguments", {}),
                            thinking=thinking,
                            raw=entry,
                        )

                # Regular assistant message
                return TranscriptEvent(
                    event_type="message",
                    event_id=entry.get("id", ""),
                    timestamp=entry.get("timestamp"),
                    role=role,
                    content=content,
                    thinking=thinking,
                    raw=entry,
                )

            elif role == "toolResult":
                result_content = msg.get("content", [])
                result_text = ""
                if isinstance(result_content, list):
                    for item in result_content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            result_text = item.get("text", "")
                            break

                return TranscriptEvent(
                    event_type="toolResult",
                    event_id=entry.get("id", ""),
                    timestamp=entry.get("timestamp"),
                    role=role,
                    tool_name=msg.get("toolName", ""),
                    tool_call_id=msg.get("toolCallId", ""),
                    result=result_text,
                    is_error=msg.get("isError", False),
                    raw=entry,
                )

            elif role == "user":
                return TranscriptEvent(
                    event_type="message",
                    event_id=entry.get("id", ""),
                    timestamp=entry.get("timestamp"),
                    role=role,
                    content=content,
                    raw=entry,
                )

        return TranscriptEvent(
            event_type=event_type,
            event_id=entry.get("id", ""),
            timestamp=entry.get("timestamp"),
            raw=entry,
        )

    def _analyze_with_llm(
        self,
        task_id: str,
        user_query: str,
        transcript: List[Dict],
        tool_calls: List[Dict],
        issues: List[Issue],
    ) -> List[LLMInsight]:
        """Run LLM analysis."""
        if not self.llm_analyzer:
            return []

        # Extract thinking sequence
        thinking_sequence = []
        for entry in transcript:
            if entry.get("type") == "message":
                msg = entry.get("message", {})
                if msg.get("role") == "assistant":
                    content = msg.get("content", [])
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "thinking":
                            thinking_sequence.append({
                                "timestamp": entry.get("timestamp"),
                                "content": item.get("thinking", ""),
                            })

        try:
            insights = self.llm_analyzer.analyze_session(
                task_id=task_id,
                user_query=user_query,
                thinking_sequence=thinking_sequence,
                tool_calls=tool_calls,
                issues=issues,
            )
            return insights
        except Exception as e:
            logger.warning("LLM analysis failed: %s", e)
            return []


class DiagnosisReporter:
    """Generate diagnosis reports."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save_diagnosis(
        self,
        diagnosis: TaskDiagnosis,
        run_id: str,
        model: str,
    ) -> Path:
        """Save single task diagnosis."""
        # Create run-specific directory
        run_dir = self.output_dir / f"{run_id}_{model.replace('/', '-')}"
        run_dir.mkdir(parents=True, exist_ok=True)

        # Build report
        report = {
            "task_id": diagnosis.task_id,
            "score": diagnosis.score,
            "status": diagnosis.status,
            "user_query": diagnosis.user_query,
            "thinking_count": diagnosis.thinking_count,
            "tool_call_count": diagnosis.tool_call_count,
            "issue_count": len(diagnosis.issues),
            "llm_insight_count": len(diagnosis.llm_insights),
            "issues": [
                {
                    "pattern_id": issue.pattern_id,
                    "pattern_name": issue.pattern_name,
                    "severity": issue.severity.value,
                    "description": issue.description,
                    "tool_name": issue.tool_name,
                    "suggestion": issue.suggestion,
                }
                for issue in diagnosis.issues
            ],
            "llm_insights": [
                {
                    "insight_id": insight.insight_id,
                    "category": insight.category,
                    "severity": insight.severity,
                    "description": insight.description,
                    "root_cause": insight.root_cause,
                    "suggestion": insight.suggestion,
                    "confidence": insight.confidence,
                }
                for insight in diagnosis.llm_insights
            ],
        }

        # Save to file
        report_path = run_dir / f"{diagnosis.task_id}_diagnosis.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        return report_path

    def print_diagnosis(self, diagnosis: TaskDiagnosis):
        """Print diagnosis to console."""
        print(f"\n🔍 Task {diagnosis.task_id}:")
        print(f"   Score: {diagnosis.score:.2f}")

        if diagnosis.issues:
            print(f"   Rule-based issues: {len(diagnosis.issues)}")
            for issue in diagnosis.issues[:3]:  # Show first 3
                emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
                    issue.severity.value, "⚪"
                )
                print(f"      {emoji} [{issue.pattern_id}] {issue.description[:60]}...")

        if diagnosis.llm_insights:
            print(f"   LLM insights: {len(diagnosis.llm_insights)}")
            for insight in diagnosis.llm_insights[:2]:  # Show first 2
                print(f"      🤖 [{insight.category}] {insight.description[:60]}...")


class DoctorIntegration:
    """
    High-level integration class for AgentBench.

    Combines analyzer and reporter for easy use.
    """

    def __init__(
        self,
        output_dir: Path,
        enable_llm: bool = False,
        llm_api_key: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_base_url: Optional[str] = None,
        score_threshold: float = 1.0,
        llm_only_on_issues: bool = False,  # 默认 False：开启 LLM 时始终分析
        domain: Optional[str] = None,
    ):
        self.analyzer = DoctorAnalyzer(
            enable_llm=enable_llm,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            llm_base_url=llm_base_url,
            llm_only_on_issues=llm_only_on_issues,
        )
        self.reporter = DiagnosisReporter(output_dir)
        self.score_threshold = score_threshold
        self.domain = domain
        self.diagnoses: List[TaskDiagnosis] = []

    def diagnose_task(
        self,
        task_id: str,
        transcript: List[Dict],
        score: float,
        status: str = "success",
        user_query: str = "",
    ) -> Optional[TaskDiagnosis]:
        """Diagnose a single task."""
        # Skip if score is above threshold
        if score >= self.score_threshold:
            return None

        # Run diagnosis
        diagnosis = self.analyzer.diagnose(task_id, transcript, score, status)

        if diagnosis:
            self.diagnoses.append(diagnosis)
            return diagnosis

        return None

    def save_report(self, run_id: str, model: str) -> Optional[Path]:
        """Save aggregated report."""
        if not self.diagnoses:
            return None

        summary = {
            "run_id": run_id,
            "model": model,
            "domain": self.domain,
            "total_diagnosed": len(self.diagnoses),
            "total_issues": sum(len(d.issues) for d in self.diagnoses),
            "total_llm_insights": sum(len(d.llm_insights) for d in self.diagnoses),
            "diagnoses": [
                {
                    "task_id": d.task_id,
                    "score": d.score,
                    "issue_count": len(d.issues),
                    "llm_insight_count": len(d.llm_insights),
                }
                for d in self.diagnoses
            ],
        }

        report_path = self.reporter.output_dir / f"{run_id}_{model.replace('/', '-')}_summary.json"
        report_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        return report_path

    def print_summary(self):
        """Print summary to console."""
        if not self.diagnoses:
            return

        print("\n" + "=" * 70)
        print("🔍 DOCTOR DIAGNOSIS SUMMARY")
        print("=" * 70)

        total_issues = sum(len(d.issues) for d in self.diagnoses)
        total_llm = sum(len(d.llm_insights) for d in self.diagnoses)

        print(f"\n   Tasks diagnosed: {len(self.diagnoses)}")
        print(f"   Total issues: {total_issues}")
        if self.analyzer.llm_analyzer:
            print(f"   LLM insights: {total_llm}")

        # Pattern breakdown
        pattern_counts: Dict[str, int] = {}
        for diagnosis in self.diagnoses:
            for issue in diagnosis.issues:
                pid = issue.pattern_id
                pattern_counts[pid] = pattern_counts.get(pid, 0) + 1

        if pattern_counts:
            print("\n   Issues by pattern:")
            for pid, count in sorted(pattern_counts.items()):
                print(f"      - {pid}: {count}")

        print("=" * 70 + "\n")
