"""Load the editable text template used by the OpenClaw agent judge."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

INPUT_SCHEMA_VERSION = "clawevolve-diagnose-native-session-input.v1"
OUTPUT_SCHEMA_VERSION = "clawevolve-diagnose-native-session-output.v1"
LEGACY_INPUT_SCHEMA_VERSION = "clawevolve-diagnose-subagent-input.v1"
LEGACY_OUTPUT_SCHEMA_VERSION = "clawevolve-diagnose-subagent-output.v1"


@lru_cache(maxsize=1)
def _prompt_template() -> str:
    return (
        files("clawevolve_diagnose.prompts")
        .joinpath("prompt_agent_session_judge.txt")
        .read_text(encoding="utf-8")
    )


def build_session_analysis_prompt(payload: dict[str, Any]) -> str:
    return (
        _prompt_template()
        .replace("{{OUTPUT_SCHEMA_VERSION}}", OUTPUT_SCHEMA_VERSION)
        .replace("{{INPUT_JSON}}", json.dumps(payload, ensure_ascii=False, indent=2))
    )


def session_analysis_prompt_prefix() -> str:
    for line in _prompt_template().splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""

__all__ = [
    "INPUT_SCHEMA_VERSION",
    "LEGACY_INPUT_SCHEMA_VERSION",
    "LEGACY_OUTPUT_SCHEMA_VERSION",
    "OUTPUT_SCHEMA_VERSION",
    "build_session_analysis_prompt",
    "session_analysis_prompt_prefix",
]
