"""Native Plan's optional business implementation. Rendering stays in Plan."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "platform"))
from clawevolve_runtime.core import BusinessCore, CoreWaiting
from clawevolve_runtime.executor import begin_stage_core

if TYPE_CHECKING:
    from .discovery.agent import DiscoveryAgentRunResult


def select_business_core(args: Any) -> BusinessCore | None:
    if getattr(args, "skip_clawweb_report", False) or not getattr(args, "clawweb_url", ""):
        return None
    selection = begin_stage_core(task_id=args.task_id, step_id=args.step_id, clawweb_url=args.clawweb_url)
    if not selection["selected"]:
        return None
    if selection.get("executionContract") != "clawevolve.plan-business/v1":
        raise ValueError("Plan replacement must use the native business Contract; regenerate its development package")
    core = BusinessCore(selection, model=str(getattr(args, "model", "") or ""))
    return core


def run_business_call(
    core: BusinessCore, phase: str, data: dict[str, Any], requirements: Any,
    *, output_path: Path | None = None, key: str = "",
) -> DiscoveryAgentRunResult:
    from .discovery.agent import DiscoveryAgentRunResult
    started = time.monotonic()
    value = core(phase, data, requirements, key=key)
    text = json.dumps(value, ensure_ascii=False)
    if output_path is not None:
        from .io import atomic_write_json
        atomic_write_json(output_path, value)
    # Preserve the native validation/postprocessing path's return interface.
    # This is a completed file IO receipt, not a fabricated model transcript.
    return DiscoveryAgentRunResult(status="succeeded", agent_id="", session_id="",
        response_text=text, elapsed_seconds=time.monotonic() - started, transport="business_skill")
