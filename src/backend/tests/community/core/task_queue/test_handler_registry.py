"""Unit tests for HandlerRegistry."""
import pytest

from agentclaw.community.core.task_queue.services.registry import HandlerRegistry, TaskHandler
from agentclaw.community.core.task_queue.types import Complete, Reschedule


class _Handler:
    def __init__(self, task_type, outcome=Complete()):
        self._task_type = task_type
        self._outcome = outcome

    @property
    def task_type(self):
        return self._task_type

    def handle(self, payload):
        return self._outcome


def test_register_then_get_returns_handler():
    reg = HandlerRegistry()
    h = _Handler("poll_publish")
    reg.register(h)
    assert reg.get("poll_publish") is h


def test_get_unregistered_returns_none():
    assert HandlerRegistry().get("missing") is None


def test_duplicate_task_type_raises():
    reg = HandlerRegistry()
    reg.register(_Handler("dup"))
    with pytest.raises(ValueError, match="already has a handler"):
        reg.register(_Handler("dup"))


def test_distinct_task_types_coexist():
    reg = HandlerRegistry()
    a = _Handler("a", Complete())
    b = _Handler("b", Reschedule(5))
    reg.register(a)
    reg.register(b)
    assert reg.get("a") is a and reg.get("b") is b


@pytest.mark.parametrize("variant", ["Job", "JOB", "jOb"])
def test_task_type_colliding_only_by_case_is_rejected(variant):
    """``task_type`` is a scope column of the dedup index, so two types the
    index cannot separate share one dedup slot and a keyed enqueue for one would
    silently join the other's task. The schema pins ``utf8mb4_bin`` to settle
    this across processes; the registry check adds a loud startup failure."""
    reg = HandlerRegistry()
    reg.register(_Handler("job"))
    with pytest.raises(ValueError, match="only by case"):
        reg.register(_Handler(variant))
    assert reg.get(variant) is None  # and it was not registered


@pytest.mark.parametrize("task_type", ["job ", " job", " job ", "job\t", "\njob"])
def test_task_type_with_surrounding_whitespace_is_rejected_outright(task_type):
    """Absolute, not pairwise — and that distinction is the whole point.

    ``utf8mb4_bin`` is PAD SPACE, so 'job ' and 'job' are one index entry and
    the collation cannot fix it. A *pairwise* check ("does this fold onto
    something already registered?") is blind across processes: a rolling deploy
    renaming ``job`` to ``job `` leaves each version's registry holding only its
    own spelling, so neither sees a collision while the index merges them.

    Rejecting the padding outright holds no matter what any other process
    registered — hence no prior ``register`` call here. This test would pass
    vacuously against a pairwise implementation, so the empty registry is
    load-bearing."""
    reg = HandlerRegistry()
    with pytest.raises(ValueError, match="leading or trailing whitespace"):
        reg.register(_Handler(task_type))
    assert reg.get(task_type) is None


def test_exact_duplicate_still_reports_as_a_duplicate_not_a_near_collision():
    """An exact repeat folds to the same key too, so the order of the two
    checks matters — the plain-duplicate message must win."""
    reg = HandlerRegistry()
    reg.register(_Handler("job"))
    with pytest.raises(ValueError, match="already has a handler"):
        reg.register(_Handler("job"))


def test_internal_spacing_is_untouched():
    """Only the *ends* are constrained. A type is never rewritten, and one with
    interior spacing is a distinct index value on every engine."""
    reg = HandlerRegistry()
    handler = _Handler("legacy job.poll")
    reg.register(handler)
    assert reg.get("legacy job.poll") is handler


def test_real_task_type_constants_do_not_collide_pairwise():
    """The guard only helps if the shipped task types satisfy it. Registering
    every real constant into one registry is the same check the app performs at
    startup, so a future name that folds onto an existing one fails here rather
    than at boot — and the constants are imported, not restated, so a rename
    cannot silently drop out of this list."""
    from agentclaw.community.core.bot_management.services import (
        teclaw_publish_task_handler as teclaw,
    )
    from agentclaw.community.core.devices.services import (
        baas_publish_task_handlers as baas,
    )
    from agentclaw.community.core.service_bot.services.publish_flow import tasks
    from agentclaw.community.core.skills_pool import quarantine, reconcile_task

    modules = (tasks, baas, teclaw, quarantine, reconcile_task)
    task_types = sorted(
        {
            value
            for module in modules
            for name, value in vars(module).items()
            if name.endswith("_TASK") and isinstance(value, str)
        }
    )
    assert len(task_types) >= 14, f"expected the full set, found {task_types}"

    reg = HandlerRegistry()
    for task_type in task_types:
        reg.register(_Handler(task_type))  # raises if any pair folds together


def test_plain_object_satisfies_taskhandler_protocol():
    # No base class required — structural typing.
    assert isinstance(_Handler("x"), TaskHandler)
