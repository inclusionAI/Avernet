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


def test_the_manifest_creation_seam_is_bound_under_its_protocol() -> None:
    """The container hands out ``ManifestCreationSeam``, never the class.

    Submission, both ``with-manifest`` routes and the creation job's handler all
    ask for the Protocol; this provider is the only place that names the
    implementation, because it is the only place that knows how to build one.
    Asserted on the provider's **return annotation**, which is what
    python-injector uses as the binding key — annotate it with the class again
    and every one of those consumers is asking for a key nobody binds.
    """
    module_path = Path(bot_management_module.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    providers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "bot_creation_manifest_seam"
    ]
    assert providers, "BotManagementModule should define the seam provider"
    provider = providers[0]

    assert isinstance(provider.returns, ast.Name)
    assert provider.returns.id == "ManifestCreationSeam", (
        "the seam is bound under the Protocol; binding the class instead leaves "
        "every consumer asking for an unbound key"
    )

    constructed = [
        node
        for node in ast.walk(provider)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "BotCreationManifestSeam"
    ]
    assert constructed, "the provider still builds the one real implementation"


def test_nothing_in_the_composition_root_resolves_the_seam_by_its_class() -> None:
    """One binding, so one seam — and one creation job queue behind it.

    ``injector.get(BotCreationManifestSeam)`` would not fail loudly: the class is
    not bound, so python-injector would *construct a second one*, unwired and
    with none of the collaborators above. The handler would then enqueue through
    a seam the routes never touch.
    """
    source = Path(bot_management_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    resolved = {
        node.args[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Name)
    }
    assert "BotCreationManifestSeam" not in resolved
    assert "ManifestCreationSeam" in resolved
