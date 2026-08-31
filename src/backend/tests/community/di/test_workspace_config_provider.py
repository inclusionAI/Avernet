"""Workspace roots are normalized once at the configuration boundary."""

from pathlib import Path

from agentclaw.community.di.modules import config_module
from agentclaw.community.di.config import WorkspaceConfig
from agentclaw.community.di.modules.config_module import ConfigModule


def test_workspace_resolves_relative_roots_before_engine_injection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        config_module,
        "_user_config",
        lambda: {
            "workspace": {
                "openclaw_root": "./data/workspace/openclaw",
                "claude_code_root": "./data/workspace/claude_code",
                "aicoding_root": "./data/workspace/aicoding",
                "hermes_root": "./data/workspace/hermes",
            }
        },
    )

    workspace = ConfigModule().workspace()

    assert workspace.openclaw_root == str(tmp_path / "data/workspace/openclaw")
    assert workspace.claude_code_root == str(tmp_path / "data/workspace/claude_code")
    assert workspace.aicoding_root == str(tmp_path / "data/workspace/aicoding")
    assert workspace.hermes_root == str(tmp_path / "data/workspace/hermes")


def test_workspace_keeps_absolute_sandbox_roots_stable(monkeypatch) -> None:
    monkeypatch.setattr(config_module, "_user_config", lambda: {})

    workspace = ConfigModule().workspace()
    defaults = WorkspaceConfig()

    assert workspace.openclaw_root == defaults.openclaw_root
    assert workspace.claude_code_root == defaults.claude_code_root
    assert workspace.aicoding_root == defaults.aicoding_root
    assert workspace.hermes_root == defaults.hermes_root
