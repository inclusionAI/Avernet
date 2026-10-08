"""Stable OpenClaw agent identity helpers for ClawEvolve-owned Bench runs."""

from __future__ import annotations

import hashlib
import os
import re


MAX_OPENCLAW_AGENT_ID_CHARS = 64


def evolve_task_marker(task_id: str | None = None) -> str:
    """Return a bounded marker, or empty for a non-ClawEvolve Bench run."""
    raw = str(task_id if task_id is not None else os.environ.get("CLAWEVOLVE_TASK_ID", "")).strip()
    if not raw:
        return ""
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw.lower()).strip("-_")
    if not slug:
        return ""
    if len(slug) <= 32:
        return slug
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{slug[:21].rstrip('-_')}-{digest}"


def task_scoped_agent_id(base: str, suffix: str = "", task_id: str | None = None) -> str:
    """Add the Evolve marker while preserving the legacy ID when it is absent."""
    marker = evolve_task_marker(task_id)
    parts = [str(base).strip("-_")]
    if marker:
        parts.append(marker)
    if suffix:
        parts.append(str(suffix).strip("-_"))
    value = "-".join(part for part in parts if part)
    if len(value) <= MAX_OPENCLAW_AGENT_ID_CHARS:
        return value

    # Keep the task marker and invocation suffix visible. Only compact the
    # pre-existing base, which may contain an unusually long model slug.
    tail = "-".join(part for part in (marker, str(suffix).strip("-_")) if part)
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    available = MAX_OPENCLAW_AGENT_ID_CHARS - len(tail) - len(digest) - 2
    compact_base = parts[0][:max(1, available)].rstrip("-_") or "bench"
    return f"{compact_base}-{digest}-{tail}"[:MAX_OPENCLAW_AGENT_ID_CHARS]
