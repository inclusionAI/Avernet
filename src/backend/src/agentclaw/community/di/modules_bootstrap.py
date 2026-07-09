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


# The ``test`` / ``singlebox`` columns reuse a small set of corp modules (the
# corp config providers + the profile-blind codefuse-vault + the corp AICoding
# services): the test profile is a corp-code CI profile, bundled with corp deps.
# Supplied through this second registry so ``profile_modules.py`` names NO
# ``infrastructure.corp`` module in the test branch either (B8 review, pt 1).
_test_corp_reuse_provider: Callable[[], list[Module]] | None = None


def register_test_corp_reuse_provider(provider: Callable[[], list[Module]]) -> None:
    """Register the thunk supplying the corp modules the test column reuses."""
    global _test_corp_reuse_provider
    _test_corp_reuse_provider = provider


def register_corp_modules(profile: DeployProfile) -> None:
    """Install the corp-module provider for ``profile`` (no-op for community).

    - ``corp`` registers the full corp infrastructure column.
    - ``corp_test`` registers the corp-reuse subset the corp test column installs
      (it runs with corp deps present in the dev/CI venv).
    - ``test`` / ``singlebox`` register **nothing** — B11 (3.2) made those columns
      corp-free, so they no longer reach the corp reuse column.

    The corp branches live in the corp-only ``di.corp_bootstrap`` module, loaded
    here via ``importlib`` (a string import, corp/corp_test only) — so this shared
    file names **no** ``infrastructure.corp`` / ``config_corp`` module and a
    community boot (without ``corp`` present) imports it fine. The corp side calls
    the setter registries below back on this module (corp → community).
    """
    if profile is DeployProfile.CORP:
        from importlib import import_module

        import_module("agentclaw.corp.di.corp_bootstrap").install_corp_column()
    elif profile is DeployProfile.CORP_TEST:
        from importlib import import_module

        import_module("agentclaw.corp.di.corp_bootstrap").install_test_corp_reuse_column()


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
    """Return the corp modules the test / singlebox column reuses.

    Raises if unregistered — the test profile is corp-code CI; a community build
    must never select it. (In a community distribution these corp modules don't
    exist, and this profile is never chosen.)
    """
    if _test_corp_reuse_provider is None:
        raise RuntimeError(
            "Test corp-reuse column not registered. Call "
            "register_corp_modules(TEST|SINGLEBOX) at the composition root "
            "before build_injector (see di/modules_bootstrap.py)."
        )
    return _test_corp_reuse_provider()
