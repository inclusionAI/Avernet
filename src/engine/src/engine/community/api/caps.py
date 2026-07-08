"""Capability-gate helper for HTTP handlers (M4, doc §17.2).

Web routes call `check_capability(cap)` at the top of each handler. The helper
consults the active engine's declared `EngineCapabilities` and:

- returns `None` for fully-supported capabilities (no warning, dispatch as usual);
- returns a short warning string for `limited` capabilities — the handler is
  expected to surface it in the response body (`message` / `warning` field);
- raises `HTTPException(501)` for unsupported capabilities, with the engine's
  `fallback` message as detail when one is declared, or a generic "not
  supported by engine X" otherwise.

Kept as a plain function (not a FastAPI dependency) so limited-capability
handlers can inject the warning into their response shape directly.
"""
from __future__ import annotations

from fastapi import HTTPException

from engine.community.core.engine.capability import Capability
from engine.community.manager import EngineManager


def check_capability(cap: Capability) -> str | None:
    """Raise 501 if `cap` is unsupported; return warning string if limited.

    Callers must handle a non-None return by relaying it to the caller (e.g.
    attaching to a `warning` or `message` response field).
    """
    manager = EngineManager.get_instance()
    caps = manager.get_capabilities()

    if cap in caps.limited:
        return caps.limited[cap]
    if cap in caps.supported:
        return None

    # Unsupported — prefer a declared fallback message if present.
    if cap in caps.fallback:
        detail = caps.fallback[cap]
    else:
        detail = (
            f"Capability {cap.value!r} is not supported by engine "
            f"{manager.engine!r}"
        )
    raise HTTPException(status_code=501, detail=detail)


__all__ = ["check_capability"]
