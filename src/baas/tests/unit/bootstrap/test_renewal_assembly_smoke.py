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
    "secret": "stub",
    "cache": "stub",
    "bot_service": "stub",
    "engine_adapter": "stub",
    "file_transfer": "stub",
    "database": "sqlite",
    "eval_env": "stub",
    "sandbox": {
        "arca": "stub",
        "desktop": "stub",
        "k8s": "stub",
        "docker": "stub",
        "poolab": "stub",
    },
    "bot": {
        "teclaw": "stub",
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
        "database": {
            "database_url": "sqlite:///:memory:",
            "create_schema": True,
            "seed_data": True,
        },
        "renewal_scheduler": {"engine": engine},
        "bot_health_checker": {
            "health_check": {"timeout_seconds": 10, "max_concurrent": 10},
            "ttl": {"extend_when_remaining_hours": 16, "target_ttl_hours": 24},
        },
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

    def test_post_extend_tol_wired_from_renewal_scheduler_config(self):
        """WR-01: a YAML-set post_extend_consistency_tol_minutes reaches the
        scheduler config — a non-default value (2) must land on the
        dataclass (silent-config-drift guard on the D-01 tolerance knob)."""
        container = _container_with("deadline")
        container.config.from_dict(
            {"renewal_scheduler": {"post_extend_consistency_tol_minutes": 2}}
        )
        set_container(container)

        task = container.tasks().deadline_renewal_task()
        assert task._config.post_extend_consistency_tol_minutes == 2

    def test_post_extend_tol_defaults_to_5_without_key(self):
        """WR-01: without the YAML key the tolerance keeps the locked
        5-minute default (D-01 code-only default remains intact)."""
        container = _container_with("deadline")
        set_container(container)

        task = container.tasks().deadline_renewal_task()
        assert task._config.post_extend_consistency_tol_minutes == 5

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


class TestEg4ThresholdConsistency:
    """EG-4 bootstrap fail-fast: renew_threshold_hours vs arca.default_ttl_minutes.

    Three assembly states at DeadlineRenewalSchedulerConfig materialisation:
    mismatch raises ValueError (startup tripwire), an absent threshold key is
    tolerated (12-hour default, no assertion), and consistent values resolve.
    """

    def test_mismatched_renew_threshold_raises_at_resolution(self):
        """An explicit threshold inconsistent with the TTL period fails at
        resolution — 12h vs "2880" (24h) must raise ValueError, never a
        half-wired config (threshold != half the TTL period, EG-4)."""
        container = _container_with("deadline")
        container.config.from_dict(
            {
                "renewal_scheduler": {
                    "engine": "deadline",
                    "renew_threshold_hours": 12,
                },
                "arca": {"default_ttl_minutes": "2880"},
            }
        )
        set_container(container)

        with pytest.raises(ValueError):
            container.tasks().deadline_renewal_task()

    def test_absent_renew_threshold_tolerated_with_default(self):
        """A missing threshold key resolves with the 12-hour default and the
        1440-minute TTL fallback — the None-tolerant path keeps minimal
        containers (only the engine key) assembling."""
        container = _container_with("deadline")
        set_container(container)

        task = container.tasks().deadline_renewal_task()
        assert task._config.renew_threshold_hours == 12
        assert task._config.default_ttl_minutes == 1440

    def test_explicit_threshold_without_arca_section_raises(self):
        """WR-02: an explicit threshold is checked against the effective TTL
        even when the arca section is absent — 8h vs the 1440-minute
        fallback (12h half-period) raises instead of silently reverting the
        tuned threshold to 12h with a dead knob."""
        container = _container_with("deadline")
        container.config.from_dict(
            {
                "renewal_scheduler": {
                    "engine": "deadline",
                    "renew_threshold_hours": 8,
                },
            }
        )
        set_container(container)

        with pytest.raises(ValueError):
            container.tasks().deadline_renewal_task()

    def test_explicit_12h_threshold_without_arca_section_resolves(self):
        """WR-02 boundary: an explicit 12h threshold stays consistent with
        the 1440-minute fallback when no arca section exists — every
        in-repo overlay carries renew_threshold_hours: 12 without an arca
        section, so this quadrant must assemble."""
        container = _container_with("deadline")
        container.config.from_dict(
            {
                "renewal_scheduler": {
                    "engine": "deadline",
                    "renew_threshold_hours": 12,
                },
            }
        )
        set_container(container)

        task = container.tasks().deadline_renewal_task()
        assert task._config.renew_threshold_hours == 12
        assert task._config.default_ttl_minutes == 1440

    def test_consistent_renew_threshold_resolves(self):
        """12h vs "1440" (string-coerced) resolves — threshold*60 ==
        default_ttl_minutes//2 holds (WR-03 coercion chain reused)."""
        container = _container_with("deadline")
        container.config.from_dict(
            {
                "renewal_scheduler": {
                    "engine": "deadline",
                    "renew_threshold_hours": 12,
                },
                "arca": {"default_ttl_minutes": "1440"},
            }
        )
        set_container(container)

        task = container.tasks().deadline_renewal_task()
        assert task._config.renew_threshold_hours == 12
