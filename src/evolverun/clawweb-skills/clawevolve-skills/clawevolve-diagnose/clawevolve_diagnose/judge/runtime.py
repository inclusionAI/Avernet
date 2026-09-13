from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from typing import Any

from ..constants import DEFAULT_BASE_URL, DEFAULT_MODEL
from ..models import JudgeRuntimeConfig, LlmRuntimeConfig, RunRequest, SubagentJudgeConfig
from .ocsa_contract import OCSA_ANALYSIS_STYLE

DEFAULT_SUBAGENT_JUDGE_ID = "clawevolve-diagnose-session-judge"
DEFAULT_SUBAGENT_WORKSPACE = "/tmp/clawevolve-diagnose/session-judge/workspace"


def resolve_judge_runtime(req: RunRequest) -> JudgeRuntimeConfig:
    """Resolve an explicit backend, then preserve legacy API-key selection."""

    requested = str(req.judge_backend or "").strip().lower()
    if requested and requested not in {"api", "subagent"}:
        raise ValueError("judge_backend must be api or subagent")
    backend = requested or ("api" if str(req.api_key or "").strip() else "subagent")
    if backend == "api" and not str(req.api_key or "").strip():
        raise ValueError(
            "API judge requires --api-key or OPENAI_API_KEY; "
            "use --judge-backend subagent to use the Bot's OpenClaw Agent."
        )
    if backend == "subagent":
        agent_id = _task_agent_id(req.task_id)
        openclaw_home = str(Path(req.openclaw_home or "~/.openclaw").expanduser())
        return JudgeRuntimeConfig(
            backend="subagent",
            subagent=SubagentJudgeConfig(
                agent_id=agent_id,
                model=req.model or DEFAULT_MODEL,
                workspace=f"/tmp/clawevolve-diagnose/{agent_id}/workspace",
                openclaw_home=openclaw_home,
                openclaw_path=os.environ.get("OPENCLAW_PATH", "openclaw"),
                transport="local",
                fallback_to_cli=True,
            ),
        )
    return JudgeRuntimeConfig(
        backend="api",
        api=LlmRuntimeConfig(
            api_key=req.api_key,
            base_url=req.llm_base_url or DEFAULT_BASE_URL,
            model=req.model or DEFAULT_MODEL,
        ),
    )


def judge_runtime_summary(runtime: JudgeRuntimeConfig) -> dict[str, Any]:
    """Return a key-safe summary of effective judge runtime settings."""

    summary = runtime.safe_summary()
    summary["selection_policy"] = "explicit_backend_else_api_key_else_subagent"
    summary["analysis_style"] = (
        OCSA_ANALYSIS_STYLE
        if runtime.backend == "api"
        else "diagnose_native_single_session_llm"
    )
    summary["api_key_configured"] = bool(runtime.api.api_key)
    summary["api_key_persisted"] = False
    return summary


def subagent_workspace_path(config: SubagentJudgeConfig) -> Path:
    """Resolve the configured subagent workspace path."""

    return Path(config.workspace or DEFAULT_SUBAGENT_WORKSPACE)


def _task_agent_id(task_id: str) -> str:
    raw = str(task_id or "diagnose").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:24] or "task"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"clawevolve-diagnose-{slug}-{digest}"
