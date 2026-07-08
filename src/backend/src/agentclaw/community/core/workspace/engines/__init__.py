from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry
from agentclaw.community.core.workspace.engines.claude_code import ClaudeCodeSandboxProvider
from agentclaw.community.core.workspace.engines.openclaw import OpenClawSandboxProvider
from agentclaw.community.di import config as cfg


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
