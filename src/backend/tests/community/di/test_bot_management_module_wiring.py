"""Wiring regression tests for bot-management composition root."""

from __future__ import annotations

import ast
from pathlib import Path

from agentclaw.community.di.modules import bot_management_module


def test_bot_service_provider_threads_task_queue_service() -> None:
    """BaaS restart polling is enqueued by BotService, not DeviceServiceRouter."""
    module_path = Path(bot_management_module.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    provider_defs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "bot_service"
    ]
    assert provider_defs, "BotManagementModule should define bot_service provider"
    provider = provider_defs[0]

    arg_names = [arg.arg for arg in provider.args.args]
    assert "task_queue_service" in arg_names

    bot_service_calls = [
        node
        for node in ast.walk(provider)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "BotService"
    ]
    assert bot_service_calls, "bot_service provider should construct BotService"
    assert any(
        keyword.arg == "task_queue_service"
        for keyword in bot_service_calls[0].keywords
    ), "BotService must receive TaskQueueService for restart publish tasks"


def test_default_bot_passport_repair_service_is_composed_without_device_service() -> None:
    module_path = Path(bot_management_module.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    providers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "default_bot_passport_repair_service"
    ]
    assert providers
    provider = providers[0]
    arg_names = [arg.arg for arg in provider.args.args]
    assert arg_names == [
        "self",
        "repository",
        "passport_plugin",
        "auth_relationship_plugin",
        "skill_set_factory",
    ]
    assert "device_service" not in arg_names
    assert "baas_service" not in arg_names


def test_create_bot_for_others_service_is_composed_from_control_plane_dependencies() -> None:
    module_path = Path(bot_management_module.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    providers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "create_bot_for_others_service"
    ]
    assert providers
    provider = providers[0]
    arg_names = [arg.arg for arg in provider.args.args]
    assert arg_names == [
        "self",
        "repository",
        "bot_service",
        "passport_plugin",
        "auth_relationship_plugin",
        "skill_set_factory",
    ]
    assert "device_service" not in arg_names
    assert "baas_service" not in arg_names
