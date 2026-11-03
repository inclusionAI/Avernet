"""TDD for process-level BCSFuse server admission defaults."""

import pytest


def _fake_uvicorn_run(monkeypatch) -> dict:
    """Patch uvicorn in a test-local way instead of executing a server."""
    captured = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    import main

    monkeypatch.setattr(main.uvicorn, "run", fake_run)
    return captured


def test_opensource_server_limits_admission_by_default(monkeypatch) -> None:
    import main

    captured = _fake_uvicorn_run(monkeypatch)
    monkeypatch.delenv("BCSFUSE_SERVER_LIMIT_CONCURRENCY", raising=False)
    monkeypatch.delenv("BCSFUSE_SERVER_BACKLOG", raising=False)

    main.run_server(["--host", "127.0.0.1", "--port", "9999"])

    assert captured["kwargs"]["limit_concurrency"] == 256
    assert captured["kwargs"]["backlog"] == 2048


def test_server_admission_limits_can_be_overridden(monkeypatch) -> None:
    import main

    captured = _fake_uvicorn_run(monkeypatch)
    monkeypatch.setenv("BCSFUSE_SERVER_LIMIT_CONCURRENCY", "512")
    monkeypatch.setenv("BCSFUSE_SERVER_BACKLOG", "4096")

    main.run_server(["--host", "127.0.0.1", "--port", "9999"])

    assert captured["kwargs"]["limit_concurrency"] == 512
    assert captured["kwargs"]["backlog"] == 4096


def test_opensource_server_starts_multiple_processes_by_default(monkeypatch) -> None:
    import main

    captured = _fake_uvicorn_run(monkeypatch)
    monkeypatch.delenv("BCSFUSE_SERVER_WORKERS", raising=False)
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "runtime")
    monkeypatch.setenv("QDRANT_MODE", "server")

    main.run_server(["--host", "127.0.0.1", "--port", "9999"])

    assert captured["kwargs"]["workers"] == 2


def test_server_workers_can_be_overridden(monkeypatch) -> None:
    import main

    captured = _fake_uvicorn_run(monkeypatch)
    monkeypatch.delenv("BCSFUSE_SERVER_WORKERS", raising=False)
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "runtime")
    monkeypatch.setenv("QDRANT_MODE", "server")
    monkeypatch.setenv("BCSFUSE_SERVER_WORKERS", "4")

    main.run_server(["--host", "127.0.0.1", "--port", "9999"])

    assert captured["kwargs"]["workers"] == 4


def test_local_qdrant_uses_one_worker_by_default(monkeypatch) -> None:
    import main

    captured = _fake_uvicorn_run(monkeypatch)
    monkeypatch.delenv("BCSFUSE_SERVER_WORKERS", raising=False)
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "dev")
    monkeypatch.delenv("QDRANT_MODE", raising=False)

    main.run_server(["--host", "127.0.0.1", "--port", "9999"])

    assert captured["kwargs"]["workers"] == 1


def test_dev_with_qdrant_server_still_defaults_to_one_worker(monkeypatch) -> None:
    """Dev uses a single SQLite writer even when testing against Qdrant Server."""
    import main

    captured = _fake_uvicorn_run(monkeypatch)
    monkeypatch.delenv("BCSFUSE_SERVER_WORKERS", raising=False)
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "dev")
    monkeypatch.setenv("QDRANT_MODE", "server")

    main.run_server(["--host", "127.0.0.1", "--port", "9999"])

    assert captured["kwargs"]["workers"] == 1


def test_local_qdrant_rejects_explicit_multi_worker_startup(monkeypatch) -> None:
    import main

    _fake_uvicorn_run(monkeypatch)
    monkeypatch.delenv("BCSFUSE_SERVER_WORKERS", raising=False)
    monkeypatch.setenv("BCSFUSE_PROVIDER_MODE", "dev")
    monkeypatch.setenv("QDRANT_MODE", "local")

    with pytest.raises(SystemExit) as exc_info:
        main.run_server(["--host", "127.0.0.1", "--workers", "2"])

    assert exc_info.value.code != 0


def test_index_owner_identity_is_process_unique_inside_a_pod(monkeypatch) -> None:
    """Multiple uvicorn workers must not co-own one durable index lease."""
    import os

    from app.bootstrap.opensource import _index_owner_id

    monkeypatch.setenv("BCSFUSE_INDEX_OWNER_ID", "pod-a")
    assert _index_owner_id() == f"pod-a:{os.getpid()}"

    monkeypatch.delenv("BCSFUSE_INDEX_OWNER_ID", raising=False)
    assert _index_owner_id().endswith(f":{os.getpid()}")


def test_invalid_server_admission_limits_fail_fast(monkeypatch) -> None:
    import main

    monkeypatch.setenv("BCSFUSE_SERVER_LIMIT_CONCURRENCY", "0")
    with pytest.raises(SystemExit):
        main.run_server(["--host", "127.0.0.1"])
