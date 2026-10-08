from __future__ import annotations

import importlib.util
from pathlib import Path


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_SELFTEST_PATH = _BACKEND_ROOT / "scripts" / "community_selftest.py"


def _load_selftest_module():
    spec = importlib.util.spec_from_file_location("community_selftest", _SELFTEST_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_boot_proof_uses_dev_and_imports_the_http_composition_root(monkeypatch):
    selftest = _load_selftest_module()
    observed: dict[str, object] = {}

    def fake_run(argv, env, timeout):
        observed.update(argv=argv, env=env, timeout=timeout)
        return 0, "COMMUNITY_BOOT_OK"

    monkeypatch.setattr(selftest, "_run", fake_run)

    assert selftest.verify_boot() == (True, "COMMUNITY_BOOT_OK")
    assert observed["env"]["SERVER_ENV"] == "dev"
    assert "from agentclaw.community.adapters.http import app" in selftest._BOOT_PROOF
