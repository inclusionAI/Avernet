from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_oss_worker_lifecycle_routes_do_not_load_legacy_api_app() -> None:
    bcsfuse_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(bcsfuse_root), env.get("PYTHONPATH")))
    )
    script = """
import sys
import src.bootstrap.oss_worker_lifecycle_routes

assert "src.interfaces.api.app" not in sys.modules
assert "src.interfaces.api.worker_routes" not in sys.modules
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=bcsfuse_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_legacy_api_package_exports_keep_their_object_types() -> None:
    bcsfuse_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(bcsfuse_root), env.get("PYTHONPATH")))
    )
    script = """
from fastapi import APIRouter, FastAPI
from src.interfaces.api import app, worker_router

assert isinstance(app, FastAPI), type(app)
assert isinstance(worker_router, APIRouter), type(worker_router)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=bcsfuse_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
