# Two-phase (preload-safe) boot seam for the community FastAPI app

## Problem

Production imports the app as `agentclaw.community.adapters.http.app:app`. Today that
module import does not only *construct* the app — it also completes every piece of
worker-owned runtime initialization:

- `build_injector(...)` (line 58) plus the six eager config resolves inside it;
- `eager_check_critical_bindings(injector)` under `SERVER_ENV in (pre, prod)` — resolves
  `DatabasePlugin` / `AuthPlugin` / `OpenBotChatServiceProtocol`, i.e. engines and pools;
- `init_principal_verifier_config(injector.get(SecretResolver), ...)` — a secret-store read
  (Layotto under corp);
- `install_middleware(app, auth_plugin=..., tracer=..., cors_config=...)` — which ends in
  `tracer.install(app)`, the tracer runtime;
- `injector.get(OptionalRouters)` (line 991) — the one module-level `injector.get` left in
  the tree, which is what forces a built injector at import at all.

Under uvicorn's spawn model every worker re-runs all of it, so the ~17s import is paid N
times. The planned fix is Gunicorn `preload_app`: import once in the master, `fork` N
workers. That requires the master import to be fork-safe — no threads, no gRPC/DB/HTTP
connections, no remote secret reads, no tracer runtime, no resource a forked child would
inherit. This spec adds the community-side seam that such a change consumes. The Gunicorn /
OCB change itself is out of scope.

## Contract

`AGENTCLAW_HTTP_BOOT_MODE` selects the boot shape. Values `eager` and `preload`; unset or
empty means `eager`; any other value is a hard error at import (same shape as
`DEPLOY_PROFILE`, which this repo already treats as a mandatory, never-silently-defaulted
switch).

- **eager** (default) — identical to today: importing `app.py` builds the app *and*
  completes all runtime init inline, before the import returns. Community, singlebox, local,
  `main.py`, the test suite and any direct `import app` are unaffected.
- **preload** — importing `app.py` yields a fully-constructed `app` with every statically
  registered route, exception handler and the health endpoint, and **no** worker-owned
  runtime. The consumer calls, once per worker after fork:

  ```python
  from agentclaw.community.adapters.http.app import app, finalize_worker_runtime
  finalize_worker_runtime()
  ```

`finalize_worker_runtime()` takes no arguments and returns `None`. It must be called
**before** the first ASGI call on `app` — which includes the lifespan startup event —
because Starlette builds and caches `middleware_stack` on that first call and
`add_middleware` raises `RuntimeError` afterwards.

## Phase split

**Phase 1 — preload-safe construction. Runs at import in both modes.**

`DeployProfile.detect()`, `validate_deploy_environment()`, `register_config_provider`,
`register_corp_modules`, `_set_openclaw_config_path()`, every router import, the `FastAPI`
object, every exception handler, `/api/health`, and every unconditional `include_router`
(including `build_public_router()`). All of it is env reads, registry mutation and route
declaration — no thread, connection, fd or remote read.

**Phase 2 — `finalize_worker_runtime()`. Runs post-fork per worker; inline at the end of
import in eager mode.**

In this exact order, matching today's:

1. `build_injector(profile=..., extra_modules=resolve_extra_modules(profile))`
2. `attach_injector(app, worker_injector)`
3. `eager_check_critical_bindings(...)` when `get_current_env() in ("pre", "prod")`
4. `init_principal_verifier_config(...)` with the same `strict=` gating
5. `install_middleware(app, auth_plugin=..., tracer=..., cors_config=...)`
6. the `SINGLEBOX_COVERAGE` coverage middleware
7. `include_router` for each `injector.get(OptionalRouters).routers`

## Correctness

- `finalize_worker_runtime()` runs at most once per process. The guard records the **pid**
  that finalized, not a boolean: a child that inherits the module state across `fork` sees
  `os.getpid() != _finalized_pid`, so it never mistakes the parent's initialization for its
  own and never silently serves on the master's injector, middleware or connections.
- What that child does instead is **refuse, not re-run** — for a parent that finished the
  sequence and for one that began it and failed alike. An inherited marker never arrives
  alone: the parent's middleware stack is on the same `app` object, and its engines, pools and
  fds sit behind it — a fork copies those rather than reopening them. `install_middleware`
  appends, so a second finalize gives the child two stacks (measured: 8 entries become 16),
  and it still holds the parent's resources. The child cannot repair its own process image,
  so it raises, naming the pre-fork finalize and pointing at `preload`. Under the supported
  flow the case never arises, because the master constructs only.
- The pid is recorded **only after** every step succeeds. A partial failure leaves the
  process un-finalized and the exception propagates — the worker fails to start rather than
  serving half-initialized. Fail-fast in `pre`/`prod` is unchanged, since the eager check and
  the strict verifier init still run, just later.
- A failure also records the **failed pid**, and a later call in that process is refused
  outright. Replaying the sequence over an app that already has an injector attached and part
  of its stack installed would stack a second copy on top; a worker that cannot initialize is
  replaced, not retried. A child forked from it is refused for the same reason — it inherited
  that same half-wired app. **Only a fork performed before any finalize attempt is supported**,
  which is what `preload` mode gives you.
- `_app_lifespan` reads participants from the injector *this process* finalized, not from a
  module-level handle captured at import — and the gate is `worker_runtime_finalized()`, not
  "is an injector attached". The two differ exactly where it matters: a child forked from a
  finalized parent inherits a non-`None` `app.state.injector`, and gating on presence would
  let it run `bootstrap()`/`startup()` over the parent's participants, starting background
  workers on the parent's pools, in a process that is simultaneously being refused traffic.
  Serving is not the only way to do damage, so both gates ask the same question. Entering the
  lifespan un-finalized raises with a message naming `finalize_worker_runtime()`.
- That lifespan check is not the only guard, because nothing in ASGI obliges a host to drive
  the lifespan protocol. `RequireWorkerRuntime` answers **503** to any request reaching a
  process that has not finalized. Without it a worker that skipped finalize would answer its
  first request by building and caching a stack with no auth, no tenant scoping, no tracing
  and no CORS, and then serve on it. The guard owns no state or resource, so it is safe to
  inherit across a fork, and it reads the per-process marker — a child that has not finalized
  refuses even though its parent had. In a finalized worker it never fires.
- It is installed by **wrapping the built middleware stack, not via `add_middleware`**.
  Starlette prepends, so a guard added at construction would sit *innermost*, with everything
  `install_middleware` adds later wrapped around it — and a child forked from a finalized
  parent inherits exactly that stack. It would then run the parent's `UserContextMiddleware`,
  and through it the parent's auth plugin against the parent's connection pool, before the
  503 came back. Refusing after touching the resources is not refusing. Wrapping
  `build_middleware_stack` puts the guard outside everything that build produces, including
  Starlette's own `ServerErrorMiddleware`, whether the stack is built fresh post-fork or
  inherited already-built.

## Behavior compatibility

Unchanged: API paths, methods, operation ids, request/response schemas, middleware set and
order, auth behavior, exception handlers, lifespan participants, the community/local default
startup, and the `agentclaw.community.adapters.http.app:app` import path.

Changed, deliberately:

- **Boot timing only, in eager mode.** `build_injector` and the eager binding check now run
  after the router imports instead of before. Nothing in the tree resolves from the injector
  at module import (see fork-safety findings), so nothing observes the difference; a
  misconfiguration still fails the same import, a few hundred milliseconds later in it.
- **Route order.** The DI-conditional `OptionalRouters` now mount last, after
  `build_public_router()`, instead of just before it. Their prefixes (`/api/local/...`, and
  empty on every non-test profile) do not overlap the public surface, so no request resolves
  to a different handler.
- **`app.py` no longer exposes a module-level `injector`.** `app.state.injector` is the
  single handle, which is what the module docstring already claimed. The one consumer,
  `scripts/community_selftest.py`'s boot proof, moves to `app.state.injector`.

## Out of scope

`agentclaw.corp`, the SOFAPy runner, Gunicorn/OCB wiring, Dockerfiles and deploy scripts.
This change ships the seam; nothing in this repo sets `AGENTCLAW_HTTP_BOOT_MODE=preload` at
runtime.
