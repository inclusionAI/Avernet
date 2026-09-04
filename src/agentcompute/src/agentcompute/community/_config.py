"""Load a ``.env`` file into ``os.environ`` (stdlib-only).

Keys in the environment already take precedence over the file, so the file
acts as defaults that can be overridden inline. Values support optional
quotes and inline ``#`` comments.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["load_dotenv", "read_dotenv"]


def load_dotenv(path: str | Path = ".env", override: bool = False) -> dict[str, str]:
    values = read_dotenv(path)
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def read_dotenv(path: str | Path = ".env") -> dict[str, str]:
    dotenv = Path(path)
    if not dotenv.exists():
        return {}
    values: dict[str, str] = {}
    for raw in dotenv.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _unquote(value.strip())
    return values


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    if " #" in value:
        return value.split(" #", 1)[0].rstrip()
    return value
