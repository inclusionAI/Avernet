"""claude_code community engine (ACL-assembled, open-source).

Composition root for the open-source claude_code engine: assembles the core
``*Service`` adapters over the shared profile-neutral transport (vendored Node
relay at ``ws://127.0.0.1:18900``). Mirrors ``engines/openclaw/`` — the assembly
root lives here (an ``engines/`` composition root, free to import
``core/adapters`` + ``plugins``), while the pure transport leaf lives in
``plugins/claude_code/`` (shared by community and future corp roots).

corp's legacy ``engines/claude_code/`` (18900 relay in-tree impl) is a separate,
export-excluded package; the two register under the same ``"claude_code"`` name
and are mutually exclusive by profile (see ``engines/__init__.py``).
"""
from __future__ import annotations

import logging as _logging

from engine.community.core.engine.registry import DEFAULT_REGISTRY
from engine.community.engines.claude_code.engine import ClaudeCodeCommunityEngine


def _self_register() -> None:
    """Register :class:`ClaudeCodeCommunityEngine` on import (best-effort).

    Mirrors corp ``corp/engines/claude_code/__init__.py``: the corp and
    community claude_code engines register under the same ``"claude_code"`` name
    and are mutually exclusive by profile. Only the test suite imports both;
    wrapping in try/except lets whichever loads second tolerate the same-name
    conflict (``register`` is idempotent for an identical class) instead of
    raising at import time. Single-profile runtime is unaffected — exactly one
    engine loads and registers cleanly.
    """
    try:
        DEFAULT_REGISTRY.register(ClaudeCodeCommunityEngine)
    except Exception as exc:  # pragma: no cover - best-effort import-time guard
        _logging.getLogger("claude-code-community-engine").warning(
            "[self-register] ClaudeCodeCommunityEngine registration skipped: %s: %s",
            type(exc).__name__,
            exc,
        )


_self_register()

__all__ = ["ClaudeCodeCommunityEngine"]
