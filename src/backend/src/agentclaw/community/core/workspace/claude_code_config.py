"""Persisted default cwd for newly created Claude Code Bots; no legacy inference."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any, Mapping

CLAUDE_CODE_DEFAULT_CWD_KEY = "claude_code_default_cwd"
DEFAULT_CLAUDE_CODE_CWD = "/home/admin/.claude_code/workspace"


def validate_claude_code_cwd(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Claude Code default cwd must be a non-empty absolute path")
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or path == PurePosixPath("/")
        or ".." in path.parts
        or any(c in value for c in ("\n", "\r", "\x00", "$", "`", "{", "}"))
    ):
        raise ValueError(
            "Claude Code default cwd must be a literal absolute non-root path"
        )
    return str(path)


def new_claude_code_ext(ext: Mapping[str, Any] | None) -> dict[str, Any]:
    result = dict(ext or {})
    result[CLAUDE_CODE_DEFAULT_CWD_KEY] = validate_claude_code_cwd(
        result.get(CLAUDE_CODE_DEFAULT_CWD_KEY, DEFAULT_CLAUDE_CODE_CWD)
    )
    return result


def claude_code_cwd_from_ext(ext: object) -> str | None:
    """No value means legacy/unknown. Never manufacture a new default on read."""
    if ext is None or ext == "":
        return None
    if isinstance(ext, str):
        ext = json.loads(ext)
    if not isinstance(ext, dict):
        raise ValueError("Bot extension metadata must be an object")
    if CLAUDE_CODE_DEFAULT_CWD_KEY not in ext:
        return None
    return validate_claude_code_cwd(ext[CLAUDE_CODE_DEFAULT_CWD_KEY])
