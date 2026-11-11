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
boolean. ``fork`` copies the flag along with everything else, so a boolean would
read ``True`` in the child and the child could not tell whether it or its parent
had set it — it would skip its own finalize and serve on the parent's injector
and the parent's connections. A pid is self-identifying: ``_finalized_pid ==
os.getpid()`` can only be true in the process that actually wrote it, so the
inherited value answers "somebody else initialized" rather than "you did".

What a child does about an inherited marker — from a parent that finished the
sequence, or began it and failed — is *refuse*, not re-run. The marker is never
the only thing it inherited: the parent's middleware stack is on the same
``app`` object, and the parent's engines, pools and fds sit behind it, and
finalizing again appends a second stack rather than replacing the first. A
child in that position cannot be repaired from here, so it fails loudly instead
of silently serving on its parent's runtime, which is the outcome the pid guard
exists to prevent. Under the supported flow the question never arises: the
master imports in ``preload`` mode, so nothing is finalized before the fork.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import TYPE_CHECKING

from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI
    from injector import Injector
    from starlette.types import ASGIApp, Receive, Scope, Send

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
    def detect(cls) -> BootMode:
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
# pid, not a bool, so the refusal below can say *which* process left the app in
# that state — this one, or the one it was forked from.
_failed_pid: int | None = None


def worker_runtime_finalized() -> bool:
    """Whether *this* process has completed its worker-runtime initialization.

    False in a forked child that inherited a finalized parent's module state,
    which is the whole point of storing a pid.
    """
    return _finalized_pid is not None and _finalized_pid == os.getpid()


def require_worker_injector(app: FastAPI) -> Injector:
    """The injector *this* process finalized, or refuse to go any further.

    The lifespan's gate, and it deliberately asks whether this process finalized
    rather than whether the app has an injector at all. Those differ in exactly
    the case that matters: a child forked from a finalized parent — or from one
    that failed after ``attach_injector`` — inherits a perfectly non-``None``
    ``app.state.injector``. Gating on presence alone would let such a child run
    ``bootstrap()`` and ``startup()`` over the *parent's* participants, starting
    background workers on the parent's pools and connections, in a process that
    :class:`RequireWorkerRuntime` is meanwhile refusing to let serve traffic.
    Serving is not the only way to do damage, so both gates ask the same
    question.
    """
    if not worker_runtime_finalized():
        raise RuntimeError(
            f"Pid {os.getpid()} has not finalized its worker runtime, so it must "
            "not start the lifespan: doing so would run bootstrap() and startup() "
            "over whatever injector this process holds — inherited from a fork, or "
            "none at all — and start background work on resources it does not own. "
            f"Under {BOOT_MODE_ENV}=preload the consumer must call "
            "finalize_worker_runtime() once per worker after the fork and before "
            "the first ASGI call (the lifespan startup event is one)."
        )
    injector = getattr(app.state, "injector", None)
    if injector is None:
        raise RuntimeError(
            "The worker runtime is marked finalized but no injector is attached; "
            "something detached app.state.injector after finalize_worker_runtime() "
            "ran. Refusing to start the lifespan."
        )
    return injector


#: Set once a refusal has been logged, so a worker stuck in this state does not
#: write one line per request for as long as it is up.
_refusal_logged = False


class RequireWorkerRuntime:
    """Refuse traffic until *this* process has finalized its worker runtime.

    ``_app_lifespan`` already fails loudly when it is entered un-finalized, but
    that only helps if the host drives the lifespan protocol — not every ASGI
    host does, and nothing in the protocol requires it. Without this, a preload
    worker that skipped its post-fork ``finalize_worker_runtime()`` would answer
    its first request by building and caching a middleware stack that has no
    auth, no tenant scoping, no tracing and no CORS, and then serve on it. That
    is the "never serve half-initialized" rule, and it has to hold independently
    of how the host handles lifespans.

    Installed by :func:`install_worker_runtime_guard` during construction, which
    puts it *outside* every other middleware — see there for why that placement
    is the whole point. It owns nothing — no state, no resource — so it is safe
    to inherit across a fork, and it reads :func:`worker_runtime_finalized`,
    which is per-process: a child that has not finalized refuses even though its
    parent had.

    In ``eager`` mode, and in a correctly finalized worker, it never fires.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind in ("http", "websocket") and not worker_runtime_finalized():
            global _refusal_logged
            if not _refusal_logged:
                _refusal_logged = True
                logger.error(
                    "[boot] pid %s is serving without a finalized worker runtime "
                    "and is refusing requests. Call finalize_worker_runtime() "
                    "after the fork, before the first request.",
                    os.getpid(),
                )
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1011})
                return
            body = b'{"detail":"Service Unavailable: worker runtime not initialized"}'
            await send({
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def install_worker_runtime_guard(app: FastAPI) -> None:
    """Put :class:`RequireWorkerRuntime` outside every other middleware.

    Deliberately not ``add_middleware``. Starlette *prepends*, so a guard added
    at construction ends up **innermost** — everything ``install_middleware``
    adds later wraps around it. That is exactly backwards for the case the guard
    exists for: a child forked from a finalized parent inherits the parent's
    whole stack, so the parent's ``UserContextMiddleware`` — and through it the
    parent's auth plugin, reading the parent's connection pool — would run
    before the guard got to refuse. Measured on the previous commit: the child
    answered 503 having already executed ``UserContextMiddleware``.

    Starlette builds the stack lazily on the first ASGI call, so wrapping the
    *builder* puts the guard outside everything that build produces, including
    Starlette's own ``ServerErrorMiddleware``. It holds however the stack is
    reached: built fresh in a worker after the fork, or inherited already-built
    from a parent that had served a request, since the wrapper is what that
    parent built too.
    """
    build_stack = app.build_middleware_stack

    def build_with_guard() -> ASGIApp:
        return RequireWorkerRuntime(build_stack())

    app.build_middleware_stack = build_with_guard  # type: ignore[method-assign]


def finalize_worker_runtime(app: FastAPI, *, profile: DeployProfile) -> None:
    """Build and install everything this worker process owns.

    Runs post-fork in ``preload`` mode, inline at the end of the ``app.py``
    import in ``eager`` mode. The sequence itself lives in
    :func:`_install_worker_runtime`; what this function owns is *when* it may
    run.

    Idempotent per process: a second call in the same pid returns immediately, so
    no middleware is installed twice, no tracer thread restarted and no injector
    replaced under a running app.

    Refused in a child forked from a process that had already run the sequence —
    to completion or partway before it raised: what such a child inherited is a
    wired (or half-wired) runtime, not just a flag, and a second finalize would
    stack on top of it. See the module docstring.

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
    # Either marker, set by a *different* pid, means this process was forked from
    # one that had already run the sequence — all the way through, or partway
    # before it raised. Both leave the same thing behind, and the marker is the
    # least of it: ``app.user_middleware`` still holds that process's stack — its
    # auth plugin, its tracer — and behind those sit its engines, pools and fds,
    # which a fork copies rather than reopens. Finalizing again cannot replace
    # any of that; ``install_middleware`` appends, so the child would serve every
    # request through two stacks (and raise outright if the cached one was
    # already built).
    #
    # So this is refused rather than repaired. It means the runtime was
    # initialized before the fork, which is the one thing preload mode exists to
    # avoid, and no work here gives the child a clean process image.
    if _finalized_pid is not None or _failed_pid is not None:
        origin, what = (
            (_finalized_pid, "finalized the worker runtime")
            if _finalized_pid is not None
            else (_failed_pid, "began finalizing the worker runtime and failed")
        )
        raise RuntimeError(
            f"Pid {origin} {what} before this process (pid {pid}) was forked "
            "from it. This process has inherited that runtime's middleware "
            "stack, injector and every resource behind them, and finalizing "
            "again would stack a second copy on top rather than replace them. "
            f"Import the app with {BOOT_MODE_ENV}=preload so the master forks "
            "before any worker runtime exists, then call "
            "finalize_worker_runtime() in each child."
        )

    try:
        env = _install_worker_runtime(app, profile)
    except BaseException:
        _failed_pid = pid
        logger.exception("[boot] worker runtime finalize failed in pid %s", pid)
        raise

    _finalized_pid = pid
    logger.info("[boot] worker runtime finalized in pid %s (env=%s)", pid, env or "<unset>")


def _install_worker_runtime(app: FastAPI, profile: DeployProfile) -> str:
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
