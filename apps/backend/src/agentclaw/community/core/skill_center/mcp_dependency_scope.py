"""Decode a Skill's declared MCP dependencies into server codes.

A Skill's dependencies are part of the runtime MCP set: the projection folds
them into the codes it delivers to the device. So a command that adds or
removes a Skill changes the MCP set too, and can only declare an accurate
``ProjectionScope`` if it knows which codes moved.

That makes two readers of the same column — the projection resolver and the
command that scopes a Skill mutation — and they must agree exactly: a mutation
that scoped a different set than it projects would leave a dependency
whitelisted but never configured, or delete one still in use. So the shape is
decoded once, here.

Deliberately dependency-free (stdlib only). The persistence layer reads this
column too, and the modules that own the richer Skill types cannot be imported
from there without closing an import cycle.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json


def mcp_dependency_codes(dependencies: Iterable[object]) -> tuple[str, ...]:
    """Normalize one Skill's declared MCP dependencies to server codes.

    The stored shape is historical and mixed: some rows hold bare codes,
    others ``{"server_code": ...}`` or ``{"code": ...}``.

    Order is preserved and duplicates are kept — callers collect into a set.
    An unrecognised entry raises rather than being skipped: a silently dropped
    dependency is an MCP the Skill needs and the device never receives.

    Tool names encoded as ``mcp__<domain>__<tool>`` are not server codes. The
    Skill Center can still surface them in historical rows, so filter them out
    as unconfigured tool identifiers instead of failing the whole projection.
    A missing tool on a real server remains a caller-level business error, not
    silently suppressed here.
    """
    codes: list[str] = []
    for dependency in dependencies:
        if isinstance(dependency, str):
            if not dependency.startswith("mcp__"):
                codes.append(dependency)
            continue
        if isinstance(dependency, dict):
            code = dependency.get("server_code") or dependency.get("code")
            if isinstance(code, str) and not code.startswith("mcp__"):
                codes.append(code)
                continue
            if isinstance(code, str):
                continue
        raise ValueError("invalid Skill MCP dependency")
    return tuple(codes)


def mcp_dependency_codes_from_version_metadata(metadata_json: object) -> tuple[str, ...]:
    """Decode exact Version metadata through the canonical dependency shape."""

    metadata = json.loads(metadata_json) if isinstance(metadata_json, str) else metadata_json
    if not isinstance(metadata, Mapping):
        raise ValueError("Skill Version metadata must be an object")
    dependencies = metadata.get("mcp_dependencies")
    if not isinstance(dependencies, list):
        raise ValueError("Skill Version has invalid MCP dependencies")
    return mcp_dependency_codes(dependencies)


__all__ = ["mcp_dependency_codes", "mcp_dependency_codes_from_version_metadata"]\n
