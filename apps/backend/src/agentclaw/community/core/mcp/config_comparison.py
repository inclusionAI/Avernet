"""Value equivalence for idempotent MCP Apply claims."""

from typing import Any


def comparable_config(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """An empty override is absent; HTTP Header names are case-insensitive."""
    if not config:
        return None
    comparable = dict(config)
    headers = comparable.get("headers")
    if isinstance(headers, dict):
        comparable["headers"] = {
            str(name).lower(): value for name, value in headers.items()
        }
    return comparable
