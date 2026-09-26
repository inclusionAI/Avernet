"""The two-phase (preload-safe) boot seam — ``AGENTCLAW_HTTP_BOOT_MODE``.

Most of these run in a **subprocess**. The conftest imports
``agentclaw.community.adapters.http.app`` at session start, so by the time any
test body runs the module is in ``sys.modules`` and already finalized in
``eager`` mode; re-importing it in-process proves nothing about what an import
does. Each subprocess therefore boots a clean interpreter, installs its probes
*before* the import, and prints one ``@@JSON@@`` line the parent asserts on.

What the probes cover:

- fork-safety — ``threading.Thread.start`` and ``socket.socket.connect`` are
  wrapped before the import, so a background thread or an outbound connection
  started at module level is recorded rather than inferred.
- route registration — ``route_table`` reads the generated OpenAPI document,
  which is the only resolved view of the surface (see its docstring).
- the three worker-runtime steps — ``eager_check_critical_bindings``,
  ``init_principal_verifier_config`` (the only boot-time secret-store read) and
  ``install_middleware`` are replaced with counting spies. They are imported
  *function-locally* inside ``boot.finalize_worker_runtime``, so patching the
  defining module before the app import is enough to intercept them.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[4]      # .../src/backend
_SRC = _BACKEND_ROOT / "src"
_MARKER = "@@JSON@@"

# Installed before the app import in every subprocess.
_PREAMBLE = """
import json, os, socket, sys, threading

_probe = {"threads": [], "connects": [], "eager_check": 0, "verifier_init": 0,
          "install_middleware": 0}

_orig_thread_start = threading.Thread.start
def _thread_start(self):
    _probe["threads"].append(self.name)
    return _orig_thread_start(self)
threading.Thread.start = _thread_start

_orig_connect = socket.socket.connect
def _connect(self, address):
    _probe["connects"].append(repr(address))
    return _orig_connect(self, address)
socket.socket.connect = _connect

# Spy on the three worker-runtime steps. ``boot.finalize_worker_runtime``
# imports each one function-locally, so replacing the module attribute here —
# before the app is imported — intercepts the real call.
import agentclaw.community.di.container as _container
_container.eager_check_critical_bindings = (
    lambda injector: _probe.__setitem__("eager_check", _probe["eager_check"] + 1)
)

import agentclaw.community.utils.gateway_principal_config as _gpc
_gpc.init_principal_verifier_config = (
    lambda resolver, name, strict=False:
        _probe.__setitem__("verifier_init", _probe["verifier_init"] + 1)
)

import agentclaw.community.adapters.http.middleware as _mw
_real_install_middleware = _mw.install_middleware
def _install_middleware(app, **kwargs):
    _probe["install_middleware"] += 1
    return _real_install_middleware(app, **kwargs)
_mw.install_middleware = _install_middleware


# [path, method, operationId] for every registered operation.
#
# Read out of the generated OpenAPI document rather than by walking
# ``app.routes``: since FastAPI 0.138 ``include_router`` appends a lazy
# ``_IncludedRouter`` marker that carries neither the child routes nor the
# prefix they are mounted under, so a naive walk sees a handful of top-level
# entries and misses the whole surface. ``openapi()`` is the resolved view.
#
# The cache is cleared on both sides because the same app is inspected before
# and after ``finalize_worker_runtime``, and ``openapi()`` memoizes into
# ``app.openapi_schema``.
def route_table(app):
    app.openapi_schema = None
    try:
        spec = app.openapi()
        return sorted(
            [path, method.upper(), operation.get("operationId")]
            for path, operations in spec.get("paths", {}).items()
            for method, operation in operations.items()
        )
    finally:
        app.openapi_schema = None


def emit(payload):
    payload.setdefault("probe", _probe)
    sys.stdout.write("%s%s\\n" % ("@@JSON@@", json.dumps(payload, sort_keys=True)))
"""


def _run(body: str, **env_overrides: str) -> dict:
    """Run ``_PREAMBLE + body`` in a fresh interpreter, return its JSON verdict."""
    env = dict(os.environ)
    env["DEPLOY_PROFILE"] = env.get("DEPLOY_PROFILE", "test")
    # FastAPI derives a multi-method route's generated operation id from
    # ``list(route.methods)[0]`` — iteration order over a set of strings, so it
    # varies with the interpreter's hash seed. That predates this seam and has
    # nothing to do with boot mode; pinning the seed is what lets the two modes
    # be compared for real rather than papered over with a normalizer.
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONPATH"] = os.pathsep.join([str(_BACKEND_ROOT), str(_SRC)])
    env.pop("AGENTCLAW_HTTP_BOOT_MODE", None)
    env.pop("SERVER_ENV", None)
    env.pop("REAL_SERVER_ENV", None)
    env.pop("ALIPAY_APP_ENV", None)
    env.update(env_overrides)

    proc = subprocess.run(
        [sys.executable, "-c", _PREAMBLE + body],
        capture_output=True, text=True, env=env, cwd=str(_BACKEND_ROOT), timeout=600,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(_MARKER)]
    assert lines, (
        f"subprocess emitted no verdict (rc={proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout[-4000:]}\n--- stderr ---\n{proc.stderr[-4000:]}"
    )
    return json.loads(lines[-1][len(_MARKER):])


# ---------------------------------------------------------------------------
# 1. eager (the default) still completes everything at import
# ---------------------------------------------------------------------------

_IMPORT_AND_REPORT = """
from agentclaw.community.adapters.http import app as app_mod
from agentclaw.community.adapters.http import boot

emit({
    "routes": route_table(app_mod.app),
    "middleware": [m.cls.__name__ for m in app_mod.app.user_middleware],
    "has_injector": getattr(app_mod.app.state, "injector", None) is not None,
    "finalized": boot.worker_runtime_finalized(),
})
"""


def test_eager_is_the_default_and_finishes_at_import():
    """No env var set ⇒ importing the app is fully initialized, as before."""
    got = _run(_IMPORT_AND_REPORT, SERVER_ENV="pre")

    assert got["has_injector"] is True
    assert got["finalized"] is True
    assert got["middleware"], "eager import installed no middleware"
    # ``pre`` gates both of these on, and each must have fired exactly once.
    assert got["probe"]["eager_check"] == 1
    assert got["probe"]["verifier_init"] == 1
    assert got["probe"]["install_middleware"] == 1


def test_eager_mode_may_be_selected_explicitly():
    got = _run(_IMPORT_AND_REPORT, AGENTCLAW_HTTP_BOOT_MODE="eager")
    assert got["has_injector"] is True
    assert got["finalized"] is True


def test_unknown_boot_mode_fails_the_import():
    """A typo must crash, not silently pick a mode — same as DEPLOY_PROFILE."""
    from agentclaw.community.adapters.http.boot import BOOT_MODE_ENV, BootMode

    os.environ[BOOT_MODE_ENV] = "prelaod"
    try:
        with pytest.raises(RuntimeError, match=BOOT_MODE_ENV):
            BootMode.detect()
    finally:
        del os.environ[BOOT_MODE_ENV]


def test_unset_and_empty_boot_mode_are_eager():
    from agentclaw.community.adapters.http.boot import BOOT_MODE_ENV, BootMode

    previous = os.environ.pop(BOOT_MODE_ENV, None)
    try:
        assert BootMode.detect() is BootMode.EAGER
        os.environ[BOOT_MODE_ENV] = "   "
        assert BootMode.detect() is BootMode.EAGER
        os.environ[BOOT_MODE_ENV] = "PRELOAD"
        assert BootMode.detect() is BootMode.PRELOAD
    finally:
        os.environ.pop(BOOT_MODE_ENV, None)
        if previous is not None:
            os.environ[BOOT_MODE_ENV] = previous


# ---------------------------------------------------------------------------
# 2. preload leaves no worker-owned runtime behind
# ---------------------------------------------------------------------------

def test_preload_import_starts_no_worker_runtime():
    got = _run(_IMPORT_AND_REPORT, AGENTCLAW_HTTP_BOOT_MODE="preload", SERVER_ENV="pre")

    assert got["routes"], "preload import registered no routes at all"
    assert got["middleware"] == [], (
        "preload import installed middleware — the master would hand forked "
        f"workers a frozen stack: {got['middleware']}"
    )
    assert got["has_injector"] is False, "preload import built and attached an injector"
    assert got["finalized"] is False
    # Even with SERVER_ENV=pre — the gate that makes these fire in eager mode —
    # neither the eager binding check nor the secret-store read may happen in a
    # process that is only going to fork.
    assert got["probe"]["eager_check"] == 0
    assert got["probe"]["verifier_init"] == 0
    assert got["probe"]["install_middleware"] == 0
    # Fork-safety proper: nothing a child could inherit.
    assert got["probe"]["threads"] == []
    assert got["probe"]["connects"] == []


# ---------------------------------------------------------------------------
# 3 + 4. finalize wires the worker, and only once per process
# ---------------------------------------------------------------------------

_PRELOAD_THEN_FINALIZE = """
from agentclaw.community.adapters.http import app as app_mod
from agentclaw.community.adapters.http import boot

before = {
    "has_injector": getattr(app_mod.app.state, "injector", None) is not None,
    "finalized": boot.worker_runtime_finalized(),
    "routes": route_table(app_mod.app),
}

# ``_app_lifespan`` looks this name up in the app module's globals at call
# time, so replacing it here records which injector the lifespan discovered
# from without driving every real participant's startup.
seen_by_lifespan = []
app_mod.discover_lifecycle_participants = (
    lambda injector: (seen_by_lifespan.append(id(injector)), [])[1]
)

app_mod.finalize_worker_runtime()
first_injector = id(app_mod.app.state.injector)
after_one = {
    "middleware": [m.cls.__name__ for m in app_mod.app.user_middleware],
    "routes": route_table(app_mod.app),
    "finalized": boot.worker_runtime_finalized(),
}

# Second call in the same pid must be inert.
app_mod.finalize_worker_runtime()

import asyncio
async def _drive(app):
    async with app.router.lifespan_context(app):
        pass
asyncio.run(_drive(app_mod.app))

emit({
    "before": before,
    "after_one": after_one,
    "first_injector": first_injector,
    "second_injector": id(app_mod.app.state.injector),
    "middleware_after_two": [m.cls.__name__ for m in app_mod.app.user_middleware],
    "routes_after_two": route_table(app_mod.app),
    "lifespan_saw": seen_by_lifespan,
})
"""


@pytest.fixture(scope="module")
def preload_then_finalize() -> dict:
    return _run(
        _PRELOAD_THEN_FINALIZE,
        AGENTCLAW_HTTP_BOOT_MODE="preload",
        SERVER_ENV="pre",
    )


def test_finalize_builds_and_attaches_a_worker_injector(preload_then_finalize):
    got = preload_then_finalize
    assert got["before"]["has_injector"] is False
    assert got["before"]["finalized"] is False
    assert got["after_one"]["finalized"] is True
    assert got["after_one"]["middleware"], "finalize installed no middleware"


def test_finalize_mounts_the_di_conditional_routers(preload_then_finalize):
    """The one route registration that waits for the worker's injector.

    Under DEPLOY_PROFILE=test the column binds a populated ``OptionalRouters``,
    so finalize adds exactly the local-mode surface on top of what construction
    registered — and touches nothing else.
    """
    got = preload_then_finalize
    before = {row[0] for row in got["before"]["routes"]}
    after = {row[0] for row in got["after_one"]["routes"]}

    assert after - before == {"/local/sql/execute"}, (
        "finalize should mount exactly the profile's OptionalRouters on top of "
        f"the constructed surface; it added {sorted(after - before)}"
    )
    assert not before - after, "finalize dropped a route registered at import"


def test_finalize_runs_each_step_exactly_once(preload_then_finalize):
    probe = preload_then_finalize["probe"]
    assert probe["eager_check"] == 1
    assert probe["verifier_init"] == 1
    assert probe["install_middleware"] == 1


def test_lifespan_uses_the_worker_injector(preload_then_finalize):
    got = preload_then_finalize
    assert got["lifespan_saw"] == [got["first_injector"]], (
        "the lifespan discovered participants from something other than the "
        "injector finalize attached to app.state"
    )


def test_second_finalize_in_the_same_pid_is_inert(preload_then_finalize):
    got = preload_then_finalize
    assert got["second_injector"] == got["first_injector"], (
        "a second finalize replaced the injector under a running app"
    )
    assert got["middleware_after_two"] == got["after_one"]["middleware"]
    assert got["routes_after_two"] == got["after_one"]["routes"]
    # The spies above already assert one call each, which is the same statement
    # from the other side: the second call did no work.


# ---------------------------------------------------------------------------
# 5. the guard is a pid, not a boolean
# ---------------------------------------------------------------------------

def test_a_foreign_finalized_pid_is_never_read_as_this_process_being_done():
    """The flag is a pid, so it says nothing about a process that did not set it.

    A boolean would read ``True`` here and let the caller skip initialization
    entirely. What this process does *instead* of skipping — refuse, because an
    inherited marker comes with an inherited runtime — is the subject of
    ``test_a_child_forked_after_finalize_refuses_rather_than_stacking``.
    """
    from fastapi import FastAPI

    from agentclaw.community.adapters.http import boot
    from agentclaw.community.di import DeployProfile

    previous = boot._finalized_pid
    try:
        boot._finalized_pid = os.getpid() + 1_000_000   # never this process
        assert boot.worker_runtime_finalized() is False

        fresh = FastAPI()
        with pytest.raises(RuntimeError, match="finalized the worker runtime"):
            boot.finalize_worker_runtime(fresh, profile=DeployProfile.detect())

        # Refused before doing anything, so nothing was half-installed.
        assert getattr(fresh.state, "injector", None) is None
        assert fresh.user_middleware == []
    finally:
        boot._finalized_pid = previous


def test_a_foreign_failed_pid_is_refused_the_same_way():
    """A fork from a process that failed *partway* inherits the same problem.

    Its parent may already have attached an injector and installed part of the
    stack before raising, so the child is in the same position as one forked
    after a successful finalize: it cannot replace what it inherited, only
    append to it.
    """
    from fastapi import FastAPI

    from agentclaw.community.adapters.http import boot
    from agentclaw.community.di import DeployProfile

    previous_finalized, previous_failed = boot._finalized_pid, boot._failed_pid
    try:
        boot._finalized_pid = None
        boot._failed_pid = os.getpid() + 1_000_000     # never this process

        fresh = FastAPI()
        with pytest.raises(RuntimeError, match="began finalizing the worker runtime and failed"):
            boot.finalize_worker_runtime(fresh, profile=DeployProfile.detect())

        assert getattr(fresh.state, "injector", None) is None
        assert fresh.user_middleware == []
    finally:
        boot._finalized_pid, boot._failed_pid = previous_finalized, previous_failed


def test_a_failed_finalize_refuses_to_be_retried_in_the_same_process():
    """A half-wired app must not get a second sequence stacked on top of it."""
    from fastapi import FastAPI

    from agentclaw.community.adapters.http import boot
    from agentclaw.community.di import DeployProfile

    boom = RuntimeError("secret store unreachable")
    previous_finalized, previous_failed = boot._finalized_pid, boot._failed_pid
    real_install = boot._install_worker_runtime
    try:
        boot._finalized_pid = None
        boot._failed_pid = None

        def _explode(app, profile):
            # Wire *part* of it first, the way a real mid-sequence failure would.
            from fastapi_injector import attach_injector

            from agentclaw.community.di import build_injector

            attach_injector(app, build_injector(profile=profile))
            raise boom

        boot._install_worker_runtime = _explode
        first = FastAPI()
        with pytest.raises(RuntimeError) as failure:
            boot.finalize_worker_runtime(first, profile=DeployProfile.detect())
        assert failure.value is boom
        assert boot.worker_runtime_finalized() is False, (
            "a failed finalize marked the process ready"
        )

        # The retry is refused, and — the point of the guard — it is refused
        # before any step runs again, so nothing is installed twice.
        boot._install_worker_runtime = real_install
        with pytest.raises(RuntimeError, match="already failed in pid"):
            boot.finalize_worker_runtime(first, profile=DeployProfile.detect())
        assert first.user_middleware == []
    finally:
        boot._install_worker_runtime = real_install
        boot._finalized_pid, boot._failed_pid = previous_finalized, previous_failed


# The supported preload shape: the master constructs only, forks, and each child
# finalizes its own runtime. Reported by both sides so the parent's and the
# child's runtimes can be compared.
_REAL_FORK = """
import os, json

from agentclaw.community.adapters.http import app as app_mod
from agentclaw.community.adapters.http import boot

# Nothing is finalized before the fork — that is what preload mode buys.
pre_fork = {
    "finalized": boot.worker_runtime_finalized(),
    "marker": boot._finalized_pid,
    "middleware": [m.cls.__name__ for m in app_mod.app.user_middleware],
}

read_fd, write_fd = os.pipe()
child_pid = os.fork()
if child_pid == 0:
    os.close(read_fd)
    try:
        report = {"pid": os.getpid(), "finalized_before": boot.worker_runtime_finalized()}
        app_mod.finalize_worker_runtime()
        report["finalized_after"] = boot.worker_runtime_finalized()
        report["marker_after"] = boot._finalized_pid
        report["injector"] = id(app_mod.app.state.injector)
        report["middleware"] = [m.cls.__name__ for m in app_mod.app.user_middleware]
        os.write(write_fd, json.dumps(report).encode())
    finally:
        os.close(write_fd)
        os._exit(0)

os.close(write_fd)
chunks = []
while True:
    chunk = os.read(read_fd, 65536)
    if not chunk:
        break
    chunks.append(chunk)
os.close(read_fd)
_, status = os.waitpid(child_pid, 0)

# The parent finalizes only after the fork, so it gets a runtime of its own.
app_mod.finalize_worker_runtime()

emit({
    "pre_fork": pre_fork,
    "parent": {
        "pid": os.getpid(),
        "injector": id(app_mod.app.state.injector),
        "middleware": [m.cls.__name__ for m in app_mod.app.user_middleware],
    },
    "child": json.loads(b"".join(chunks).decode()),
    "child_status": status,
})
"""


def test_a_forked_child_finalizes_its_own_runtime():
    """The supported shape: construct, fork, then finalize in each worker."""
    got = _run(_REAL_FORK, AGENTCLAW_HTTP_BOOT_MODE="preload")

    assert got["child_status"] == 0
    assert got["pre_fork"] == {"finalized": False, "marker": None, "middleware": []}

    parent, child = got["parent"], got["child"]
    assert child["pid"] != parent["pid"]
    assert child["finalized_before"] is False
    assert child["finalized_after"] is True
    assert child["marker_after"] == child["pid"], (
        "the child recorded someone else's pid as its own finalize marker"
    )
    # Each side wired itself, and exactly once — no doubling anywhere.
    assert child["middleware"] == parent["middleware"]
    assert len(set(child["middleware"])) == len(child["middleware"]), (
        f"a middleware class was installed twice: {child['middleware']}"
    )


# The unsupported shape the pid guard has to catch: a process that finalized and
# only then forked. The child inherits a whole wired runtime, not just a flag.
_FINALIZE_THEN_FORK = """
import os, json

from agentclaw.community.adapters.http import app as app_mod
from agentclaw.community.adapters.http import boot

app_mod.finalize_worker_runtime()
parent = {
    "pid": os.getpid(),
    "middleware": [m.cls.__name__ for m in app_mod.app.user_middleware],
}

read_fd, write_fd = os.pipe()
child_pid = os.fork()
if child_pid == 0:
    os.close(read_fd)
    try:
        report = {
            "pid": os.getpid(),
            "inherited_marker": boot._finalized_pid,
            "finalized_before": boot.worker_runtime_finalized(),
        }
        try:
            app_mod.finalize_worker_runtime()
            report["raised"] = None
        except RuntimeError as exc:
            report["raised"] = str(exc)
        report["middleware"] = [m.cls.__name__ for m in app_mod.app.user_middleware]
        os.write(write_fd, json.dumps(report).encode())
    finally:
        os.close(write_fd)
        os._exit(0)

os.close(write_fd)
chunks = []
while True:
    chunk = os.read(read_fd, 65536)
    if not chunk:
        break
    chunks.append(chunk)
os.close(read_fd)
os.waitpid(child_pid, 0)

emit({"parent": parent, "child": json.loads(b"".join(chunks).decode())})
"""


def test_a_child_forked_after_finalize_refuses_rather_than_stacking():
    """Re-finalizing an inherited runtime would append a second stack, not replace it.

    A child of a process that already finalized inherits that process's
    ``user_middleware`` — holding its auth plugin and tracer, and behind them its
    engines, pools and fds, which a fork copies rather than reopens. Running the
    sequence again cannot swap any of that out; it only appends, so every request
    would traverse two stacks. The child cannot repair its own process image, so
    it must say so instead of serving.
    """
    got = _run(_FINALIZE_THEN_FORK, AGENTCLAW_HTTP_BOOT_MODE="preload")
    parent, child = got["parent"], got["child"]

    assert child["inherited_marker"] == parent["pid"]
    assert child["finalized_before"] is False, (
        "the child treated the parent's marker as its own"
    )
    assert child["raised"] is not None, (
        "the child silently re-finalized over an inherited runtime"
    )
    assert f"Pid {parent['pid']} finalized the worker runtime" in child["raised"]
    assert "preload" in child["raised"], (
        "the refusal should name the mode that avoids this shape"
    )
    assert child["middleware"] == parent["middleware"], (
        "the refused finalize still mutated the inherited middleware stack: "
        f"{len(parent['middleware'])} entries became {len(child['middleware'])}"
    )


# ---------------------------------------------------------------------------
# 6. the two modes produce the same application
# ---------------------------------------------------------------------------

_OPENAPI_REPORT = """
from agentclaw.community.adapters.http import app as app_mod

if os.environ.get("AGENTCLAW_HTTP_BOOT_MODE") == "preload":
    app_mod.finalize_worker_runtime()

emit({
    "routes": route_table(app_mod.app),
    "openapi": app_mod.app.openapi(),
    "middleware": [m.cls.__name__ for m in app_mod.app.user_middleware],
})
"""


def test_eager_and_preload_plus_finalize_are_the_same_app():
    eager = _run(_OPENAPI_REPORT)
    preload = _run(_OPENAPI_REPORT, AGENTCLAW_HTTP_BOOT_MODE="preload")

    assert preload["routes"] == eager["routes"], (
        "route paths / methods / operation ids diverge between boot modes"
    )
    assert preload["openapi"] == eager["openapi"], (
        "the generated OpenAPI document diverges between boot modes"
    )
    assert preload["middleware"] == eager["middleware"], (
        "middleware set or order diverges between boot modes"
    )


# ---------------------------------------------------------------------------
# 7. call timing: finalize must precede the first ASGI call
# ---------------------------------------------------------------------------

_LIFESPAN_WITHOUT_FINALIZE = """
import asyncio

from agentclaw.community.adapters.http import app as app_mod

async def _drive():
    async with app_mod.app.router.lifespan_context(app_mod.app):
        pass

try:
    asyncio.run(_drive())
except RuntimeError as exc:
    emit({"raised": type(exc).__name__, "message": str(exc)})
else:
    emit({"raised": None, "message": ""})
"""


def test_lifespan_without_finalize_fails_loudly():
    """Serving un-finalized is the one outcome worse than not booting."""
    got = _run(_LIFESPAN_WITHOUT_FINALIZE, AGENTCLAW_HTTP_BOOT_MODE="preload")

    assert got["raised"] == "RuntimeError", (
        "an un-finalized preload app entered its lifespan without complaint — "
        "it would then serve requests with no auth/tenant/CORS middleware"
    )
    assert "finalize_worker_runtime()" in got["message"]


_REQUEST_WITHOUT_FINALIZE = """
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http import app as app_mod

# TestClient only drives the lifespan when used as a context manager. Calling it
# directly is exactly the host that never sends a lifespan event — the case the
# lifespan guard cannot cover.
client = TestClient(app_mod.app)
response = client.get("/api/health")

emit({
    "status": response.status_code,
    "body": response.text,
    "middleware": [m.cls.__name__ for m in app_mod.app.user_middleware],
})
"""


def test_an_unfinalized_worker_refuses_requests_without_a_lifespan():
    """A host that skips the lifespan must not get an un-wired app serving 200s.

    Nothing in ASGI obliges a host to drive the lifespan protocol, so the
    ``_app_lifespan`` check alone would let a preload worker that skipped
    finalize answer its first request on a stack with no auth, no tenant
    scoping, no tracing and no CORS — and cache that stack for the rest of its
    life.
    """
    got = _run(_REQUEST_WITHOUT_FINALIZE, AGENTCLAW_HTTP_BOOT_MODE="preload")

    assert got["status"] == 503, (
        f"an un-finalized worker answered {got['status']} on a real request; it "
        "would have served every later one through an un-wired stack too"
    )
    assert "worker runtime not initialized" in got["body"]
    # The guard is not in ``user_middleware`` at all — it wraps the built stack
    # from outside, which is what lets it short-circuit before anything else.
    assert got["middleware"] == []


def test_a_finalized_worker_serves_normally_through_the_guard():
    """The guard is inert once the runtime is up — in either mode."""
    got = _run(_REQUEST_WITHOUT_FINALIZE.replace(
        "client = TestClient(app_mod.app)",
        "app_mod.finalize_worker_runtime()\nclient = TestClient(app_mod.app)",
    ), AGENTCLAW_HTTP_BOOT_MODE="preload")

    assert got["status"] == 200, got["body"][:300]
    assert "RequireWorkerRuntime" not in got["middleware"], (
        "the guard wraps the stack from outside; it is not a user middleware"
    )


_INHERITED_STACK_REQUEST = """
import os, json

import agentclaw.community.adapters.http.middleware as mw

# Record whether any *worker-owned* middleware body runs. UserContextMiddleware
# is the one that matters most: it calls the auth plugin, which reads the
# database — on the parent's pool, in an inherited stack.
ran = []
_real_dispatch = mw.UserContextMiddleware.dispatch
async def _spy(self, request, call_next):
    ran.append("UserContextMiddleware")
    return await _real_dispatch(self, request, call_next)
mw.UserContextMiddleware.dispatch = _spy

from agentclaw.community.adapters.http import app as app_mod

app_mod.finalize_worker_runtime()          # parent wires the stack, then forks
parent_middleware = [m.cls.__name__ for m in app_mod.app.user_middleware]

read_fd, write_fd = os.pipe()
child_pid = os.fork()
if child_pid == 0:
    os.close(read_fd)
    try:
        from fastapi.testclient import TestClient
        response = TestClient(app_mod.app).get("/api/health")
        os.write(write_fd, json.dumps({
            "status": response.status_code,
            "worker_middleware_ran": ran,
        }).encode())
    finally:
        os.close(write_fd)
        os._exit(0)

os.close(write_fd)
chunks = []
while True:
    chunk = os.read(read_fd, 65536)
    if not chunk:
        break
    chunks.append(chunk)
os.close(read_fd)
os.waitpid(child_pid, 0)

emit({
    "parent_middleware": parent_middleware,
    "child": json.loads(b"".join(chunks).decode()),
})
"""


def test_the_guard_short_circuits_before_inherited_middleware_runs():
    """Refusing late is not refusing: the guard must be outermost.

    Starlette's ``add_middleware`` prepends, so a guard added at construction
    would end up *innermost* — everything ``install_middleware`` adds later wraps
    around it. A child forked from a finalized parent inherits that whole stack,
    so the parent's ``UserContextMiddleware`` would run, and with it the parent's
    auth plugin against the parent's connection pool, before the 503 came back.
    Hence wrapping the built stack rather than joining it.
    """
    got = _run(_INHERITED_STACK_REQUEST, AGENTCLAW_HTTP_BOOT_MODE="preload")

    assert got["parent_middleware"], "the parent never installed its stack"
    assert got["child"]["status"] == 503
    assert got["child"]["worker_middleware_ran"] == [], (
        "the child executed inherited worker middleware before refusing — the "
        "auth plugin it ran belongs to the parent, and so does the pool behind it"
    )


def test_lifespan_refuses_an_inherited_injector_even_though_one_is_attached():
    """Presence of an injector is not evidence *this* process may use it.

    A child forked from a finalized parent inherits a perfectly non-``None``
    ``app.state.injector``. Gating the lifespan on presence alone would let it
    run ``bootstrap()``/``startup()`` over the parent's participants — starting
    background workers on the parent's pools — while the request guard is
    meanwhile refusing to let that same process serve.
    """
    import asyncio

    from agentclaw.community.adapters.http import app as app_mod
    from agentclaw.community.adapters.http import boot

    # Eager mode finalized at import, so an injector really is attached here;
    # only the pid is made foreign, which is exactly a forked child's view.
    assert getattr(app_mod.app.state, "injector", None) is not None

    discovered: list[object] = []
    real_discover = app_mod.discover_lifecycle_participants
    previous = boot._finalized_pid
    try:
        app_mod.discover_lifecycle_participants = (
            lambda injector: (discovered.append(injector), [])[1]
        )
        boot._finalized_pid = os.getpid() + 1_000_000

        async def _drive():
            async with app_mod.app.router.lifespan_context(app_mod.app):
                pass

        with pytest.raises(RuntimeError, match="has not finalized its worker runtime"):
            asyncio.run(_drive())

        assert discovered == [], (
            "the lifespan resolved participants from an inherited injector before "
            "refusing — bootstrap()/startup() would have run on the parent's"
        )
    finally:
        app_mod.discover_lifecycle_participants = real_discover
        boot._finalized_pid = previous


# Deliberately does NOT clear ``app.openapi_schema``: the point is what a worker
# serves when a preload master warmed the cache before forking.
_WARM_OPENAPI_CACHE = """
from agentclaw.community.adapters.http import app as app_mod

# A master that exports or inspects the schema pre-fork warms the cache with the
# phase-1 route set only — finalize has not mounted OptionalRouters yet.
before = sorted(app_mod.app.openapi()["paths"])
cached_before = app_mod.app.openapi_schema is not None

app_mod.finalize_worker_runtime()

after = sorted(app_mod.app.openapi()["paths"])

emit({
    "cached_before": cached_before,
    "before_has_local_sql": "/local/sql/execute" in before,
    "after_has_local_sql": "/local/sql/execute" in after,
})
"""


def test_a_warm_openapi_cache_does_not_hide_routers_finalize_mounts():
    """What the worker serves must match what it routes, cache or no cache.

    ``finalize_worker_runtime`` mounts the DI-conditional routers *after*
    construction, so a master that called ``app.openapi()`` before forking has a
    cache describing fewer routes than the worker actually serves. FastAPI 0.138
    invalidates on ``router._get_routes_version()`` rather than on "is the cache
    empty", so ``include_router`` is enough and the seam needs no explicit
    invalidation — but that is a property of the pinned FastAPI, not of this
    code, so it is asserted here rather than assumed. If a future FastAPI goes
    back to caching unconditionally, this fails and the fix is to clear
    ``app.openapi_schema`` after the mount loop in ``_install_worker_runtime``.
    """
    got = _run(_WARM_OPENAPI_CACHE, AGENTCLAW_HTTP_BOOT_MODE="preload")

    assert got["cached_before"] is True, "the master never warmed the cache"
    assert got["before_has_local_sql"] is False, (
        "construction mounted the DI-conditional router after all — this test no "
        "longer exercises the stale-cache shape"
    )
    assert got["after_has_local_sql"] is True, (
        "the worker routes /local/sql/execute but serves an OpenAPI document that "
        "omits it; clear app.openapi_schema after finalize mounts OptionalRouters"
    )


def test_middleware_cannot_be_installed_after_the_stack_is_built():
    """Why the timing requirement exists, pinned against Starlette's behavior.

    ``finalize_worker_runtime`` calls ``add_middleware``; Starlette caches
    ``middleware_stack`` on the first ASGI call and rejects additions after. If
    this ever stops raising, the ordering constraint in :mod:`.boot` has changed
    and the seam's contract needs revisiting.
    """
    from fastapi import FastAPI
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    probe = FastAPI()
    probe.middleware_stack = probe.build_middleware_stack()   # what __call__ does
    with pytest.raises(RuntimeError, match="after an application has started"):
        probe.add_middleware(TrustedHostMiddleware)
