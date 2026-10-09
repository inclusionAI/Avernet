"""Business-call IO for native Handlers; no Stage orchestration or business rules."""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
from typing import Any

from . import executor, runtime


class CoreWaiting(Exception):
    """A business call returned a question; the native Handler reports and exits."""

    def __init__(self, question: dict[str, Any], request_id: str, *, reported: bool = False):
        super().__init__("waiting for business input")
        self.output = {"hitl": True, "question": question}
        self.progress = {"business_resume": {
            "request_id": request_id,
            "question_sha256": _digest(question),
        }}
        self.reported = reported


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class BusinessCore:
    """One frozen implementation, called with the native core's actual IO.

    Completed calls are reused on same-Step HITL resume. Model calls with an
    unknown outcome are never silently repeated. A new Loop has a new Step
    directory and therefore executes the business flow again.
    """

    def __init__(self, context: dict[str, Any], *, model: str = ""):
        self.context = context
        self.model = model
        self.base_input = runtime._read_json(Path(context["inputFile"]), strict=True)
        self.root = Path(context["resultFile"]).parent / "core-calls"
        self.root.mkdir(parents=True, exist_ok=True)
        self.loop_request: dict[str, Any] | None = None

    def __call__(self, phase: str, data: dict[str, Any], requirements: Any, *, key: str = "") -> dict[str, Any]:
        with (self.root / "execution.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return self._call(phase, data, requirements, key=key)

    def _call(self, phase: str, data: dict[str, Any], requirements: Any, *, key: str) -> dict[str, Any]:
        identity = {"phase": phase, "key": key, "input": data, "output_requirements": requirements}
        request_id = _digest({"phase": phase, "key": key})
        directory = self.root / request_id
        directory.mkdir(exist_ok=True)
        request_file = directory / "request.json"
        if request_file.exists():
            frozen = runtime._read_json(request_file, strict=True)
            if frozen.get("output_requirements") != requirements:
                raise runtime.RuntimeFailure("the native business Contract changed during a frozen Step")
            identity = frozen
        else:
            runtime._atomic_json(request_file, identity)
        state_file = directory / "state.json"
        state = runtime._read_json(state_file, strict=True) if state_file.exists() else {}
        if state.get("status") == "completed":
            self._remember_loop(state["output"])
            return state["output"]["result"]
        if state.get("status") in {"running", "failed", "reporting"}:
            raise runtime.RuntimeFailure(f"business call {phase} is incomplete or has an uncertain outcome")
        human = self.base_input.get("human_input") or {}
        history = [item for item in human.get("history", [])
                   if isinstance(item, dict) and isinstance(item.get("question"), dict)
                   and (item["question"].get("business_resume") or {}).get("request_id") == request_id]
        if state.get("status") == "waiting":
            waiting = state["question"]
            latest = history[-1] if history else None
            if (len(history) <= state.get("answer_count", 0) or not latest
                    or (latest["question"].get("business_resume") or {}).get("question_sha256") != _digest(waiting)):
                raise CoreWaiting(waiting, request_id, reported=bool(state.get("reported")))
        delivered = {name: value for name, value in identity.items() if name != "key"}
        # Platform resources and Loop context are outer input fields. They do
        # not change the native business call's data or output requirements.
        for name in ("target_skill", "loop"):
            if name in self.base_input:
                delivered[name] = self.base_input[name]
        if history:
            clean = [{"question": {k: v for k, v in item["question"].items() if k != "business_resume"},
                      "answer": item["answer"]} for item in history]
            delivered["hitl"] = {**clean[-1], "history": clean}
        input_file, result_file = directory / "input.json", directory / "result.json"
        runtime._atomic_json(input_file, delivered)
        result_file.unlink(missing_ok=True)
        runtime._atomic_json(state_file, {"status": "running"})
        try:
            raw = executor.execute_stage_skill({**self.context, "inputFile": str(input_file),
                "resultFile": str(result_file), "outputContract": requirements}, model=self.model)
            output = runtime._normalize_result(raw)
        except Exception:
            runtime._atomic_json(state_file, {"status": "failed"})
            raise
        if output["hitl"]:
            runtime._atomic_json(state_file, {"status": "waiting", "question": output["question"],
                                             "reported": False, "answer_count": len(history)})
            raise CoreWaiting(output["question"], request_id)
        self._remember_loop(output)
        runtime._atomic_json(state_file, {"status": "completed", "output": output})
        return output["result"]

    def _remember_loop(self, output: dict[str, Any]) -> None:
        requested = output.get("loop")
        if requested:
            if self.loop_request is not None and self.loop_request != requested:
                raise runtime.RuntimeFailure("business calls returned conflicting Stage Loop requests")
            self.loop_request = requested

    def is_waiting(self, phase: str, *, key: str = "") -> bool:
        state_file = self.root / _digest({"phase": phase, "key": key}) / "state.json"
        return state_file.is_file() and runtime._read_json(state_file, strict=True).get("status") == "waiting"

    def report_waiting(self, args: Any, waiting: CoreWaiting) -> dict[str, Any]:
        if waiting.reported:
            return {"ok": True, "status": "waiting_context"}
        state_file = self.root / waiting.progress["business_resume"]["request_id"] / "state.json"
        state = runtime._read_json(state_file, strict=True)
        runtime._atomic_json(state_file, {**state, "status": "reporting"})
        result = runtime._report(args, "succeeded", "等待用户补充信息", output=waiting.output, progress=waiting.progress)
        runtime._atomic_json(state_file, {**state, "status": "waiting", "reported": True})
        return result

    def final_output(self, value: dict[str, Any]) -> dict[str, Any]:
        # Native Handlers may reuse their already generated artifacts after
        # HITL. Retain Loop requests from those completed business calls too.
        for state_file in sorted(self.root.glob("*/state.json")):
            state = runtime._read_json(state_file, strict=True)
            if state.get("status") == "completed":
                self._remember_loop(state["output"])
        return {"hitl": False, "result": value, **({"loop": self.loop_request} if self.loop_request else {})}
