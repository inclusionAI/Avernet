from __future__ import annotations

from typing import Any

from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry
from agentclaw.community.core.workspace.engines.claude_code import ClaudeCodeSandboxProvider
from agentclaw.community.core.workspace.engines.openclaw import OpenClawSandboxProvider
from agentclaw.community.di import config as cfg


def parse_build_rsync_excludes_from_ext(
    ext: dict[str, Any] | None,
) -> list[str] | None:
    """Parse build_rsync_excludes from ac_bots.ext field.

    Args:
        ext: The parsed JSON dict from ac_bots.ext column.

    Returns:
        None if not configured (use defaults), or a list of exclude patterns.
    """
    if not ext:
        return None

    patterns = ext.get("build_rsync_excludes")
    if not patterns or not isinstance(patterns, list):
        return None

    # Validate all items are strings (convert numbers to strings for safety)
    # Note: bool is a subclass of int, so explicitly exclude bool
    return [
        str(p) for p in patterns
        if isinstance(p, (str, int, float)) and not isinstance(p, bool)
    ]


def create_engine_sandbox_registry(
    workspace: cfg.WorkspaceConfig,
) -> EngineSandboxRegistry:
    """Wire one registry holding both engines' sandbox providers.

    Each provider receives the same ``WorkspaceConfig`` — the per-engine
    root path lives on the config dataclass, so providers can stay
    mode-blind.
    """
    registry = EngineSandboxRegistry()
    registry.register(OpenClawSandboxProvider(workspace=workspace))
    registry.register(ClaudeCodeSandboxProvider(workspace=workspace))
    return registry
