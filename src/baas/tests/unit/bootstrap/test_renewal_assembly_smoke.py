"""Assembly smoke tests: engine-switch wiring for ARCA TTL renewal.

Rewritten from the enterprise ``test_enterprise_registration.py`` per
D-10' — the override topology is now config-driven (no task registry):

- deadline engine: ``tasks.deadline_renewal_task`` materialises without
  error (proves the ``arca_ttl_schedule_repository`` Dependency has a
  target, T-05.2-19), ``services.device_service`` resolves to the
  schedule-aware wrapper override, and the cron selection callable picks
  the DeadlineRenewalScheduler.
- legacy engine: cron mounts ``device_ttl_timer_task``, device_service
  stays the native DefaultDeviceService (no override, zero cold-table
  writes), and the deadline task still materialises safely with
  ``enabled=False`` (assumption A5).

Only assembly results are asserted (provider products / instance types);
no renewal loop runs and no external service / network / real database
is touched (pure container assembly).
"""

from __future__ import annotations

import pytest

from secbaas.community.bootstrap import ApplicationContainer, set_container
from secbaas.community.core.repository.arca_ttl import (
    OrmTtlRenewalScheduleRepository,
)
from secbaas.community.core.service.device_manage import (
    ArcaScheduleAwareDeviceService,
)
from secbaas.community.core.service.scheduler import (
    DeadlineRenewalScheduler,
    DeviceTtlTimerTask,
)
from tests.utils import load_web_port

# Community e2e-sqlite overlay plugin key set (all stubs) — the minimal
# set the full services/repository chains resolve against.
_PLUGINS = {
    "auth": "stub",
    "crypto": "stub",
    "secret": "stub",
    "scheduler": "stub",
    "cache": "stub",
    "bot_service": "stub",
    "engine_adapter": "stub",
    "file_transfer": "stub",
    "sandbox": {
        "arca": "stub",
        "desktop": "stub",
        "teclaw": "stub",
        "k8s": "stub",
        "docker": "stub",
        "poolab": "stub",
    },
    "database": {
        "plugin_database": "SQLITE_ORM",
        "database_url": "sqlite:///:memory:",
    },
}


@pytest.fixture(autouse=True)
def _isolated_container_state():
    """Save/restore the community bootstrap singleton around each test."""
    import secbaas.community.bootstrap as bootstrap

    original = bootstrap._container
    bootstrap._container = None
    yield
    bootstrap._container = original


def _container_with(engine: str) -> ApplicationContainer:
    """Build an ApplicationContainer with the minimal plugin key set."""
    container = ApplicationContainer()
    config = {
        "web_port": load_web_port(),
        "plugins": _PLUGINS,
        "renewal_scheduler": {"engine": engine},
    }
    container.config.from_dict(config)
    return container


class TestDeadlineEngineAssembly:
    """engine='deadline': deadline task active + wrapper override applied."""

    def test_deadline_task_materialises_with_enabled_true(self):
        """tasks.deadline_renewal_task resolves — Dependency has a target."""
        container = _container_with("deadline")
        set_container(container)

        task = container.tasks().deadline_renewal_task()
        assert isinstance(task, DeadlineRenewalScheduler)
        assert task._config.enabled is True
        assert task._config.engine == "deadline"

    def test_deadline_task_coerces_string_ttl_to_int(self):
        """WR-03: arca.default_ttl_minutes arrives untyped (no
        ArcaConfigSchema) — a quoted YAML number must resolve to an int in
        the scheduler config, not raise TypeError at the first cron run."""
        container = _container_with("deadline")
        container.config.from_dict({"arca": {"default_ttl_minutes": "1440"}})
        set_container(container)

        task = container.tasks().deadline_renewal_task()
        assert task._config.default_ttl_minutes == 1440

    def test_device_service_overridden_with_schedule_aware_wrapper(self):
        """services.device_service resolves to the schedule-aware wrapper."""
        container = _container_with("deadline")
        set_container(container)

        svc = container.services().device_service()
        assert isinstance(svc, ArcaScheduleAwareDeviceService)
        assert isinstance(svc._schedule_repo, OrmTtlRenewalScheduleRepository)
        # Singleton-cached override: repeat resolution is the same object
        assert container.services().device_service() is svc

    def test_wrapper_receives_configured_ttl_for_register_window(self):
        """WR-02: the override wires arca.default_ttl_minutes into the
        wrapper's register lead window — a quoted YAML number ("2880") is
        still coerced and derives a 24h window (half the TTL period)."""
        from datetime import timedelta

        container = _container_with("deadline")
        container.config.from_dict({"arca": {"default_ttl_minutes": "2880"}})
        set_container(container)

        svc = container.services().device_service()
        assert isinstance(svc, ArcaScheduleAwareDeviceService)
        assert svc._renewal_window == timedelta(hours=24)

    def test_wrapper_defaults_to_12h_window_without_arca_section(self):
        """WR-02: overlays without an arca section keep the former
        hardcoded semantics — 1440-minute default -> 12h lead window."""
        from datetime import timedelta

        container = _container_with("deadline")
        set_container(container)

        svc = container.services().device_service()
        assert isinstance(svc, ArcaScheduleAwareDeviceService)
        assert svc._renewal_window == timedelta(hours=12)

    def test_cron_selection_returns_deadline_scheduler(self):
        """The cron task-list selector picks the DeadlineRenewalScheduler."""
        container = _container_with("deadline")
        set_container(container)

        tasks = container.cron_lifecycle.kwargs["tasks"]()
        assert isinstance(tasks[0], DeadlineRenewalScheduler)


class TestLegacyEngineAssembly:
    """engine='legacy': legacy timer mounted + device_service untouched."""

    def test_deadline_task_materialises_disabled(self):
        """Deadline task still materialises safely under legacy (A5)."""
        container = _container_with("legacy")
        set_container(container)

        task = container.tasks().deadline_renewal_task()
        assert isinstance(task, DeadlineRenewalScheduler)
        assert task._config.enabled is False

    def test_cron_mounts_device_ttl_timer_task(self):
        """The cron task-list selector keeps mounting the legacy timer."""
        container = _container_with("legacy")
        set_container(container)

        tasks = container.cron_lifecycle.kwargs["tasks"]()
        assert isinstance(tasks[0], DeviceTtlTimerTask)

    def test_device_service_keeps_native_default(self):
        """No override: the native DefaultDeviceService runs (cold table unused)."""
        container = _container_with("legacy")
        set_container(container)

        svc = container.services().device_service()
        assert type(svc).__name__ == "DefaultDeviceService"
        assert not isinstance(svc, ArcaScheduleAwareDeviceService)
        # Community de-hook: no schedule repository residue on the default
        assert "_schedule_repo" not in type(svc).__dict__
