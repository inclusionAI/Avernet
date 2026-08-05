"""Self-tests for the DI substitution helpers.

Many endpoint cases now depend on :mod:`di_seams` for the branches no request
can reach, so the properties that make it safer than a patch — the production
class stays untouched, the stand-in keeps the wired collaborators, and every
other method still runs for real — are pinned here rather than assumed.
"""
from __future__ import annotations

import pytest
from injector import Injector

from tests.community.framework.di_seams import (
    bind_failing_method,
    bind_method,
    bind_overrides,
)
from tests.community.framework.world import World


class _Service:
    """Stand-in for a wired core service: some state, sync and async methods."""

    def __init__(self, rows: list[str]) -> None:
        self._rows = rows

    def read(self) -> list[str]:
        return list(self._rows)

    def write(self, row: str) -> int:
        self._rows.append(row)
        return len(self._rows)

    async def fetch(self) -> str:
        return "real"


@pytest.fixture
def seam_world() -> World:
    """A tiny injector of our own — deliberately not the framework ``world``
    fixture, so these tests exercise the helpers rather than the app graph."""
    injector = Injector()
    injector.binder.bind(_Service, to=_Service(["seeded"]), scope=None)
    return World(injector)


@pytest.mark.unit
def test_bind_method_replaces_only_the_named_method(seam_world: World) -> None:
    bind_method(seam_world, _Service, "write", lambda _self, _row: -1)

    service = seam_world.get(_Service)
    assert service.write("ignored") == -1
    # Everything else is still the real implementation over the real state.
    assert service.read() == ["seeded"]


@pytest.mark.unit
def test_stand_in_keeps_the_wired_collaborators(seam_world: World) -> None:
    """The stand-in reads the same state the wired instance held."""
    seam_world.get(_Service).write("added")

    bind_method(seam_world, _Service, "fetch", _fetch_stub)

    assert seam_world.get(_Service).read() == ["seeded", "added"]


async def _fetch_stub(_self) -> str:
    return "stubbed"


@pytest.mark.unit
def test_production_class_is_untouched(seam_world: World) -> None:
    """The whole point: no edit escapes to the class every other test shares."""
    bind_failing_method(seam_world, _Service, "read", RuntimeError("boom"))

    assert _Service(["fresh"]).read() == ["fresh"]


@pytest.mark.unit
def test_bind_failing_method_raises_the_given_error(seam_world: World) -> None:
    bind_failing_method(seam_world, _Service, "read", RuntimeError("ledger unavailable"))

    with pytest.raises(RuntimeError, match="ledger unavailable"):
        seam_world.get(_Service).read()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_async_methods_stay_awaitable(seam_world: World) -> None:
    bind_method(seam_world, _Service, "fetch", _fetch_stub)

    assert await seam_world.get(_Service).fetch() == "stubbed"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bind_failing_method_on_async_raises_when_awaited(seam_world: World) -> None:
    bind_failing_method(seam_world, _Service, "fetch", ValueError("upstream down"))

    with pytest.raises(ValueError, match="upstream down"):
        await seam_world.get(_Service).fetch()


@pytest.mark.unit
def test_bind_overrides_replaces_several_methods_on_one_stand_in(seam_world: World) -> None:
    bind_overrides(
        seam_world,
        _Service,
        {"read": lambda _self: ["stubbed"], "write": lambda _self, _row: 0},
    )

    service = seam_world.get(_Service)
    assert service.read() == ["stubbed"]
    assert service.write("ignored") == 0


@pytest.mark.unit
def test_also_bind_serves_the_same_stand_in_for_every_key(seam_world: World) -> None:
    """A service reached by two keys must be substituted on both paths."""

    class _Protocol:
        pass

    seam_world.injector.binder.bind(_Protocol, to=seam_world.get(_Service), scope=None)
    stand_in = bind_method(
        seam_world,
        _Service,
        "read",
        lambda _self: ["stubbed"],
        also_bind=(_Protocol,),
    )

    assert seam_world.get(_Service) is stand_in
    assert seam_world.get(_Protocol) is stand_in


@pytest.mark.unit
def test_unknown_method_is_rejected(seam_world: World) -> None:
    """A typo must fail here, not silently add a method nothing calls."""
    with pytest.raises(AttributeError, match="no method 'raed'"):
        bind_method(seam_world, _Service, "raed", lambda _self: [])


@pytest.mark.unit
def test_sync_impl_for_an_async_method_is_rejected(seam_world: World) -> None:
    """Otherwise the caller's ``await`` fails far from the mistake."""
    with pytest.raises(TypeError, match="is async"):
        bind_method(seam_world, _Service, "fetch", lambda _self: "not awaitable")
