"""Community BaaS addressing; enterprise host paths stay in the corp resolver."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.devices.services.device_filesystem import DeviceFileSystem
from agentclaw.community.core.devices.services.device_filesystem_resolver import (
    DefaultDeviceFileSystemResolver,
)
from agentclaw.community.core.workspace.path_factory import get_bot_engine_dir


class CommunityDeviceFileSystemResolver(DefaultDeviceFileSystemResolver):
    def __call__(
        self, ctx: DeviceContext, path_mapper: Callable[[str], str]
    ) -> DeviceFileSystem:
        if ctx.provider != "baas":
            return super().__call__(ctx, path_mapper)
        bot = self._bot_repo.get_by_id(ctx.bot_id)
        if not bot or bot.get("active_engine") != "claude_code":
            return super().__call__(ctx, path_mapper)
        host_root = get_bot_engine_dir(
            str(bot["entity_id"]),
            ctx.bot_id,
            "claude_code",
            str(bot.get("entity_type") or "staff"),
        )

        def container_path(path: str) -> str:
            mapped = path_mapper(path)
            candidate = Path(mapped)
            if ".." in candidate.parts:
                raise ValueError("File path contains parent traversal")
            try:
                relative = candidate.relative_to(host_root)
            except ValueError:
                # Existing engine-view locators retain their exact identity;
                # never remap historical uploaded files to another directory.
                return mapped
            if relative.parts[:1] == ("workspace",):
                # Engine resolves its configured workspace, including a retained
                # legacy cwd. Backend must not guess it from the engine name.
                return relative.as_posix()
            return str(Path("/home/admin/.claude_code") / relative)

        return super().__call__(ctx, container_path)
