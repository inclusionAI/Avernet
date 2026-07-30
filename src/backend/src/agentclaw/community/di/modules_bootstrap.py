"""Corp infrastructure-column registration seam (B8).

The corp profile installs a column of ~19 ``infrastructure/corp/*`` modules.
Naming those modules inline in ``profile_modules.py`` would put corp imports in
a community-shipped source file. Instead the corp column is supplied at the
composition root through this registry — exactly the way ``config_bootstrap``
supplies the corp ``ConfigProvider``.

Flow (mirrors ``register_config_provider``):

- ``adapters/http/app.py`` and ``main.py`` (corp branch) call
  :func:`register_corp_modules` right after :func:`register_config_provider`,
  before ``build_injector``. The corp branch defers the import of
  ``infrastructure.corp.column`` to call time, so a community build (which omits
  the corp subpackage) never imports it.
- ``profile_modules.modules_for(CORP)`` calls :func:`get_corp_modules` and
  returns its result, which reads the registered provider. If unregistered (e.g.
  a community build somehow selected the corp profile), it raises loudly rather
  than silently omitting the infrastructure column.

``profile_modules.py`` therefore names **no** ``infrastructure.corp`` module.
"""
from __future__ import annotations

from collections.abc import Callable

from injector import Module

from agentclaw.community.di.profile import DeployProfile


# Module-level registry: a thunk returning the corp infrastructure column.
# ``None`` = unregistered (community build, or corp registration not yet run).
_corp_modules_provider: Callable[[], list[Module]] | None = None


def register_corp_modules_provider(provider: Callable[[], list[Module]]) -> None:
    """Register the thunk that supplies the corp infrastructure column.

    Called from the composition root before ``build_injector``. The thunk
    closes over the ``infrastructure.corp`` imports so they are deferred to
    column-construction time (corp builds only).
    """
    global _corp_modules_provider
    _corp_modules_provider = provider


# The monorepo-only ``corp_test`` column reuses a small set of corp modules
# supplied through this second registry. The community ``test`` and ``singlebox``
# columns are corp-free and never read this provider. Keeping the registry here
# lets ``profile_modules.py`` name no ``infrastructure.corp`` module.
_test_corp_reuse_provider: Callable[[], list[Module]] | None = None


def register_test_corp_reuse_provider(provider: Callable[[], list[Module]]) -> None:
    """Register the thunk supplying the corp modules ``corp_test`` reuses."""
    global _test_corp_reuse_provider
    _test_corp_reuse_provider = provider


def register_corp_modules(profile: DeployProfile) -> None:
    """Install the corp-module provider for ``profile`` (no-op when corp-free).

    - ``corp`` registers the full corp infrastructure column.
    - ``corp_test`` registers the corp-reuse subset the corp test column installs
      (it runs with corp deps present in the dev/CI venv).
    - ``singlebox`` is a no-op in community builds — the singlebox overlay modules
      are only needed when corp deps are present (OCB monorepo). In a community
      CI the singlebox column stays corp-free; ``get_singlebox_overlay_modules``
      returns an empty list.
    - ``test`` registers **nothing** — B11 (3.2) made that column corp-free.

    The corp branches live in the corp-only ``di.corp_bootstrap`` module, loaded
    here via ``importlib`` (a string import, corp/corp_test only) — so
    this shared file names **no** ``infrastructure.corp`` / ``config_corp`` module
    and a community boot (without ``corp`` present) imports it fine. The corp side
    calls the setter registries below back on this module (corp → community).
    """
    if profile is DeployProfile.CORP:
        from importlib import import_module

        import_module("agentclaw.corp.di.corp_bootstrap").install_corp_column()
    elif profile is DeployProfile.CORP_TEST:
        from importlib import import_module

        import_module("agentclaw.corp.di.corp_bootstrap").install_test_corp_reuse_column()
    # SINGLEBOX: corp overlay (default-env-bot router + DI) is only needed when
    # the corp package is present (OCB monorepo). In a community build
    # (Avernet CI) agentclaw.corp is absent — the import is silently skipped
    # and get_singlebox_overlay_modules() returns an empty list, keeping the
    # singlebox column corp-free.
    elif profile is DeployProfile.SINGLEBOX:
        from importlib import import_module

        try:
            import_module("agentclaw.corp.di.corp_bootstrap").install_singlebox_overlay()
        except ModuleNotFoundError:
            pass


def get_corp_modules() -> list[Module]:
    """Return the registered corp infrastructure column.

    Raises if no provider is registered — a community build must never resolve
    the corp column, and a corp build that forgot to register should fail loudly
    at composition time, not silently boot without its infrastructure.
    """
    if _corp_modules_provider is None:
        raise RuntimeError(
            "Corp infrastructure column not registered. Call "
            "register_corp_modules(profile) at the composition root before "
            "build_injector (see di/modules_bootstrap.py). This profile must "
            "not be selected in a community distribution."
        )
    return _corp_modules_provider()


# Corp-only critical bindings for the pre/prod eager-check. Registered by the
# corp branch of ``register_corp_modules`` so neutral ``container.py`` names no
# corp config type (B8 review, pt 2).
_eager_check_extra_keys: list[type] = []


def register_eager_check_keys(keys: list[type]) -> None:
    """Register additional (corp-only) critical bindings for the eager-check."""
    _eager_check_extra_keys.clear()
    _eager_check_extra_keys.extend(keys)


def get_eager_check_keys() -> list[type]:
    """Return the registered extra eager-check keys (empty if none)."""
    return list(_eager_check_extra_keys)


def get_test_corp_modules() -> list[Module]:
    """Return the corp modules the monorepo-only ``corp_test`` column reuses.

    Raises if unregistered — ``corp_test`` requires the corp package and its
    bootstrap registration. Community ``test`` and ``singlebox`` never call this
    function.
    """
    if _test_corp_reuse_provider is None:
        raise RuntimeError(
            "Test corp-reuse column not registered. Call "
            "register_corp_modules(CORP_TEST) at the composition root "
            "before build_injector (see di/modules_bootstrap.py)."
        )
    return _test_corp_reuse_provider()


# Singlebox overlay modules — the singlebox column may need a small set of corp
# modules (default-env-bot router + DI). Supplied through this registry so
# ``profile_modules.py`` names **no** corp module (B8).
_singlebox_overlay_provider: Callable[[], list[Module]] | None = None


def register_singlebox_overlay_provider(provider: Callable[[], list[Module]]) -> None:
    """Register the thunk supplying the corp overlay modules for singlebox."""
    global _singlebox_overlay_provider
    _singlebox_overlay_provider = provider


def get_singlebox_overlay_modules() -> list[Module]:
    """Return the registered singlebox overlay modules (empty list if none).

    A community build (without corp present) never registers a provider, so
    this returns an empty list — the singlebox column stays corp-free.
    """
    if _singlebox_overlay_provider is None:
        return []
    return _singlebox_overlay_provider()
