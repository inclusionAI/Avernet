"""Two-phase boot seam for the HTTP composition root.

``adapters/http/app.py`` used to do two different jobs in one module import:
*construct* the ASGI app (routes, handlers, the ``FastAPI`` object) and
*initialize the worker runtime* (build the injector, resolve prod bindings, read
the gateway signing secret, install auth/tracer/CORS middleware). Under uvicorn's
spawn model every worker re-runs both, so the whole cold start is paid N times.

This module splits them, behind ``AGENTCLAW_HTTP_BOOT_MODE``:

``eager`` (the default)
    Nothing changes. ``app.py`` calls :func:`finalize_worker_runtime` inline at
    the end of its own import, so an import still returns a fully initialized
    app. Community, singlebox, local, ``main.py`` and the test suite all take
    this path.

``preload``
    Importing ``app.py`` performs construction only. The result owns no thread,
    no connection, no file descriptor and no secret, so a master process may
    import it once and ``fork`` workers. Each worker then calls, after the fork::

        from agentclaw.community.adapters.http.app import app, finalize_worker_runtime
        finalize_worker_runtime()

Call timing is not advisory. Starlette builds and caches ``middleware_stack`` on
the first ASGI call — ``add_middleware`` raises ``RuntimeError`` afterwards — and
the lifespan startup event *is* an ASGI call. So finalize must run before the
lifespan and before the first request, or the auth, tenant, tracing and CORS
middleware this module installs would silently never take effect.

The "already finalized" guard records the **pid** that finalized rather than a
boolean. A boolean would be inherited across ``fork``: a child would see
``True``, skip its own finalize, and serve traffic on the master's injector and
the master's connections. Comparing ``os.getpid()`` makes the flag meaningless
in any process that did not set it.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import TYPE_CHECKING

from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

    from agentclaw.community.di.profile import DeployProfile

logger = get_logger()

#: Selects the boot shape. Unset or empty means :attr:`BootMode.EAGER`.
BOOT_MODE_ENV = "AGENTCLAW_HTTP_BOOT_MODE"


class BootMode(Enum):
    """How ``app.py`` splits construction from worker-runtime initialization."""

    #: Import constructs *and* initializes — today's behavior, and the default.
    EAGER = "eager"
    #: Import constructs only; the consumer calls ``finalize_worker_runtime()``
    #: once per worker after fork.
    PRELOAD = "preload"

    @classmethod
    def detect(cls) -> "BootMode":
        """Resolve the mode from :data:`BOOT_MODE_ENV`.

        Unset or empty resolves to :attr:`EAGER` so every existing launch site
        keeps its behavior without setting anything. An *unknown* value is a hard
        error rather than a silent fallback — the same treatment
        ``DEPLOY_PROFILE`` gets, and for the same reason: a typo that quietly
        boots the wrong shape is worse than a crash at import.
        """
        raw = (os.getenv(BOOT_MODE_ENV) or "").strip().lower()
        if not raw:
            return cls.EAGER
        try:
            return cls(raw)
        except ValueError:
            raise RuntimeError(
                f"Unknown {BOOT_MODE_ENV}={raw!r}; expected 'eager' or 'preload'"
            ) from None


# The pid of the process that completed ``finalize_worker_runtime``. ``None``
# means no process has. Deliberately a pid and not a bool — see the module
# docstring.
_finalized_pid: int | None = None

# The pid in which ``finalize_worker_runtime`` raised. A failure can leave the
# app half-wired (an injector attached but no middleware, say), and re-running
# the sequence over that would install the middleware stack twice. Recorded as a
# pid for the same reason as above: a forked child gets its own attempt.
_failed_pid: int | None = None


def worker_runtime_finalized() -> bool:
    """Whether *this* process has completed its worker-runtime initialization.

    False in a forked child that inherited a finalized parent's module state,
    which is the whole point of storing a pid.
    """
    return _finalized_pid is not None and _finalized_pid == os.getpid()


def finalize_worker_runtime(app: "FastAPI", *, profile: "DeployProfile") -> None:
    """Build and install everything this worker process owns.

    Runs post-fork in ``preload`` mode, inline at the end of the ``app.py``
    import in ``eager`` mode. The sequence itself lives in
    :func:`_install_worker_runtime`; what this function owns is *when* it may
    run.

    Idempotent per process: a second call in the same pid returns immediately, so
    no middleware is installed twice, no tracer thread restarted and no injector
    replaced under a running app.

    Fail-fast, and it stays failed: the finalize marker is set only after every
    step has succeeded, and a step that raises records the failure and re-raises.
    A later call in the same process refuses rather than replaying the sequence
    over a half-wired app — the first attempt may already have attached an
    injector or installed part of the stack, and re-running would stack a second
    copy on top. A worker that cannot initialize must be replaced, not retried.
    """
    global _finalized_pid, _failed_pid

    pid = os.getpid()
    if _finalized_pid == pid:
        logger.debug("[boot] worker runtime already finalized in pid %s", pid)
        return
    if _failed_pid == pid:
        raise RuntimeError(
            f"finalize_worker_runtime() already failed in pid {pid} and left the "
            "app partially wired; this process cannot be recovered by calling it "
            "again. Let the worker exit and start a new one."
        )
    if _finalized_pid is not None:
        # Inherited across fork. Not an error — it is the case the pid guard
        # exists for — but worth a line, because it is also what a stray second
        # call in a *new* process looks like.
        logger.info(
            "[boot] inherited finalize marker from pid %s; finalizing pid %s",
            _finalized_pid, pid,
        )

    try:
        env = _install_worker_runtime(app, profile)
    except BaseException:
        _failed_pid = pid
        logger.exception("[boot] worker runtime finalize failed in pid %s", pid)
        raise

    _finalized_pid = pid
    logger.info("[boot] worker runtime finalized in pid %s (env=%s)", pid, env or "<unset>")


def _install_worker_runtime(app: "FastAPI", profile: "DeployProfile") -> str:
    """The finalize sequence itself. Returns the resolved ``SERVER_ENV``.

    The steps are exactly the ones that used to sit at ``app.py`` module level,
    in exactly that order — middleware ordering is load bearing
    (``install_middleware`` documents it), and the eager binding check must
    precede anything that would rather fail on first request.
    """
    # Imports are function-local on purpose: in ``preload`` mode this module is
    # imported by the master, and nothing it pulls in at *its* import time may
    # touch the injector, a secret store or a tracer.
    from fastapi_injector import attach_injector

    from agentclaw.community.adapters.http.middleware import install_middleware
    from agentclaw.community.di import build_injector
    from agentclaw.community.di.config import CorsConfig, SecretNamesConfig
    from agentclaw.community.di.modules_bootstrap import resolve_extra_modules
    from agentclaw.community.di.optional_routers import OptionalRouters
    from agentclaw.community.plugin_api.auth import AuthPlugin
    from agentclaw.community.plugin_api.secret_resolver import SecretResolver
    from agentclaw.community.plugin_api.tracer import TracerPlugin
    from agentclaw.community.utils.env_utils import get_current_env
    from agentclaw.community.utils.gateway_principal_config import (
        init_principal_verifier_config,
    )

    # ── 1. The injector this worker owns. In preload mode the master never
    # built one, so every provider — and every pool, engine and client it
    # constructs — is created after the fork, by the process that will use it.
    injector = build_injector(
        profile=profile, extra_modules=resolve_extra_modules(profile),
    )

    # ── 2. Bind it to the app. ``attach_injector`` only sets
    # ``app.state.injector`` and binds the request-scope keys into that
    # injector; it adds no middleware, so calling it here (rather than at
    # construction) repoints request-time ``Injected(...)`` resolution, the
    # request scope, and ``_app_lifespan``'s participant discovery in one go.
    attach_injector(app, injector)

    # ── 3. Startup integrity check: resolve a small set of critical bindings
    # now so misconfiguration surfaces at boot instead of on first request.
    # Gated on ``SERVER_ENV`` — fires in ``pre`` and ``prod`` (where every
    # prod-only dep is expected to resolve), skipped in ``dev`` / local
    # (where ZDAS handle, Arca sandbox config, etc. aren't reachable).
    env = get_current_env()
    if env in ("pre", "prod"):
        from agentclaw.community.di.container import eager_check_critical_bindings

        eager_check_critical_bindings(injector)

    # ── 4. Resolve the key the gateway signs /openapi/v1 principals with. Done
    # here because AvernetTenantMiddleware reads the verifier config from the raw
    # ASGI layer, before any route and outside the injector — so the composition
    # root pushes it in rather than the middleware pulling it out.
    #
    # Strict in ``pre``/``prod``, matching the eager binding check above and
    # gated on the same SERVER_ENV: a deployment that serves the public API
    # without a signing key answers 401 to every request while looking healthy,
    # so it must fail the rollout instead. Local, dev, and singlebox legitimately
    # have no key (singlebox ships it empty on purpose), so there it degrades to
    # deny-everything rather than refusing to boot.
    init_principal_verifier_config(
        injector.get(SecretResolver),
        injector.get(SecretNamesConfig).gateway_principal_signing_key,
        strict=env in ("pre", "prod"),
    )

    # ── 5. Middleware. This is the step with the hard timing constraint: it must
    # happen before Starlette caches the stack on the first ASGI call.
    install_middleware(
        app,
        auth_plugin=injector.get(AuthPlugin),
        tracer=injector.get(TracerPlugin),
        cors_config=injector.get(CorsConfig),
    )

    # ── 6. Singlebox coverage recording, outside everything installed above.
    if os.environ.get("SINGLEBOX_COVERAGE") == "1":
        from agentclaw.community.adapters.http.singlebox_coverage import (
            install_singlebox_coverage_middleware,
        )

        install_singlebox_coverage_middleware(app)

    # ── 7. Runtime-mode-conditional routers (bound by DI: empty in prod,
    # populated in local boots via the test column's app-services module). This
    # is the one route registration that cannot happen during construction: it is
    # the only place in the tree that resolves from the injector outside a
    # request, so it has to wait for the injector this function just built. The
    # app still does not branch on mode — DI decides what gets mounted.
    for router in injector.get(OptionalRouters).routers:
        app.include_router(router)

    return env
