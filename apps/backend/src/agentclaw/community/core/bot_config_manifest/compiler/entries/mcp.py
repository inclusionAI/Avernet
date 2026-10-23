"""``manifest.mcp`` → ``DesiredMcpServer``: the server code and its typed override."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from agentclaw.community.core.apply_engine.desired_state import (
    Declaration,
    DesiredMcpServer,
    EntryFailure,
    McpOverride,
)

__all__ = ["compile_mcp"]


def compile_mcp(entries: Sequence[Mapping[str, Any]]) -> Declaration[DesiredMcpServer]:
    desired: list[DesiredMcpServer] = []
    failures: list[EntryFailure] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        server_code = entry.get("server_code") if isinstance(entry, Mapping) else None
        if not isinstance(server_code, str) or not server_code:
            failures.append(EntryFailure(f"[{index}]", "an mcp entry must name a 'server_code'"))
            continue
        if server_code in seen:
            # A set cannot contain a duplicate. Refusing rather than
            # de-duplicating: two entries for one code means the author
            # believes something that is not true of the result.
            failures.append(EntryFailure(server_code, "declared more than once in this category"))
            continue
        seen.add(server_code)
        config = entry.get("config")
        try:
            override = McpOverride.from_config(config) if isinstance(config, Mapping) and config else None
        except ValueError as exc:
            failures.append(EntryFailure(server_code, str(exc)))
            continue
        desired.append(DesiredMcpServer(server_code, override))
    return Declaration(tuple(desired), failures=tuple(failures))
