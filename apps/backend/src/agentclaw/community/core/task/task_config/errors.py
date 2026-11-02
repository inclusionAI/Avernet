"""Domain errors for ``task_config`` (spec 2026-11-02).

``TaskConfigValueError`` is a ``ValueError`` subclass so the service's own unit
tests that assert ``pytest.raises(ValueError)`` keep passing, AND it is a leaf
registered in ``ENVELOPE_ERRORS`` (``adapters/http/openapi_v1/responses.py``) so
a form/type mismatch raised at the service layer surfaces through
``@envelope_errors`` as a mapped 400 (per spec §value-encode), not as an
unmapped 500. Mirrors ``McpConfigValueError`` (MCP config, also mapped 400).
A bare ``ValueError`` would re-raise past ``@envelope_errors`` to the app's
generic 500 handler — wrong status and trips 5xx dashboards on a client error.
"""

from __future__ import annotations


class TaskConfigValueError(ValueError):
    """A ``config_value`` / match-``value`` shape does not match the declared or
    stored ``config_type`` (single_string/bool/set), or ``config_type`` is not a
    governed enum value."""
