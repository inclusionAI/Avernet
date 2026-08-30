"""Configuration-source abstraction (B2).

The backend reads a single module-level configuration object at import time
(``core/config/sofa.py``'s ``sofa_config``) and derives all typed config from
it. Historically that object came straight from the company-internal
``sofapy_base.app.config.get_config()``; local/test boots only worked because a
``sys.modules`` monkeypatch faked that package.

This module introduces the seam that lets the *source* of that object vary by
deploy profile without core importing the internal package:

- :class:`AppConfig` — the neutral config holder every consumer already reads
  (``user_config`` / ``app_name`` / ``model_dump()`` plus transparent forwarding
  of any other top-level attribute, e.g. ``bcsfuse``, to the corporate
  delegate).
- :class:`ConfigProvider` — the ``load() -> AppConfig`` Protocol.
- :func:`set_config_provider` / :func:`load_config` — a tiny pre-DI registry.
  Config is read before the injector exists, so this is a bootstrap registry,
  not a Rule-20 DI plugin. The composition root must register the provider before
  the first read for every deploy profile.

This module imports nothing internal (no ``sofapy_base``, no plugins).
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class AppConfig:
    """Neutral, in-memory configuration holder.

    Exposes exactly the contract every consumer reads (formerly satisfied by
    sofapy's ``Config`` and the monkeypatch's ``LocalConfig``): ``user_config``,
    ``app_name`` and ``model_dump()``.

    Every field is explicit and required — there are no defaults. ``delegate`` is
    the corporate source object (the real sofapy ``Config``) or ``None`` for the
    YAML path; unknown top-level attribute access (``config.bcsfuse``, …) is
    forwarded to it when present, so corp behaviour is identical, and otherwise
    raises ``AttributeError`` — so ``getattr(config, "bcsfuse", None)`` yields
    ``None`` on the YAML/community/test path.
    """

    def __init__(
        self,
        *,
        user_config: dict[str, Any],
        raw: dict[str, Any],
        app_name: str,
        delegate: Any,
    ) -> None:
        self.user_config = user_config
        self.app_name = app_name
        self._raw = raw
        self._delegate = delegate

    def model_dump(self) -> dict[str, Any]:
        return self._raw

    def __getattr__(self, name: str) -> Any:
        # Only called for attributes not set in __init__. Forward to the
        # delegate (corp: real sofapy Config) so arbitrary top-level keys keep
        # working; otherwise raise so getattr(..., default) falls back.
        if name.startswith("_"):
            raise AttributeError(name)
        delegate = self.__dict__.get("_delegate")
        if delegate is not None:
            return getattr(delegate, name)
        raise AttributeError(name)


@runtime_checkable
class ConfigProvider(Protocol):
    """Yields the active :class:`AppConfig` for the current deploy profile."""

    def load(self) -> AppConfig: ...


_provider: ConfigProvider | None = None
_cached: AppConfig | None = None


def set_config_provider(provider: ConfigProvider) -> None:
    """Register the active provider (called once by the composition root).

    Resets any cached config so the next :func:`load_config` picks up the new
    provider — registration always precedes the first config read on a corp
    boot, but resetting keeps the contract honest if order ever changes.
    """
    global _provider, _cached
    _provider = provider
    _cached = None


def load_config() -> AppConfig:
    """Return the active provider's :class:`AppConfig`, cached after first load.

    Configuration-source selection belongs to the composition root. Failing
    explicitly prevents an import path from silently choosing an overlay that
    disagrees with the active deploy profile.
    """
    global _cached
    if _cached is None:
        provider = _provider
        if provider is None:
            raise RuntimeError(
                "ConfigProvider has not been registered by the composition root"
            )
        _cached = provider.load()
    return _cached


def reset_config_provider() -> None:
    """FOR TESTS ONLY — not called on any production path.

    Clears both the registered provider and cached config. The next
    :func:`load_config` fails until a composition root registers a provider.
    Tests that register a provider call this to isolate.
    """
    global _provider, _cached
    _provider = None
    _cached = None
