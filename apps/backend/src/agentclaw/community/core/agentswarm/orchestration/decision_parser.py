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
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        return None
        return None

    def _validate(self, obj: dict[str, Any]) -> DecisionParse:
        kind_raw = obj.get("kind")
        if kind_raw is None:
            return DecisionParse(decision=None, error="missing 'kind'")
        try:
            kind = DecisionKind(kind_raw)
        except ValueError:
            return DecisionParse(decision=None, error=f"unknown kind: {kind_raw}")

        if kind == DecisionKind.FINISH:
            answer = obj.get("answer", "")
            return DecisionParse(decision=FinishDecision(answer=answer))

        if kind == DecisionKind.WAIT:
            refs_raw = obj.get("refs", [])
            if not isinstance(refs_raw, list) or not refs_raw:
                return DecisionParse(decision=None, error="wait requires non-empty refs")
            mode_raw = obj.get("mode", "all")
            try:
                mode = WaitMode(mode_raw)
            except ValueError:
                return DecisionParse(decision=None, error=f"invalid mode: {mode_raw}")
            return DecisionParse(decision=WaitDecision(refs=tuple(str(r) for r in refs_raw), mode=mode))

        if kind == DecisionKind.HANDOFF:
            target = obj.get("target")
            if not target or not isinstance(target, str):
                return DecisionParse(decision=None, error="handoff requires target")
            return DecisionParse(decision=HandoffDecision(target=target, reason=obj.get("reason", "")))

        if kind == DecisionKind.SPAWN_AGENT:
            agents_raw = obj.get("agents", [])
            if not isinstance(agents_raw, list) or not agents_raw:
                return DecisionParse(decision=None, error="spawn requires non-empty agents")
            envelopes: list[TaskEnvelope] = []
            for a in agents_raw:
                if not isinstance(a, dict):
                    return DecisionParse(decision=None, error="spawn agent must be a dict")
                name = a.get("name")
                objective = a.get("objective")
                if not name or not isinstance(name, str) or not objective or not isinstance(objective, str):
                    return DecisionParse(decision=None, error="spawn requires name and objective")
                envelopes.append(TaskEnvelope(
                    name=name,
                    objective=objective,
                    output_schema=a.get("output_schema") or {},
                    boundaries=a.get("boundaries") or "",
                    parent_goal=a.get("parent_goal") or "",
                ))
            return DecisionParse(decision=SpawnDecision(agents=tuple(envelopes)))

        return DecisionParse(decision=None, error=f"unhandled kind: {kind}")