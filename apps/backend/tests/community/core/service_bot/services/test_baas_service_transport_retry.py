"""Transient-transport handling for ``BaasService`` read calls.

Covers the fix for the agentclaw production report
``[PublishFlowService.get_baas_publish_progress] Failed: Error in httpx send
hook``: ``ant_baselib``'s httpx tracing hook re-raises a generic message and
keeps the real timeout/disconnect only in the exception chain.
"""
from __future__ import annotations

import httpx
import pytest

from agentclaw.community.core.service_bot.services.baas_service import (
    _error_chain,
)


def test_error_chain_surfaces_hook_wrapped_cause():
    """The hook's generic message alone hides the real transport failure."""
    underlying = httpx.ReadTimeout("The read operation timed out")
    try:
        raise underlying
    except httpx.ReadTimeout as cause:
        wrapped = RuntimeError("Error in httpx send hook")
        wrapped.__cause__ = cause
        rendered = _error_chain(wrapped)

    assert "Error in httpx send hook" in rendered
    assert "The read operation timed out" in rendered


def test_error_chain_is_cycle_safe():
    """A self-referential ``__context__`` must not loop forever."""
    exc = RuntimeError("boom")
    exc.__context__ = exc

    assert _error_chain(exc) == "boom"