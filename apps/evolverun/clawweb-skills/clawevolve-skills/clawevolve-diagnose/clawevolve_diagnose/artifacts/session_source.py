"""Read explicit source identity only; never infer it from conversation text."""
from __future__ import annotations

import json
from pathlib import Path

from ..models import SessionRow


def source_session_key(session: SessionRow) -> str:
    if session.session_key:
        return session.session_key
    keys: set[str] = set()

    def collect(value: object) -> None:
        if not isinstance(value, dict):
            return
        message = value.get("message")
        history = message.get("historyMeta") if isinstance(message, dict) else None
        for metadata in (value, history):
            if isinstance(metadata, dict):
                for field in ("sessionKey", "session_key"):
                    key = metadata.get(field)
                    if isinstance(key, str) and key.strip():
                        keys.add(key.strip())

    collect(session.raw_session)
    try:
        with Path(session.path).open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    collect(json.loads(line))
                except ValueError:
                    continue
    except (OSError, UnicodeError):
        pass
    return next(iter(keys)) if len(keys) == 1 else ""
