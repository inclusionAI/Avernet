"""The optional session analyzer at Diagnose's existing native judge seam."""
from __future__ import annotations

import sys
from dataclasses import MISSING, asdict, fields
from pathlib import Path
from typing import Any, get_origin, get_type_hints

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "platform"))
from clawevolve_runtime.core import BusinessCore, CoreWaiting
from clawevolve_runtime.executor import begin_stage_core

from .models import Diagnosis


def select_business_core(args: Any) -> BusinessCore | None:
    if getattr(args, "skip_clawweb_report", False) or not getattr(args, "clawweb_url", ""):
        return None
    selection = begin_stage_core(task_id=args.task_id, step_id=args.step_id, clawweb_url=args.clawweb_url)
    return BusinessCore(selection, model=str(getattr(args, "model", "") or "")) if selection["selected"] else None


def diagnosis_contract() -> dict[str, Any]:
    types = get_type_hints(Diagnosis)
    names = {str: "string", bool: "boolean", float: "number", int: "integer", list: "array", dict: "object"}
    members = [field for field in fields(Diagnosis) if field.name != "session"]
    return {"type": "object", "required": ["diagnoses"], "additionalProperties": False,
        "properties": {"diagnoses": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": [field.name for field in members if field.default is MISSING and field.default_factory is MISSING],
            "properties": {field.name: {"type": names[get_origin(types[field.name]) or types[field.name]]}
                           for field in members},
        }}}}


def _diagnoses(value: dict[str, Any], row: Any) -> list[Diagnosis]:
    contract = diagnosis_contract()["properties"]["diagnoses"]["items"]
    if set(value) != {"diagnoses"} or not isinstance(value["diagnoses"], list):
        raise ValueError("session analysis must return diagnoses")
    expected_types = {"string": str, "boolean": bool, "integer": int, "number": (int, float), "array": list, "object": dict}
    result = []
    for item in value["diagnoses"]:
        if (not isinstance(item, dict) or not set(contract["required"]).issubset(item)
                or not set(item).issubset(contract["properties"])):
            raise ValueError("diagnosis fields do not match the native analyzer result")
        for key, entry in item.items():
            kind = contract["properties"][key]["type"]
            if not isinstance(entry, expected_types[kind]) or (kind in {"number", "integer"} and isinstance(entry, bool)):
                raise ValueError(f"diagnosis.{key} must be {kind}")
        if item["case_type"] not in {"good", "bad"}:
            raise ValueError("diagnosis.case_type must be good or bad")
        # Source evidence is supplied by the native acquisition path. Business
        # output cannot replace a SessionRow or invent a different session.
        result.append(Diagnosis(session=row, **item))
    return result


class BusinessSessionAnalyzer:
    def __init__(self, core: BusinessCore, preference: Any):
        self.core = core
        self.preference = preference

    def analyze(self, rows, bot_id=""):
        diagnoses = []
        for row in rows:
            output = self.core("session_analysis", {"session": asdict(row), "requirements": asdict(self.preference)},
                               diagnosis_contract(), key=row.session_id)
            diagnoses.extend(_diagnoses(output, row))
        return diagnoses

    def close(self):
        pass
