"""``/api/cli`` — the wire contract the platform parses strictly.

The platform's reader (``ArcaCliToolPort``) is deliberately unforgiving about
one thing: a ``replace`` response that omits a name it sent is treated as
unreadable rather than as an implicit success. These tests pin the engine side
of that agreement, plus the two status conventions a careless implementation
gets wrong — reserving 404 for "no CLI endpoints", and keeping a partial
failure at 200.
"""
from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from engine.community.api.cli import router
from engine.community.core.cli_tools.service import LocalCliToolsService
from engine.community.core.engine.base import BaseEngine
from engine.community.core.engine.capability import Capability, EngineCapabilities
from engine.community.core.engine.registry import EngineRegistry
from engine.community.manager import EngineManager

ELF = b"\x7fELF\x02\x01\x01"
CLI_CAPS = {
    Capability.CLI_INSTALL,
    Capability.CLI_DELETE,
    Capability.CLI_LIST,
    Capability.CLI_REPLACE,
    Capability.CLI_DOWNLOAD,
}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _make_engine(cli_dir: Path, *, caps: set[Capability]):
    class _CliTestEngine(BaseEngine):
        name = "cli-test"
        version = "1.0.0"
        _CAPABILITIES = EngineCapabilities(supported=set(caps))

        @property
        def capabilities(self) -> EngineCapabilities:
            return self._CAPABILITIES

        def __init__(self) -> None:
            super().__init__(None)
            self._session = MagicMock()
            self._chat = MagicMock()
            if caps:
                self._cli_tools = LocalCliToolsService(lambda: cli_dir)

    return _CliTestEngine


def _client(cli_dir: Path, *, caps: set[Capability] = CLI_CAPS) -> TestClient:
    engine_cls = _make_engine(cli_dir, caps=caps)
    EngineManager.reset_instance()
    registry = EngineRegistry()
    registry.register(engine_cls)
    manager = EngineManager(engine_cls.name, registry=registry)
    manager._active_engine = engine_cls()
    EngineManager._instance = manager

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def cli_dir(tmp_path: Path) -> Path:
    return tmp_path / "cli"


@pytest.fixture
def client(cli_dir: Path):
    client = _client(cli_dir)
    try:
        yield client
    finally:
        EngineManager.reset_instance()


class TestInstall:
    def test_places_an_executable_and_leaves_others_alone(self, client, cli_dir):
        client.post("/api/cli/install", json={"name": "keep", "content_b64": _b64(ELF)})

        response = client.post(
            "/api/cli/install",
            json={"name": "mycli", "size_bytes": 8, "content_b64": _b64(ELF + b"x")},
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert (cli_dir / "mycli").read_bytes() == ELF + b"x"
        assert (cli_dir / "keep").read_bytes() == ELF
        assert (cli_dir / "mycli").stat().st_mode & 0o111  # executable

    def test_at_the_documented_size_cap(self, client, cli_dir):
        """200 MiB plus base64 overhead must not trip a body limit."""
        payload = b"\x7fELF" + b"\0" * (200 * 1024 * 1024 - 4)

        response = client.post(
            "/api/cli/install", json={"name": "big", "content_b64": _b64(payload)}
        )

        assert response.status_code == 200
        assert (cli_dir / "big").stat().st_size == 200 * 1024 * 1024

    def test_malformed_base64_is_refused_rather_than_truncated(self, client, cli_dir):
        """Without validate=True this would install a silently short binary."""
        response = client.post(
            "/api/cli/install", json={"name": "mycli", "content_b64": "not!base64!!"}
        )

        assert response.status_code == 400
        assert not (cli_dir / "mycli").exists()

    def test_a_traversing_name_is_refused(self, client):
        response = client.post(
            "/api/cli/install", json={"name": "../escape", "content_b64": _b64(ELF)}
        )

        assert response.status_code == 400


class TestDelete:
    def test_removes_the_command(self, client, cli_dir):
        client.post("/api/cli/install", json={"name": "mycli", "content_b64": _b64(ELF)})

        response = client.post("/api/cli/delete", json={"name": "mycli"})

        assert response.json()["success"] is True
        assert not (cli_dir / "mycli").exists()

    def test_an_absent_command_reports_success(self, client):
        response = client.post("/api/cli/delete", json={"name": "never-existed"})

        assert response.status_code == 200
        assert response.json()["success"] is True


class TestReplace:
    def test_removes_tools_not_named_in_the_request(self, client, cli_dir):
        client.post("/api/cli/install", json={"name": "old", "content_b64": _b64(ELF)})

        response = client.post(
            "/api/cli/replace",
            json={"tools": [{"name": "kept", "content_b64": _b64(ELF)}]},
        )

        assert response.status_code == 200
        assert sorted(p.name for p in cli_dir.iterdir()) == ["kept"]

    def test_an_empty_list_clears_every_tool(self, client, cli_dir):
        client.post("/api/cli/install", json={"name": "a", "content_b64": _b64(ELF)})
        client.post("/api/cli/install", json={"name": "b", "content_b64": _b64(ELF)})

        response = client.post("/api/cli/replace", json={"tools": []})

        assert response.status_code == 200
        assert response.json()["data"]["results"] == []
        assert list(cli_dir.iterdir()) == []

    def test_answers_for_every_requested_name_including_failures(self, client):
        response = client.post(
            "/api/cli/replace",
            json={
                "tools": [
                    {"name": "good", "content_b64": _b64(ELF)},
                    {"name": "../bad", "content_b64": _b64(ELF)},
                    {"name": "undecodable", "content_b64": "not!base64!!"},
                ]
            },
        )

        results = response.json()["data"]["results"]
        by_name = {r["name"]: r for r in results}
        assert set(by_name) == {"good", "../bad", "undecodable"}
        assert by_name["good"]["success"] is True
        assert by_name["../bad"]["success"] is False
        assert by_name["undecodable"]["success"] is False

    def test_a_partial_failure_is_still_http_200(self, client):
        response = client.post(
            "/api/cli/replace",
            json={
                "tools": [
                    {"name": "good", "content_b64": _b64(ELF)},
                    {"name": "../bad", "content_b64": _b64(ELF)},
                ]
            },
        )

        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_the_response_satisfies_the_platforms_strict_reader(self, client):
        """Replicates the platform's parser so the two sides cannot drift.

        The engine package cannot import the backend — they are separate
        installables and engine CI runs from ``src/engine`` alone — so this
        re-implements the *rule* that ``_failures_in``
        (``…/bot_config_manifest/cli_tools/arca_port.py``) enforces rather
        than calling it. Keep the two in step: it raises
        ``CliToolDeliveryError`` when a requested name is missing from
        ``results``, refusing to record any tool as installed, because
        silence read as success is how a tool the bot does not have ends up
        in a green apply report.
        """
        requested = ["good", "../bad", "undecodable"]
        response = client.post(
            "/api/cli/replace",
            json={
                "tools": [
                    {"name": "good", "content_b64": _b64(ELF)},
                    {"name": "../bad", "content_b64": _b64(ELF)},
                    {"name": "undecodable", "content_b64": "not!base64!!"},
                ]
            },
        )

        payload = response.json()
        # The parser reads data.results, and requires a dict per entry with a
        # str "name" and an explicit "success".
        results = payload["data"]["results"]
        assert isinstance(results, list)
        verdicts = {}
        for item in results:
            assert isinstance(item, dict), item
            assert isinstance(item.get("name"), str), item
            assert isinstance(item.get("success"), bool), item
            verdicts[item["name"]] = item

        # The strictness itself: not one requested name may be unanswered.
        unanswered = [name for name in requested if name not in verdicts]
        assert unanswered == [], f"the platform would reject this response: {unanswered}"

        # A failure must carry a reason the apply report can show the user.
        assert verdicts["../bad"]["message"]


class TestListAndDownload:
    def test_list_is_empty_not_a_404_when_the_bot_has_no_tools(self, client):
        response = client.get("/api/cli/list")

        assert response.status_code == 200
        assert response.json()["data"]["tools"] == []

    def test_list_carries_md5_so_a_swapped_binary_is_detectable(
        self, client, cli_dir
    ):
        client.post("/api/cli/install", json={"name": "mycli", "content_b64": _b64(ELF)})
        before = client.get("/api/cli/list").json()["data"]["tools"][0]

        (cli_dir / "mycli").write_bytes(ELF + b"swapped")
        after = client.get("/api/cli/list").json()["data"]["tools"][0]

        assert before["name"] == after["name"]
        assert before["md5"] != after["md5"]

    def test_download_returns_the_bytes(self, client):
        client.post(
            "/api/cli/install",
            json={"name": "mycli", "content_b64": _b64(ELF + b"one")},
        )

        body = client.get("/api/cli/download", params={"name": "mycli"}).json()

        assert body["success"] is True
        assert base64.b64decode(body["data"]["content_b64"]) == ELF + b"one"

    def test_download_of_an_absent_tool_is_200_not_found_never_404(self, client):
        """404 means "this engine build has no CLI endpoints".

        Reusing it for an unknown name would make one bad tool look like a
        permanently endpoint-less engine, which the platform reports as a
        broken engine rather than a bad declaration.
        """
        response = client.get("/api/cli/download", params={"name": "absent"})

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["error"] == "not_found"


class TestCapabilityGate:
    def test_an_engine_without_the_capability_refuses(self, cli_dir):
        """A refusal, never a silent success — the platform must not record a
        tool as installed on an engine that cannot place one."""
        client = _client(cli_dir, caps=set())
        try:
            for method, path, kwargs in (
                ("post", "/api/cli/install",
                 {"json": {"name": "x", "content_b64": _b64(ELF)}}),
                ("post", "/api/cli/delete", {"json": {"name": "x"}}),
                ("post", "/api/cli/replace", {"json": {"tools": []}}),
                ("get", "/api/cli/list", {}),
                ("get", "/api/cli/download", {"params": {"name": "x"}}),
            ):
                response = getattr(client, method)(path, **kwargs)
                assert response.status_code == 501, path
                # Nothing was placed by a refused call.
                assert not cli_dir.exists() or list(cli_dir.iterdir()) == []
        finally:
            EngineManager.reset_instance()
