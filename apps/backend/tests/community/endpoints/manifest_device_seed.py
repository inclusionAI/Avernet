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


def seed_empty_default_scope(world, engine_type: str) -> None:
    """Seed the real platform Default scope required to record policy exclusions."""
    from agentclaw.community.core.models.skill import SkillSet
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from agentclaw.community.utils.env_utils import get_current_env

    with world.get(DatabasePlugin).orm_session() as session:
        if (
            session.query(SkillSet)
            .filter_by(
                is_default=True,
                engine_type=engine_type,
                bolt_id="",
                env=get_current_env(),
            )
            .first()
            is None
        ):
            session.add(
                SkillSet(
                    name="default",
                    user_id="",
                    bolt_id="",
                    engine_type=engine_type,
                    is_default=True,
                    is_active=True,
                    env=get_current_env(),
                )
            )
