"""Harness business data models (Pydantic + dataclass).

Zero external dependencies; used across services, repository, and API layers.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from agentclaw.community.utils.env_utils import get_current_env


class Layer(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"

    def __str__(self) -> str:
        """Return the value, not the name (prevents 'Layer.L1' instead of 'L1')."""
        return self.value


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"

    def __str__(self) -> str:
        return self.value


class PatchStatus(str, Enum):
    PLANNED = "planned"
    PREVIEWED = "previewed"
    APPLYING = "applying"
    APPLIED = "applied"
    VERIFIED = "verified"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"

    def __str__(self) -> str:
        return self.value


class PatchTemplateStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DRAFT = "draft"

    def __str__(self) -> str:
        return self.value


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    def __str__(self) -> str:
        return self.value


@dataclass(slots=True)
class FindingsReport:
    """Result of a ContentScanner run."""

    bot_id: str
    entity_id: str
    scan_type: str = "full"
    layer: Layer = Layer.L1
    health_score: int = 0
    score_grade: str | None = None
    check_items: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    findings_summary: dict[str, int] = field(default_factory=dict)
    trigger_source: str = "api"
    duration_ms: int = 0
    status: str = "completed"
    failed_reason: str | None = None
    env: str = field(default_factory=get_current_env)
    gmt_create: datetime = field(default_factory=datetime.utcnow)
    bot_publish_id: str | None = None

    def compute_grade(self) -> str:
        if self.health_score >= 90:
            return "excellent"
        elif self.health_score >= 70:
            return "good"
        elif self.health_score >= 50:
            return "warning"
        return "critical"


def severity_to_result(severity: Severity | str) -> str:
    """Map a severity level to a diagnosis result.

    "critical" → "fail", "warning"/"info" → "warning".
    """
    val = severity.value if isinstance(severity, Severity) else str(severity)
    if val == "critical":
        return "fail"
    return "warning"


def score_to_result(avg_score: float) -> str:
    """Map a file-level average score to a diagnosis result.

    ==0 → "error" (LLM disabled or check failed),
    >=80 → "pass", >60 → "warning", ≤60 → "fail".
    """
    if avg_score == 0:
        return "error"
    if avg_score >= 80:
        return "pass"
    if avg_score > 60:
        return "warning"
    return "fail"


@dataclass(slots=True)
class Finding:
    """A single diagnostic finding."""

    rule_id: str
    rule_name: str
    severity: Severity
    file_type: str
    message: str
    short_summary: str = ""
    result: str = ""  # "fail" | "warning", derived from severity if empty
    score: int = 0  # 0-100, LLM diagnostic score
    suggested_template_ids: list[int] = field(default_factory=list)
    line_start: int | None = None
    line_end: int | None = None

    def __post_init__(self) -> None:
        if not self.result:
            # score > 0 → score-based result; score == 0 → "error" (LLM failed/disabled)
            if self.score > 0:
                self.result = score_to_result(self.score)
            else:
                self.result = "error"


@dataclass(slots=True)
class PatchOperation:
    """A single operation inside a patch template.

    固定字段：op, target, template。
    自由字段：全放在 `detail` 中（如 src_md_content, dst_md_content 等）。
    """

    op: str
    target: str
    template: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PatchTarget:
    """Target specification for a patch."""

    files: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PatchDefinition:
    """A concrete patch instance (linked to template, ready to apply)."""

    template_id: int
    name: str
    layer: Layer
    id: int | None = None
    description: str | None = None
    scope: str = "all"
    scope_value: str | None = None
    content: str | None = None
    is_applied: bool = False
    advise: str | None = None
    scan_id: int | None = None
    env: str = field(default_factory=get_current_env)
    gmt_create: datetime = field(default_factory=datetime.utcnow)
    gmt_modified: datetime = field(default_factory=datetime.utcnow)


@dataclass(slots=True)
class PatchTemplate:
    """A patch template from PatchLibrary."""

    name: str
    layer: Layer
    target: PatchTarget
    id: int | None = None
    version: int = 1
    description: str | None = None
    applicable_when: dict[str, Any] | None = None
    operations: list[PatchOperation] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    status: PatchTemplateStatus = PatchTemplateStatus.ACTIVE
    env: str = field(default_factory=get_current_env)
    gmt_create: datetime = field(default_factory=datetime.utcnow)
    gmt_modified: datetime = field(default_factory=datetime.utcnow)


@dataclass(slots=True)
class PatchRecord:
    """Full lifecycle record of a patch application."""

    bot_id: str
    entity_id: str
    patch_id: int
    layer: Layer
    target: PatchTarget
    id: int | None = None
    status: PatchStatus = PatchStatus.PLANNED
    operations: list[PatchOperation] = field(default_factory=list)
    preview_diff: str | None = None
    backup_content: str | None = None
    health_score: int | None = None
    backup_path: str | None = None
    backup_type: str = "partial"
    backup_file_types: list[str] = field(default_factory=list)
    backup_checksum: str | None = None
    applied_by: str | None = None
    applied_at: datetime | None = None
    verified_at: datetime | None = None
    rolled_back_at: datetime | None = None
    failed_reason: str | None = None
    env: str = field(default_factory=get_current_env)
    gmt_create: datetime = field(default_factory=datetime.utcnow)
    gmt_modified: datetime = field(default_factory=datetime.utcnow)
    bot_publish_id: str | None = None

    def compute_checksum(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class BotFileRef:
    """Reference to a single Bot configuration file."""

    entity_type: str
    entity_id: str
    bot_id: str
    file_type: str
    content: str = ""
    checksum: str = ""

    def compute_checksum(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def serialize_findings_grouped(findings: list[Finding]) -> str:
    """Serialize a list of Finding objects to grouped JSON for DB storage.

    The grouped format is:
    [{"check_item": "AGENTS.md", "all_patch_id_list": [],
      "finding_details": [{"rule_id": "...", "name": "...", "message": "...",
                           "risk_level": "...", "result": "fail"|"warning",
                           "suggested_template_ids": [], "patch_id_list": []}]}]
    """
    import json as _json

    groups: dict[str, list[Finding]] = {}
    for f in findings:
        ft = f.file_type or "_unknown"
        if ft not in groups:
            groups[ft] = []
        groups[ft].append(f)

    result = []
    for ft, flist in groups.items():
        details = []
        for f in flist:
            risk = f.severity.value if isinstance(f.severity, Severity) else str(f.severity)
            details.append({
                "rule_id": f.rule_id,
                "name": f.rule_name,
                "message": f.message,
                "risk_level": risk,
                "result": f.result,
                "score": f.score,
                "suggested_template_ids": f.suggested_template_ids,
                "patch_id_list": [],
            })
        result.append({
            "check_item": ft,
            "all_patch_id_list": [],
            "finding_details": details,
        })
    return _json.dumps(result, default=str)
