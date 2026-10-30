"""Live-journey tests for the structured density transport against a loopback station.

A real loopback HTTP server implements the admitted Singlebox wire shape of
the 17-request density journey (public-envelope internal-v1 responses), so the
live transport exercise covers the request count, the mutation-once contract,
the memoized field rows, the journey order guard, and the honest first-leg
failure receipt without touching any remote origin.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bcs_e2e.profile import ConfigError
from bcs_e2e.route_bindings import _EDGE_CASES
from bcs_e2e.run_state import RunStateError
from bcs_e2e.structured_density.live_transport import LiveStructuredDensityTransport
from bcs_e2e.structured_density.run import run_structured_density

GROUP_ID = "group-structured-density-1"
DEFINITION_ID = "definition-structured-density-1"
RUN_ID = "run-structured-density-1"


def loopback_density_profile(port: int) -> dict:
    value = json.loads((ROOT / "profiles" / "local.singlebox.example.json").read_text(encoding="utf-8"))
    value["environment"].update(id="structured-density-loopback", expected_bcs_env="local")
    value["human"]["user_id"] = "owner-1"
    value["actors"]["driver"].update(bot_id="driver-bot", owner_id="owner-1")
    value["actors"]["member"].update(bot_id="member-bot", owner_id="owner-1")
    value["entrypoint"]["origin"] = f"http://127.0.0.1:{port}"
    value["policy"]["fixture_pool"] = "structured-density-loopback-pool"
    value["auth"]["header_env"] = {
        "Cookie": "BCS_E2E_LOCAL_COOKIE",
        "Authorization": "BCS_E2E_LOCAL_DRIVER_TOKEN",
    }
    bindings = {}
    for operation in (
        "structured.group.create", "structured.group.read",
        "structured.definition.read", "structured.definition.patch",
        "structured.definition.upgrade.missing", "structured.run.start",
        "structured.run.read", "structured.run.graph.read",
        "structured.run.node.read", "structured.run.pending.human.nodes",
        "structured.run.human.response.denied", "structured.run.cancel",
        "structured.run.rerun.aborted.denied", "structured.group.delete",
        "structured_templates.list", "structured_templates.detail",
    ):
        method, path, auth = _EDGE_CASES[operation]
        binding = {"method": method, "path": path, "api_face": "internal-v1",
                   "auth": auth, "response": "public-envelope", "traceparent": "disabled"}
        if auth == "authenticated_bot":
            binding["caller_actor_id"] = "driver-bot"
        bindings[operation] = binding
    value["edge_case_bindings"] = bindings
    return value


def _loopback_env() -> dict:
    env = {key: item for key, item in os.environ.items() if "proxy" not in key.lower()}
    env.update({
        "BCS_E2E_LOCAL_COOKIE": "loopback-human-cookie",
        "BCS_E2E_LOCAL_DRIVER_TOKEN": "loopback-driver-token",
        "no_proxy": "127.0.0.1,localhost",
    })
    return env


def envelope(*, code: int, message: str, data, http: int, request_id: str) -> bytes:
    return json.dumps({"code": code, "message": message,
                       "request_id": request_id, "data": data}).encode()


class DensityStationHandler(BaseHTTPRequestHandler):
    server_version = "DensityLoopback/1"

    def log_message(self, *args) -> None:
        pass

    # --- routing ---------------------------------------------------------

    def _kind(self, path: str):
        clean = path.split("?")[0]
        if clean == "/api/v1/groups":
            return "groups_root"
        if clean.startswith("/api/v1/groups/"):
            rest = clean[len("/api/v1/groups/"):]
            if rest.endswith("/collaboration-definition/upgrade"):
                return "definition_upgrade", rest
            if rest.endswith("/collaboration-definition"):
                return "definition", rest
            if rest.endswith("/state-machine-runs"):
                return "run_start", rest
            if re.fullmatch(r"[^/]+", rest):
                return "group", rest
        if clean.startswith("/api/v1/state-machine-runs/"):
            rest = clean[len("/api/v1/state-machine-runs/"):]
            if rest == RUN_ID:
                return "run_read", rest
            if rest == RUN_ID + "/graph":
                return "run_graph", rest
            if rest == RUN_ID + "/nodes/answer/respond":
                return "run_respond", rest
            if rest == RUN_ID + "/nodes/answer":
                return "run_node", rest
            if rest == RUN_ID + "/pending-human-nodes":
                return "run_pending", rest
            if rest == RUN_ID + "/cancel":
                return "run_cancel", rest
            if rest == RUN_ID + "/reruns":
                return "run_rerun", rest
        if clean.startswith("/api/v1/collaboration/templates"):
            if clean == "/api/v1/collaboration/templates":
                return "templates_list"
            if clean.startswith("/api/v1/collaboration/templates/single-bot-guided-answer"):
                return "templates_detail"
        return "unknown"

    def _reply(self, *, http: int, code: int, message: str, data) -> None:
        request_id = self.headers.get("X-Request-ID", "loopback")
        raw = envelope(code=code, message=message, data=data, http=http, request_id=request_id)
        self.send_response(http)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Request-ID", request_id)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode() or "{}")

    def _handle(self, method: str) -> None:
        self.server.hits.append((method, self.path.split("?")[0]))
        kind = self._kind(self.path)
        if isinstance(kind, tuple):
            kind, _bound = kind
        state = self.server.state
        if kind == "templates_list":
            self._reply(http=200, code=20000, message="template catalog",
                        data={"templates": [{"id": "single-bot-guided-answer"}]})
        elif kind == "templates_detail":
            self._reply(http=200, code=20000, message="template detail",
                        data={"id": "single-bot-guided-answer", "lang": "zh-CN",
                              "yaml": "name: E2E Guided Answer"})
        elif kind == "groups_root" and method == "POST":
            self._read_body()
            state["group"] = GROUP_ID
            self._reply(http=200, code=20000, message="group created", data={"id": GROUP_ID})
        elif kind == "group" and method == "GET":
            self._reply(http=200, code=20000, message="group read",
                        data={"id": GROUP_ID, "group_strategy": "state_machine"})
        elif kind == "definition" and method == "GET":
            self._reply(http=200, code=20000, message="definition read",
                        data={"default_definition": {"id": DEFINITION_ID, "version": 7},
                              "yaml_source": "original"})
        elif kind == "definition" and method == "PATCH":
            self._read_body()
            state["definition_version"] = 8
            self._reply(http=200, code=20000, message="definition patched",
                        data={"default_definition": {"id": DEFINITION_ID, "version": 8},
                              "body": "Draft and verify a concise release recommendation."})
        elif kind == "definition_upgrade":
            self._reply(http=404, code=40400,
                        message=f"{DEFINITION_ID} @107 not found",
                        data={"error": {"message": f"{DEFINITION_ID} @107 not found"}})
        elif kind == "run_start":
            if state.get("fail_run_start"):
                self._reply(http=400, code=40000, message="state machine start refused",
                            data={"error": {"message": "state machine start refused"}})
                return
            self._read_body()
            state["run"] = RUN_ID
            self._reply(http=202, code=20200, message="run started",
                        data={"run": {"run_id": RUN_ID, "group_id": GROUP_ID},
                              "nodes": [{"node_id": "answer"}]})
        elif kind == "run_read":
            self._reply(http=200, code=20000, message="run read",
                        data={"run": {"run_id": RUN_ID, "group_id": GROUP_ID,
                                      "status": state.get("run_status", "running")}})
        elif kind == "run_graph":
            self._reply(http=200, code=20000, message="run graph",
                        data={"run": {"run_id": RUN_ID}, "nodes": [{"node_id": "answer"}]})
        elif kind == "run_node":
            self._reply(http=200, code=20000, message="run node",
                        data={"node": {"node_id": "answer", "run_id": RUN_ID}})
        elif kind == "run_pending":
            self._reply(http=200, code=20000, message="pending human nodes",
                        data=[])
        elif kind == "run_respond":
            self._reply(http=400, code=40000,
                        message="not a human_input node",
                        data={"error": {"message": "not a human_input node"}})
        elif kind == "run_cancel":
            state["run_status"] = "aborted"
            self._reply(http=200, code=20000, message="run cancelled",
                        data={"run": {"status": "aborted"}})
        elif kind == "run_rerun":
            self._reply(http=409, code=40900,
                        message="must be failed before rerun",
                        data={"error": {"message": "must be failed before rerun"}})
        elif kind == "group" and method == "DELETE":
            state["deleted"] = True
            self._reply(http=200, code=20000, message="group deleted", data={})
        else:
            self._reply(http=404, code=40400, message="loopback route not implemented", data={})

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")


class DensityStation:
    def __init__(self, *, fail_run_start: bool = False):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), DensityStationHandler)
        self.server.hits: list[tuple[str, str]] = []
        self.server.state: dict = {"fail_run_start": fail_run_start}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def count(self, method: str, path: str) -> int:
        return sum(1 for hit_method, hit_path in self.server.hits
                   if hit_method == method and hit_path == path)


class LiveStructuredDensityTransportTests(unittest.TestCase):
    def setUp(self):
        self.station = DensityStation()
        self.station.start()
        self.profile = loopback_density_profile(self.station.port)

    def tearDown(self):
        self.station.stop()

    def test_live_journey_passes_43_assertions_with_exactly_17_requests(self):
        with patch.dict(os.environ, _loopback_env(), clear=True):
            transport = LiveStructuredDensityTransport(self.profile, self.profile)
            report = run_structured_density(profile=self.profile, transport=transport)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["execution"]["completed_count"], 43)
        self.assertTrue(all(row["status"] == "PASS" for row in report["assertions"]))
        self.assertEqual(len(self.station.server.hits), 17)
        self.assertEqual(len(transport._memo), 17)
        self.assertEqual(self.station.count("GET", "/api/v1/collaboration/templates"), 1)
        self.assertEqual(
            self.station.count("GET", "/api/v1/collaboration/templates/single-bot-guided-answer"), 1)
        self.assertEqual(self.station.count("POST", "/api/v1/groups"), 1)
        self.assertEqual(self.station.count("GET", f"/api/v1/groups/{GROUP_ID}"), 1)
        self.assertEqual(
            self.station.count("GET", f"/api/v1/groups/{GROUP_ID}/collaboration-definition"), 1)
        self.assertEqual(
            self.station.count("PATCH", f"/api/v1/groups/{GROUP_ID}/collaboration-definition"), 1)
        self.assertEqual(
            self.station.count("POST", f"/api/v1/groups/{GROUP_ID}/collaboration-definition/upgrade"), 1)
        self.assertEqual(
            self.station.count("POST", f"/api/v1/groups/{GROUP_ID}/state-machine-runs"), 1)
        self.assertEqual(self.station.count("GET", f"/api/v1/state-machine-runs/{RUN_ID}"), 2)
        self.assertEqual(
            self.station.count("GET", f"/api/v1/state-machine-runs/{RUN_ID}/graph"), 1)
        self.assertEqual(
            self.station.count("GET", f"/api/v1/state-machine-runs/{RUN_ID}/nodes/answer"), 1)
        self.assertEqual(
            self.station.count("GET", f"/api/v1/state-machine-runs/{RUN_ID}/pending-human-nodes"), 1)
        self.assertEqual(
            self.station.count("POST", f"/api/v1/state-machine-runs/{RUN_ID}/nodes/answer/respond"), 1)
        self.assertEqual(
            self.station.count("POST", f"/api/v1/state-machine-runs/{RUN_ID}/cancel"), 1)
        self.assertEqual(
            self.station.count("POST", f"/api/v1/state-machine-runs/{RUN_ID}/reruns"), 1)
        self.assertEqual(self.station.count("DELETE", f"/api/v1/groups/{GROUP_ID}"), 1)

    def test_field_rows_are_memo_hits_and_not_replayed_to_the_station(self):
        with patch.dict(os.environ, _loopback_env(), clear=True):
            transport = LiveStructuredDensityTransport(self.profile, self.profile)
            report = run_structured_density(profile=self.profile, transport=transport)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(len(self.station.server.hits), 17)
        self.assertEqual(len(transport.journey_log()), 17)
        mutations = [entry["operation"] for entry in transport.journey_log() if entry["mutation"]]
        self.assertEqual(sorted(mutations), sorted([
            "group.create", "definition.patch", "definition.upgrade", "run.start",
            "run.human-response", "run.cancel", "run.rerun", "group.delete"]))

    def test_journey_order_is_enforced_before_any_station_request(self):
        with patch.dict(os.environ, _loopback_env(), clear=True):
            transport = LiveStructuredDensityTransport(self.profile, self.profile)
            with self.assertRaises(RunStateError):
                transport.request("run.read", "STRUCTURED.P09.status")
        self.assertEqual(self.station.server.hits, [])

    def test_unreachable_first_leg_fails_honestly_without_cleanup_claims(self):
        unreachable = deepcopy(self.profile)
        unreachable["entrypoint"]["origin"] = "http://127.0.0.1:1"
        with patch.dict(os.environ, _loopback_env(), clear=True):
            transport = LiveStructuredDensityTransport(unreachable, unreachable)
            report = run_structured_density(profile=unreachable, transport=transport)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["execution"]["status"], "FAIL")
        first = report["assertions"][0]
        self.assertEqual(first["status"], "FAIL")
        self.assertTrue(first["error_type"])
        blocked = [row for row in report["assertions"][1:]]
        self.assertTrue(all(row["status"] == "NOT_RUN" for row in blocked))
        self.assertEqual(transport.journey_log(), [])

    def test_missing_credential_references_fail_closed_before_any_request(self):
        clean = {key: value for key, value in os.environ.items() if "proxy" not in key.lower()}
        with patch.dict(os.environ, clean, clear=True):
            with self.assertRaises(ConfigError):
                LiveStructuredDensityTransport(self.profile, self.profile)
        self.assertEqual(self.station.server.hits, [])

    def test_station_failure_mid_journey_blocks_later_rows(self):
        station = DensityStation(fail_run_start=True)
        station.start()
        try:
            profile = loopback_density_profile(station.port)
            with patch.dict(os.environ, _loopback_env(), clear=True):
                transport = LiveStructuredDensityTransport(profile, profile)
                report = run_structured_density(profile=profile, transport=transport)
        finally:
            station.stop()
        self.assertEqual(report["status"], "FAIL")
        statuses = [row["status"] for row in report["assertions"]]
        self.assertEqual(statuses.count("PASS"), 7)
        self.assertEqual(statuses.count("FAIL"), 1)
        self.assertEqual(statuses.count("NOT_RUN"), 35)
        self.assertEqual(station.count("DELETE", f"/api/v1/groups/{GROUP_ID}"), 0)


if __name__ == "__main__":
    unittest.main()
