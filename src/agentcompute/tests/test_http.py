import pytest
from fastapi.testclient import TestClient

from agentcompute.community.adapters.http import create_app


@pytest.fixture
def client(tmp_path):
    yaml_path = tmp_path / "application.yaml"
    yaml_path.write_text(
        "app:\n  name: agentcompute\n  port: 8000\n"
        "user_config:\n  plugins:\n    logger: stdlib\n    tracer: stdlib\n"
        "    database: sqlite\n"
        "    llm:\n      provider: stub\n"
        f"  database:\n    database_url: sqlite:///{tmp_path / 'test.db'}\n",
        encoding="utf-8",
    )
    with TestClient(create_app(yaml_path)) as test_client:
        yield test_client


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_sync_run(client):
    resp = client.post(
        "/run",
        json={
            "goal": "research X",
            "agents": [
                {"name": "searcher", "role": "finds"},
                {"name": "summarizer", "role": "summarizes"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] is True
    assert set(body["node_statuses"]) == {"1", "2"}
    assert resp.headers.get("x-trace-id")


def test_sync_run_requires_agents(client):
    resp = client.post("/run", json={"goal": "research X", "agents": []})
    assert resp.status_code == 422


def test_job_lifecycle(client):
    resp = client.post(
        "/jobs",
        json={
            "goal": "research X",
            "agents": [{"name": "searcher", "role": "finds"}],
        },
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    status = client.get(f"/jobs/{job_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["node_statuses"]["1"] == "SUCCEEDED"


def test_job_report_returns_html(client):
    resp = client.post(
        "/jobs",
        json={
            "goal": "research X",
            "agents": [{"name": "searcher", "role": "finds"}],
        },
    )
    job_id = resp.json()["job_id"]
    viz = client.get(f"/jobs/{job_id}/report")
    assert viz.status_code == 200
    assert "<svg" in viz.text


def test_job_not_found(client):
    assert client.get("/jobs/does-not-exist").status_code == 404


def test_config_env_overlay(tmp_path, monkeypatch):
    base = tmp_path / "application.yaml"
    base.write_text(
        "app:\n  name: agentcompute\nlog:\n  level: INFO\n",
        encoding="utf-8",
    )
    dev = tmp_path / "application-dev.yaml"
    dev.write_text("log:\n  level: DEBUG\n", encoding="utf-8")
    monkeypatch.setenv("DEPLOY_ENV", "dev")

    from agentcompute.community.bootstrap import load_config

    config = load_config(base)
    assert config.get("log.level") == "DEBUG"
    assert config.env == "dev"


def test_job_stream_completed_job(client):
    resp = client.post(
        "/jobs",
        json={"goal": "research X", "agents": [{"name": "searcher", "role": "finds"}]},
    )
    job_id = resp.json()["job_id"]
    with client.stream("GET", f"/jobs/{job_id}/stream") as stream:
        body = "".join(stream.iter_text())
    assert "completed" in body


def test_job_report_missing_plan_404(client):
    resp = client.post(
        "/jobs",
        json={"goal": "research X", "agents": [{"name": "searcher", "role": "finds"}]},
    )
    job_id = resp.json()["job_id"]
    job = client.app.state.store.get(job_id)
    job.full_plan = None
    assert client.get(f"/jobs/{job_id}/report").status_code == 404


def test_env_file_loaded_from_config(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=fromfile\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    yaml_path = tmp_path / "application.yaml"
    yaml_path.write_text(
        "app:\n  name: agentcompute\n"
        f"env_file: {env}\n"
        "user_config:\n  plugins:\n    logger: stdlib\n    tracer: stdlib\n    llm:\n      provider: stub\n",
        encoding="utf-8",
    )
    import os

    _ = create_app(yaml_path)
    assert os.environ["OPENAI_API_KEY"] == "fromfile"
