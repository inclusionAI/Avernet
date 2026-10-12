"""Workspace roots are normalized once at the configuration boundary."""

from pathlib import Path
from inspect import signature
from unittest.mock import MagicMock

import pytest

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
                "deepseek_harness_root": "./data/workspace/dsh",
            }
        },
    )

    workspace = ConfigModule().workspace()

    assert workspace.openclaw_root == str(tmp_path / "data/workspace/openclaw")
    assert workspace.claude_code_root == str(tmp_path / "data/workspace/claude_code")
    assert workspace.aicoding_root == str(tmp_path / "data/workspace/aicoding")
    assert workspace.hermes_root == str(tmp_path / "data/workspace/hermes")
    assert workspace.deepseek_harness_root == str(tmp_path / "data/workspace/dsh")


def test_workspace_keeps_absolute_sandbox_roots_stable(monkeypatch) -> None:
    monkeypatch.setattr(config_module, "_user_config", lambda: {})

    workspace = ConfigModule().workspace()
    defaults = WorkspaceConfig()

    assert workspace.openclaw_root == defaults.openclaw_root
    assert workspace.claude_code_root == defaults.claude_code_root
    assert workspace.aicoding_root == defaults.aicoding_root
    assert workspace.hermes_root == defaults.hermes_root
    assert workspace.deepseek_harness_root == defaults.deepseek_harness_root
    assert workspace.home_admin_artifact_root == "home_admin_root"


def test_workspace_artifact_root_override_is_injected_into_build_service(monkeypatch):
    from agentclaw.community.di.modules.service_bot_module import ServiceBotModule

    monkeypatch.setattr(config_module, "_user_config", lambda: {
        "workspace": {"home_admin_artifact_root": "deployment_home_root"},
    })
    workspace = ConfigModule().workspace()
    assert workspace.home_admin_artifact_root == "deployment_home_root"
    module = ServiceBotModule()
    arguments = {name: MagicMock() for name in signature(module.bot_build_service).parameters}
    arguments["workspace"] = workspace
    service = module.bot_build_service(**arguments)
    assert service._home_admin_artifact_root == "deployment_home_root"


@pytest.mark.parametrize("invalid", [
    None, "", " ", ".", "..", "../escape", "/absolute", "a/b", "a\\b",
    "bad\x00root", "a*", "a?", "a[", "a]", "a\n", "a\r", "a\x7f",
])
def test_workspace_artifact_root_rejects_invalid_directory_name(monkeypatch, invalid):
    monkeypatch.setattr(config_module, "_user_config", lambda: {
        "workspace": {"home_admin_artifact_root": invalid},
    })
    with pytest.raises(ValueError, match="single directory name"):
        ConfigModule().workspace()
