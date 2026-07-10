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

TEST_BOTS_ROOT = Path(__file__).resolve().parents[6] / "test-bots"


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

        response = client.post(
            f"/api/resources/files/upload?bot_id={bot_id}&path={parent}",
            files={"files": (filename, io.BytesIO(payload), "text/plain")},
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
