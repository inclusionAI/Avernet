"""Device reads for Manifest endpoint cases with an unprovisioned test bot."""

from agentclaw.community.core.services.identity import IdentityService
from agentclaw.community.core.services.resource_file_service import ResourceFileService
from tests.community.framework import bind_overrides


async def _empty_files(_self, *_args, **_kwargs):
    return ()


def seed_empty_identity(world) -> None:
    bind_overrides(world, IdentityService, {"list_bot_files": _empty_files})


def seed_empty_manifest_device(world) -> None:
    seed_empty_identity(world)
    bind_overrides(world, ResourceFileService, {"list_entries": _empty_files})
