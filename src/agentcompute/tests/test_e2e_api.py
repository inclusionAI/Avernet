"""End-to-end API tests against a live uvicorn server.

These boot a real uvicorn server on an ephemeral port (via a fixture) and
exercise the full HTTP surface over the wire — the closest thing to a deployed
service we can assert in CI.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import pytest
import uvicorn

from agentcompute.community.adapters.http import create_app


class _Server:
    def __init__(self, tmp_path) -> None:
        yaml_path = tmp_path / "application.yaml"
        yaml_path.write_text(
            "app:\n  name: agentcompute\n"
            "user_config:\n  plugins:\n    logger: stdlib\n    tracer: stdlib\n"
            "    database: sqlite\n"
            "    llm:\n      provider: stub\n"
            f"  database:\n    database_url: sqlite:///{tmp_path / 'e2e.db'}\n",
            encoding="utf-8",
        )
        self.app = create_app(str(yaml_path))
        self.thread: threading.Thread | None = None
        self.server: uvicorn.Server | None = None

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:8767"

    def start(self) -> None:
        config = uvicorn.Config(self.app, host="127.0.0.1", port=8767, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return
            time.sleep(0.05)
        raise RuntimeError("uvicorn did not start")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=5)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    srv = _Server(tmp_path_factory.mktemp("e2e"))
    srv.start()
    yield srv
    srv.stop()


def _get(path: str, timeout: float = 5.0) -> Any:
    return urllib.request.urlopen(f"http://127.0.0.1:8767{path}", timeout=timeout)


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:8767{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def test_health_over_wire(server):
    resp = _get("/health")
    assert resp.status == 200
    assert json.loads(resp.read()) == {"status": "ok"}


def test_openapi_schema_present(server):
    resp = _get("/openapi.json")
    assert resp.status == 200
    schema = json.loads(resp.read())
    assert "/run" in schema["paths"]
    assert "/jobs" in schema["paths"]


def test_sync_run_over_wire(server):
    result = _post(
        "/run",
        {
            "goal": "research trends",
            "agents": [
                {"name": "searcher", "role": "finds"},
                {"name": "summarizer", "role": "summarizes"},
            ],
        },
    )
    assert result["succeeded"] is True
    assert set(result["node_statuses"]) == {"1", "2"}
    assert result["final_output"] is not None


def test_async_job_full_cycle_over_wire(server):
    job = _post("/jobs", {"goal": "g", "agents": [{"name": "searcher", "role": "f"}]})
    job_id = job["job_id"]

    status_resp = _get(f"/jobs/{job_id}")
    status = json.loads(status_resp.read())
    assert status["status"] == "completed"
    assert status["node_statuses"]["1"] == "SUCCEEDED"

    viz_resp = _get(f"/jobs/{job_id}/report")
    assert viz_resp.status == 200
    assert b"<svg" in viz_resp.read()


def test_job_stream_emits_terminal_event(server):
    job = _post("/jobs", {"goal": "g", "agents": [{"name": "searcher", "role": "f"}]})
    job_id = job["job_id"]
    resp = _get(f"/jobs/{job_id}/stream", timeout=10)
    body = resp.read().decode()
    assert resp.headers["Content-Type"].startswith("text/event-stream")
    assert "completed" in body


def test_404_for_unknown_job(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get("/jobs/does-not-exist")
    assert exc.value.code == 404


def test_422_for_missing_agents(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post("/run", {"goal": "g", "agents": []})
    assert exc.value.code == 422


def test_trace_id_header_present(server):
    resp = _get("/health")
    assert resp.headers.get("X-Trace-Id")


def test_run_is_persisted_to_database(tmp_path):
    db_path = tmp_path / "e2e.db"
    yaml_path = tmp_path / "app.yaml"
    yaml_path.write_text(
        "app:\n  name: agentcompute\n"
        "user_config:\n  plugins:\n    logger: stdlib\n    tracer: stdlib\n"
        "    database: sqlite\n"
        "    llm:\n      provider: stub\n"
        f"  database:\n    database_url: sqlite:///{db_path}\n",
        encoding="utf-8",
    )
    from starlette.testclient import TestClient

    with TestClient(create_app(str(yaml_path))) as client:
        resp = client.post(
            "/run",
            json={"goal": "persist me", "agents": [{"name": "searcher", "role": "f"}]},
        )
        assert resp.status_code == 200

    import sqlite3

    conn = sqlite3.connect(db_path)
    runs = conn.execute("SELECT goal, status FROM runs").fetchall()
    plans = conn.execute("SELECT plan_json FROM plans").fetchall()
    nodes = conn.execute("SELECT node_id, status, result FROM node_executions").fetchall()
    conn.close()
    assert runs and runs[0][0] == "persist me"
    assert runs[0][1] == "completed"
    assert plans, "plan should be persisted"
    assert nodes and nodes[0][1] == "SUCCEEDED"
    assert nodes[0][2] is not None


def test_full_e2e_journey_submit_run_viz(tmp_path):
    """End-to-end journey: submit a task, run it through agents, then viz the result."""
    db_path = tmp_path / "journey.db"
    yaml_path = tmp_path / "app.yaml"
    yaml_path.write_text(
        "app:\n  name: agentcompute\n"
        "user_config:\n  plugins:\n    logger: stdlib\n    tracer: stdlib\n"
        "    database: sqlite\n"
        "    llm:\n      provider: stub\n"
        f"  database:\n    database_url: sqlite:///{db_path}\n",
        encoding="utf-8",
    )
    from starlette.testclient import TestClient

    with TestClient(create_app(str(yaml_path))) as client:
        # 1. Submit the task
        submit = client.post(
            "/jobs",
            json={
                "goal": "research the best Python web framework",
                "agents": [
                    {"name": "searcher", "role": "Searches sources"},
                    {"name": "summarizer", "role": "Summarizes findings"},
                ],
            },
        )
        assert submit.status_code == 200
        job_id = submit.json()["job_id"]

        # 2. Run by agent — poll status until terminal + assert per-node success
        status = client.get(f"/jobs/{job_id}")
        assert status.status_code == 200
        body = status.json()
        assert body["status"] == "completed"
        assert set(body["node_statuses"]) == {"1", "2"}
        assert all(v == "SUCCEEDED" for v in body["node_statuses"].values())
        assert body["final_output"] is not None

        # 3. Viz the result — HTML/SVG DAG reflecting both nodes
        viz = client.get(f"/jobs/{job_id}/report")
        assert viz.status_code == 200
        html = viz.text
        assert "<svg" in html
        assert "searcher" in html
        assert "summarizer" in html

        # The submitted request was persisted with its plan + node records
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        request = conn.execute("SELECT goal FROM requests").fetchone()
        plan = conn.execute("SELECT plan_json FROM plans").fetchone()
        nodes = conn.execute(
            "SELECT node_id, status FROM node_executions ORDER BY node_id"
        ).fetchall()
        conn.close()
        assert request["goal"] == "research the best Python web framework"
        assert plan is not None
        assert [n["node_id"] for n in nodes] == ["1", "2"]
        assert {n["status"] for n in nodes} == {"SUCCEEDED"}
