from __future__ import annotations

from pathlib import Path
import re
from typing import Any


def is_session_transcript(item: dict[str, Any], path: Path) -> bool:
    """Keep source transcripts, not trajectory attachments, in version selection."""
    kind = item.get("kind")
    if kind is not None:
        return kind == "raw"
    # Legacy manifests have no kind. Both live and deleted transcripts are valid.
    name = path.name.split(".jsonl.deleted.", 1)[0]
    if ".jsonl.deleted." in path.name:
        name += ".jsonl"
    return name.endswith(".jsonl") and not name.endswith(".trajectory.jsonl")


def _is_main_agent_source(item: dict[str, Any]) -> bool:
    aliases = [item.get("sourceAlias")]
    duplicates = item.get("duplicateAliases")
    if isinstance(duplicates, list):
        aliases.extend(duplicates)
    for alias in aliases:
        text = str(alias or "").replace("\\", "/").rstrip("/")
        if re.search(r"(?:^|/)agent/main$", text) or text.startswith("agent:main:"):
            return True
    return False


def _version_rank(item: dict[str, Any], path: Path) -> tuple[int, str]:
    deleted = bool(item.get("isDeletedArchive")) or ".jsonl.deleted." in path.name
    return (0 if deleted else 1, path.name)
