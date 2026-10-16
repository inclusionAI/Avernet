from __future__ import annotations

import json
import re
from typing import Any

from agentclaw.community.core.agentswarm.domain.models import (
    DecisionKind,
    DecisionParse,
    FinishDecision,
    HandoffDecision,
    SpawnDecision,
    TaskEnvelope,
    WaitDecision,
    WaitMode,
)

MAX_AGENTS = 64
MAX_FIELD_CHARS = 20_000
MAX_SCHEMA_CHARS = 20_000


class DecisionParser:

    def parse(self, raw: str) -> DecisionParse:
        text = self._strip_fence(raw)
        obj = self._extract_json(text)
        if obj is None:
            return DecisionParse(decision=None, error="no JSON object found")
        return self._validate(obj)

    @staticmethod
    def _strip_fence(raw: str) -> str:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return text.strip()

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        for start, ch in enumerate(text):
            if ch != "{":
                continue
            end = DecisionParser._balanced_end(text, start)
            if end == -1:
                continue
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _balanced_end(text: str, start: int) -> int:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        return -1

    def _validate(self, obj: dict[str, Any]) -> DecisionParse:
        kind_raw = obj.get("kind")
        if kind_raw is None:
            return DecisionParse(decision=None, error="missing 'kind'")
        try:
            kind = DecisionKind(kind_raw)
        except (ValueError, TypeError):
            return DecisionParse(decision=None, error=f"unknown kind: {kind_raw}")

        if kind == DecisionKind.FINISH:
            answer = obj.get("answer", "")
            error = self._field_too_long("answer", answer)
            if error:
                return DecisionParse(decision=None, error=error)
            return DecisionParse(decision=FinishDecision(answer=answer))

        if kind == DecisionKind.WAIT:
            refs_raw = obj.get("refs", [])
            if not isinstance(refs_raw, list) or not refs_raw:
                return DecisionParse(decision=None, error="wait requires non-empty refs")
            mode_raw = obj.get("mode", "all")
            try:
                mode = WaitMode(mode_raw)
            except (ValueError, TypeError):
                return DecisionParse(decision=None, error=f"invalid mode: {mode_raw}")
            refs = [str(r) for r in refs_raw]
            for ref in refs:
                error = self._field_too_long("refs entry", ref)
                if error:
                    return DecisionParse(decision=None, error=error)
            return DecisionParse(decision=WaitDecision(refs=tuple(refs), mode=mode))

        if kind == DecisionKind.HANDOFF:
            target = obj.get("target")
            if not target or not isinstance(target, str):
                return DecisionParse(decision=None, error="handoff requires target")
            reason = obj.get("reason", "")
            for field_name, value in (("target", target), ("reason", reason)):
                error = self._field_too_long(field_name, value)
                if error:
                    return DecisionParse(decision=None, error=error)
            return DecisionParse(decision=HandoffDecision(target=target, reason=reason))

        if kind == DecisionKind.SPAWN_AGENT:
            agents_raw = obj.get("agents", [])
            if not isinstance(agents_raw, list) or not agents_raw:
                return DecisionParse(decision=None, error="spawn requires non-empty agents")
            if len(agents_raw) > MAX_AGENTS:
                return DecisionParse(
                    decision=None,
                    error=f"spawn lists {len(agents_raw)} agents; max is {MAX_AGENTS}",
                )
            envelopes: list[TaskEnvelope] = []
            for a in agents_raw:
                if not isinstance(a, dict):
                    return DecisionParse(decision=None, error="spawn agent must be a dict")
                name = a.get("name")
                objective = a.get("objective")
                if not name or not isinstance(name, str) or not objective or not isinstance(objective, str):
                    return DecisionParse(decision=None, error="spawn requires name and objective")
                boundaries = a.get("boundaries") or ""
                parent_goal = a.get("parent_goal") or ""
                for field_name, value in (
                    ("name", name),
                    ("objective", objective),
                    ("boundaries", boundaries),
                    ("parent_goal", parent_goal),
                ):
                    error = self._field_too_long(field_name, value)
                    if error:
                        return DecisionParse(decision=None, error=error)
                # output_schema becomes a jsonschema validator key
                # downstream (cached by serialized form): only a JSON
                # object within the size cap may cross the boundary.
                schema = a.get("output_schema")
                error = self._schema_violation(schema)
                if error:
                    return DecisionParse(decision=None, error=error)
                envelopes.append(TaskEnvelope(
                    name=name,
                    objective=objective,
                    output_schema=schema or {},
                    boundaries=boundaries,
                    parent_goal=parent_goal,
                ))
            return DecisionParse(decision=SpawnDecision(agents=tuple(envelopes)))

        return DecisionParse(decision=None, error=f"unhandled kind: {kind}")

    @staticmethod
    def _field_too_long(field_name: str, value: Any) -> str | None:
        if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
            return f"{field_name} exceeds {MAX_FIELD_CHARS} chars"
        return None

    @staticmethod
    def _schema_violation(schema: Any) -> str | None:
        if schema is None:
            return None
        if not isinstance(schema, dict):
            return "spawn output_schema must be a JSON object"
        if len(json.dumps(schema)) > MAX_SCHEMA_CHARS:
            return f"output_schema exceeds {MAX_SCHEMA_CHARS} chars"
        return None