"""Real singlebox lifecycle for a bot workspace file."""

from __future__ import annotations

import io
import time
from pathlib import Path

import httpx
import pytest

from tests.community.acceptance._fixtures.live_personal_bot import (
    create_live_personal_bot,
    fresh_id,
)


def _resolve_repo_root() -> Path:
    """Locate the checkout that owns the singlebox runtime directories."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts" / "singlebox.sh").is_file():
            return parent
    raise RuntimeError("could not locate repository root from acceptance test path")


TEST_BOTS_ROOT = _resolve_repo_root() / "test-bots"


def _upload_when_baas_visible(
    client: httpx.Client,
    *,
    bot_id: str,
    path: str,
    filename: str,
    payload: bytes,
) -> httpx.Response:
    """Upload after BaaS's freshly activated bot becomes queryable.

    A device callback can make Backend report the binding ACTIVE just before
    BaaS's WebSocket lookup is visible. The file endpoint represents that
    transient failure as a 200 response with an empty ``uploaded`` list, so
    retry only the two observed BaaS errors; other response shapes fail at the
    original test boundary.
    """
    last_response: httpx.Response | None = None
    for attempt in range(5):
        response = client.post(
            f"/api/resources/files/upload?bot_id={bot_id}&path={path}",
            files={"files": (filename, io.BytesIO(payload), "text/plain")},
        )
        body = response.json() if response.status_code == 200 else {}
        if response.status_code == 200 and body.get("uploaded"):
            return response
        last_response = response
        if not any(
            marker in response.text
            for marker in ("BOT_NOT_FOUND", "tuple index out of range")
        ):
            break
        time.sleep(0.1 * (attempt + 1))

    assert last_response is not None
    assert last_response.status_code == 200, last_response.text
    assert last_response.json().get("uploaded"), last_response.text
    return last_response


@pytest.mark.acceptance
def test_workspace_file_roundtrip(live_backend):
    """Create, upload, inspect, download, and delete a real workspace file."""
    user_id = fresh_id("e2e_files_user")
    headers = {"x-user-id": user_id}
    parent = f"files_live_{time.time_ns()}"
    filename = "roundtrip.txt"
    payload = b"singlebox workspace file roundtrip\n"

    with httpx.Client(base_url=live_backend, headers=headers, timeout=60.0) as client:
        bot = create_live_personal_bot(
            client,
            user_id=user_id,
            bot_name_prefix="Files Acceptance",
            bot_desc="workspace files lifecycle bot",
        )
        bot_id = bot["bot_id"]

        response = client.post(
            f"/api/resources/files/mkdir?bot_id={bot_id}",
            data={"path": parent},
        )
        assert response.status_code == 200, response.text

        response = _upload_when_baas_visible(
            client,
            bot_id=bot_id,
            path=parent,
            filename=filename,
            payload=payload,
        )
        assert response.status_code == 200, response.text
        assert response.json()["uploaded"][0]["path"] == f"{parent}/{filename}"

        response = client.get(f"/api/resources/files?bot_id={bot_id}&path={parent}")
        assert response.status_code == 200, response.text
        assert filename in {item["name"] for item in response.json()["items"]}

        file_path = f"{parent}/{filename}"
        response = client.get(
            f"/api/resources/files/preview?bot_id={bot_id}&path={file_path}"
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["content"] == payload.decode()

        response = client.get(
            f"/api/resources/files/download?bot_id={bot_id}&path={file_path}"
        )
        assert response.status_code == 200, response.text
        assert response.content == payload
        assert response.headers["content-disposition"].startswith("attachment;")

        physical_matches = [
            path
            for path in TEST_BOTS_ROOT.rglob(filename)
            if bot_id in str(path)
        ]
        assert physical_matches, "uploaded file was not materialized in the bot workspace"
        assert physical_matches[0].read_bytes() == payload

        response = client.delete(
            f"/api/resources/files?bot_id={bot_id}&path={file_path}"
        )
        assert response.status_code == 200, response.text

        response = client.get(f"/api/resources/files?bot_id={bot_id}&path={parent}")
        assert response.status_code == 200, response.text
        assert filename not in {item["name"] for item in response.json()["items"]}
        assert not physical_matches[0].exists()


@pytest.mark.acceptance
def test_workspace_file_readonly_guard(live_backend):
    """Reject deletion of protected workspace identity files before device I/O."""
    headers = {"x-user-id": fresh_id("e2e_files_guard")}
    with httpx.Client(base_url=live_backend, headers=headers, timeout=30.0) as client:
        response = client.delete(
            "/api/resources/files?bot_id=default&path=AGENTS.md"
        )
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Cannot delete read-only file"
