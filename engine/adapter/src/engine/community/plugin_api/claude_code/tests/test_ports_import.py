"""Import smoke for claude_code native ports.

Asserts each per-domain port class is importable from
`engine.community.plugin_api.claude_code.<domain>` and that the aggregate
`ClaudeCodePlugin` composes them. Layering (port imports only
`engine.community.kernel` + stdlib/typing) is enforced separately by importlinter
(`plugin_api-no-impls`), so this test stays a cheap import + attribute
presence check — it does not instantiate or call anything.
"""
from __future__ import annotations

import importlib


def test_chat_port_imports() -> None:
    m = importlib.import_module("engine.community.plugin_api.claude_code.chat")
    assert hasattr(m, "ClaudeCodeChatPort")


def test_session_port_imports() -> None:
    m = importlib.import_module("engine.community.plugin_api.claude_code.session")
    assert hasattr(m, "ClaudeCodeSessionPort")


def test_mcp_port_imports() -> None:
    m = importlib.import_module("engine.community.plugin_api.claude_code.mcp")
    assert hasattr(m, "ClaudeCodeMcpPort")


def test_skills_port_imports() -> None:
    m = importlib.import_module("engine.community.plugin_api.claude_code.skills")
    assert hasattr(m, "ClaudeCodeSkillsPort")


def test_cron_port_imports() -> None:
    m = importlib.import_module("engine.community.plugin_api.claude_code.cron")
    assert hasattr(m, "ClaudeCodeCronPort")


def test_models_port_imports() -> None:
    m = importlib.import_module("engine.community.plugin_api.claude_code.models_port")
    assert hasattr(m, "ClaudeCodeModelsPort")


def test_file_port_imports() -> None:
    m = importlib.import_module("engine.community.plugin_api.claude_code.file")
    assert hasattr(m, "ClaudeCodeFilePort")


def test_commands_port_imports() -> None:
    m = importlib.import_module("engine.community.plugin_api.claude_code.commands")
    assert hasattr(m, "ClaudeCodeCommandsPort")


def test_relay_port_imports() -> None:
    m = importlib.import_module("engine.community.plugin_api.claude_code.relay")
    assert hasattr(m, "ClaudeCodeRelayPort")


def test_aggregate_plugin_imports() -> None:
    m = importlib.import_module("engine.community.plugin_api.claude_code")
    assert hasattr(m, "ClaudeCodePlugin")
