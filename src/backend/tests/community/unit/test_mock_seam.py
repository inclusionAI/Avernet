"""Unit tests for the configurable mock seam (``plugins/local/_mock_seam.py``).

These exercise the seam's own behaviour — override / canned-response / call
recording / async-await handling / property wrapping / the startup hook / the
lazy-state and ``__init_subclass__`` skip branches — which the per-plugin
contract tests never touch (they only hit the no-override fallback path).
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from agentclaw.community.plugin_api.base import Plugin
from agentclaw.community.plugins.local._mock_seam import CallRecord, MockSeam


def _run(coro):
    """Run a coroutine to completion without depending on pytest-asyncio."""
    return asyncio.new_event_loop().run_until_complete(coro)


class SampleProtocol(Plugin, Protocol):
    """A miniature plugin Protocol covering every member kind the seam wraps."""

    LABEL = "label"          # non-callable class attr -> "not callable" skip
    SENTINEL = None          # None-valued attr -> "attr is None" skip

    def sync_m(self, x: int) -> int: ...

    async def async_m(self, x: int) -> int: ...

    @property
    def prop(self) -> str: ...

    @staticmethod
    def static_m() -> str: ...

    @classmethod
    def class_m(cls) -> str: ...


class SampleImpl(MockSeam, SampleProtocol):
    """Concrete impl with real fallback bodies."""

    def sync_m(self, x: int) -> int:
        return x + 1

    async def async_m(self, x: int) -> int:
        return x + 100

    @property
    def prop(self) -> str:
        return "real"

    @staticmethod
    def static_m() -> str:
        return "static"

    @classmethod
    def class_m(cls) -> str:
        return "class"


# Defining a subclass of an already-wrapped impl exercises the
# ``_WRAPPED_FLAG`` "already wrapped higher in the MRO" skip branch.
class SubImpl(SampleImpl):
    pass


# A MockSeam subclass with no Plugin base exercises the "intermediate
# subclass / protocol is None" early-return branch.
class IntermediateSeam(MockSeam):
    pass


# ----------------------------- sync method --------------------------------

def test_sync_fallback_records_and_returns_original():
    impl = SampleImpl()
    assert impl.sync_m(1) == 2
    assert impl.calls == [CallRecord("sync_m", (1,), {})]


def test_sync_override_takes_precedence():
    impl = SampleImpl()
    impl.set_override("sync_m", lambda x: x * 10)
    assert impl.sync_m(2) == 20


def test_sync_canned_response_used_when_no_override():
    impl = SampleImpl()
    impl.set_response("sync_m", 999)
    assert impl.sync_m(2) == 999


# ----------------------------- async method -------------------------------

def test_async_fallback():
    impl = SampleImpl()
    assert _run(impl.async_m(1)) == 101


def test_async_override_plain_value():
    impl = SampleImpl()
    impl.set_override("async_m", lambda x: x + 5)  # sync override on async method
    assert _run(impl.async_m(1)) == 6


def test_async_override_awaitable_is_awaited():
    impl = SampleImpl()

    async def ov(x):
        return x * 3

    impl.set_override("async_m", ov)
    assert _run(impl.async_m(4)) == 12


def test_async_canned_plain_value():
    impl = SampleImpl()
    impl.set_response("async_m", 7)
    assert _run(impl.async_m(1)) == 7


def test_async_canned_awaitable_is_awaited():
    impl = SampleImpl()

    async def canned():
        return 88

    impl.set_response("async_m", canned())  # an awaitable as the canned value
    assert _run(impl.async_m(1)) == 88


# ------------------------------- property ---------------------------------

def test_property_fallback():
    assert SampleImpl().prop == "real"


def test_property_override():
    impl = SampleImpl()
    impl.set_override("prop", lambda: "overridden")
    assert impl.prop == "overridden"


def test_property_canned_response():
    impl = SampleImpl()
    impl.set_response("prop", "canned")
    assert impl.prop == "canned"


# --------------------------- staticmethod/classmethod ---------------------

def test_static_and_classmethods_are_not_intercepted():
    # They are skipped by the seam, so they behave as plain (unrecorded).
    impl = SampleImpl()
    assert impl.static_m() == "static"
    assert impl.class_m() == "class"
    assert impl.calls_to("static_m") == []


# ----------------------------- public test API ----------------------------

def test_calls_and_calls_to_filtering():
    impl = SampleImpl()
    impl.sync_m(1)
    _run(impl.async_m(1))
    impl.sync_m(2)
    assert len(impl.calls) == 3
    assert [c.args for c in impl.calls_to("sync_m")] == [(1,), (2,)]


def test_setters_return_self_for_chaining():
    impl = SampleImpl()
    assert impl.set_override("sync_m", lambda x: x) is impl
    assert impl.set_response("sync_m", 1) is impl


def test_clear_override_restores_fallback():
    impl = SampleImpl()
    impl.set_override("sync_m", lambda x: -1)
    assert impl.sync_m(5) == -1
    impl.clear_override("sync_m")
    assert impl.sync_m(5) == 6


def test_clear_response_restores_fallback():
    impl = SampleImpl()
    impl.set_response("sync_m", -1)
    assert impl.sync_m(5) == -1
    impl.clear_response("sync_m")
    assert impl.sync_m(5) == 6


def test_reset_mock_clears_everything():
    impl = SampleImpl()
    impl.set_override("sync_m", lambda x: -1)
    impl.set_response("async_m", 0)
    impl.sync_m(1)
    impl.reset_mock()
    assert impl.calls == []
    assert impl.sync_m(1) == 2          # override gone
    assert _run(impl.async_m(1)) == 101  # response gone


# ----------------------------- startup hook -------------------------------

def test_install_default_mocks_hook_runs_at_construction():
    class WithDefaults(MockSeam, SampleProtocol):
        def sync_m(self, x: int) -> int:
            return x

        async def async_m(self, x: int) -> int:
            return x

        @property
        def prop(self) -> str:
            return "real"

        @staticmethod
        def static_m() -> str:
            return "s"

        @classmethod
        def class_m(cls) -> str:
            return "c"

        def install_default_mocks(self) -> None:
            self.set_response("sync_m", 42)

    impl = WithDefaults()
    assert impl.sync_m(1) == 42  # default applied without any test-time setup


# ----------------------------- lazy state ---------------------------------

def test_seam_state_is_recreated_lazily_if_missing():
    impl = SampleImpl()
    # Simulate a path where __new__ did not seed the state.
    del impl.__dict__["_mock_seam_state"]
    assert impl.calls == []          # access recreates state lazily
    assert impl.sync_m(1) == 2


# ------------------ __init_subclass__ skip branches -----------------------

def test_subclass_of_wrapped_impl_does_not_double_wrap():
    impl = SubImpl()
    assert impl.sync_m(1) == 2
    # Recorded exactly once (not twice), proving no double-wrapping.
    assert impl.calls == [CallRecord("sync_m", (1,), {})]


def test_intermediate_seam_without_protocol_is_constructible():
    # Its creation already exercised the protocol-is-None early return.
    assert isinstance(IntermediateSeam(), IntermediateSeam)
