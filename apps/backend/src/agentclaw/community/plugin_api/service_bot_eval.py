"""Service Bot unified evaluation gate plugin contract.

The community distribution must not know SecurityAC, DRM, or corp tables.  It
only needs a transport-independent seam for the publication flow and the
Phase-1 evaluation HTTP surface to call.  Corp provides the real evaluator;
community/local profiles provide deterministic no-op implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, Sequence

from agentclaw.community.plugin_api.base import Plugin


@dataclass(frozen=True)
class ServiceBotEvalContext:
    """Server-derived identity of one service-Bot release candidate."""

    bot_id: str
    bot_version: str
    release_id: str
    publish_id: str
    operator_id: str
    latest_bot_modified_at: str


@dataclass(frozen=True)
class ServiceBotEvalFile:
    """One upload already read into memory by the HTTP adapter."""

    filename: str
    content: bytes
    content_type: str = "application/octet-stream"
    file_kind: Literal["sample", "report"] = "sample"


class ServiceBotEvalPlugin(Plugin, Protocol):
    """Evaluate and manage service-Bot release certifications."""

    async def precheck(
        self,
        context: ServiceBotEvalContext,
        *,
        trigger_source: str = "draft_to_validate",
    ) -> dict[str, Any]:
        """Return the final ``allowed`` decision for the release trigger."""
        ...

    async def detail(self, context: ServiceBotEvalContext) -> dict[str, Any]:
        """Return certification state and tab visibility for the evaluation page."""
        ...

    async def submit(
        self,
        context: ServiceBotEvalContext,
        files: Sequence[ServiceBotEvalFile],
        *,
        trigger_source: str,
    ) -> dict[str, Any]:
        """Submit evaluation data and create a pollable certification task."""
        ...

    async def get_submission(
        self,
        context: ServiceBotEvalContext,
        submission_id: str,
    ) -> dict[str, Any]:
        """Return the current status of one submission."""
        ...

    async def history(
        self,
        context: ServiceBotEvalContext,
        *,
        page_no: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        """Return completed evaluation records for this Bot version."""
        ...

    async def sync(self, context: ServiceBotEvalContext) -> dict[str, Any]:
        """Synchronize cached certification state and return the latest view."""
        ...
