"""The operations bot creation asks of the manifest layer, and the contract.

Most of this file is behavioural: what each operation does, and what it must not
do. The section at the bottom pins the seam against ``ManifestCreationSeam`` — the
Protocol it declares, the container binds, and every consumer holds — because no
type checker runs on this tree to do it.
"""
from __future__ import annotations

from agentclaw.community.core.bot_management.creation_source import validate_creation_source

import inspect

import pytest

from agentclaw.community.core.bot_config_manifest.create_job import (
    DEFAULT_CREATE_DEADLINE_SECONDS,
)
from agentclaw.community.core.apply_engine.order import ApplyPhase
from agentclaw.community.core.bot_config_manifest.creation import (
    CREATE_PRE_CONTAINER_TRIGGER,
    BotCreationManifestSeam,
)
from agentclaw.community.core.bot_management.manifest_seam import (
    ManifestCreationSeam,
)


class _Applies:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self._fail = fail

    def materialised_constructs(self):
        from agentclaw.community.core.apply_engine.capabilities import (
            Category,
            Section,
        )

        return frozenset({Section.SCRIPT, Category.MCP})

    def start_apply(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail:
            raise RuntimeError("the apply could not be started")

        class _Accepted:
            apply_id = "apply-1"

        return _Accepted()


class _Manifests:
    def __init__(self) -> None:
        self.puts: list[dict] = []
        self.deletes: list[dict] = []

    def put(self, **kwargs):
        self.puts.append(kwargs)

    def delete(self, **kwargs):
        self.deletes.append(kwargs)
        return True


class _Scripts:
    def __init__(self, *, fail: bool = False) -> None:
        self.deletes: list[dict] = []
        self._fail = fail

    def delete(self, **kwargs):
        if self._fail:
            raise RuntimeError("script delete failed")
        self.deletes.append(kwargs)
        return True


class _Jobs:
    """Records what the seam asks of the queue, without a queue."""

    def __init__(self, found=None) -> None:
        self.started: list[dict] = []
        self.looked_up: list[dict] = []
        self._found = found

    def start(self, **fields):
        self.started.append(fields)

    def find(self, **fields):
        self.looked_up.append(fields)
        return self._found


def _seam(applies=None, manifests=None, scripts=None, jobs=None,
          purge_cli_tools=None):
    jobs = jobs or _Jobs()
    return BotCreationManifestSeam(
        validate_creation_spec=validate_creation_source,
        manifest_service=manifests or _Manifests(),
        apply_service=applies or _Applies(),
        script_service_provider=lambda: scripts or _Scripts(),
        start_job=jobs.start,
        find_job=jobs.find,
        authorization_window_seconds=DEFAULT_CREATE_DEADLINE_SECONDS,
        # W9: required, so a seam can never be wired without a way to collect
        # the tool rows a bot-less creation leaves behind.
        purge_cli_tools=purge_cli_tools or (lambda entity_id, bot_id: 0),
    )


def test_the_pre_container_apply_needs_no_bot_record():
    applies = _Applies()
    seam = _seam(applies=applies)

    apply_id = seam.apply_pre_container(
        entity_id="e",
        bot_id="b",
        owner_id="o",
        actor_id="a",
        engine_type="claude_code",
        bot_type="personal",
    )

    assert apply_id == "apply-1"
    (call,) = applies.calls
    assert call["bot"] is None, (
        "the pre-container phase runs before the record exists; passing one "
        "would make the ordering guarantee accidental"
    )
    assert call["engine_type"] == "claude_code"
    assert call["trigger"] == CREATE_PRE_CONTAINER_TRIGGER
    assert call["phase"] is ApplyPhase.PRE_CONTAINER


def test_the_pre_container_apply_never_raises_so_it_cannot_abort_creation():
    """§2.7: a manifest-layer failure never mutates or prevents the bot."""
    seam = _seam(applies=_Applies(fail=True))
    assert (
        seam.apply_pre_container(
            entity_id="e",
            bot_id="b",
            owner_id="o",
            actor_id="a",
            engine_type="claude_code",
            bot_type="personal",
        )
        is None
    )


def test_discard_removes_both_rows_a_bot_less_creation_leaves_behind():
    manifests, scripts = _Manifests(), _Scripts()
    _seam(manifests=manifests, scripts=scripts).discard(entity_id="e", bot_id="b")
    assert manifests.deletes == [{"entity_id": "e", "bot_id": "b"}]
    assert scripts.deletes == [{"entity_id": "e", "bot_id": "b"}], (
        "the pre-container phase can write a startup-script row before anyone "
        "knows the creation will complete; leaving it is an orphan nothing "
        "else can reach"
    )


def test_discard_of_a_failing_delete_does_not_raise():
    manifests = _Manifests()
    _seam(manifests=manifests, scripts=_Scripts(fail=True)).discard(
        entity_id="e", bot_id="b"
    )
    # The manifest still went, and cleanup failing did not become an error on an
    # already-terminal creation.
    assert manifests.deletes


def test_persist_goes_through_the_ordinary_manifest_service():
    manifests = _Manifests()
    entity_id = _seam(manifests=manifests).persist(
        spec_entity_id="e",
        bot_id="b",
        document="schema_version: 1\n",
        modifier="someone",
        engine_type="claude_code",
        bot_type="personal",
    )
    (put,) = manifests.puts
    assert put["entity_id"] == "e" and put["bot_id"] == "b"
    assert put["active_engine"] == "claude_code"
    assert entity_id == "e", "the caller needs the key it was stored under"


def test_persist_keys_by_the_spec_entity_id_and_nothing_else():
    """The key is the value ``create_bot`` will be handed, not a derived one.

    This used to assert a ``staff_{user_id}`` default, mirroring ``create_bot``'s
    own. Both fallbacks are unreachable — ``entity_id`` is a required ``str`` on
    the spec and reaches both sides concrete — so the mirror pinned nothing while
    reading as though it did. What matters is that nothing between the request
    and the ``put`` transforms the value.
    """
    manifests = _Manifests()
    entity_id = _seam(manifests=manifests).persist(
        spec_entity_id="u_owner",
        bot_id="b",
        document="schema_version: 1\n",
        modifier="someone",
        engine_type="claude_code",
        bot_type="personal",
    )
    assert entity_id == "u_owner"
    assert manifests.puts[0]["entity_id"] == "u_owner"


# ── the contract itself (``ManifestCreationSeam``) ─────────────────────────

#: The operations the Protocol declares, read off the Protocol rather than
#: retyped: a contract that grows is covered by the signature test below
#: without anyone remembering to extend a list here. Dunders are dropped
#: because ``Protocol`` puts one of its own (``__init__``) on every subclass.
_CONTRACT_OPERATIONS = sorted(
    name
    for name, member in vars(ManifestCreationSeam).items()
    if inspect.isfunction(member) and not name.startswith("_")
)


def test_the_seam_declares_the_creation_protocol_rather_than_matching_it():
    """The contract is a base class, not a resemblance.

    ``core/bot_management`` states what submission needs as a ``Protocol`` and
    must not import this package to say it — that closes a cycle. The dependency
    runs the other way, so this class can and does inherit it, which is what lets
    a reader (and an IDE) get from ``submit_bot_creation_with_manifest``'s
    parameter to the one class that answers it. Matching by shape looked
    identical to a type checker and led nowhere for a human.
    """
    assert ManifestCreationSeam in BotCreationManifestSeam.__mro__


def test_the_contract_names_the_whole_seam():
    """Every operation a consumer reaches through the container is on it.

    The container binds ``ManifestCreationSeam``, so an operation missing from
    it is an operation nobody can call: submission's four, the creation job's
    ``apply_pre_container`` and the poll's ``find_job``. Pinned as an exact set
    rather than left to the parametrization below, which would silently shrink
    along with the contract and go on passing.
    """
    assert _CONTRACT_OPERATIONS == [
        "apply_pre_container",
        "discard",
        "find_job",
        "persist",
        "preflight",
        "recovery_manifest_digest",
        "start_job",
        "verify_binding",
    ]


@pytest.mark.parametrize("operation", _CONTRACT_OPERATIONS)
def test_every_declared_operation_keeps_the_contract_signature(operation):
    """Signature drift is caught here, because no type checker runs in CI.

    Inheriting the Protocol is what makes a checker verify these; the suite is
    what verifies them on this tree. Each contract parameter must survive by
    name — silently renaming one would leave every caller passing an argument
    the implementation no longer takes. Extra parameters are allowed only with a
    default, since a required one the Protocol does not name could never be
    supplied by a caller holding the Protocol — which, now that the container
    binds it, is every caller.

    The first assertion is the hazard the base class brings with it: a Protocol
    method's body is ``...``, so an operation dropped from the seam would be
    *inherited* and answer ``None`` — a submission that silently persisted
    nothing — where before the base class it raised ``AttributeError``.
    """
    assert operation in vars(BotCreationManifestSeam), (
        f"{operation} is inherited from the Protocol rather than implemented; "
        "its body is `...`, so it would answer None instead of raising"
    )

    declared = inspect.signature(getattr(ManifestCreationSeam, operation))
    implemented = inspect.signature(getattr(BotCreationManifestSeam, operation))

    missing = set(declared.parameters) - set(implemented.parameters)
    assert not missing, f"{operation} no longer accepts {sorted(missing)}"

    for name, parameter in implemented.parameters.items():
        if name in declared.parameters:
            continue
        assert parameter.default is not inspect.Parameter.empty, (
            f"{operation} requires {name!r}, which no caller holding the "
            "Protocol can pass"
        )


def test_bound_document_mismatch_stops_without_overwriting_user_document():
    from types import SimpleNamespace
    manifests = _Manifests()
    manifests.get = lambda **kwargs: SimpleNamespace(document='{"schema_version": 1, "manifest": {"identity": []}}')
    seam = _seam(manifests=manifests)
    with pytest.raises(ValueError, match="bound Manifest"):
        seam.verify_binding(entity_id="e", bot_id="b", manifest_digest="0" * 64)
    assert manifests.puts == []
    assert manifests.deletes == []


def _recoverable_seam():
    from types import SimpleNamespace
    from agentclaw.community.core.bot_config_manifest.binding import manifest_digest, bound_manifest_state, DIGEST_ENCODING
    from agentclaw.community.core.bot_config_manifest.creation import CREATE_ON_CONTAINER_TRIGGER
    from agentclaw.community.utils.env_utils import get_current_env
    from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
    from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
    from agentclaw.community.core.task_queue.types import TaskStatus

    document = "schema_version: 1\nmanifest:\n  skills: []\n"
    digest = manifest_digest(document)
    job = SimpleNamespace(status=TaskStatus.SUCCEEDED, payload={"spec": {"manifest_binding": {
        "manifest_digest": digest, "entity_id": "e", "bot_id": "b",
        "digest_encoding": DIGEST_ENCODING, "tenant": get_current_avernet_tenant(), "env": get_current_env(),
    }}})
    manifests = _Manifests()
    manifests.get = lambda **_: SimpleNamespace(document=document)
    applies = _Applies()
    applies.last_apply = lambda **_: SimpleNamespace(
        status=ApplyStatus.PARTIAL, trigger=CREATE_ON_CONTAINER_TRIGGER, state=bound_manifest_state(digest)
    )
    seam = _seam(applies=applies, manifests=manifests, jobs=_Jobs(found=job))
    return seam, job, applies, manifests, digest


def test_failed_expert_creation_recovers_the_original_bound_document():
    seam, _, _, manifests, digest = _recoverable_seam()
    assert seam.recovery_manifest_digest(entity_id="e", bot_id="b") == digest
    assert manifests.puts == []


def test_recovery_refuses_changed_document_without_rewriting_it():
    from types import SimpleNamespace
    seam, _, _, manifests, _ = _recoverable_seam()
    manifests.get = lambda **_: SimpleNamespace(document="schema_version: 1\nmanifest: {}\n")
    with pytest.raises(ValueError, match="bound Manifest has changed"):
        seam.recovery_manifest_digest(entity_id="e", bot_id="b")
    assert manifests.puts == []
    assert manifests.deletes == []


@pytest.mark.parametrize("status", ["RUNNING", "SUCCEEDED"])
def test_recovery_refuses_pending_or_successful_apply(status):
    from types import SimpleNamespace
    from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
    seam, _, applies, _, _ = _recoverable_seam()
    report = applies.last_apply()
    applies.last_apply = lambda **_: SimpleNamespace(status=ApplyStatus(status), trigger=report.trigger, state=report.state)
    with pytest.raises(ValueError, match="ordinary Apply"):
        seam.recovery_manifest_digest(entity_id="e", bot_id="b")


def test_recovery_refuses_unrelated_explicit_apply_and_storage_scope():
    from types import SimpleNamespace
    from agentclaw.community.core.bot_config_manifest.bot_config_manifest_service_protocol import MANIFEST_STATE
    from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
    seam, job, applies, _, _ = _recoverable_seam()
    applies.last_apply = lambda **_: SimpleNamespace(status=ApplyStatus.FAILED, trigger="explicit", state=MANIFEST_STATE)
    with pytest.raises(ValueError, match="ordinary Apply"):
        seam.recovery_manifest_digest(entity_id="e", bot_id="b")
    job.payload["spec"]["manifest_binding"]["entity_id"] = "another-owner"
    with pytest.raises(ValueError, match="storage reference"):
        seam.recovery_manifest_digest(entity_id="e", bot_id="b")


def test_failed_bound_recovery_can_be_retried_without_new_creation():
    from types import SimpleNamespace
    seam, _, applies, _, digest = _recoverable_seam()
    previous = applies.last_apply()
    applies.last_apply = lambda **_: SimpleNamespace(status=previous.status, trigger="explicit", state=previous.state)
    assert seam.recovery_manifest_digest(entity_id="e", bot_id="b") == digest


@pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT"])
def test_terminal_job_failure_before_post_container_enqueue_remains_recoverable(status):
    from types import SimpleNamespace
    from agentclaw.community.core.task_queue.types import TaskStatus
    from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
    seam, job, applies, _, digest = _recoverable_seam()
    job.status = TaskStatus(status)
    previous = applies.last_apply()
    applies.last_apply = lambda **_: SimpleNamespace(status=ApplyStatus.SUCCEEDED,
        trigger=CREATE_PRE_CONTAINER_TRIGGER, state=previous.state)
    assert seam.recovery_manifest_digest(entity_id="e", bot_id="b") == digest
