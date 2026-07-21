"""Unit tests for PluginAccessor — lazy plugin loading, caching, and override."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gateway.community.plugin_accessor import PluginAccessor


class FakePlugin:
    """Simple plugin stub for testing."""

    def __init__(self, label: str = "fake") -> None:
        self.label = label


def _make_fallback(label: str = "fallback") -> FakePlugin:
    return FakePlugin(label)


class TestPluginAccessorGet:
    def test_returns_fallback_in_bare_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GATEWAY_RUN_MODE", raising=False)
        accessor = PluginAccessor[FakePlugin](
            "gateway.test", lambda: _make_fallback("bare")
        )
        plugin = accessor.get()
        assert isinstance(plugin, FakePlugin)
        assert plugin.label == "bare"

    def test_caches_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GATEWAY_RUN_MODE", raising=False)
        call_count = 0

        def counting_fallback() -> FakePlugin:
            nonlocal call_count
            call_count += 1
            return FakePlugin()

        accessor = PluginAccessor[FakePlugin]("gateway.test", counting_fallback)
        p1 = accessor.get()
        p2 = accessor.get()
        assert p1 is p2
        assert call_count == 1

    def test_set_overrides_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GATEWAY_RUN_MODE", raising=False)
        accessor = PluginAccessor[FakePlugin]("gateway.test", lambda: _make_fallback())
        original = accessor.get()
        replacement = FakePlugin("replacement")
        accessor.set(replacement)
        assert accessor.get() is replacement
        assert accessor.get() is not original

    def test_set_then_get_returns_same(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GATEWAY_RUN_MODE", raising=False)
        accessor = PluginAccessor[FakePlugin]("gateway.test", lambda: _make_fallback())
        custom = FakePlugin("custom")
        accessor.set(custom)
        assert accessor.get() is custom

    def test_sofa_mode_loads_entry_point(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_RUN_MODE", "sofa")

        def fake_entry_points(group: str):
            class _EP:
                name = "sofa"

                @staticmethod
                def load() -> type[FakePlugin]:
                    return lambda: FakePlugin("sofa-loaded")

            if group == "gateway.test":
                return [_EP()]
            return []

        with patch(
            "gateway.community.plugin_accessor.entry_points",
            side_effect=fake_entry_points,
        ):
            accessor = PluginAccessor[FakePlugin](
                "gateway.test", lambda: _make_fallback()
            )
            plugin = accessor.get()
            assert plugin.label == "sofa-loaded"

    def test_sofa_mode_no_matching_entry_point_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GATEWAY_RUN_MODE", "sofa")

        def fake_entry_points(group: str):
            class _EP:
                name = "other"

                @staticmethod
                def load() -> type[FakePlugin]:
                    return lambda: FakePlugin("should-not-load")

            if group == "gateway.test":
                return [_EP()]
            return []

        with patch(
            "gateway.community.plugin_accessor.entry_points",
            side_effect=fake_entry_points,
        ):
            accessor = PluginAccessor[FakePlugin](
                "gateway.test", lambda: _make_fallback("fb")
            )
            plugin = accessor.get()
            assert plugin.label == "fb"

    def test_sofa_mode_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GATEWAY_RUN_MODE", "SOFA")

        def fake_entry_points(group: str):
            class _EP:
                name = "sofa"

                @staticmethod
                def load() -> type[FakePlugin]:
                    return lambda: FakePlugin("sofa-ci")

            if group == "gateway.test":
                return [_EP()]
            return []

        with patch(
            "gateway.community.plugin_accessor.entry_points",
            side_effect=fake_entry_points,
        ):
            accessor = PluginAccessor[FakePlugin](
                "gateway.test", lambda: _make_fallback()
            )
            plugin = accessor.get()
            assert plugin.label == "sofa-ci"

    def test_bare_mode_does_not_query_entry_points(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GATEWAY_RUN_MODE", raising=False)

        def should_not_be_called(group: str):
            raise AssertionError("entry_points should not be called in bare mode")

        with patch(
            "gateway.community.plugin_accessor.entry_points",
            side_effect=should_not_be_called,
        ):
            accessor = PluginAccessor[FakePlugin](
                "gateway.test", lambda: _make_fallback()
            )
            assert accessor.get().label == "fallback"
