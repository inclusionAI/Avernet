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
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

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


def resolve_manifest_entity_id(
    *, spec_entity_id: Optional[str], user_id: str
) -> str:
    """The storage key's ``entity_id``, resolved exactly as creation will.

    **One definition, because a second one is silent.** The manifest is stored
    before the bot record exists, keyed by ``(tenant, sha256(env, entity_id,
    bot_id))``; the bot record is written later by ``BotService.create_bot``,
    which resolves ``entity_id or f"staff_{user_id}"``. If the two ever
    disagreed, submission would store a document at one key and everything
    afterwards would look for it at another — and nothing would raise. The apply
    would find no manifest, report that it applied nothing, and be *correct*
    about what it did. The bot would simply come up unconfigured.

    Mirroring the rule here rather than importing ``BotService`` keeps this
    module free of the creation graph; the pairing is held by a test that runs
    both.
    """
    return spec_entity_id or f"staff_{user_id}"


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


class BotCreationManifestSeam:
    """Everything bot creation asks of the manifest layer, in one place.

    Deliberately small and free of creation policy: creation decides *when* to
    call these, this decides what each one means. Everything underneath is W1's
    and W4's — the same validator, the same storage, the same apply engine — so
    a manifest submitted through creation cannot end up held to different rules
    than one submitted through ``PUT``.

    The last two — starting the durable job and reading it back — are here so
    the HTTP layer never touches the task queue. A router that enqueued work
    would be doing business, and a router that knew the job's idempotency key
    would be a second place for it to be spelled.

    Both are passed in rather than imported, because ``create_job`` imports this
    module for the triggers below: wiring them at construction is how the DI
    module already resolves the same shape for ``is_teclaw`` and the job's own
    collaborators, and it keeps the two modules' dependency in one direction.
    """

    def __init__(
        self,
        *,
        manifest_service: Any,
        apply_service: Any,
        script_service_provider: Callable[[], Any],
        is_teclaw: Callable[[Optional[str]], bool],
        start_job: Callable[..., None],
        find_job: Callable[..., Any],
    ) -> None:
        self._manifests = manifest_service
        self._applies = apply_service
        self._script_service_provider = script_service_provider
        self._is_teclaw = is_teclaw
        self._start_job = start_job
        self._find_job = find_job

    def preflight(
        self, *, document: str, engine_type: Optional[str], bot_type: Optional[str]
    ) -> dict[str, Any]:
        """Refuse now, or never. Raises ``ManifestValidationError``.

        Called **before Passport is applied for**, so a caller with an invalid
        manifest never completes an authorization only to be told their document
        was wrong — that wastes their time and burns a Passport application.
        """
        return preflight_creation_manifest(
            document=document,
            engine_type=engine_type,
            bot_type=bot_type,
            validate=self._manifests.validate,
            materialised=self._applies.materialised_constructs(),
            is_teclaw=self._is_teclaw,
        )

    def persist(
        self,
        *,
        spec_entity_id: Optional[str],
        user_id: str,
        bot_id: str,
        document: str,
        modifier: str,
        engine_type: Optional[str],
        bot_type: Optional[str],
    ) -> str:
        """Store the submitted document against the allocated ``bot_id``.

        Through the ordinary manifest service, so the same validation and the
        same all-or-nothing rule apply, and the same storage key: no schema
        change, because ``(tenant, sha256(env, entity_id, bot_id))`` has all
        three parts in hand before the bot record exists.

        This is what makes "the manifest that was validated is the manifest that
        is applied" structural — the caller submits it once and never re-sends
        it.

        Resolves the key here rather than taking it, and returns it: the caller
        is ``create_flow``, which must not import this package (that closes a
        cycle through the creation graph), and the resolution belongs with the
        storage that depends on it anyway.
        """
        entity_id = resolve_manifest_entity_id(
            spec_entity_id=spec_entity_id, user_id=user_id
        )
        self._manifests.put(
            entity_id=entity_id,
            bot_id=bot_id,
            document=document,
            modifier=modifier,
            active_engine=engine_type,
            bot_type=bot_type,
        )
        return entity_id

    def phase_a(
        self,
        *,
        entity_id: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        engine_type: Optional[str],
        bot_type: Optional[str],
    ) -> Optional[str]:
        """Apply the pre-container phase. Returns its ``apply_id``.

        **Runs before the bot record exists**, which is what makes the ordering
        guarantee structural rather than a hook in the right place: the
        startup-script row is keyed by ``(entity_id, bot_id)`` and needs no
        record, so the row is written before anything composes a start command.

        **Never raises.** A manifest-layer failure must not abort creation
        (§2.7): the bot is still created, and the failure surfaces in the poll's
        terminal report. Returning ``None`` means the phase could not even be
        started, which the caller treats the same way — it does not stop
        creation.

        **Runs even when the document declares no script.** The record it writes
        is what tells the creation job and the poll that this phase is done, so
        skipping it as an apparent no-op would break both.
        """
        try:
            accepted = self._applies.start_apply(
                entity_id=entity_id,
                bot_id=bot_id,
                bot=None,
                owner_id=owner_id,
                actor_id=actor_id,
                trigger=CREATE_PRE_CONTAINER_TRIGGER,
                phases=frozenset({ApplyPhase.PRE_CONTAINER}),
                engine_type=engine_type,
                bot_type=bot_type,
            )
            return accepted.apply_id
        except Exception:  # noqa: BLE001 — §2.7: never abort creation
            logger.exception(
                "[manifest_creation] pre-container phase could not start for "
                "bot_id=%s; creation continues and the report will say so",
                bot_id,
            )
            return None

    def start_job(
        self,
        *,
        bot_id: str,
        entity_id: str,
        user_id: str,
        document_owner: str,
        spec: dict[str, Any],
        iframe_url: Optional[str],
        redirect_url: Optional[str],
    ) -> None:
        """Hand the creation to the durable job.

        Called **after** the Passport application, because the job's first step
        is reading its status: enqueueing earlier would only mean a first run
        that finds nothing and reschedules.
        """
        self._start_job(
            bot_id=bot_id,
            entity_id=entity_id,
            user_id=user_id,
            tenant=get_current_avernet_tenant(),
            env=get_current_env(),
            document_owner=document_owner,
            spec=spec,
            iframe_url=iframe_url,
            redirect_url=redirect_url,
        )

    def find_job(self, *, entity_id: str, bot_id: str) -> Optional[Any]:
        """This creation's task row, or ``None`` if no creation was submitted.

        The tenant is resolved here, from the request, rather than taken as an
        argument — the same rule the storage key follows, and for the same
        reason: a caller who could supply it could read another tenant's row.
        ``entity_id`` is the caller's own, resolved by the caller exactly as it
        is at submission, and it is what keeps one owner's ``bot_id`` from
        finding another's creation.
        """
        return self._find_job(
            tenant=get_current_avernet_tenant(),
            entity_id=entity_id,
            bot_id=bot_id,
        )

    def discard(self, *, entity_id: str, bot_id: str) -> None:
        """Remove what submission and the pre-container phase wrote.

        Called when a creation ends **without a bot** — declined or expired. Both
        deletes are idempotent, and both are needed: the manifest row is keyed by
        a ``bot_id`` that will never become a bot, and the pre-container phase
        may already have written a startup-script row for the same id. Nothing
        else would ever reach either, because ordinary bot deletion needs a bot
        record and allocating a ``bot_id`` consumes no quota.

        Never raises: cleanup failing must not turn an already-terminal creation
        into an error.
        """
        for what, delete in (
            ("manifest", lambda: self._manifests.delete(
                entity_id=entity_id, bot_id=bot_id
            )),
            ("startup script", lambda: self._script_service_provider().delete(
                entity_id=entity_id, bot_id=bot_id
            )),
        ):
            try:
                delete()
            except Exception:  # noqa: BLE001 — see docstring
                logger.exception(
                    "[manifest_creation] could not discard the %s for bot_id=%s",
                    what,
                    bot_id,
                )


__all__ = [
    "CREATE_ON_CONTAINER_TRIGGER",
    "CREATE_PRE_CONTAINER_TRIGGER",
    "ApplyPhase",
    "BotCreationManifestSeam",
    "declared_constructs",
    "preflight_creation_manifest",
    "resolve_manifest_entity_id",
]
