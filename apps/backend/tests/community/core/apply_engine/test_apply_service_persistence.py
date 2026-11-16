"""Apply persisted-report and late worker lifecycle regressions."""

from __future__ import annotations


import pytest


from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    MANIFEST_STATE,
)

from agentclaw.community.core.apply_engine.compiler import CompileRefused

from agentclaw.community.core.apply_engine.outcomes import (
    ApplyStatus,
)


from agentclaw.community.utils.env_utils import get_current_env


from agentclaw.community.core.apply_engine.repository.apply_models import (  # noqa: F401
    BotConfigManifestApplyLockModel,
    BotConfigManifestApplyModel,
)

from agentclaw.community.core.bot_config_manifest.repository.models import (  # noqa: F401
    BotConfigManifestModel,
)


from .test_apply_service_lifecycle import (
    world as world,
    _ENTITY,
    _BOT,
    _BOT_RECORD,
    _ManifestService,
    _start,
    _dry_run,
    _drain,
)


def test_materialised_constructs_reports_what_is_registered_today(world):
    """W4 registered two; W5 brought the count to four, W6 to five, W9 to six.

    Pinned as an equality rather than a subset so a materialiser that lands
    without its documentation, its ordering entry or its capability gate is
    caught here — the set is a published contract, not an accident of imports.
    Widening it is the whole edit a new materialiser needs, which is the
    property the next test pins.
    """
    service, *_ = world
    assert {c.value for c in service.materialised_constructs()} == {
        "script",
        "capabilities",
        "identity",
        "resources",
        "cli_tools",
    }


def test_registering_a_materialiser_widens_it_with_no_edit_to_any_caller(world):
    """The property W5 and W6 depend on.

    A hand-written set would need an edit here when they land, and forgetting it
    is invisible until a creation accepts a category nothing can apply — after a
    Passport application, a user's click, and a live bot. Deriving it from the
    registry makes landing a materialiser the whole change.
    """
    service, *_ = world
    from agentclaw.community.core.apply_engine.capabilities import (
        Category,
    )

    real = service._build_materialisers

    class _StubResourcesMaterialiser:
        construct = Category.RESOURCES

    def _widened(strategy):
        built = dict(real(strategy))
        built[Category.RESOURCES] = _StubResourcesMaterialiser()
        return built

    service._build_materialisers = _widened
    try:
        assert Category.RESOURCES in service.materialised_constructs()
    finally:
        service._build_materialisers = real


def test_an_apply_that_cannot_be_rebuilt_terminates_instead_of_looping(world):
    """A compile failure on the worker is terminal, not a retry.

    The document is re-read at execution rather than carried in the payload, so
    it can fail to validate then — the bot's engine changed since the enqueue,
    say. Letting that escape hands the worker an exception it treats as a retry,
    and the apply would loop until its deadline with the lock still held and the
    record still RUNNING. Both halves are asserted here because leaving either
    out is the bug: no report means a poller waits, no release means the bot is
    locked against every future apply.
    """
    service, _applies, locks, _scripts, _manifests = world

    accepted = service.start_apply(
        entity_id=_ENTITY,
        bot_id=_BOT,
        bot=_BOT_RECORD,
        owner_id=_ENTITY,
        actor_id=_ENTITY,
        state=MANIFEST_STATE,
    )

    # A second apply, whose compile will fail.
    async def _refuse(*_args, **_kwargs):
        raise RuntimeError("the stored document no longer validates")

    service._compilers[MANIFEST_STATE.kind].compile = _refuse
    payload = {
        "apply_id": "apply-that-cannot-start",
        "entity_id": _ENTITY,
        "bot_id": _BOT,
        "owner_id": _ENTITY,
        "actor_id": _ENTITY,
        "env": get_current_env(),
        "tenant": "",
        "trigger": "explicit",
        "lock_token": "other",
        "started_at": None,
        "phase": None,
        "state": {"kind": "manifest"},
        "carry_from_apply_id": None,
        "engine_type": None,
        "bot_type": None,
    }
    service._applies.start(
        env=get_current_env(),
        entity_id=_ENTITY,
        bot_id=_BOT,
        apply_id="apply-that-cannot-start",
        trigger="explicit",
        actor=_ENTITY,
        report="{}",
    )

    # Must not raise: raising is what the worker turns into a doomed retry.
    service.run_apply_task(payload)

    stranded = service.get_apply(
        entity_id=_ENTITY, bot_id=_BOT, apply_id="apply-that-cannot-start"
    )
    assert stranded.status is ApplyStatus.FAILED, (
        "an apply that cannot be rebuilt must terminate, not poll forever"
    )
    assert accepted.apply_id  # the first apply is untouched


class _DomainRefusal(Exception):
    """Stands in for a compiler's own error (ManifestValidationError, NotAppliable): what its HTTP surface maps."""


class _RefusingCompiler:
    """Passes ``check`` and refuses ``compile``: the reference moved between the two, as a promotion's can."""

    def check(self, ctx, ref):
        return None

    async def compile(self, ctx, ref, *, only=None):
        raise CompileRefused(
            "promotion p1 moved active to r1, but active is r2 now; a superseded promotion is not delivered",
            domain_error=_DomainRefusal("superseded"),
        )


def test_a_compile_refused_in_the_worker_is_a_failed_report_with_the_reason(world):
    """A refusal is not an engine failure: the report says why, in the compiler's
    words, and no category was touched. An engine exception, by contrast, is
    recorded without its text (``record_engine_failure``)."""
    service, applies, locks, scripts, _ = world
    service._compilers = {MANIFEST_STATE.kind: _RefusingCompiler()}
    accepted = _start(service)
    report = _drain(service)
    assert report.apply_id == accepted.apply_id
    assert report.status is ApplyStatus.FAILED and report.categories == ()
    assert report.notes == (
        "compile refused: promotion p1 moved active to r1, but active is r2 now; "
        "a superseded promotion is not delivered",
    )
    assert scripts.writes == 0
    assert locks.get(env="dev", entity_id=_ENTITY, bot_id=_BOT) is None, (
        "the lock is released after a refusal"
    )


def test_a_dry_run_hears_the_compilers_own_refusal_not_the_workers(world):
    """A dry run answers an HTTP caller: a refusal is unwrapped to the domain
    error the compiler carries (mapped to 422 / 404 / 409 by its surface);
    ``CompileRefused`` itself is what only the worker records."""
    service, applies, _locks, _scripts, _ = world
    service._compilers = {MANIFEST_STATE.kind: _RefusingCompiler()}
    with pytest.raises(_DomainRefusal, match="superseded") as raised:
        _dry_run(service)
    assert raised.value.__context__ is None, (
        "the worker-only refusal is not on the domain error's chain"
    )
    assert service.last_apply(entity_id=_ENTITY, bot_id=_BOT) is None, (
        "a dry run leaves no record, refused or not"
    )


def test_a_dry_run_of_a_document_that_stopped_validating_raises_the_validators_error(
    world,
):
    """The real road: the stored document fails the validator (a resource naming a protected workspace entry),
    and the caller gets ``ManifestValidationError`` with its violations, as the route maps it."""
    from agentclaw.community.core.bot_config_manifest.schema import (
        ManifestValidationError,
        validate_document,
    )

    class _ValidatingManifests(_ManifestService):
        """The fixture's document fake, with the real validator behind ``validate``."""

        def validate(self, *, document, active_engine, bot_type):
            return validate_document(
                document,
                self.capabilities_for_bot(
                    {"active_engine": active_engine, "bot_type": bot_type}
                ),
            )

    service, *_ = world
    service._compilers[MANIFEST_STATE.kind]._manifests = _ValidatingManifests(
        "schema_version: 1\nmanifest:\n  resources:\n    - path: SOUL.md\n      content: x\n"
    )
    with pytest.raises(ManifestValidationError) as refused:
        _dry_run(service)
    assert refused.value.violations
    assert refused.value.__context__ is None, (
        "the route sees the validator's error alone"
    )
