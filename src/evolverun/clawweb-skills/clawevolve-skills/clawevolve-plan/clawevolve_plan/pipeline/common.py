from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import logger
from ..io import atomic_write_json

def _write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_json(path, data)
    json_size = len(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")) + 1
    logger.info(
        "write json done",
        path=path,
        bytes=json_size,
        keys=list(data.keys()) if isinstance(data, dict) else "",
    )


def _read_text_file(path: Path) -> str:
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            logger.info("read step report text content done", path=path, bytes=len(text.encode("utf-8")), preview=_text_preview(text))
            return text
    except OSError as exc:
        logger.warning("read step report text content failed", path=path, error=f"{type(exc).__name__}: {exc}")
    return ""


def _text_preview(text: str, limit: int = 500) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) > limit:
        return compact[:limit] + f"...<truncated {len(compact) - limit} chars>"
    return compact
