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

from agentclaw.community.core.bot_config_manifest.apply.delivery import (
    CreationSequence,
)
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
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_apply_service_protocol import (
    BotConfigManifestApplyServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import (
    BotConfigManifestServiceProtocol,
)
from agentclaw.community.core.bot_startup_script.bot_startup_script_service_protocol import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.schema import (
    ManifestValidationError,
    Violation,
)
from agentclaw.community.core.bot_management.manifest_seam import (
    ManifestCreationSeam,
)
from agentclaw.community.core.task_queue.types import TaskRecord
from agentclaw.community.log import get_logger
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()

#: The trigger the pre-container phase records under. The poll and the creation
#: job both recognise a creation's phases by these, via ``last_apply``.
CREATE_PRE_CONTAINER_TRIGGER = "create:pre_container"
#: The trigger the post-container phase records under.
CREATE_ON_CONTAINER_TRIGGER = "create:on_container"

def _no_materialiser_refusal(construct: ApplyConstruct) -> str:
    return (
        f"'{construct.value}' cannot be applied by this build, so a bot created "
        "with it would be authorized, created and only then fail to configure. "
        "Its materialiser has not landed yet; create the bot first and PUT the "
        "manifest once it has"
    )


def resolve_manifest_entity_id(*, spec_entity_id: str) -> str:
    """The storage key's ``entity_id``. The caller's, unchanged.

    **Why this is a named function rather than a bare attribute read.** The
    manifest is stored *before* the bot record exists, keyed by
    ``(tenant, sha256(env, entity_id, bot_id))``; the record is written later by
    ``BotService.create_bot``. If those two ever keyed off different values,
    submission would store a document at one key and everything afterwards would
    look for it at another — and **nothing would raise**. The apply would find no
    manifest, report that it applied nothing, and be *correct* about what it did.
    The bot would simply come up unconfigured.

    What makes them agree is not a rule mirrored here, it is that both are handed
    the **same** ``BotCreateSpec.entity_id``: this seam gets it from
    ``submit_bot_creation_with_manifest``, and ``create_bot`` gets it from the
    spec the job payload carries. That is what the pairing test pins.

    It took ``user_id`` and defaulted to ``staff_{user_id}`` for a while,
    mirroring ``create_bot``'s own ``entity_id or f"staff_{user_id}"``. That was
    dead weight, and misleading with it: ``entity_id`` is a required ``str`` on
    the spec and reaches both sides concrete, so neither fallback can fire and
    the mirror had nothing to keep in step.
    """
    return spec_entity_id


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
) -> dict[str, Any]:
    """Refuse anything this creation path could not actually deliver.

    Returns the parsed document. Raises :class:`ManifestValidationError` with
    **every** reason at once — the same all-or-nothing shape `PUT` has, so fixing
    a document is one pass rather than a queue of resubmissions.

    Every engine family creates here (W8): what a family cannot deliver is the
    validator's to refuse per construct (``script`` on teclaw is
    ``unsupported_script``), not this preflight's to refuse per engine.
    """
    violations: list[Violation] = []

    # W1's validator, unchanged: whatever it refuses, this refuses. Its
    # violations propagate as they are — the all-or-nothing shape is its own.
    parsed = validate(
        document=document, active_engine=engine_type, bot_type=bot_type
    ).parsed

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


class BotCreationManifestSeam(ManifestCreationSeam):
    """Everything bot creation asks of the manifest layer, in one place.

    The base class is ``core/bot_management``'s :class:`ManifestCreationSeam`,
    the Protocol naming these six operations — **declared** here rather than
    merely satisfied by shape. Structural conformance was checked nowhere: a
    signature drifting from the contract surfaced, if at all, as a type error at
    whichever call site happened to pass this class where the Protocol was
    asked for, and neither a reader nor an IDE could get from the contract to
    its single real implementation. Inheriting costs nothing at runtime — every
    method the Protocol names is overridden below — and makes both hold; the
    signatures themselves are pinned by ``test_creation_seam``, since no type
    checker runs on this tree.

    It does bring one hazard, which the same test pins: a Protocol method's body
    is ``...``, so an operation dropped from this class would be *inherited* and
    quietly answer ``None`` where it used to raise ``AttributeError``.

    The dependency direction is unchanged: this package already imports
    ``core/bot_management`` (the engine registry, the token vault), and it is the
    *reverse* that would close a cycle, which is why the contract lives over
    there.

    **This class is named in two places and no more**: here, and the DI provider
    that constructs it. Everything that *uses* the seam — submission, the two
    routes, the creation job — holds the Protocol, which is also what the
    container binds. That is why the Protocol names ``apply_pre_container`` and
    ``find_job`` too: they have exactly one caller each, but those callers are
    injected like every other, and a binding that handed out the class would
    make them depend on how the seam is built.

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
    module already resolves the job's own collaborators, and it keeps the two
    modules' dependency in one direction.
    """

    def __init__(
        self,
        *,
        manifest_service: BotConfigManifestServiceProtocol,
        apply_service: BotConfigManifestApplyServiceProtocol,
        script_service_provider: Callable[[], BotStartupScriptServiceProtocol],
        start_job: Callable[..., None],
        find_job: Callable[..., Optional[TaskRecord]],
        authorization_window_seconds: int,
        purge_cli_tools: Callable[[str, str], int],
        purge_managed_files: Optional[Callable[[str, str], int]] = None,
        creation_sequence: Optional[Callable[[Optional[str]], CreationSequence]] = None,
    ) -> None:
        self._manifests = manifest_service
        self._applies = apply_service
        self._script_service_provider = script_service_provider
        self._start_job = start_job
        self._find_job = find_job
        # W8: ``(owner_id, bot_id) -> rows removed`` — the managed-files store's
        # purge, for a creation that ends without a bot after its pre-container
        # phase wrote platform state. None means no store is bound.
        self._purge_managed_files = purge_managed_files
        # W9: ``(entity_id, bot_id) -> objects removed``. Unlike the managed-files
        # purge above it runs under **either** sequence, because ``cli_tools`` is
        # PRE_CONTAINER on teclaw whatever the switch says and is always
        # platform-managed — so a creation that ends without a bot can have
        # written tool rows either way. It reaches no engine, which is correct
        # rather than a shortcut: there is no container to remove a tool from.
        #
        # Required, not defaulted: a seam wired without it leaves rows and
        # objects for a ``bot_id`` that was never created, and nothing else
        # enumerates them — the key layout is per-bot, so no later sweep finds
        # them. Its W8 neighbour above keeps its default because W8 wired it
        # that way; changing that is not this work item's to make.
        self._purge_cli_tools = purge_cli_tools
        # W8: the delivery strategy's sequence for an engine, frozen into the
        # job's payload at submission. None (the pre-W8 wiring) freezes nothing
        # and the job asks the live strategy on each run.
        self._creation_sequence = creation_sequence
        # Read from configuration once, at construction, and handed to the
        # enqueue below. The job freezes it into its payload, so a creation
        # keeps the window it was submitted under even if the setting moves.
        self._authorization_window_seconds = authorization_window_seconds

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
        )

    def persist(
        self,
        *,
        spec_entity_id: str,
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
        entity_id = resolve_manifest_entity_id(spec_entity_id=spec_entity_id)
        self._manifests.put(
            entity_id=entity_id,
            bot_id=bot_id,
            document=document,
            modifier=modifier,
            active_engine=engine_type,
            bot_type=bot_type,
        )
        return entity_id

    def apply_pre_container(
        self,
        *,
        entity_id: str,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        engine_type: Optional[str],
        bot_type: Optional[str],
        bot: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """Apply the pre-container phase. Returns its ``apply_id``.

        **Runs before the bot record exists** under the ``CREATE_BETWEEN_PHASES``
        sequence, which is what makes the ordering guarantee structural rather
        than a hook in the right place: the startup-script row is keyed by
        ``(entity_id, bot_id)`` and needs no record, so the row is written
        before anything composes a start command. Under
        ``RECORD_APPLY_PROVISION`` (W8) the record exists first and is handed in
        as ``bot``, so the materialisers that read the record (the skills
        area's flush) see the real row rather than the stand-in.

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
                bot=bot,
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
            window_seconds=self._authorization_window_seconds,
            creation_sequence=(
                self._creation_sequence(spec.get("engine_type")).value
                if self._creation_sequence is not None
                else None
            ),
        )

    def find_job(self, *, entity_id: str, bot_id: str) -> Optional[TaskRecord]:
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

    def discard(
        self, *, entity_id: str, bot_id: str, owner_id: Optional[str] = None
    ) -> bool:
        """Remove what submission and the pre-container phase wrote.

        With ``owner_id`` (W8) the managed-files store is purged as well: under
        the ``RECORD_APPLY_PROVISION`` sequence the pre-container phase writes
        platform files for a bot that, if creation then ends without one, has
        no record for ordinary deletion to reach. W9's CLI tools are purged
        under **either** sequence, for the same reason stated differently: that
        category is always platform-managed, so its rows — and the objects they
        alone name — can exist whichever way the creation ran.

        Called when a creation ends **without a bot** — declined or expired. Both
        deletes are idempotent, and both are needed: the manifest row is keyed by
        a ``bot_id`` that will never become a bot, and the pre-container phase
        may already have written a startup-script row for the same id. Nothing
        else would ever reach either, because ordinary bot deletion needs a bot
        record and allocating a ``bot_id`` consumes no quota.

        **Never raises, but says whether it succeeded**, and the caller is
        expected to act on that. Both deletes are attempted even when the first
        fails, so one broken store does not hide the other's outcome.

        Returning ``False`` rather than raising is deliberate: this runs on a
        path that is already ending, and an exception here would be indistinguishable
        from the creation itself failing. But swallowing the answer would be
        worse — the caller would report the creation terminal while the rows are
        still there, and this is the only thing that can ever reach them.
        """
        discarded = True
        deletes: list[tuple[str, Callable[[], Any]]] = [
            ("manifest", lambda: self._manifests.delete(
                entity_id=entity_id, bot_id=bot_id
            )),
            ("startup script", lambda: self._script_service_provider().delete(
                entity_id=entity_id, bot_id=bot_id
            )),
        ]
        if owner_id is not None and self._purge_managed_files is not None:
            purge = self._purge_managed_files
            deletes.append(
                ("managed files", lambda: purge(owner_id, bot_id))
            )
        purge_tools = self._purge_cli_tools
        deletes.append(("cli tools", lambda: purge_tools(entity_id, bot_id)))
        for what, delete in deletes:
            try:
                delete()
            except Exception:  # noqa: BLE001 — see docstring
                discarded = False
                logger.exception(
                    "[manifest_creation] could not discard the %s for bot_id=%s",
                    what,
                    bot_id,
                )
        return discarded


__all__ = [
    "CREATE_ON_CONTAINER_TRIGGER",
    "CREATE_PRE_CONTAINER_TRIGGER",
    "ApplyPhase",
    "BotCreationManifestSeam",
    "declared_constructs",
    "preflight_creation_manifest",
    "resolve_manifest_entity_id",
]
