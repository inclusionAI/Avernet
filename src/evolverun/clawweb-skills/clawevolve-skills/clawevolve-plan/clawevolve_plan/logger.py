from __future__ import annotations

import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

_LOCK = threading.Lock()
_LOG_FILE: Path | None = None
_SECRETS: list[str] = []

_FALSE_VALUES = {"0", "false", "no", "off"}
_LEVEL_ORDER = {"debug": 10, "info": 20, "warning": 30, "error": 40}


def configure(log_file: str | Path | None = None, secrets: Iterable[str] | None = None) -> None:
    """Configure clawevolve-plan progress logging.

    Logs are emitted to stderr and, when a path is available, to a run-local
    log file. Logging is best-effort and must never break the skill command.
    """

    global _LOG_FILE, _SECRETS
    chosen = Path(log_file).expanduser() if log_file else None
    with _LOCK:
        _LOG_FILE = chosen
        if secrets is not None:
            _SECRETS = [str(s) for s in secrets if str(s or "").strip()]
        if _LOG_FILE:
            try:
                _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                _LOG_FILE = None


def log_path() -> str:
    return str(_LOG_FILE) if _LOG_FILE else ""


def debug(message: str, **fields: object) -> None:
    log("debug", message, **fields)


def info(message: str, **fields: object) -> None:
    log("info", message, **fields)


def warning(message: str, **fields: object) -> None:
    log("warning", message, **fields)


def error(message: str, **fields: object) -> None:
    log("error", message, **fields)


def log(level: str, message: str, **fields: object) -> None:
    if not _enabled():
        return
    normalized = level.lower().strip() or "info"
    if _LEVEL_ORDER.get(normalized, 20) < _configured_level():
        return
    line = _format_line(normalized, message, fields)
    with _LOCK:
        print(line, file=sys.stderr, flush=True)
        if _LOG_FILE:
            try:
                with _LOG_FILE.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass


def _enabled() -> bool:
    return True


def _configured_level() -> int:
    return _LEVEL_ORDER["info"]


def _format_line(level: str, message: str, fields: dict[str, object]) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    suffix = ""
    if fields:
        parts = [f"{key}={_safe_value(value)}" for key, value in sorted(fields.items())]
        suffix = " " + " ".join(parts)
    return _redact(f"{ts} [clawevolve-plan] {level.upper()} {message}{suffix}")


def _safe_value(value: object) -> str:
    text = str(value)
    text = " ".join(text.split())
    return text[:500]


def _redact(text: str) -> str:
    out = text
    for secret in _SECRETS:
        if secret:
            out = out.replace(secret, "***REDACTED***")
    return out
