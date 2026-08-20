"""Unit tests for the generic community task registry (D-05).

Registry semantics pinned here:

- get_cron_task_factories() returns a snapshot copy of name → factory;
  mutating the returned dict must not affect subsequent snapshots.
- Registering the same cron task name twice: last registration wins.
- get_device_service_factories() returns a list copy in registration
  (append) order; mutating the copy must not leak.
- Both getters return empty collections before anything is registered.

The registry keeps module-level state, so each test reloads the module
(importlib.reload) to stay isolated from its siblings.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture(autouse=True)
def _fresh_registry():
    """Reset the task registry module state around each test."""
    import secbaas.community.task_registry as task_registry

    importlib.reload(task_registry)
    yield task_registry


def _factory():
    return object()


class TestCronTaskFactories:
    def test_register_then_get_returns_snapshot_with_factory(self, _fresh_registry):
        """get returns the registered name → factory mapping."""
        _fresh_registry.register_cron_task_factory(
            "deadline_renewal_scheduler", _factory
        )

        snapshot = _fresh_registry.get_cron_task_factories()
        assert snapshot == {"deadline_renewal_scheduler": _factory}

    def test_snapshot_is_a_copy(self, _fresh_registry):
        """Mutating one snapshot does not change a subsequent snapshot."""
        _fresh_registry.register_cron_task_factory(
            "deadline_renewal_scheduler", _factory
        )

        first = _fresh_registry.get_cron_task_factories()
        first["cleared"] = _factory
        first.clear()

        second = _fresh_registry.get_cron_task_factories()
        assert second == {"deadline_renewal_scheduler": _factory}

    def test_same_name_twice_last_registration_wins(self, _fresh_registry):
        """Later registration for a name replaces the earlier factory."""
        first_factory = _factory
        second_factory = _factory

        _fresh_registry.register_cron_task_factory(
            "deadline_renewal_scheduler", first_factory
        )
        _fresh_registry.register_cron_task_factory(
            "deadline_renewal_scheduler", second_factory
        )

        snapshot = _fresh_registry.get_cron_task_factories()
        assert snapshot is not first_factory
        assert snapshot["deadline_renewal_scheduler"] is second_factory

    def test_empty_when_nothing_registered(self, _fresh_registry):
        """Empty dict before any registration."""
        assert _fresh_registry.get_cron_task_factories() == {}


class TestDeviceServiceFactories:
    def test_append_order_preserved(self, _fresh_registry):
        """get returns factories in registration order."""
        factories = [_factory for _ in range(3)]
        for factory in factories:
            _fresh_registry.register_device_service_factory(factory)

        snapshot = _fresh_registry.get_device_service_factories()
        assert snapshot == factories

    def test_snapshot_list_is_a_copy(self, _fresh_registry):
        """Mutating the returned list does not leak into the registry."""
        _fresh_registry.register_device_service_factory(_factory)

        first = _fresh_registry.get_device_service_factories()
        first.clear()
        first.append(_factory)

        second = _fresh_registry.get_device_service_factories()
        assert len(second) == 1
        assert second[0] is _factory

    def test_empty_when_nothing_registered(self, _fresh_registry):
        """Empty list before any registration."""
        assert _fresh_registry.get_device_service_factories() == []
