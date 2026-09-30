# Plan — two-phase (preload-safe) boot seam

## Fork-safety findings (investigation, done before design)

**1. Does importing `app.py` + every router start anything fork-unsafe?**

Measured, not assumed: a probe patched `threading.Thread.start` and `socket.socket.connect`
with stack capture, snapshotted `/proc/self/fd`, then imported
`agentclaw.community.adapters.http.app`.

| profile | new live threads | connect() calls | fd delta | operations registered |
| --- | --- | --- | --- | --- |
| `test` | none | 0 | 0 | 798 over 678 paths |
| `singlebox` | none | 0 | 0 | 798 over 678 paths |

(Operations are counted off `app.openapi()`, not `len(app.routes)`: since FastAPI 0.138
`include_router` appends a lazy `_IncludedRouter` marker rather than flattening, so
`app.routes` shows a couple of dozen entries and says nothing about the real surface. The
tests read the same resolved view.)

`community` could not be probed in this checkout (`DATABASE_URL` is required with no
inline default); `corp` and `corp_test` need `agentclaw.corp`, which does not exist in this
repo. So on every profile this tree can actually boot, **today's import already starts no
thread and opens no connection** — all such work already lives behind the `Lifecycle`
participants that `_app_lifespan` drives, not at module level.

What *is* fork-unsafe at import today is what Phase 2 takes over, and it is corp-shaped, so
it does not show up in the community probe:

- `eager_check_critical_bindings` resolves `DatabasePlugin` → SQLAlchemy engine + its
  connection pool; under corp also the ZDAS handle. A forked child inheriting a pool is the
  classic fork hazard.
- `init_principal_verifier_config(injector.get(SecretResolver), ...)` is a remote secret read
  under corp (Layotto/secret store). In this checkout it resolves to nothing and logs
  `secret 'gateway_principal_signing_key' is not present in the secret store`.
- `tracer.install(app)` — a no-op for `NoopTracer` (test/singlebox) and a middleware-only add
  for `CommunityTracer`, but the corp impl installs the SofaTracer runtime.
- `build_injector` itself constructs ~50 base modules plus the profile column (~19 corp
  infrastructure modules under corp) and eagerly resolves six config keys through the active
  `ConfigProvider` — which is the sofapy-backed one under corp.

**Conclusion: nothing has to be made lazy for this change.** Every fork-unsafe side effect is
already inside the resolve/install steps that Phase 2 owns; Phase 1 as specified is
thread-free, connection-free and secret-free. The one residual caveat is that corp-only
providers could not be exercised here — see "Residual risk".

**2. Module-level `injector.get(...)` / eager resolves.** Exactly one in the whole tree:
`adapters/http/app.py:991`, `for _r in injector.get(OptionalRouters).routers`. Nothing else
matches, and `get_app_injector()` (the legacy service-locator) has four call sites, all of
them inside function bodies (`core/nas_usage/service.py`,
`core/devices/services/device_service.py`). So no router or service needs a built injector at
import — this single line is what does, and per the brief it moves into finalize.

**3. Can auth/tracer/CORS middleware be added in finalize?** Yes, with a hard ordering
requirement. Starlette 1.3.1 `Starlette.__call__` does
`if self.middleware_stack is None: self.middleware_stack = self.build_middleware_stack()`,
and `add_middleware` raises `RuntimeError("Cannot add middleware after an application has
started")` once it is non-`None`. So finalize must run before the first ASGI call, lifespan
included. That is the call-timing requirement in the spec, and it is enforced by a test.

**4. The two corp composition-root registrations.** `register_config_provider(profile)`
writes a process-global `ConfigProvider` (`set_config_provider`, or the corp branch via
`importlib`); `register_corp_modules(profile)` assigns a module-level thunk in
`di/modules_bootstrap.py`. Both are pure registry mutation — no injector, no I/O, no
resource. They stay in Phase 1, where they must be: `build_injector` reads both, and in
preload mode `build_injector` runs post-fork in a child that inherits these registries.

## Design

New module `adapters/http/boot.py` owns the mode switch and the Phase-2 body. Rationale
beyond separation of concerns: `app.py` is 998 lines against the 1000-line cap in
`tests/community/architecture/test_no_oversized_modules.py`, and it is not on that
allowlist, so the boot machinery cannot live there.

```python
# adapters/http/boot.py
_BOOT_MODE_ENV = "AGENTCLAW_HTTP_BOOT_MODE"

class BootMode(Enum):
    EAGER = "eager"
    PRELOAD = "preload"
    @classmethod
    def detect(cls) -> BootMode: ...     # unset/empty -> EAGER; unknown -> RuntimeError

_finalized_pid: int | None = None        # pid, not bool — survives fork correctly

_failed_pid: int | None = None           # a failed attempt poisons its process

def worker_runtime_finalized() -> bool: ...
def finalize_worker_runtime(app, *, profile) -> None: ...   # owns *when* it may run
def _install_worker_runtime(app, profile) -> str: ...       # the seven steps
```

`app.py` keeps the public surface:

```python
def finalize_worker_runtime() -> None:
    _finalize_worker_runtime(app, profile=_deploy_profile)

if _BOOT_MODE is BootMode.EAGER:
    finalize_worker_runtime()
```

`_app_lifespan` switches from the module global to `app.state.injector`, raising a message
that names `finalize_worker_runtime()` when it is absent.

## Task list

1. `adapters/http/boot.py` — `BootMode`, pid-guarded `finalize_worker_runtime(app, profile)`
   over `_install_worker_runtime`'s seven steps in spec order, plus
   `worker_runtime_finalized()`.
2. `adapters/http/app.py` — delete the import-time `build_injector` / eager check /
   `attach_injector` / `init_principal_verifier_config` / `install_middleware` /
   `SINGLEBOX_COVERAGE` / `OptionalRouters` block and the module-level `injector`; add the
   zero-arg `finalize_worker_runtime` wrapper and the eager-mode call at the end; point
   `_app_lifespan` at `app.state.injector`; refresh the module docstring.
3. `scripts/community_selftest.py` — boot proof asserts `http_app.app.state.injector`.
4. `tests/community/adapters/http/test_two_phase_boot.py` — the seven test groups below.
5. Full `tests/community` run, plus a corp-absent boot proof via
   `scripts/community_selftest.py --skip-tests`.

## Tests

Preload-mode tests import `app.py` in a **subprocess** with
`AGENTCLAW_HTTP_BOOT_MODE=preload`, because the pytest session's conftest already imported it
eagerly and `sys.modules` caching makes an in-process re-import meaningless. Each subprocess
prints a JSON verdict the parent asserts on.

1. **eager default** — unset env; after import `app.state.injector` exists, `user_middleware`
   holds the full expected stack, `worker_runtime_finalized()` is true.
2. **preload leaves no runtime** — after import: `app` exists; the statically registered route
   set matches eager's minus the DI-conditional `OptionalRouters`; `user_middleware` is
   empty; `app.state` has no `injector`; `eager_check_critical_bindings` was not called;
   `SecretResolver` was never resolved; `threading.enumerate()` gained nothing and
   `socket.socket.connect` was never called (same probe as the investigation).
3. **finalize wires the worker** — after `finalize_worker_runtime()`: `app.state.injector` is
   a fresh `Injector`; the eager check, the verifier init and `install_middleware` each ran
   exactly once (counted via monkeypatched spies in the subprocess);
   `discover_lifecycle_participants` receives that same injector.
4. **idempotent in-pid** — calling finalize twice leaves one injector identity, one
   middleware stack, and one call of each spy. And the failure side of the same guard: a
   finalize that raises mid-sequence leaves the process un-finalized, and the next call in
   that pid is refused before re-running any step.
5. **pid guard** — (a) the supported shape: a real `os.fork()` in preload mode with nothing
   finalized before it, child and parent each finalizing their own runtime, neither stack
   doubled; (b) the unsupported shape: finalize *then* fork, where the child must refuse
   rather than append a second stack — asserted both against a real fork and against a
   rewritten `_finalized_pid`; (c) the same refusal for a parent that *failed* partway,
   which leaves the child the same half-wired app to inherit.
6. **eager ≡ preload+finalize** — route paths, methods, `operation_id`s and the full
   `app.openapi()` document compare equal across the two modes.
7. **call-timing guards** — two of them, because a host need not drive the lifespan at all
   and because serving is not the only way an un-finalized process can do damage.
   (a) The lifespan refuses when `worker_runtime_finalized()` is false, including the case
   where an injector *is* attached because it was inherited across a fork — asserted by
   driving the lifespan with a foreign `_finalized_pid` and checking participants were never
   resolved. (b) A real request to an un-finalized worker whose lifespan was never driven
   (a `TestClient` used outside its context manager) gets 503 from `RequireWorkerRuntime`
   rather than a 200 through an un-wired stack; a finalized worker serves 200 through the
   same guard. (c) A real fork after a finalize, where the child issues a request and must
   get its 503 **without** any inherited worker middleware having executed — spied through
   `UserContextMiddleware.dispatch`, since that one reaches the parent's auth plugin and its
   pool. This is what forces the guard to wrap the stack rather than join it.

## Deviation from the brief, and why

The brief said to "re-run if `os.getpid()` != recorded pid". Implemented literally that is a
defect, and a review bot found it independently: a marker inherited across `fork` never
arrives alone, so re-running appends a second middleware stack to the parent's rather than
replacing it (measured: 8 entries became 16), while the child still holds the parent's
engines, pools and fds. The brief's *intent* — a child must never silently skip its own
initialization — is what the pid guard delivers; the child now fails loudly instead, which is
the same intent carried to the case the instruction did not anticipate. The supported flow is
unaffected: the master constructs only, so nothing is finalized before the fork.

## Residual risk

The corp providers (sofapy `ConfigProvider`, corp `SecretResolver`, SofaTracer, ZDAS
`DatabasePlugin`) cannot be imported or exercised from this repo. Phase 1 names none of them
and resolves nothing from the injector, so by construction none of them runs at import — but
"verified by running it" only covers `test` and `singlebox`. The OCB side should re-run the
thread/connect probe against a corp image before enabling `preload_app`.
