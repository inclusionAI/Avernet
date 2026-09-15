from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS


@dataclass(frozen=True)
class LlmRuntimeConfig:
    """OpenAI-compatible API runtime used by direct LLM helpers."""

    api_key: str = ""
    base_url: str = ""
    model: str = ""

    def safe_summary(self) -> dict[str, Any]:
        return {
            "configured": bool(self.api_key),
            "base_url": self.base_url,
            "model": self.model,
        }


@dataclass(frozen=True)
class SubagentJudgeConfig:
    """Runtime configuration for keyless OpenClaw subagent judge calls.

    The subagent backend deliberately uses the currently configured OpenClaw
    agent infrastructure instead of accepting or persisting a model API key.
    """

    agent_id: str = "clawevolve-diagnose-session-judge"
    model: str = ""
    workspace: str = ""
    openclaw_home: str = ""
    openclaw_path: str = "openclaw"
    timeout_seconds: int = DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS
    max_message_chars: int = 24000
    transport: str = "native"
    fallback_to_cli: bool = True

    def safe_summary(self) -> dict[str, Any]:
        return {
            "configured": True,
            "agent_id": self.agent_id,
            "model": self.model,
            "workspace": self.workspace,
            "openclaw_home": self.openclaw_home,
            "openclaw_path": self.openclaw_path,
            "timeout_seconds": self.timeout_seconds,
            "max_message_chars": self.max_message_chars,
            "transport": self.transport,
            "fallback_to_cli": self.fallback_to_cli,
        }


@dataclass(frozen=True)
class JudgeRuntimeConfig:
    """Transport-agnostic runtime for local session judge calls."""

    backend: str = "api"
    api: LlmRuntimeConfig = field(default_factory=LlmRuntimeConfig)
    subagent: SubagentJudgeConfig = field(default_factory=SubagentJudgeConfig)

    def safe_summary(self) -> dict[str, Any]:
        backend = normalize_judge_backend(self.backend)
        return {
            "backend": backend,
            "api": self.api.safe_summary(),
            "subagent": self.subagent.safe_summary() if backend == "subagent" else {},
        }

    @property
    def api_key(self) -> str:
        """Compatibility accessor for API-key redaction call sites."""

        return self.api.api_key

    @property
    def model(self) -> str:
        return (
            self.api.model
            if normalize_judge_backend(self.backend) == "api"
            else self.subagent.model
        )

    @property
    def base_url(self) -> str:
        return self.api.base_url


def normalize_judge_backend(value: str) -> str:
    """Return a supported judge backend name."""

    backend = str(value or "").strip().lower()
    return backend if backend in {"api", "subagent"} else "api"


@dataclass
class CasePreference:
    """Structured user intent for one diagnose run.

    The raw CLI message is intentionally preserved as the primary relevance
    contract for judge backends, but secrets are never stored here by the CLI.
    """

    raw_message: str = ""
    # LLM-normalized semantic intent reused by session judges and plan.
    intent_text: str = ""
    intent_confidence: float = 0.0
    # Backward-compatible intent metadata. It no longer disables quota-based
    # early stopping: diagnose stops as soon as the requested case mix is met.
    requires_broad_recall: bool = False
    # exploratory discovers major problems; hypothesis validates a user-supplied claim.
    diagnosis_mode: str = "exploratory"
    hypothesis_text: str = ""
    case_limit: int = 5
    diagnosis_limit: int = 20
    include_good: bool = True
    good_min_ratio: float = 0.25
    good_max_ratio: float = 0.40
    bad_case_count: int | None = None
    good_case_count: int | None = None
    dataset_profile: str = "default"
    since: str = ""
    until: str = ""
    time_range_label: str = ""
    focus_terms: list[str] = field(default_factory=list)
    # Legacy parsed hints kept for reports and backward-compatible artifacts.
    # Relevance is now judged from raw_message by the selected judge backend.
    required_terms: list[str] = field(default_factory=list)
    excluded_terms: list[str] = field(default_factory=list)
    target_failure_modes: list[str] = field(default_factory=list)
    scoring_requirements: str = ""
    timeout_seconds: int = DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS
    drop_context_dependent: bool = True
    rewrite_multi_sentence_query: bool = True
    require_diversity: bool = True
    search_evidence_required_for_high_score: bool = True
    stable_run_required: bool = True
    max_sessions: int | None = None

    def normalized_modes(self) -> list[str]:
        modes = self.target_failure_modes
        seen: set[str] = set()
        out: list[str] = []
        for mode in modes:
            value = str(mode or "").strip()
            if value and value not in seen:
                out.append(value)
                seen.add(value)
        return out


@dataclass
class SessionRow:
    session_id: str
    path: str
    bot_id: str
    created_at: str
    first_question: str
    user_text: str
    assistant_text: str
    tool_text: str
    raw_text: str
    original_model: str = ""
    original_model_source: str = ""
    raw_session: dict[str, Any] = field(default_factory=dict)


@dataclass
class Diagnosis:
    session: SessionRow
    case_type: str
    symptom_class: str
    root_cause_class: str
    common_problem_key: str
    evolution_failure_mode: str
    query: str
    root_cause_summary: str
    original_query: str = ""
    evidence: list[dict[str, str]] = field(default_factory=list)
    requires_search: bool = False
    confidence: float = 0.5
    quality_score: float = 0.5
    quality_notes: list[str] = field(default_factory=list)
    tool_hints: list[str] = field(default_factory=list)
    evidence_file_hints: list[dict[str, str]] = field(default_factory=list)
    failure_controllability: str = ""
    optimization_value: str = ""
    selection_bucket: str = ""
    selection_reason: str = ""
    backfilled: bool = False
    # Structured LLM result explaining relevance without hard-coded filters.
    intent_match: dict[str, Any] = field(default_factory=dict)
    # Original OCSA task facts. Diagnose may enrich selection metadata but must
    # not translate or overwrite OCSA's labels.
    ocsa: dict[str, Any] = field(default_factory=dict)
    eligibility: dict[str, Any] = field(default_factory=dict)
    query_fidelity: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunRequest:
    api_key: str
    message: str
    output_dir: Path
    task_id: str
    step_id: str = ""
    llm_base_url: str = ""
    model: str = ""
    max_sessions: int | None = None
    debug_session_path: str = ""
    openclaw_home: str = ""
    judge_backend: str = ""
    session_source: str = "local"
    source_user_id: str = ""
    source_bot_id: str = ""
    source_download_network: str = "office"
    clawweb_url: str = ""


@dataclass
class RunResult:
    summary_path: Path
    summary: dict[str, Any]
