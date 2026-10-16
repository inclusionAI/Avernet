"""The Bot-local Skill adapter keeps identity out of prompts and responses."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


_SCRIPT = (
    Path(__file__).resolve().parents[4]
    / "specs/2026-08-23-task-loop-skill/task-loop/scripts/relay_http.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("relay_skill_http", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skill_http_uses_runtime_bot_session_without_printing_token(
    tmp_path, monkeypatch, capsys,
) -> None:
    module = _load_script()
    session_dir = tmp_path / ".bcs"
    session_dir.mkdir()
    (session_dir / "session.json").write_text(
        json.dumps({"token": "bot-session-only"}), encoding="utf-8",
    )
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"data":{"task_id":"t1"}}'

    def open_url(request, *, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr(module, "urlopen", open_url)
    assert module.main([
        "GET", "/api/v1/collaboration/tasks/t1/nodes/n1/context",
        "--backend-url", "http://127.0.0.1:8888",
        "--relay-token", "generation-1",
    ]) == 0
    output = capsys.readouterr()
    assert "bot-session-only" not in output.out + output.err
    request, timeout = requests[0]
    assert request.get_header("Authorization") == "Bearer bot-session-only"
    assert request.get_header("X-relay-token") == "generation-1"
    assert timeout == 30


def test_skill_http_refuses_invalid_configured_backend(tmp_path, monkeypatch, capsys) -> None:
    module = _load_script()
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    assert module.main([
        "GET", "/api/v1/collaboration/tasks/t1/nodes/n1/context",
        "--backend-url", "",
        "--relay-token", "generation-1",
    ]) == 1
    assert "configured origin" in capsys.readouterr().err
