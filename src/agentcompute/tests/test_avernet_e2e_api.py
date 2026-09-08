from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest
import uvicorn
from _avernet_fake_gateway import FakeAvernetGateway

from agentcompute.community.adapters.http import create_app

_HOST = "127.0.0.1"
_PORT = 8799


class _Server:
    def __init__(self, yaml_path, fake_url) -> None:
        yaml_path.write_text(
            "app:\n  name: agentcompute\n"
            "user_config:\n  plugins:\n    logger: stdlib\n    tracer: stdlib\n"
            "    database: sqlite\n"
            "    llm:\n      provider: stub\n"
            "    avernet:\n"
            f"      gateway_base_url: {fake_url}\n"
            "      principal_token: test-token\n"
            "      user_id: test-user\n"
            '      manifest_template: "name: {role}\\nrole: {role}\\ngoal: {goal}"\n'
            "      poll_timeout: 5\n"
            "      poll_interval: 0.01\n"
            f"  database:\n    database_url: sqlite:///{yaml_path.parent / 'e2e.db'}\n",
            encoding="utf-8",
        )
        self.app = create_app(str(yaml_path))
        self.thread: threading.Thread | None = None
        self.server: uvicorn.Server | None = None

    def start(self) -> None:
        config = uvicorn.Config(self.app, host=_HOST, port=_PORT, log_level="warning")
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
def fake_gateway():
    gw = FakeAvernetGateway()
    url = gw.start()
    yield gw, url
    gw.stop()


@pytest.fixture(scope="module")
def server(tmp_path_factory, fake_gateway):
    gw, url = fake_gateway
    srv = _Server(tmp_path_factory.mktemp("avernet_e2e") / "application.yaml", url)
    srv.start()
    yield srv
    srv.stop()


def _post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"http://{_HOST}:{_PORT}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def test_mixed_roster_avernet_plus_llm_over_wire(server, fake_gateway):
    gw, _ = fake_gateway
    initial_count = len(gw.recorded_requests)
    result = _post(
        "/run",
        {
            "goal": "integration test goal",
            "agents": [
                {"name": "searcher", "role": "summarizes"},
                {
                    "name": "summarizer",
                    "role": "researches",
                    "metadata": {"type": "avernet"},
                },
            ],
        },
    )
    assert result["succeeded"] is True
    assert set(result["node_statuses"]) == {"1", "2"}
    assert all(v == "SUCCEEDED" for v in result["node_statuses"].values())
    assert result["final_output"] is not None
    output = result["final_output"]
    assert isinstance(output, str)
    assert "Integration" in output
    assert "test passed" in output

    with gw.lock:
        recorded = list(gw.recorded_requests)[initial_count:]
    assert len(recorded) == 4
    methods = [r["method"] for r in recorded]
    assert methods == ["POST", "GET", "POST", "DELETE"]
    for r in recorded:
        auth = r["headers"].get("authorization", "")
        assert "Bearer test-token" in auth, f"missing auth on {r['method']} {r['path']}"
        assert "user_id=test-user" in r["path"], f"missing user_id on {r['method']}"
    assert any(r["method"] == "DELETE" for r in recorded), "avernet bot was not cleaned up"
