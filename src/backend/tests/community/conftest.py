"""Community test-suite bootstrap (self-contained).

After the community/corp split there is **no shared root ``tests/conftest.py``**:
pytest applies conftests by directory ancestry, so once ``tests/community`` is
extracted into its own repo a community-owned root conftest would no longer cover
``tests/corp`` (and vice versa). Each subtree therefore carries its own full
bootstrap. The trivial, stable lines (chdir / NO_PROXY / markers / sofapy patch)
are duplicated in the corp conftest by design — each repo owns its copy — while
the logic-bearing helpers (disk-leak guard, BaaS respx mock) are imported from the
shipped ``agentclaw.community.testing`` package so both trees share one source.

This conftest selects ``DEPLOY_PROFILE=test`` (corp-free community wiring). The
corp conftest selects ``corp_test`` (community doubles + the corp reuse column).
"""
from unittest.mock import MagicMock, patch
import os
import pytest


def pytest_addoption(parser):
    """Register --snapshot-update (read by the gateway contract conftest).

    Registered once per invocation. The two subtree conftests each register it;
    a single invocation only loads one subtree, so there is no double-registration.
    """
    parser.addoption(
        "--snapshot-update",
        action="store_true",
        default=False,
        help="Update schema snapshots instead of validating against them",
    )


# Ensure config is loaded from the backend root (src/backend), not src/agentclaw.
# This must happen before any module import that uses get_config(). Resolve the
# root by walking up to the dir containing pytest.ini so it is independent of this
# conftest's depth.
def _backend_root() -> str:
    from pathlib import Path

    here = Path(os.path.abspath(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pytest.ini").exists():
            return str(candidate)
    raise RuntimeError("could not locate the backend root (no pytest.ini upward)")


os.chdir(_backend_root())

# Single deploy-profile switch (B1): default the community suite into the ``test``
# profile (corp-free LOCAL stubs + SQLite, no MOSN / no ZDAS). ``setdefault`` — not
# ``[...] =`` — so an integration runner can opt out by exporting a different
# ``DEPLOY_PROFILE`` before invoking pytest.
os.environ.setdefault("DEPLOY_PROFILE", "test")

# Bypass any corporate HTTP_PROXY for loopback addresses. Without this, the
# data-proxy test factory (which points the engine URL at a freshly-released
# 127.0.0.1 port to force ``EngineUnreachable``) ends up routing the request
# through the dev's local proxy, which intercepts and responds 500 instead of
# letting httpx see a ``ConnectError``.
_existing_no_proxy = os.environ.get("NO_PROXY", "")
_loopback_entries = ("127.0.0.1", "localhost", "::1")
if not all(host in _existing_no_proxy for host in _loopback_entries):
    extra = ",".join(_loopback_entries)
    new_value = f"{_existing_no_proxy},{extra}".lstrip(",") if _existing_no_proxy else extra
    os.environ["NO_PROXY"] = new_value
    os.environ["no_proxy"] = new_value

try:
    import sofapy_base  # noqa: F401
    # sofapy_base is installed: patch at the source before it tries to connect to MOSN
    patch("sofapy_base.app.layotto_manager.get_layotto_manager", return_value=MagicMock()).start()
except ImportError:
    # sofapy_base not installed: use local mode stubs
    from agentclaw.community.local import patch_sofapy_for_local
    patch_sofapy_for_local()


# Wire the DI container once before any test runs. Most services resolve their
# typed config through the injector attached to the FastAPI app (set by
# ``adapters/http/app.py`` during normal startup). Unit tests rarely import
# ``app.py`` themselves, so we attach a default injector here for tests that DO
# import it. The profile is read from ``DEPLOY_PROFILE`` (set above), matching the
# app.py / main.py bootstrap.
def _wire_app_injector_for_tests():
    from fastapi_injector import attach_injector

    from agentclaw.community.adapters.http.app import app
    from agentclaw.community.di import DeployProfile, build_injector

    injector = build_injector(profile=DeployProfile.detect())
    attach_injector(app, injector)


_wire_app_injector_for_tests()


# ── Shared DI test fixtures ─────────────────────────────────────────────────
@pytest.fixture
def test_injector():
    """A fresh injector wired the way unit tests want it.

    Mirrors the profile-column app boot: the business modules plus the
    profile's infrastructure column. Tests that need to swap a binding can
    install ``Module`` instances on top via the returned injector.
    """
    from agentclaw.community.di import DeployProfile, build_injector

    return build_injector(profile=DeployProfile.detect())


@pytest.fixture
def client(test_injector):
    """``TestClient`` over the FastAPI app with a custom injector attached.

    Imports ``app`` lazily — pulling it at fixture time avoids dragging
    the full router graph into tests that only want the injector.
    """
    from fastapi.testclient import TestClient
    from fastapi_injector import attach_injector

    from agentclaw.community.adapters.http.app import app

    # Save/restore ``app.state.injector`` so this fixture doesn't leak
    # ``test_injector`` into later tests.
    prev_app_injector = getattr(app.state, "injector", None)
    attach_injector(app, test_injector)
    try:
        yield TestClient(app)
    finally:
        if prev_app_injector is not None:
            attach_injector(app, prev_app_injector)


@pytest.fixture(autouse=True)
def _no_real_baas_calls():
    """Autouse: 拦截单测进程内所有 httpx 调到 BaaS (localhost:8890) 的请求。

    路由体在 ``agentclaw.community.testing.baas_mock``（社区包内, 两棵测试树共享）。
    某条 test 想自定义 endpoint 行为：用 respx 在 test 内 add route 覆盖。
    """
    from agentclaw.community.testing.baas_mock import mock_baas_calls

    with mock_baas_calls() as r:
        yield r


def pytest_sessionfinish(session, exitstatus):
    """Fail the session if any production code path wrote a known file-backed
    SQLite DB to the backend root during the run (belt-and-suspenders regression
    guard; ``plugins/local/database.py`` is structurally in-memory only)."""
    from pathlib import Path
    from agentclaw.community.testing.disk_leak_guard import find_leaked_sqlite_files

    # The top-level ``os.chdir(_backend_root())`` puts us at ``src/backend/``.
    leaked = find_leaked_sqlite_files(Path.cwd())
    if leaked:
        names = ", ".join(str(p) for p in leaked)
        print(f"\n[disk-leak guard] SQLite leak detected: {names}", flush=True)
        session.exitstatus = 1


def pytest_configure(config):
    """Register custom markers for test categorization."""
    config.addinivalue_line("markers", "e2e: end-to-end tests (requires full environment)")
    config.addinivalue_line("markers", "acceptance: live-backend acceptance tests (RUN_ACCEPTANCE=1)")
    config.addinivalue_line("markers", "requires_mosn: requires MOSN service running")
    config.addinivalue_line("markers", "requires_zdas: requires ZDAS database connection")
    config.addinivalue_line("markers", "requires_services: requires external services")
    config.addinivalue_line("markers", "integration: integration tests")
    config.addinivalue_line("markers", "unit: unit tests")


def pytest_runtest_setup(item):
    """Skip tests based on environment variables (MOSN / ZDAS / external services)."""
    markers = {m.name for m in item.iter_markers()}

    if os.getenv("RUN_ALL_TESTS"):
        return

    if "requires_mosn" in markers and not os.getenv("RUN_MOSN_TESTS"):
        pytest.skip("跳过 MOSN 依赖测试（设置 RUN_MOSN_TESTS=1 运行）")

    if "requires_zdas" in markers and not os.getenv("RUN_ZDAS_TESTS"):
        pytest.skip("跳过 ZDAS 依赖测试（设置 RUN_ZDAS_TESTS=1 运行）")

    if "requires_services" in markers and not os.getenv("RUN_SERVICE_TESTS"):
        pytest.skip("跳过外部服务依赖测试（设置 RUN_SERVICE_TESTS=1 运行）")
