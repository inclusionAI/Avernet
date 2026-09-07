"""Community addressing at the real device-filesystem transport boundary."""

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_filesystem_dispatcher import (
    DeviceFilesystemDispatcher,
)
from agentclaw.community.core.devices.services.community_device_filesystem_resolver import (
    CommunityDeviceFileSystemResolver,
)


@pytest.mark.asyncio
async def test_workspace_address_reaches_container_namespace():
    bot = {
        "entity_id": "staff_u",
        "entity_type": "staff",
        "active_engine": "claude_code",
    }
    baas = MagicMock()
    baas.invoke_http.return_value = httpx.Response(
        200, content=b"project", request=httpx.Request("POST", "http://fixture")
    )
    resolver = CommunityDeviceFileSystemResolver(
        baas_service=baas,
        bot_repo=MagicMock(**{"get_by_id.return_value": bot}),
        binding_repo=MagicMock(),
        sandbox_client=MagicMock(),
    )
    ctx = DeviceContext(
        provider="baas",
        conn_info={"engine_port": 20003, "bind_id": 1},
        binding_id=1,
        bot_id="bot",
        user_id="u",
        bot_type="personal",
    )
    fs = DeviceFilesystemDispatcher(MagicMock(), resolver).dispatch_addressed(
        ctx,
        namespace="workspace",
        entity_type="staff",
        entity_id="staff_u",
        bot_id="bot",
        engine_type="claude_code",
    )
    assert await fs.read_file("workspace/project.txt") == b"project"
    assert (
        baas.invoke_http.call_args.kwargs["json"]["file_path"]
        == "workspace/project.txt"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "namespace,logical,expected",
    [
        (
            "identity",
            "identity/AGENTS.md",
            "/home/admin/.claude_code/workspace/.claude/AGENTS.md",
        ),
        ("config", "config/engine.json", "/home/admin/.claude_code/config.json"),
    ],
)
async def test_other_file_consumers_keep_correct_engine_addresses(
    namespace, logical, expected
):
    bot = {
        "entity_id": "staff_u",
        "entity_type": "staff",
        "active_engine": "claude_code",
    }
    baas = MagicMock()
    baas.invoke_http.return_value = httpx.Response(
        200, content=b"content", request=httpx.Request("POST", "http://fixture")
    )
    resolver = CommunityDeviceFileSystemResolver(
        baas_service=baas,
        bot_repo=MagicMock(**{"get_by_id.return_value": bot}),
        binding_repo=MagicMock(),
        sandbox_client=MagicMock(),
    )
    ctx = DeviceContext(
        provider="baas",
        conn_info={"engine_port": 20003, "bind_id": 1},
        binding_id=1,
        bot_id="bot",
        user_id="u",
        bot_type="personal",
    )
    fs = DeviceFilesystemDispatcher(MagicMock(), resolver).dispatch_addressed(
        ctx,
        namespace=namespace,
        entity_type="staff",
        entity_id="staff_u",
        bot_id="bot",
        engine_type="claude_code",
    )
    assert await fs.read_file(logical) == b"content"
    assert baas.invoke_http.call_args.kwargs["json"]["file_path"] == expected


def test_community_local_skill_root_uses_claude_layout_without_changing_other_profiles():
    from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory

    plugin = MagicMock(
        **{"get_local_skills_root.return_value": Path("/shared/openclaw/skills")}
    )
    community = WorkspacePathFactory(plugin, container_engine_paths=True)
    legacy = WorkspacePathFactory(plugin)
    assert community.get_bot_skills_local_dir("u", "b", "claude_code") == Path(
        "/home/admin/.claude_code/workspace/skills/skills-local"
    )
    assert community.get_bot_skills_local_dir(
        "u", "b", "openclaw"
    ) == legacy.get_bot_skills_local_dir("u", "b", "openclaw")


def test_community_assembly_selects_container_paths_without_global_profile_checks():
    from injector import Injector, Module
    from agentclaw.community.di.modules.infrastructure.community.devices import CommunityDevicesModule
    from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin
    from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory

    class Inputs(Module):
        def configure(self, binder):
            binder.bind(SkillRepoSyncPlugin, to=MagicMock())

    injector = Injector([CommunityDevicesModule(), Inputs()])
    factory = injector.get(WorkspacePathFactory)
    assert factory.get_bot_skills_local_dir('u', 'b', 'claude_code') == Path('/home/admin/.claude_code/workspace/skills/skills-local')
