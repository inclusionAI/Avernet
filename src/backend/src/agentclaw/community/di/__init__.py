"""Dependency-injection container for the agentclaw backend.

Public surface:

- :func:`build_injector` — construct the app's ``Injector``. The
  composition root (``adapters/http/app.py``) calls this once at boot
  and passes the result to ``attach_injector(app, injector)`` so
  FastAPI routes can resolve ``Injected(X)`` parameters. There is no
  module-global injector — every consumer goes through DI or
  ``request.app.state.injector``.
- :func:`get_app_injector` — return the global injector instance.
  Used for service locator pattern in legacy code paths where DI is
  not available (e.g., deep inside service methods). Prefer constructor
  injection via ``Injected(X)`` for new code.
- :class:`DeployProfile` — the single deploy-profile selector the
  container wires from (``corp`` | ``singlebox`` | ``test`` | ``community``).
- :func:`modules_for` — the concern × profile matrix selector.
- :data:`Injected` — re-exported from ``fastapi_injector`` for use in
  FastAPI route signatures (``svc: Foo = Injected(Foo)``).
- :data:`request_scope`, :data:`RequestScope` — for per-request bindings.
"""
from fastapi_injector import Injected

from agentclaw.community.di.container import build_injector, get_app_injector
from agentclaw.community.di.profile import DeployProfile, validate_deploy_environment
from agentclaw.community.di.scopes import RequestScope, request_scope
from agentclaw.community.di.profile_modules import modules_for


__all__ = [
    "DeployProfile",
    "Injected",
    "RequestScope",
    "build_injector",
    "get_app_injector",
    "modules_for",
    "request_scope",
    "validate_deploy_environment",
]
