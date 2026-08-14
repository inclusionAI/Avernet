"""Path translation helpers for the Skills Pool cutover window."""

from __future__ import annotations

from pathlib import Path
from typing import Callable


def canonical_pool_local_path(path_value: str, pool_local: Path) -> str:
    """Translate a Legacy/local locator to the canonical Pool local root."""

    path = Path(path_value.rstrip("/"))
    parts = path.parts
    if "skills-local" in parts:
        index = len(parts) - 1 - tuple(reversed(parts)).index("skills-local")
        relative_parts = parts[index + 1 :]
    elif path.is_absolute():
        relative_parts = (path.name,)
    else:
        relative_parts = parts
    if any(part in {"", ".", ".."} for part in relative_parts):
        raise ValueError(f"invalid local skill path: {path_value}")
    return str(pool_local.joinpath(*relative_parts))


def build_pool_local_path_adapter(pool_local: Path) -> Callable[[str], str]:
    """Build the adapter used by SkillService during/after Pool cutover."""

    return lambda path_value: canonical_pool_local_path(path_value, pool_local)
