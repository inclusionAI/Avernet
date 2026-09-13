"""
PinchBench Doctor - Session Diagnosis System.

Analyzes OpenClaw agent session transcripts to identify issues and generate
optimization suggestions for skills and memories.

Usage:
    python -m doctor --run-id 0042 --model kimi-k2-5

Or:
    cd doctor && python run.py --run-id 0042 --model kimi-k2-5
"""

from lib_doctor import DoctorEngine, DiagnosisReport, TaskDiagnosis, TranscriptEvent
from lib_patterns import (
    PatternDetector,
    Issue,
    IssueSeverity,
    RepeatedToolCallFailureDetector,
    ParameterContextLossDetector,
    MissingRequiredToolDetector,
)
from lib_optimizer import PatchGenerator, Patch
from lib_report import ReportGenerator

__all__ = [
    # Main engine
    "DoctorEngine",
    "DiagnosisReport",
    "TaskDiagnosis",
    "TranscriptEvent",
    # Pattern detection
    "PatternDetector",
    "Issue",
    "IssueSeverity",
    "RepeatedToolCallFailureDetector",
    "ParameterContextLossDetector",
    "MissingRequiredToolDetector",
    # Optimization
    "PatchGenerator",
    "Patch",
    # Reporting
    "ReportGenerator",
]

__version__ = "0.1.0"