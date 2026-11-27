"""Error→(status, fixed message) rows for the service publish-progress surface.

Merged into ``responses.ENVELOPE_ERRORS`` — the single map sits at the
architecture line cap, so this group brings its rows in a side module (the
source-credentials/skill-center precedents).

The progress query relays BaaS's own answers by semantic class instead of
letting them escape as unmapped 500s: a workflow BaaS confirms missing is a
404, every other provider failure is a 502. Fixed messages, never raw
exception text.
"""
from __future__ import annotations

from agentclaw.community.core.service_bot.errors import (
    ServicePublishUpstreamError,
    ServicePublishWorkflowNotFoundError,
)

SERVICE_PUBLISH_ENVELOPE_ERRORS: dict[type[Exception], tuple[int, str]] = {
    ServicePublishWorkflowNotFoundError: (404, "Not found"),
    ServicePublishUpstreamError: (502, "Publish service error"),
}

__all__ = ["SERVICE_PUBLISH_ENVELOPE_ERRORS"]
