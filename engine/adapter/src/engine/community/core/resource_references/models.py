"""Neutral result passed from Chat reference validation to adapters."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ResolvedResourceContext:
    prompt: str
    resource_references: list[dict] = field(default_factory=list)
    materialized_files: list[dict] = field(default_factory=list)
    prompt_context: str = ""
