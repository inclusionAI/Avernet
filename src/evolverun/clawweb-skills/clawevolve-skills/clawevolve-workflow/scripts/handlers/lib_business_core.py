"""Optional business call at Optimize's existing Tune/Review boundary."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "platform"))
from clawevolve_runtime.core import BusinessCore, CoreWaiting
from clawevolve_runtime.executor import begin_stage_core
from clawevolve_runtime.runtime import _runtime_path as resolve_runtime_path


def select_business_core(args: Any) -> BusinessCore | None:
    if hasattr(args, "_business_core"):
        return args._business_core
    args._business_core = None
    base = (getattr(args, "clawweb_url", "") or getattr(args, "clawweb_url_camel", "")
            or os.environ.get("CLAWEVOLVE_CLAWWEB_URL") or os.environ.get("CLAWWEB_URL"))
    if getattr(args, "skip_clawweb", False) or not base or not getattr(args, "step_id", ""):
        return None
    context = begin_stage_core(task_id=args.task_id, step_id=args.step_id, clawweb_url=base)
    if context["selected"]:
        args._business_core = BusinessCore(context)
    return args._business_core


def is_waiting(args: Any, step: str) -> bool:
    phase = {"ensure-tune": "tune", "ensure-review": "review"}.get(step)
    core = getattr(args, "_business_core", None)
    return bool(phase and core and core.is_waiting(phase, key=str(args.round)))


def run_business_call(core: BusinessCore, phase: str, data: dict[str, Any],
                      outputs: list[Path], *, round_id: int, model: str) -> dict[str, Any]:
    """Collect the same files the native model call produces, at native paths.

    No Bench decisions, rendering, packaging, or lifecycle reporting occurs
    here. The original Handler validates and consumes the returned artifacts.
    """
    expected = {path.name: str(path.resolve()) for path in outputs}
    requirements = {"type": "object", "required": ["artifacts"], "additionalProperties": False,
        "properties": {"artifacts": {"type": "object", "required": list(expected),
            "additionalProperties": False, "properties": {
                name: {"type": "string", "const": path} for name, path in expected.items()}}}}
    core.model = model
    started = time.monotonic()
    result = core(phase, {**data, "output_files": expected}, requirements, key=str(round_id))
    if set(result) != {"artifacts"} or result["artifacts"] != expected:
        raise ValueError(f"{phase} must return exactly the native output file references")
    for name, location in expected.items():
        path = Path(location)
        if path.is_symlink() or not path.is_file() or path.resolve() != path:
            raise ValueError(f"{phase} native output is missing or symlinked: {name}")
    # A file receipt, not a fabricated model transcript or completion marker.
    return {"status": "succeeded", "elapsed": time.monotonic() - started,
            "transport": "business_skill", "stdout": "", "stderr": ""}
