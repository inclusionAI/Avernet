"""Share Default checks between planning and the first irreversible write."""

from agentclaw.community.core.skill_center.manifest_defaults import (
    ManifestDefaults,
    ManifestDefaultsConflictError,
)


def read_defaults(
    activation, ctx, *, check_skill_sources: bool = True
) -> ManifestDefaults:
    return activation.manifest_defaults(
        bot_id=ctx.bot_id,
        owner_id=ctx.owner_id,
        actor_id=ctx.actor_id,
        check_skill_sources=check_skill_sources,
    )


def guard_defaults(activation, ctx, expected: ManifestDefaults) -> None:
    if (
        read_defaults(activation, ctx, check_skill_sources=expected.check_skill_sources)
        != expected
    ):
        raise ManifestDefaultsConflictError(
            "Default supply changed; re-plan this category"
        )
