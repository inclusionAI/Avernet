"""Strict in-browser Pack API fixtures; not a Git, SQL, or authorization test.

Only reuse the old harness's fail-closed static/network guard. None of its legacy
DB-save/Git-save fixtures are reachable through this fixture implementation.
"""

from __future__ import annotations

import base64
import copy
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import yaml
from playwright.sync_api import Request, Route
from visual_editor_network import OfflineNetworkGuard

A, B = "pack-browser-a", "pack-browser-b"
PACK = "browser-pack"
BASE_COMMIT, SAVED_COMMIT, LATER_COMMIT = "a" * 40, "b" * 40, "c" * 40
SCRIPT = "scripts/main.py"
ORIGINAL_SCRIPT = '#!/usr/bin/env python3\nprint("original")\n'
# Exercise the production client's >14 KiB auto-gzip threshold, not a tiny PUT.
EDITED_SCRIPT = (
    '#!/usr/bin/env python3\nprint("浏览器草稿")\n' + "# byte-fidelity\n" * 1200
)
BINARY = bytes(range(256)) * 80


def encode(value: str | bytes) -> str:
    return base64.b64encode(
        value.encode() if isinstance(value, str) else value
    ).decode()


def spec(workflow_id: str) -> dict[str, Any]:
    return {
        "id": workflow_id,
        "title": "浏览器工作流 A" if workflow_id == A else "浏览器工作流 B",
        "version": "1",
        "nodes": [
            {
                "id": "script",
                "title": "执行脚本",
                "phase": "P1",
                "dependsOn": [],
                "executor": {
                    "type": "cli-script",
                    "command": "python3",
                    "args": [SCRIPT],
                },
            }
        ],
    }


def stored_workflow(workflow_id: str) -> dict[str, Any]:
    """Full DB candidate projection before GET overlays row metadata."""
    document = spec(workflow_id)
    return {**document, "content": yaml.safe_dump(document, allow_unicode=True)}


def workflow_response(workflow_id: str) -> dict[str, Any]:
    """DB GET shape, distinct from the Git/YAML execution document.

    workflows.ts overlays numeric row.version and epoch-ms updatedAt onto the
    parsed spec; stored specs can also retain their source YAML in content.
    Every successful workflow GET uses this shape, including history navigation.
    Do not normalize these fields in the fixture: the real UI owns that boundary.
    """
    return {
        **stored_workflow(workflow_id),
        "version": 1,
        "updatedAt": 1_793_404_800_000,
    }


def file(path: str, content: str | bytes, mode: str = "100644") -> dict[str, str]:
    return {"path": path, "contentBase64": encode(content), "mode": mode}


def initial_files(workflow_id: str) -> list[dict[str, str]]:
    return [
        file(
            "workflow.pack.yaml",
            yaml.safe_dump(
                {
                    "id": PACK,
                    "version": "1",
                    "workflows": [workflow_id],
                }
            ),
        ),
        file(
            f"workflows/{workflow_id}.yaml",
            yaml.safe_dump(spec(workflow_id), allow_unicode=True),
        ),
        file(SCRIPT, ORIGINAL_SCRIPT, "100755"),
        file("assets/original.bin", b"\x00\xff\x80unchanged\r\n"),
        file("README.md", "unchanged resource\n"),
    ]


class FixtureReply(Exception):
    def __init__(
        self,
        status: int,
        body: dict[str, Any] | str,
        content_type: str = "application/json",
    ) -> None:
        super().__init__(f"Intentional fixture HTTP {status}")
        self.status, self.body, self.content_type = status, body, content_type


class LostResponse(Exception):
    """The synthetic operation persisted, but its HTTP response was lost."""


class PackFixture(OfflineNetworkGuard):
    def __init__(self, dist_dir: Path) -> None:
        super().__init__("http://127.0.0.1:5189", dist_dir, spec(A))
        self.files = {workflow_id: initial_files(workflow_id) for workflow_id in (A, B)}
        self.draft_files = copy.deepcopy(self.files)
        self.draft_head = BASE_COMMIT
        self.write_gate: tuple[str, str] | None = None
        self.writes: list[dict[str, Any]] = []
        self.responses: list[dict[str, Any]] = []
        self.expected_changes: list[dict[str, str]] = []
        self.attempt: dict[str, Any] | None = None
        self.receipt: dict[str, Any] | None = None
        self.save_mode = "success"
        self.operation_state = "unknown"
        self.applied_saves = 0
        self.fail_workflow: str | None = None

    @property
    def save_path(self) -> str:
        return f"/api/workflows/{A}/pack-version"

    @contextmanager
    def allow_write(self, method: str, path: str) -> Iterator[None]:
        if self.write_gate is not None:
            raise AssertionError("Nested write gates are forbidden")
        self.write_gate = (method, path)
        try:
            yield
            self.assert_clean()
            if self.write_gate is not None:
                raise AssertionError(f"Expected UI write was not sent: {method} {path}")
        finally:
            self.write_gate = None

    def _consume_write(self, request: Request, path: str) -> None:
        if self.write_gate != (request.method, path):
            raise ValueError(
                "Write outside its explicit UI action, duplicate, or legacy API"
            )
        self.write_gate = None
        headers = request.all_headers()
        if headers.get("origin") != self.base_url:
            raise ValueError("Write origin differs from the synthetic local origin")
        if headers.get("content-type") != "application/json":
            raise ValueError("Write must use application/json")
        if headers.get("content-encoding"):
            raise ValueError("Pack writes must not be gzipped")
        if headers.get("x-pack-version-csrf") != "1":
            raise ValueError("Missing X-Pack-Version-CSRF: 1")
        body = request.post_data_buffer or b""
        self.writes.append(
            {
                "method": request.method,
                "path": path,
                "headers": {
                    key: headers[key]
                    for key in (
                        "content-type",
                        "content-encoding",
                        "origin",
                        "x-pack-version-csrf",
                    )
                    if key in headers
                },
                "bytes": len(body),
                "body": json.loads(body) if body else None,
            }
        )

    def _save(self, request: Request) -> dict[str, Any]:
        self._consume_write(request, self.save_path)
        body = self.writes[-1]["body"]
        if not isinstance(body, dict) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{7,127}", body.get("requestId", "")
        ):
            raise ValueError("Missing/invalid stable request ID")
        expected = {
            "sourceVersion": "current",
            "sourceCommit": BASE_COMMIT,
            "expectedSavedHead": BASE_COMMIT,
            "changes": self.expected_changes,
            "requestId": body["requestId"],
        }
        if body != expected:
            raise ValueError(
                "PUT differs from the original opened baseline or exact edited bytes/modes"
            )
        if self.attempt is not None:
            if body != self.attempt:
                raise ValueError(
                    "Retry changed request ID, baseline, or candidate bytes"
                )
            if self.receipt is None:
                raise ValueError("No synthetic receipt available for retry")
            self.operation_state = "completed"
            return self.receipt
        self.attempt = copy.deepcopy(body)
        if self.save_mode == "conflict":
            raise FixtureReply(
                409, {"code": "VERSION_CONFLICT", "requestId": body["requestId"]}
            )
        changed = {entry["path"]: entry for entry in self.draft_files[A]}
        for change in body["changes"]:
            if change["operation"] == "delete":
                del changed[change["path"]]
            else:
                changed[change["path"]] = {
                    key: change[key] for key in ("path", "contentBase64", "mode")
                }
        self.draft_files[A] = list(changed.values())
        self.draft_head = SAVED_COMMIT
        self.applied_saves += 1
        self.receipt = {
            "branch": f"saved/{A}",
            "commit": SAVED_COMMIT,
            "requestId": body["requestId"],
            "version": 2,
            "packId": PACK,
        }
        if self.save_mode == "lost":
            raise LostResponse()
        self.operation_state = "completed"
        return self.receipt

    def advance_draft(self) -> None:
        """Simulate another writer's S2 without altering the saved S receipt."""
        self.draft_head = LATER_COMMIT
        self.draft_files[A] = [
            file(SCRIPT, 'print("later writer")\n', "100755")
            if entry["path"] == SCRIPT
            else entry
            for entry in self.draft_files[A]
        ]

    def _api_response(self, request: Request, path: str) -> Any:
        method = request.method
        query = parse_qs(urlsplit(request.url).query)
        if path == self.save_path and method == "PUT" and not query:
            return self._save(request)
        if path == "/api/auto-heal/deploy" and method == "POST" and not query:
            self._consume_write(request, path)
            body = self.writes[-1]["body"]
            if (
                self.receipt is None
                or body.get("candidateCommit") != self.receipt["commit"]
            ):
                raise ValueError(
                    "Deployment must bind the original saved S, not mutable S2"
                )
            if (
                body.get("workflowId") != A
                or body.get("packId") != PACK
                or body.get("autoRun") is not False
            ):
                raise ValueError("Unexpected deployment identity or rerun")
            if not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_-]{7,127}", body.get("requestId", "")
            ):
                raise ValueError("Deployment request ID is missing")
            if body["requestId"] == self.attempt["requestId"]:
                raise ValueError(
                    "Save and deploy must have separate operation identities"
                )
            return {
                "ok": True,
                "deployed": True,
                "workflowId": A,
                "packId": PACK,
                "requestId": body["requestId"],
                "version": 2,
                "deployNumber": 1,
                "tagName": f"deploy/{A}/#1",
            }
        if self.attempt:
            operation_path = (
                f"/api/workflows/{A}/pack-operations/{self.attempt['requestId']}"
            )
            recover = path == operation_path + "/reconcile" and method == "POST"
            if not query and (recover or (path == operation_path and method == "GET")):
                if recover:
                    self._consume_write(request, path)
                    self.operation_state = "completed"
                return {
                    "requestId": self.attempt["requestId"],
                    "kind": "save",
                    "state": self.operation_state,
                    **(
                        {"result": self.receipt}
                        if self.operation_state == "completed"
                        else {}
                    ),
                }
        if method == "GET":
            if path == "/api/runs/workflow-types":
                return {
                    "workflows": [
                        {
                            "workflow_id": workflow_id,
                            "workflow_title": spec(workflow_id)["title"],
                            "owner_id": "browser-user",
                            "run_count": 0,
                            "last_status": "pending",
                        }
                        for workflow_id in (A, B)
                    ]
                }
            if path == "/api/auth/me":
                return {"userId": "browser-user", "isAdmin": False}
            if path == "/api/facades":
                return []
            for workflow_id in (A, B):
                root = f"/api/workflows/{workflow_id}"
                if path == root and not query:
                    if workflow_id == self.fail_workflow:
                        raise FixtureReply(
                            503,
                            {
                                "code": "SERVICE_UNAVAILABLE",
                                "message": "synthetic target read failure",
                            },
                        )
                    return workflow_response(workflow_id)
                if path == root + "/export" and not query:
                    # Existing /export serializes the full stored spec, before
                    # row.version/updatedAt overlays, including projection content.
                    raise FixtureReply(
                        200,
                        yaml.safe_dump(
                            stored_workflow(workflow_id), allow_unicode=True
                        ),
                        "application/x-yaml",
                    )
                if path == root + "/history":
                    return {"workflowId": workflow_id, "history": [], "active": None}
                if path == root + "/pack-version" and query in (
                    {"version": ["current"]},
                    {"version": ["draft"]},
                ):
                    draft = query["version"] == ["draft"]
                    head = self.draft_head if workflow_id == A else BASE_COMMIT
                    return {
                        "workflowId": workflow_id,
                        "packId": PACK,
                        "canWrite": True,
                        "baseRef": f"origin/{'saved' if draft else 'wf'}/{workflow_id}",
                        "baseCommit": head if draft else BASE_COMMIT,
                        "draftHead": head,
                        "versionFiles": copy.deepcopy(
                            (self.draft_files if draft else self.files)[workflow_id]
                        ),
                    }
        # Deliberately DO NOT fall back to the old _api_response implementation.
        raise ValueError(
            f"Unlisted Pack fixture API (legacy writes forbidden): {method} {path}"
        )

    def handle_route(self, route: Route) -> None:
        status = 200
        try:
            content_type, body = self._response(route.request)
        except FixtureReply as reply:
            status, content_type, body = (
                reply.status,
                reply.content_type,
                json.dumps(reply.body)
                if reply.content_type == "application/json"
                else reply.body,
            )
        except LostResponse:
            self.responses.append(
                {"url": route.request.url, "status": "intentionally-lost"}
            )
            route.abort("connectionclosed")
            return
        except (ValueError, TypeError, OSError, KeyError, AttributeError) as error:
            self.violations.append(
                {
                    "method": route.request.method,
                    "url": route.request.url,
                    "reason": str(error),
                }
            )
            route.abort("blockedbyclient")
            return
        if "/api/" in route.request.url:
            self.responses.append({"url": route.request.url, "status": status})
        route.fulfill(status=status, content_type=content_type, body=body)
