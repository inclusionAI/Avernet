# src/backend/tests/acceptance/conftest.py
"""Acceptance (路 B) fixtures — start real backend + baas, yield base_url + fs_root.

These tests are off by default: every @pytest.mark.acceptance item is skipped
unless RUN_ACCEPTANCE=1, so CI and the normal suite never spawn services.

Process model:
  live_baas (session)  : starts ONCE per pytest invocation. baas uses
                          /tmp/secbaas_singlebox.db (disk SQLite); wiped on
                          session start so each pytest run is reproducible.
                          Multiple tests share the same baas process —
                          test code uses unique-per-call IDs (e.g. nanos
                          suffix) so cross-test data doesn't collide.
  live_backend (func)  : depends on live_baas, starts a fresh backend per
                          test. Backend init = brand new in-memory SQLite
                          via bootstrap; strongest isolation per test.

This split mirrors reality: backend init is cheap (in-memory init), baas
init is heavy (mosn / sofa-registry / disk DB) — no reason to restart it
per test.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest


# conftest.py lives at src/backend/tests/acceptance/ → worktree root is parents[5]:
#   [0]=acceptance [1]=tests [2]=backend [3]=src [4]=<worktree root>
PROJECT_ROOT = Path(__file__).resolve().parents[5]
LOCAL_SETUP = PROJECT_ROOT / "scripts" / "local_setup.sh"
ENGINE_DIR = PROJECT_ROOT / "src" / "engine"
BACKEND_URL = "http://127.0.0.1:8888"
BAAS_URL = "http://127.0.0.1:8890"
BAAS_DB_FILE = Path("/tmp/secbaas_singlebox.db")
ACCEPTANCE_RUNTIME = Path(__file__).resolve().parent / "_runtime"
MCP_CENTER_FIXTURE = Path(__file__).resolve().parent / "mcp" / "fixture_mcp_center.json"
HEALTH_TIMEOUT_SEC = 90
BAAS_HEALTH_TIMEOUT_SEC = 120  # baas 比 backend 慢（mosn / sofa-registry 启动）
TEST_BOTS_DIR = PROJECT_ROOT / "test-bots"
OPENCLAW_WORKSPACE = Path.home() / ".openclaw" / "workspace" / "skills"


def pytest_collection_modifyitems(config, items):
    """Skip all acceptance tests unless RUN_ACCEPTANCE=1."""
    if os.environ.get("RUN_ACCEPTANCE") == "1":
        return
    skip = pytest.mark.skip(reason="acceptance test; set RUN_ACCEPTANCE=1 to run")
    for item in items:
        if "acceptance" in item.keywords:
            item.add_marker(skip)


def _is_backend_healthy(base_url: str) -> bool:
    try:
        r = httpx.get(f"{base_url}/api/health", timeout=2.0)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:
        return False


def _wait_for_health(base_url: str, timeout_sec: int) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if _is_backend_healthy(base_url):
            return True
        time.sleep(1.0)
    return False


def _is_baas_healthy(base_url: str = BAAS_URL) -> bool:
    """baas 用自己的 /health 端点（无 /api 前缀），返 {"status":"healthy"}."""
    try:
        r = httpx.get(f"{base_url}/health", timeout=2.0)
        return r.status_code == 200 and r.json().get("status") == "healthy"
    except Exception:
        return False


def _wait_for_baas_healthy(timeout_sec: int = BAAS_HEALTH_TIMEOUT_SEC) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if _is_baas_healthy():
            return True
        time.sleep(1.0)
    return False


def _engine_venv_ready() -> bool:
    engine_python = ENGINE_DIR / ".venv" / "bin" / "python"
    if not engine_python.exists():
        return False
    result = subprocess.run(
        [str(engine_python), "-c", "import injector, uvicorn"],
        cwd=str(ENGINE_DIR),
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _ensure_engine_venv(env: dict[str, str]) -> None:
    """BaaS local_proc starts engine adapter with src/engine/.venv when present.

    In a clean worktree that venv may not exist; without it the adapter falls
    back to the BaaS interpreter and misses engine dependencies.
    """
    if _engine_venv_ready():
        return
    subprocess.run(
        ["uv", "sync"],
        cwd=str(ENGINE_DIR),
        env=env,
        timeout=240,
        check=True,
    )


def _clean_test_bots():
    if TEST_BOTS_DIR.exists():
        shutil.rmtree(TEST_BOTS_DIR)


def _keep_acceptance_artifacts() -> bool:
    return os.environ.get("SINGLEBOX_ACCEPTANCE_KEEP_ARTIFACTS") == "1"


def _reuse_live_services() -> bool:
    return os.environ.get("SINGLEBOX_ACCEPTANCE_REUSE_LIVE") == "1"


def _backend_only_mode() -> bool:
    return os.environ.get("SINGLEBOX_ACCEPTANCE_BACKEND_ONLY") == "1"


# top-level entries in OPENCLAW_WORKSPACE that are material sources, not run output
_WORKSPACE_KEEP = {"skills-repo", "skills-local"}


def _clean_workspace_skills():
    """Drop business symlinks + skill_sets.json from the shared global root so a
    run leaves no cross-run residue; keep skills-repo/ skills-local/ (the source
    material rsynced at boot, not produced by a flow)."""
    if not OPENCLAW_WORKSPACE.exists():
        return
    for entry in OPENCLAW_WORKSPACE.iterdir():
        if entry.name in _WORKSPACE_KEEP:
            continue
        if entry.is_symlink():
            entry.unlink()
        elif entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


@pytest.fixture(scope="session")
def live_baas():
    """Start baas ONCE per pytest session; wipe disk SQLite at session start.

    baas 用 ``/tmp/secbaas_singlebox.db``（磁盘 SQLite）。重启进程数据不丢，
    session 内多测试共用 baas 进程，数据累积。测试代码用 unique-per-call
    后缀（e.g. ``time.time_ns()``）避免数据撞。

    session 开头先 stop 残留 baas + 删 db 文件 → fresh state，每次 pytest
    invocation 可重现。

    If RUN_ACCEPTANCE != 1, this fixture is never requested (live_backend
    skips early), so baas won't be started for non-acceptance runs.
    """
    if os.environ.get("RUN_ACCEPTANCE") != "1":
        pytest.skip("acceptance fixture; set RUN_ACCEPTANCE=1 to run")

    if _backend_only_mode():
        yield None
        return

    if _reuse_live_services():
        if not _wait_for_baas_healthy():
            raise RuntimeError(
                f"baas not healthy within {BAAS_HEALTH_TIMEOUT_SEC}s; "
                f"check scripts/.dependencies/logs/baas.log"
            )
        yield BAAS_URL
        return

    # 1) 把任何残留 baas 干掉（之前 session 没正常退出 / 手动 start 留下的）
    subprocess.run(
        ["bash", str(LOCAL_SETUP), "stop", "baas"],
        cwd=str(PROJECT_ROOT), check=False, capture_output=True,
    )
    time.sleep(1)

    # 2) 删 baas 磁盘 db 文件确保 fresh state（session 隔离的核心）
    if BAAS_DB_FILE.exists():
        BAAS_DB_FILE.unlink()

    # 3) 启 baas（local_setup.sh start baas 已用 env -u 清掉 SOCKS proxy）
    env = os.environ.copy()
    env["PATH"] = f"/opt/homebrew/opt/python@3.12/libexec/bin:{env.get('PATH', '')}"
    _ensure_engine_venv(env)
    subprocess.run(
        ["bash", str(LOCAL_SETUP), "start", "baas"],
        cwd=str(PROJECT_ROOT), env=env, timeout=180, check=False,
    )

    try:
        if not _wait_for_baas_healthy():
            raise RuntimeError(
                f"baas not healthy within {BAAS_HEALTH_TIMEOUT_SEC}s; "
                f"check scripts/.dependencies/logs/baas.log"
            )
        yield BAAS_URL
    finally:
        subprocess.run(
            ["bash", str(LOCAL_SETUP), "stop", "baas"],
            cwd=str(PROJECT_ROOT), check=False, capture_output=True,
        )
        # 不删 db 文件 —— 留着方便测试失败后排查；下次 session 启动时再删


@pytest.fixture(scope="function")
def live_backend(live_baas):
    """Start a real singlebox backend, yield base_url; stop + clean on teardown.

    function scope: each test gets a fresh in-memory SQLite + clean test-bots/,
    so flows don't collide on auto-increment ids (HANDOFF §6.3). Slow but
    deterministic; widen to session scope once flows carry a run-id suffix.

    Depends on ``live_baas`` (session scope) — baas is started once at session
    start with a wiped disk SQLite; backend stops/starts per test. Most backend
    code paths now route device / file ops through baas (R-Class FS-to-BaaS
    refactor), so acceptance is invalid without a real baas running.

    Refuses to start if 8888 already has a healthy backend (would clobber its
    state) — stop it first via 'bash scripts/local_setup.sh stop backend'.
    """
    if os.environ.get("RUN_ACCEPTANCE") != "1":
        pytest.skip("acceptance fixture; set RUN_ACCEPTANCE=1 to run")
    if _reuse_live_services():
        if not _wait_for_health(BACKEND_URL, HEALTH_TIMEOUT_SEC):
            raise RuntimeError(f"backend not healthy within {HEALTH_TIMEOUT_SEC}s")
        yield BACKEND_URL
        return
    if _is_backend_healthy(BACKEND_URL):
        pytest.fail(
            "port 8888 already serves a healthy backend — stop it first "
            "('bash scripts/local_setup.sh stop backend') to avoid clobbering its state."
        )

    _clean_test_bots()
    _clean_workspace_skills()
    env = os.environ.copy()
    env["PATH"] = f"/opt/homebrew/opt/python@3.12/libexec/bin:{env.get('PATH', '')}"
    pythonpath_entries = [
        str(ACCEPTANCE_RUNTIME),
        str(PROJECT_ROOT / "src" / "backend" / "src"),
    ]
    if env.get("PYTHONPATH"):
        pythonpath_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    env["SINGLEBOX_ACCEPTANCE_REAL_MCP_SYNC"] = "1"
    env["SINGLEBOX_ACCEPTANCE_MCP_FIXTURE_FILE"] = str(MCP_CENTER_FIXTURE)
    env["SINGLEBOX_BOT_PUBLIC_APPROVAL_MODE"] = "pending"

    # local_setup.sh forks a nohup'd python and returns; monitor the port, not proc.
    subprocess.run(
        ["bash", str(LOCAL_SETUP), "start", "backend"],
        cwd=str(PROJECT_ROOT), env=env, timeout=180, check=False,
    )
    try:
        if not _wait_for_health(BACKEND_URL, HEALTH_TIMEOUT_SEC):
            raise RuntimeError(f"backend not healthy within {HEALTH_TIMEOUT_SEC}s")
        yield BACKEND_URL
    finally:
        subprocess.run(
            ["bash", str(LOCAL_SETUP), "stop", "backend"],
            cwd=str(PROJECT_ROOT), check=False,
        )
        time.sleep(2)
        if not _keep_acceptance_artifacts():
            _clean_test_bots()
            _clean_workspace_skills()


@pytest.fixture(scope="function")
def acceptance_fs_root() -> Path:
    """Filesystem root for FsAssert resolution. LOCAL skill symlinks land in the
    unified global root ~/.openclaw/workspace/skills (get_bot_skills_dir), so
    flows use home-relative paths and resolve against $HOME."""
    return Path.home()
