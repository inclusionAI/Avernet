"""Plugin impl registry — populated at import time by ``@plugin_impl``.

The ``@plugin_impl`` decorator records each plugin implementation class
along with its Protocol, mode (local/prod), and (for local impls) Rule
21 isolation ``Flavor``. The arch test
``tests/architecture/test_plugin_pairing.py`` imports every module under
``plugins/{local,prod}/`` to populate the registry, then asserts:

- Rule 20: every Protocol has ≥1 local + ≥1 prod entry.
- Rule 21: every local entry has a ``flavor`` from the allowed set.

Decorator validation runs at import time, so a misconfigured impl fails
the moment its module is imported (not later, in a test).

The Protocol is auto-detected from the impl class's ``__mro__`` — the
unique direct base that's a ``Plugin`` subclass. This forces nominal
inheritance ("class LocalAuth(AuthPlugin):") and removes a class of
"liar" annotations (claiming protocol X while actually implementing Y).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentclaw.community.plugin_api.base import Plugin


class Mode(StrEnum):
    LOCAL = "local"
    PROD = "prod"


class Flavor(StrEnum):
    NOOP = "noop"
    FAKE = "fake"
    STUB = "stub"
    MOCK = "mock"
    SIMULATOR = "simulator"
    FIXTURE_BACKED = "fixture-backed"


@dataclass(frozen=True)
class ImplEntry:
    cls: type
    protocol: type           # auto-detected Plugin subclass from cls bases
    mode: Mode
    flavor: Flavor | None    # required iff mode is LOCAL
    rationale: str


IMPL_REGISTRY: list[ImplEntry] = []
_DECORATED_CLASSES: set[type] = set()


def _detect_protocol(cls: type) -> type:
    """Return the Plugin Protocol that ``cls`` implements.

    Forces every impl to nominally inherit its Protocol (directly or via
    a single Plugin chain). Removes ``protocol=`` from the decorator
    surface — one less thing to lie about, one less thing to keep in sync.

    Resolution rules:
    1. The class's direct bases must have exactly one Plugin subclass
       (concrete impl or Protocol). Multiple → ambiguity error.
    2. If that base is itself a genuine ``Protocol`` (``_is_protocol``),
       return it.
    3. Else walk its MRO and return the nearest Plugin **Protocol** — this
       lets a local impl inherit a prod impl
       (``LocalX(MockSeam, ProdX)`` where ``ProdX`` inherits ``XProtocol``)
       and still register against ``XProtocol``.
    """
    candidates = [
        b for b in cls.__bases__
        if isinstance(b, type) and b is not Plugin and Plugin in b.__mro__
    ]
    if not candidates:
        raise TypeError(
            f"{cls.__name__}: must directly inherit a Plugin subclass "
            f"(no Plugin Protocol found in __bases__)"
        )
    if len(candidates) > 1:
        names = ", ".join(c.__name__ for c in candidates)
        raise TypeError(
            f"{cls.__name__}: ambiguous Plugin parents ({names}); "
            f"a plugin impl must inherit exactly one Plugin Protocol"
        )
    base = candidates[0]
    # Common case: base is itself a Plugin Protocol — return it directly.
    if getattr(base, "_is_protocol", False):
        return base
    # Otherwise (base is a concrete prod impl), walk its MRO to find the
    # closest Plugin Protocol it ultimately implements.
    for ancestor in base.__mro__:
        if ancestor is base or ancestor is Plugin or ancestor is object:
            continue
        if getattr(ancestor, "_is_protocol", False) and Plugin in ancestor.__mro__:
            return ancestor
    raise TypeError(
        f"{cls.__name__}: base {base.__name__} is a Plugin subclass but "
        f"no Plugin Protocol found in its MRO"
    )


def plugin_impl(
    *,
    mode: Mode,
    flavor: Flavor | None = None,
    rationale: str = "",
):
    """Decorator: record an impl class against its Protocol.

    The Protocol is auto-detected from ``cls.__bases__``. Local impls
    MUST declare a ``flavor``; prod impls MUST NOT. At most one
    ``@plugin_impl`` per class. All validated at decoration time so
    misuse surfaces during import, not later in a test.
    """
    if mode is Mode.LOCAL and flavor is None:
        raise ValueError(
            f"plugin_impl: mode={Mode.LOCAL} requires a flavor"
        )
    if mode is Mode.PROD and flavor is not None:
        raise ValueError(
            f"plugin_impl: mode={Mode.PROD} must not set flavor"
        )

    def decorate(cls):
        if cls in _DECORATED_CLASSES:
            raise TypeError(
                f"{cls.__name__}: @plugin_impl already applied "
                f"(at most one annotation per class)"
            )
        protocol = _detect_protocol(cls)
        _DECORATED_CLASSES.add(cls)
        IMPL_REGISTRY.append(
            ImplEntry(
                cls=cls,
                protocol=protocol,
                mode=mode,
                flavor=flavor,
                rationale=rationale,
            )
        )
        return cls

    return decorate
