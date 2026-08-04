"""Domain errors raised by the MCP config/market flow.

Dependency-free, mirroring ``adapters/http/openapi_v1/errors.py``: the flow in
``config_flow.py`` and the helpers in ``presentation.py`` raise these so each API
surface can map them onto its own response shape — the internal ``/api/mcp``
router onto its historical ``HTTPException`` bodies, the public ``/openapi/v1``
router onto the envelope — without either surface importing the other's types.
"""

from __future__ import annotations


class McpError(Exception):
    """Base for MCP flow errors. Never mapped directly — map the leaves."""


class McpServerNotFoundError(McpError):
    """The named MCP server does not exist in the marketplace.

    Raised before any configuration is written, so a bad server code never
    reaches the database.
    """


class McpHeadersInvalidError(McpError):
    """The caller's headers failed validation.

    Carries the validator's own message for the internal surface, which has
    always echoed it. The public surface maps to a fixed message instead — the
    validator's text is internal-language and must not reach an external caller.
    """


class McpConfigValueError(McpError):
    """A configuration value (endpoint env / transport protocol) is not accepted."""


class McpSyncFailedError(McpError):
    """Pushing the configuration to the caller's devices failed.

    Raised only after the stored configuration has been rolled back to its prior
    state, so a caller never ends up with a configuration that is saved but not
    in effect.
    """


class McpMarketUnavailableError(McpError):
    """An upstream marketplace call reported failure.

    A dependency problem, not a caller mistake — distinct from a genuinely empty
    result, which is a success with no rows.
    """
