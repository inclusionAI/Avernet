"""
PinchBench Doctor - Session Diagnosis System

Analyzes OpenClaw agent session transcripts to identify issues and generate
optimization suggestions for skills and memories.

Usage:
    python -m doctor.lib_doctor --run-id 0042 --model kimi-k2-5
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib_patterns import PatternDetector, Issue, IssueSeverity
from lib_optimizer import PatchGenerator, Patch
from lib_report import ReportGenerator
from lib_llm_analyzer import LLMAnalyzer, LLMInsight

logger = logging.getLogger(__name__)


@dataclass
class TranscriptEvent:
    """Represents a single event in the transcript."""
    event_type: str
    event_id: str
    timestamp: Optional[str] = None
    role: Optional[str] = None
    content: Optional[List[Dict]] = None
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    arguments: Optional[Dict] = None
    result: Optional[str] = None
    is_error: bool = False
    thinking: Optional[str] = None  # Agent's thinking content
    raw: Dict = field(default_factory=dict)


@dataclass
class TaskDiagnosis:
    """Diagnosis result for a single task."""
    task_id: str
    status: str
    score: float
    transcript_path: Path
    issues: List[Issue] = field(default_factory=list)
    tool_calls: List[Dict] = field(default_factory=list)
    token_usage: Dict[str, int] = field(default_factory=dict)
    execution_time: float = 0.0
    user_query: str = ""  # Original user query
    thinking_sequence: List[Dict] = field(default_factory=list)  # Agent thinking history
    llm_insights: List[LLMInsight] = field(default_factory=list)  # LLM-generated insights


@dataclass
class DiagnosisReport:
    """Complete diagnosis report for a benchmark run."""
    run_id: str
    model: str
    benchmark: str
    timestamp: str
    tasks_diagnosed: int
    total_issues: int
    issues_by_severity: Dict[str, int] = field(default_factory=dict)
    issues_by_pattern: Dict[str, int] = field(default_factory=dict)
    task_diagnoses: List[TaskDiagnosis] = field(default_factory=list)
    patches: List[Patch] = field(default_factory=list)


class TranscriptParser:
    """Parse JSONL transcript files into structured events."""

    def parse(self, transcript_path: Path) -> List[TranscriptEvent]:
        """Parse a JSONL transcript file."""
        events = []

        if not transcript_path.exists():
            logger.warning("Transcript file not found: %s", transcript_path)
            return events

        try:
            content = transcript_path.read_text(encoding="utf-8")
            for line_num, line in enumerate(content.splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    event = self._parse_event(data)
                    if event:
                        events.append(event)
                except json.JSONDecodeError as e:
                    logger.warning(
                        "Failed to parse line %d in %s: %s",
                        line_num, transcript_path.name, e
                    )
        except Exception as e:
            logger.error("Failed to read transcript %s: %s", transcript_path, e)

        return events

    def _parse_event(self, data: Dict) -> Optional[TranscriptEvent]:
        """Parse a single event from transcript JSON."""
        event_type = data.get("type", "")

        if event_type == "session":
            return TranscriptEvent(
                event_type="session",
                event_id=data.get("id", ""),
                timestamp=data.get("timestamp"),
                raw=data,
            )

        if event_type == "message":
            message = data.get("message", {})
            role = message.get("role", "")
            content = message.get("content", [])

            # Extract thinking content if present
            thinking_content = None
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "thinking":
                        thinking_content = item.get("thinking", "")
                        break

            # Handle different content types
            if role == "assistant":
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "toolCall":
                        return TranscriptEvent(
                            event_type="toolCall",
                            event_id=data.get("id", ""),
                            timestamp=data.get("timestamp"),
                            role=role,
                            tool_name=item.get("name", ""),
                            tool_call_id=item.get("id", ""),
                            arguments=item.get("arguments", {}),
                            thinking=thinking_content,
                            raw=data,
                        )
                # Regular assistant message (may contain thinking)
                return TranscriptEvent(
                    event_type="message",
                    event_id=data.get("id", ""),
                    timestamp=data.get("timestamp"),
                    role=role,
                    content=content,
                    thinking=thinking_content,
                    raw=data,
                )

            elif role == "toolResult":
                result_content = message.get("content", [])
                result_text = ""
                if result_content and isinstance(result_content, list):
                    for item in result_content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            result_text = item.get("text", "")
                            break

                return TranscriptEvent(
                    event_type="toolResult",
                    event_id=data.get("id", ""),
                    timestamp=data.get("timestamp"),
                    role=role,
                    tool_name=message.get("toolName", ""),
                    tool_call_id=message.get("toolCallId", ""),
                    result=result_text,
                    is_error=message.get("isError", False),
                    raw=data,
                )

            elif role == "user":
                return TranscriptEvent(
                    event_type="message",
                    event_id=data.get("id", ""),
                    timestamp=data.get("timestamp"),
                    role=role,
                    content=content,
                    raw=data,
                )

        # Other event types (model_change, thinking_level_change, etc.)
        return TranscriptEvent(
            event_type=event_type,
            event_id=data.get("id", ""),
            timestamp=data.get("timestamp"),
            raw=data,
        )


class DoctorEngine:
    """Main diagnosis engine."""

    def __init__(
        self,
        results_dir: Path,
        output_dir: Path,
        score_threshold: float = 0.6,
        enable_llm: bool = False,
        llm_api_key: Optional[str] = None,
        llm_model: Optional[str] = None,
    ):
        self.results_dir = Path(results_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.score_threshold = score_threshold
        self.enable_llm = enable_llm

        self.parser = TranscriptParser()
        self.detector = PatternDetector()
        self.patch_generator = PatchGenerator()
        self.report_generator = ReportGenerator()

        # Initialize LLM analyzer if enabled
        self.llm_analyzer = None
        if enable_llm:
            self.llm_analyzer = LLMAnalyzer(api_key=llm_api_key, model=llm_model)
            if self.llm_analyzer.is_available():
                logger.info("✅ LLM analysis enabled with model: %s", llm_model or "default")
            else:
                logger.warning("⚠️ LLM analysis requested but not available (check API key)")
                self.llm_analyzer = None

    def diagnose(
        self,
        run_id: str,
        model: str,
        benchmark: Optional[str] = None,
    ) -> DiagnosisReport:
        """
        Run diagnosis on a benchmark run.

        Args:
            run_id: The benchmark run ID (e.g., "0042")
            model: Model name (e.g., "kimi-k2-5")
            benchmark: Optional benchmark name for context
        """
        logger.info("🏥 Doctor: Starting diagnosis for run %s, model %s", run_id, model)

        # Find results file
        results_file = self._find_results_file(run_id, model)
        if not results_file:
            raise FileNotFoundError(
                f"Results file not found for run_id={run_id}, model={model} "
                f"in {self.results_dir}"
            )

        logger.info("📁 Loading results from: %s", results_file)
        results = self._load_results(results_file)

        # Find transcripts directory
        transcripts_dir = self._find_transcripts_dir(run_id, model)
        if not transcripts_dir:
            raise FileNotFoundError(
                f"Transcripts directory not found for run_id={run_id} "
                f"in {self.results_dir}"
            )

        logger.info("📁 Loading transcripts from: %s", transcripts_dir)

        # Initialize report
        report = DiagnosisReport(
            run_id=run_id,
            model=model,
            benchmark=benchmark or results.get("suite", "unknown"),
            timestamp=datetime.now().isoformat(),
            tasks_diagnosed=0,
            total_issues=0,
        )

        # Process each task
        for task_result in results.get("tasks", []):
            task_id = task_result.get("task_id")
            status = task_result.get("status", "unknown")
            grading = task_result.get("grading", {})
            score = grading.get("mean", 0.0)
            usage = task_result.get("usage", {})

            # Decide whether to diagnose this task
            should_diagnose = (
                score < self.score_threshold or
                status in ("error", "timeout") or
                status == "success"  # Also analyze successful tasks for efficiency
            )

            if not should_diagnose:
                continue

            logger.info("🔍 Diagnosing task: %s (score=%.2f, status=%s)", task_id, score, status)

            # Load transcript
            transcript_path = transcripts_dir / f"{task_id}.jsonl"
            events = self.parser.parse(transcript_path)

            # Extract tool calls for analysis
            tool_calls = self._extract_tool_calls(events)

            # Extract user query and thinking sequence
            user_query = self._extract_user_query(events)
            thinking_sequence = self._extract_thinking_sequence(events)

            # Run pattern detection
            issues = self.detector.detect(events, task_id, tool_calls)

            # LLM deep analysis
            llm_insights = []
            if self.llm_analyzer and issues:
                logger.info("🤖 Running LLM analysis for task: %s", task_id)
                llm_insights = self.llm_analyzer.analyze_session(
                    task_id=task_id,
                    user_query=user_query,
                    thinking_sequence=thinking_sequence,
                    tool_calls=tool_calls,
                    issues=issues,
                )
                if llm_insights:
                    logger.info("  Found %d LLM insights", len(llm_insights))

            # Create task diagnosis
            task_diagnosis = TaskDiagnosis(
                task_id=task_id,
                status=status,
                score=score,
                transcript_path=transcript_path,
                issues=issues,
                tool_calls=tool_calls,
                token_usage=usage,
                execution_time=task_result.get("execution_time", 0.0),
                user_query=user_query,
                thinking_sequence=thinking_sequence,
                llm_insights=llm_insights,
            )

            report.task_diagnoses.append(task_diagnosis)
            report.tasks_diagnosed += 1
            report.total_issues += len(issues)

            # Update statistics
            for issue in issues:
                sev = issue.severity.value
                report.issues_by_severity[sev] = report.issues_by_severity.get(sev, 0) + 1
                report.issues_by_pattern[issue.pattern_id] = (
                    report.issues_by_pattern.get(issue.pattern_id, 0) + 1
                )

        # Generate patches for all issues
        logger.info("🔧 Generating optimization patches...")
        for task_diagnosis in report.task_diagnoses:
            # Generate patches from rule-based issues
            for issue in task_diagnosis.issues:
                patches = self.patch_generator.generate(issue)
                report.patches.extend(patches)

            # Generate patches from LLM insights
            for insight in task_diagnosis.llm_insights:
                patches = self.patch_generator.generate_from_llm_insight(insight)
                report.patches.extend(patches)

        logger.info(
            "✅ Diagnosis complete: %d tasks, %d issues, %d patches",
            report.tasks_diagnosed,
            report.total_issues,
            len(report.patches),
        )

        return report

    def save_report(self, report: DiagnosisReport) -> Path:
        """Save diagnosis report to files."""
        # Normalize model name for filename
        model_slug = report.model.replace("/", "-").replace(".", "-").lower()

        # Save JSON report (flat structure with model in filename)
        json_path = self.output_dir / f"{report.run_id}_{model_slug}_doctor.json"
        json_path.write_text(
            json.dumps(self._report_to_dict(report), indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        logger.info("📄 Saved diagnosis JSON: %s", json_path)

        # Save markdown report
        md_path = self.output_dir / f"{report.run_id}_{model_slug}_doctor_report.md"
        md_content = self.report_generator.generate_markdown(report)
        md_path.write_text(md_content, encoding="utf-8")
        logger.info("📄 Saved summary: %s", md_path)

        # Save patches in a subdirectory
        if report.patches:
            patches_dir = self.output_dir / f"{report.run_id}_{model_slug}_patches"
            patches_dir.mkdir(parents=True, exist_ok=True)
            skill_patches_dir = patches_dir / "skill"
            skill_patches_dir.mkdir(parents=True, exist_ok=True)
            memory_patches_dir = patches_dir / "memory"
            memory_patches_dir.mkdir(parents=True, exist_ok=True)

            for i, patch in enumerate(report.patches):
                patch_id = f"patch_{i+1:03d}"
                if patch.patch_type == "skill":
                    patch_file = skill_patches_dir / f"{patch_id}_{patch.target_tool}.yaml"
                else:
                    patch_file = memory_patches_dir / f"{patch_id}.md"

                patch_file.write_text(patch.content, encoding="utf-8")

            logger.info("📄 Saved %d patches to: %s", len(report.patches), patches_dir)

        return self.output_dir

    def _find_results_file(self, run_id: str, model: str) -> Optional[Path]:
        """Find the results JSON file for a run."""
        model_slug = model.replace("/", "-").replace(".", "-").lower()

        # Try new format with _benchmark_report suffix
        results_file = self.results_dir / f"{run_id}_{model_slug}_benchmark_report.json"
        if results_file.exists():
            return results_file

        # Try with any model suffix matching the run_id
        for f in self.results_dir.glob(f"{run_id}_*_benchmark_report.json"):
            return f

        return None

    def _find_transcripts_dir(self, run_id: str, model: str) -> Optional[Path]:
        """Find the transcripts directory for a run."""
        model_slug = model.replace("/", "-").replace(".", "-").lower()

        # Try new format: run_id_model_slug_transcripts
        transcripts_dir = self.results_dir / f"{run_id}_{model_slug}_transcripts"
        if transcripts_dir.exists():
            return transcripts_dir

        # Try matching any directory with run_id prefix
        for d in self.results_dir.glob(f"{run_id}_*_transcripts"):
            if d.is_dir():
                return d

        return None

    def _load_results(self, results_file: Path) -> Dict:
        """Load results JSON file."""
        return json.loads(results_file.read_text(encoding="utf-8"))

    def _extract_user_query(self, events: List[TranscriptEvent]) -> str:
        """Extract the original user query from events."""
        for event in events:
            if event.event_type == "message" and event.role == "user":
                if event.content and isinstance(event.content, list):
                    for item in event.content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text = item.get("text", "")
                            # Skip system messages
                            if not text.startswith("[") or "GMT" not in text[:30]:
                                return text
                            # Extract actual query after timestamp
                            if "]" in text:
                                return text.split("]", 1)[1].strip()
        return ""

    def _extract_thinking_sequence(self, events: List[TranscriptEvent]) -> List[Dict]:
        """Extract agent thinking content from events."""
        thinking_sequence = []
        for event in events:
            if event.thinking:
                thinking_sequence.append({
                    "timestamp": event.timestamp,
                    "content": event.thinking,
                })
        return thinking_sequence

    def _extract_tool_calls(self, events: List[TranscriptEvent]) -> List[Dict]:
        """Extract tool call sequences for analysis."""
        tool_calls = []

        for event in events:
            if event.event_type == "toolCall":
                tool_calls.append({
                    "tool_name": event.tool_name,
                    "tool_call_id": event.tool_call_id,
                    "arguments": event.arguments,
                    "timestamp": event.timestamp,
                    "result": None,
                    "is_error": False,
                })
            elif event.event_type == "toolResult":
                # Match with corresponding tool call
                for tc in reversed(tool_calls):
                    if tc["tool_call_id"] == event.tool_call_id:
                        tc["result"] = event.result
                        tc["is_error"] = event.is_error
                        break

        return tool_calls

    def _report_to_dict(self, report: DiagnosisReport) -> Dict:
        """Convert report to dictionary for JSON serialization."""
        return {
            "run_id": report.run_id,
            "model": report.model,
            "benchmark": report.benchmark,
            "timestamp": report.timestamp,
            "summary": {
                "tasks_diagnosed": report.tasks_diagnosed,
                "total_issues": report.total_issues,
                "issues_by_severity": report.issues_by_severity,
                "issues_by_pattern": report.issues_by_pattern,
            },
            "tasks": [
                {
                    "task_id": td.task_id,
                    "status": td.status,
                    "score": td.score,
                    "transcript_path": str(td.transcript_path),
                    "execution_time": td.execution_time,
                    "token_usage": td.token_usage,
                    "tool_call_count": len(td.tool_calls),
                    "issues": [
                        {
                            "issue_id": issue.issue_id,
                            "pattern_id": issue.pattern_id,
                            "pattern_name": issue.pattern_name,
                            "severity": issue.severity.value,
                            "tool_name": issue.tool_name,
                            "description": issue.description,
                            "evidence": issue.evidence,
                            "suggestion": issue.suggestion,
                        }
                        for issue in td.issues
                    ],
                }
                for td in report.task_diagnoses
            ],
            "patches": [
                {
                    "patch_id": f"patch_{i+1:03d}",
                    "patch_type": patch.patch_type,
                    "target_tool": patch.target_tool,
                    "related_issues": patch.related_issues,
                }
                for i, patch in enumerate(report.patches)
            ],
        }


def main():
    parser = argparse.ArgumentParser(
        description="PinchBench Doctor - Session Diagnosis System"
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="Benchmark run ID (e.g., 0042)",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Model name (e.g., kimi-k2-5)",
    )
    parser.add_argument(
        "--benchmark",
        default=None,
        help="Benchmark name for context",
    )
    parser.add_argument(
        "--results-dir",
        default="results",
        help="Directory containing benchmark results",
    )
    parser.add_argument(
        "--output-dir",
        default="doctor/output",
        help="Output directory for diagnosis reports",
    )
    parser.add_argument(
        "--score-threshold",
        type=float,
        default=0.6,
        help="Score threshold for diagnosis (default: 0.6)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    # Run diagnosis
    engine = DoctorEngine(
        results_dir=Path(args.results_dir),
        output_dir=Path(args.output_dir),
        score_threshold=args.score_threshold,
    )

    report = engine.diagnose(
        run_id=args.run_id,
        model=args.model,
        benchmark=args.benchmark,
    )

    output_dir = engine.save_report(report)

    print(f"\n{'='*60}")
    print("🏥 Doctor Diagnosis Complete")
    print(f"{'='*60}")
    print(f"Tasks diagnosed: {report.tasks_diagnosed}")
    print(f"Total issues found: {report.total_issues}")
    print(f"Patches generated: {len(report.patches)}")
    print(f"\nOutput directory: {output_dir}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()