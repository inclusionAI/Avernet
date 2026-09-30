"""Shared configurable-mock seam for ``plugins/local/*`` impls.

This module provides **one** mixin — :class:`MockSeam` — that turns any
local plugin impl into a configurable mock **additively**: tests can
override per-method behaviour *before the DI container starts up*, while
the impl's current behaviour is preserved verbatim as the no-override
fallback. The same impls are what ``--local`` dev mode runs on, so the
real in-memory fakes (``SqliteDB``, ``MemoryCachePlugin``,
``LocalFsStorage``, ``InMemoryDeviceAdapterTransport``, …) keep working
exactly as before; pure-noop impls keep returning their noop defaults.

Design
------

``MockSeam`` is **not** a ``Plugin`` subclass and **not** a Protocol, so
it is invisible to ``impl_registry._detect_protocol`` (which only
considers ``cls.__bases__`` entries that are ``Plugin`` subclasses). The
established ``SqliteDB(DatabasePlugin, LifecycleBase)`` pattern already
mixes a non-Plugin base in, so this is the proven shape.

At **class creation** (``__init_subclass__``), the seam detects the
served Protocol the same way the registry does — the unique direct
``Plugin``-subclass base — and wraps each *public Protocol method* the
impl actually defines so that, at call time, it:

  1. records the invocation into ``self.calls``;
  2. if a per-method **override callable** was registered, calls it
     (awaiting the result if it is awaitable, for async methods);
  3. else if a per-method **canned value** was registered in
     ``responses``, returns it (wrapping in a coroutine for async
     methods);
  4. else falls through to the impl's **own original method** — the
     current ``--local`` behaviour, byte-for-byte.

State (``calls`` / ``overrides`` / ``responses``) is created **lazily**
on first access, so impls with a custom ``__init__`` (even ones with
required injected ctor args, like ``LocalDeviceAccessor`` /
``LocalHealthProbe``) do not need to cooperate with any
``super().__init__()`` ordering. The only construction-time work is a
single call to :meth:`install_default_mocks` — an empty, overridable
hook where global/default mock behaviours can be coded in later.

Properties, ``@contextmanager`` methods, ``@staticmethod`` /
``@classmethod``, and dunders are handled correctly:

  - **Properties** are wrapped as properties (override/response/fallback
    on the getter), preserving attribute-access semantics.
  - Plain functions decorated with ``@contextmanager`` (whose result is
    already the value callers use, e.g. ``with db.session() as s:``)
    pass straight through the same resolve path — the *override* /
    *response*, when set, replaces the whole returned object, so a test
    may hand back its own context manager.
  - dunders and non-Protocol helpers are never wrapped.

Public test API
---------------

Before DI builds the injector, a test constructs the impl (or the
testing module does) and configures it::

    cache = MemoryCachePlugin()
    cache.set_override("get", lambda key: "forced")     # callable override
    cache.set_response("get_json", {"k": "v"})          # canned value
    # ... build injector binding this instance ...
    cache.calls                                         # -> list[CallRecord]
    cache.reset_mock()                                  # clear all three

For async methods the override may be sync or async; a canned response is
returned as-is (awaited transparently). ``calls`` is a list of
:class:`CallRecord` ``(method, args, kwargs)`` in invocation order;
``calls_to("get")`` filters by method name.
"""
from __future__ import annotations

import functools
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

from agentclaw.community.plugin_api.base import Plugin

# Sentinel marking "no per-method response registered" (distinct from a
# legitimately-registered ``None`` response).
_UNSET: Any = object()

# Attribute set on a wrapper so a class is never double-wrapped (e.g. if a
# subclass re-declares the seam in its MRO).
_WRAPPED_FLAG = "__mock_seam_wrapped__"


@dataclass
class CallRecord:
    """One recorded invocation of a wrapped Protocol member."""

    method: str
    args: tuple
    kwargs: dict


@dataclass
class _SeamState:
    """Per-instance mock state, created lazily on first access."""

    overrides: dict[str, Callable[..., Any]] = field(default_factory=dict)
    responses: dict[str, Any] = field(default_factory=dict)
    calls: list[CallRecord] = field(default_factory=list)


def _detect_served_protocol(cls: type) -> type | None:
    """Return the unique direct ``Plugin``-subclass base, or ``None``.

    Mirrors ``impl_registry._detect_protocol`` but is tolerant: an
    intermediate seam subclass (one that does not itself name a Protocol
    base) returns ``None`` rather than raising, so the final concrete
    impl is the one that triggers wrapping.
    """
    candidates = [
        b
        for b in cls.__bases__
        if isinstance(b, type) and b is not Plugin and Plugin in b.__mro__
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _protocol_member_names(protocol: type) -> set[str]:
    """Public member names declared on the served Protocol (no dunders).

    Walks the MRO so members declared on intermediate Protocols are
    included, but collects ONLY from genuine ``Protocol`` classes. This
    matters when an impl is itself subclassed: ``_detect_served_protocol``
    then resolves the *base impl* (a concrete class) as the "protocol", and
    without this filter the impl's own helpers and — worse — the
    :class:`MockSeam` public API (``calls``, ``set_override`` …) would be
    treated as wrap targets. Restricting to ``_is_protocol`` classes keeps
    the wrap surface to the real Protocol regardless of nesting.
    """
    names: set[str] = set()
    for klass in protocol.__mro__:
        if klass in (object, Plugin):
            continue
        # Only genuine Protocol classes contribute the wrappable surface.
        if not getattr(klass, "_is_protocol", False):
            continue
        for name in vars(klass):
            # Skip dunders and any private/Protocol-internal attribute
            # (``_abc_impl``, ``_is_protocol``, ``_is_runtime_protocol``,
            # ``_callable_members`` …). Plugin Protocol methods are all
            # public, so a leading underscore is never a wrap target.
            if name.startswith("_"):
                continue
            names.add(name)
    return names


def _make_method_wrapper(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a sync function with the resolve(override→response→fallback) path."""

    @functools.wraps(original)
    def wrapper(self, *args, **kwargs):
        state = MockSeam._seam_state(self)
        state.calls.append(CallRecord(name, args, kwargs))
        if name in state.overrides:
            return state.overrides[name](*args, **kwargs)
        canned = state.responses.get(name, _UNSET)
        if canned is not _UNSET:
            return canned
        return original(self, *args, **kwargs)

    return wrapper


def _make_async_method_wrapper(
    name: str, original: Callable[..., Any]
) -> Callable[..., Any]:
    """Wrap a coroutine function; awaits awaitable overrides/responses."""

    @functools.wraps(original)
    async def wrapper(self, *args, **kwargs):
        state = MockSeam._seam_state(self)
        state.calls.append(CallRecord(name, args, kwargs))
        if name in state.overrides:
            result = state.overrides[name](*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result
        canned = state.responses.get(name, _UNSET)
        if canned is not _UNSET:
            if inspect.isawaitable(canned):
                return await canned
            return canned
        return await original(self, *args, **kwargs)

    return wrapper


def _make_property_wrapper(name: str, original: property) -> property:
    """Wrap a property getter through the resolve path (recorded read)."""
    fget = original.fget
    if fget is None:  # pragma: no cover - properties without getters are unused here
        return original

    @functools.wraps(fget)
    def getter(self):
        state = MockSeam._seam_state(self)
        state.calls.append(CallRecord(name, (), {}))
        if name in state.overrides:
            return state.overrides[name]()
        canned = state.responses.get(name, _UNSET)
        if canned is not _UNSET:
            return canned
        return fget(self)

    # Preserve setter/deleter so attribute semantics are unchanged.
    return property(getter, original.fset, original.fdel, original.__doc__)


class MockSeam:
    """Additive configurable-mock mixin for ``plugins/local/*`` impls.

    Inherit it *first* alongside the Protocol, e.g.::

        @plugin_impl(mode=Mode.LOCAL, flavor=Flavor.FAKE, rationale="...")
        class MemoryCachePlugin(MockSeam, CachePlugin):
            ...

    Everything else (methods, ``__init__``, other mixins like
    ``LifecycleBase``) is unchanged. ``MockSeam`` is not a ``Plugin``
    subclass, so ``_detect_protocol`` still resolves the Protocol from
    the impl's ``CachePlugin`` base.
    """

    # ---- class-creation: auto-wrap the served Protocol's members ----

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        protocol = _detect_served_protocol(cls)
        if protocol is None:
            # Intermediate subclass that does not yet name a Protocol
            # base (rare). The concrete impl further down will trigger
            # wrapping.
            return
        member_names = _protocol_member_names(protocol)
        for name in member_names:
            # Resolve the attribute as the impl class actually provides
            # it (could be defined on the impl, or inherited). We wrap a
            # copy on ``cls`` so the original stays reachable via
            # ``original``-closure; we never mutate a base class.
            attr = getattr(cls, name, None)
            if attr is None:
                # Method declared on the Protocol but not implemented by
                # the impl (e.g. StoragePlugin's empty surface) — nothing
                # to wrap.
                continue
            if getattr(attr, _WRAPPED_FLAG, False):
                continue  # already wrapped higher in the MRO

            if isinstance(attr, property):
                wrapped: Any = _make_property_wrapper(name, attr)
                # property objects can't carry our flag; mark the getter.
                if wrapped.fget is not None:
                    setattr(wrapped.fget, _WRAPPED_FLAG, True)
                setattr(cls, name, wrapped)
                continue

            # staticmethod / classmethod: leave untouched (not bound
            # Protocol behaviour the seam intercepts).
            raw = inspect.getattr_static(cls, name, None)
            if isinstance(raw, (staticmethod, classmethod)):
                continue

            if not callable(attr):
                continue

            if inspect.iscoroutinefunction(attr):
                wrapped = _make_async_method_wrapper(name, attr)
            else:
                wrapped = _make_method_wrapper(name, attr)
            setattr(wrapped, _WRAPPED_FLAG, True)
            setattr(cls, name, wrapped)

    # ---- construction-time default hook (empty, overridable) ----

    def __new__(cls, *args, **kwargs):
        # Use ``__new__`` so install_default_mocks runs once at
        # construction WITHOUT requiring impls to call super().__init__()
        # in any particular order — crucial for impls with required
        # injected ctor args that don't chain to MockSeam.__init__.
        obj = super().__new__(cls)
        # Eagerly create seam state + run the default hook exactly once.
        object.__setattr__(obj, "_mock_seam_state", _SeamState())
        obj.install_default_mocks()
        return obj

    def install_default_mocks(self) -> None:
        """Overridable, **empty by default** startup hook.

        Invoked exactly once at construction. Subclasses (or a future
        global default) may register baseline overrides/responses here::

            def install_default_mocks(self) -> None:
                self.set_response("get_secret", "default-secret")

        Default impls leave this empty so behaviour is unchanged.
        """

    # ---- internal: lazy state accessor (also resilient if __new__ skipped) ----

    @staticmethod
    def _seam_state(self: "MockSeam") -> _SeamState:
        state = self.__dict__.get("_mock_seam_state")
        if state is None:
            state = _SeamState()
            object.__setattr__(self, "_mock_seam_state", state)
        return state

    # ---- public test API (set BEFORE DI builds the injector) ----

    def set_override(self, method: str, fn: Callable[..., Any]) -> "MockSeam":
        """Register a per-method override callable (sync or async).

        The callable receives the same positional/keyword args the caller
        passed (it does *not* receive ``self``). For an async Protocol
        method, an awaitable return value is awaited transparently.
        """
        self._seam_state(self).overrides[method] = fn
        return self

    def set_response(self, method: str, value: Any) -> "MockSeam":
        """Register a per-method canned return value.

        For an async Protocol method the value is returned as-is
        (awaited if it is itself an awaitable). A registered ``None`` is
        honoured (distinct from "no response set").
        """
        self._seam_state(self).responses[method] = value
        return self

    def clear_override(self, method: str) -> "MockSeam":
        self._seam_state(self).overrides.pop(method, None)
        return self

    def clear_response(self, method: str) -> "MockSeam":
        self._seam_state(self).responses.pop(method, _UNSET)
        return self

    def reset_mock(self) -> "MockSeam":
        """Clear all overrides, responses, and recorded calls."""
        state = self._seam_state(self)
        state.overrides.clear()
        state.responses.clear()
        state.calls.clear()
        return self

    @property
    def calls(self) -> list[CallRecord]:
        """Invocations recorded in order (read-only view of the list)."""
        return self._seam_state(self).calls

    def calls_to(self, method: str) -> list[CallRecord]:
        """Recorded invocations of ``method`` only, in order."""
        return [c for c in self._seam_state(self).calls if c.method == method]
