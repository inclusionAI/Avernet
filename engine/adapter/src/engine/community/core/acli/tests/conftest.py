"""Fixtures for the acli tests: a runner wired to the fake ``acli``."""
from __future__ import annotations

import json
import os
import stat
import sys

import pytest
from engine.community.core.acli.runner import AcliRunner
from engine.community.core.acli.tests.fake_acli import FAKE_ACLI_SCRIPT


@pytest.fixture
def fake_acli(tmp_path, install_dir, monkeypatch):
    script = tmp_path / "acli"
    script.write_text(FAKE_ACLI_SCRIPT.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_ACLI_LOG", str(log))

    def calls() -> list[dict]:
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    return AcliRunner(environ=os.environ, install_dir=install_dir, executable=str(script)), calls


@pytest.fixture
def install_dir(tmp_path) -> str:
    return str(tmp_path / "agentclaw" / "bin")
