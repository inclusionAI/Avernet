"""Creating a bot *from* a manifest — the seam creation calls (W13, #1696).

`PUT …/config-manifest` and this path answer the same question — "may this
document be accepted?" — and give **different answers on purpose**. `PUT` may
accept a category no materialiser can act on yet: the document sits inert, the
capabilities endpoint says so, and nothing has been created. Here the same
acceptance costs a Passport application, a user's authorization click and a live
bot before the failure appears, so the bar is higher: a construct with nothing to
apply it is refused at submission.

The extra refusal is **ARCA-only**, and its reason is structural rather than a
missing materialiser. This item's whole pre/post-container split exists because
``BaasService._build_create_bot_payload`` reads the startup-script row while
composing a start command; teclaw has no analogue — ``TeclawProvisionService``
composes a config artifact at provision time. A teclaw manifest delivered after
the container came up would be both a worse fit and a *different mechanism* from
the one W8 lands, so a teclaw bot created here would get semantics that change
under it. W8 owns that arm, including lifting this refusal.

Everything else about the document is W1's: this calls the same validator and the
same capability resolver, so it can never accept something `PUT` would refuse.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from agentclaw.community.core.bot_config_manifest.apply.order import (
    APPLY_ORDER,
    ApplyPhase,
)
from agentclaw.community.core.bot_config_manifest.apply.orchestrator import (
    declared_entries,
)
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    ApplyConstruct,
)
from agentclaw.community.core.bot_config_manifest.schema import (
    ManifestValidationError,
    Violation,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: The trigger the pre-container phase records under. The poll and the creation
#: job both recognise a creation's phases by these, via ``last_apply``.
CREATE_PRE_CONTAINER_TRIGGER = "create:pre_container"
#: The trigger the post-container phase records under.
CREATE_ON_CONTAINER_TRIGGER = "create:on_container"

_TECLAW_REFUSAL = (
    "creating a bot from a manifest is not available on this engine: a teclaw "
    "bot is configured by the artifact composed when its container is "
    "provisioned, which is a different mechanism from this endpoint's "
    "pre/post-container delivery. Tracked by W8 (#1476); until it lands, create "
    "the bot first and PUT its manifest afterwards"
)


def _no_materialiser_refusal(construct: ApplyConstruct) -> str:
    return (
        f"'{construct.value}' cannot be applied by this build, so a bot created "
        "with it would be authorized, created and only then fail to configure. "
        "Its materialiser has not landed yet; create the bot first and PUT the "
        "manifest once it has"
    )


def declared_constructs(parsed: dict[str, Any]) -> tuple[ApplyConstruct, ...]:
    """Every construct the document declares, in apply order.

    "Declares" is ``declared_entries(...) is not None`` — the same distinction
    the orchestrator draws, so a **declared-empty** category counts. `mcp: []`
    is not "nothing to do": under §3.2's overwrite it empties the category, which
    is a write, and a write needs something able to make it.
    """
    return tuple(
        step.construct
        for step in APPLY_ORDER
        if declared_entries(parsed, step.construct) is not None
    )


def preflight_creation_manifest(
    *,
    document: str,
    engine_type: Optional[str],
    bot_type: Optional[str],
    validate: Callable[..., Any],
    materialised: frozenset[ApplyConstruct],
    is_teclaw: Callable[[Optional[str]], bool],
) -> dict[str, Any]:
    """Refuse anything this creation path could not actually deliver.

    Returns the parsed document. Raises :class:`ManifestValidationError` with
    **every** reason at once — the same all-or-nothing shape `PUT` has, so fixing
    a document is one pass rather than a queue of resubmissions.

    Ordering is deliberate: the engine refusal is reported *alongside* whatever
    else is wrong rather than short-circuiting, because a caller on teclaw with a
    typo should learn both.
    """
    violations: list[Violation] = []
    if is_teclaw(engine_type):
        violations.append(
            Violation(
                location="engine",
                code="engine_not_supported_for_creation",
                message=_TECLAW_REFUSAL,
            )
        )

    # W1's validator, unchanged: whatever it refuses, this refuses.
    #
    # Its violations are **merged** rather than allowed to propagate on their
    # own. Letting it raise here would silently drop anything already collected
    # above — a caller on teclaw with a typo would be told about the typo, fix
    # it, resubmit, and only then learn the engine is not supported. That is the
    # resubmission queue the all-or-nothing rule exists to prevent.
    try:
        parsed = validate(
            document=document, active_engine=engine_type, bot_type=bot_type
        ).parsed
    except ManifestValidationError as refused:
        raise ManifestValidationError(
            tuple(violations) + tuple(refused.violations)
        ) from None

    for construct in declared_constructs(parsed):
        if construct not in materialised:
            violations.append(
                Violation(
                    location=_location_for(construct),
                    code="construct_not_appliable_at_creation",
                    message=_no_materialiser_refusal(construct),
                )
            )

    if violations:
        raise ManifestValidationError(violations)
    return parsed


def _location_for(construct: ApplyConstruct) -> str:
    """Where in the document the offending declaration sits.

    ``script`` is a top-level section; the categories live under ``manifest``.
    """
    if construct.value == "script":
        return "script"
    return f"manifest.{construct.value}"


__all__ = [
    "CREATE_ON_CONTAINER_TRIGGER",
    "CREATE_PRE_CONTAINER_TRIGGER",
    "ApplyPhase",
    "declared_constructs",
    "preflight_creation_manifest",
]
