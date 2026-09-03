"""Real singlebox zip download of a bot workspace directory (openapi_v1).

The files module's acceptance denominator had no story that reached
``ResourceFileService.iter_directory_files`` — the download-dir walk is only
invoked by the public ``/openapi/v1`` endpoint, and the console
``/api/resources/files`` stories in this directory never touch it. This story
drives the public endpoint against a real baas-backed workspace: the same
device filesystem, the same auth the gateway fronts, over the wire.
"""

from __future__ import annotations

import io
import os
import time
import zipfile

import httpx
import jwt
import pytest

from tests.community.acceptance._fixtures.live_personal_bot import (
    create_live_personal_bot,
    fresh_id,
)

# The key the singlebox backend is configured with (see singlebox_coverage.sh);
# test and backend must agree on it for the minted principal to verify.
_PRINCIPAL_KEY = os.environ.get(
    "SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY",
    "singlebox-gateway-principal-key-not-for-production",
)


def _principal_headers(user_id: str) -> dict[str, str]:
    """Gateway-signed principal for the bot's owner — what the public API trusts."""
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": user_id, "username": "files@example.test"},
                }
            ],
        },
        _PRINCIPAL_KEY,
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": token}


def _upload(client: httpx.Client, bot_id: str, path: str, filename: str, data: bytes):
    response = client.post(
        f"/api/resources/files/upload?bot_id={bot_id}&path={path}",
        files={"files": (filename, io.BytesIO(data), "text/plain")},
    )
    assert response.status_code == 200, response.text


def _mkdir_when_baas_visible(client: httpx.Client, bot_id: str, path: str) -> None:
    """Create a directory after the newly active BaaS bot is queryable.

    The Backend status poll may observe the activation callback a few
    milliseconds before BaaS's bot-read path sees the same record. Retry only
    that transient ``BOT_NOT_FOUND`` response; every other error remains a
    test failure at its original boundary.
    """
    last_response: httpx.Response | None = None
    for attempt in range(5):
        response = client.post(
            f"/api/resources/files/mkdir?bot_id={bot_id}",
            data={"path": path},
        )
        if response.status_code == 200:
            return
        last_response = response
        if "BOT_NOT_FOUND" not in response.text:
            break
        time.sleep(0.1 * (attempt + 1))

    assert last_response is not None
    assert last_response.status_code == 200, last_response.text


@pytest.mark.acceptance
def test_workspace_directory_download(live_backend):
    """Zip a workspace directory through the public API, get what the browser shows.

    One story over one live bot: build a tree, download the directory as one
    archive (subdirectories included, dotfiles excluded), download the whole
    workspace (root identity files excluded), ask for a directory that is not
    there (an ordinary 404, not a 500 — the baas providers answer a missing
    directory with an upstream 404), and remove the tree.
    """
    user_id = fresh_id("e2e_dl_user")
    headers = {"x-user-id": user_id}
    parent = f"dl_live_{time.time_ns()}"
    notes_payload = b"notes at the top of the downloaded directory\n"
    deep_payload = b"nested deeper in the same archive\n"

    with httpx.Client(base_url=live_backend, headers=headers, timeout=60.0) as client:
        bot = create_live_personal_bot(
            client,
            user_id=user_id,
            # Prefix short on purpose: fresh_id appends "_<12 hex>", and the
            # bot name cap is 32 — "Files DL" lands at 21, the long spelling
            # above 400s on create before the story does anything.
            bot_name_prefix="Files DL",
            bot_desc="workspace directory download bot",
        )
        bot_id = bot["bot_id"]

        # A tree the walk has to recurse into, plus a dot on the flat level
        # (each mkdir drops a .keep the download must filter) and an identity
        # file at the workspace root (hidden from a *root* download only).
        for directory in (parent, f"{parent}/nested"):
            _mkdir_when_baas_visible(client, bot_id, directory)
        _upload(client, bot_id, parent, "notes.txt", notes_payload)
        _upload(client, bot_id, f"{parent}/nested", "deep.txt", deep_payload)
        _upload(client, bot_id, "", "AGENTS.md", b"# workspace identity\n")

        public_headers = _principal_headers(user_id)
        download_dir = f"/openapi/v1/bots/{bot_id}/resources/download-dir"
        query = {"user_id": user_id}

        # -- the directory: one recursive zip, dotfiles filtered --
        response = client.get(
            download_dir, params={**query, "path": parent}, headers=public_headers
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/zip")
        assert response.headers["content-disposition"].startswith("attachment;")
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            assert f"{parent}/notes.txt" in names
            assert f"{parent}/nested/deep.txt" in names
            assert archive.read(f"{parent}/notes.txt") == notes_payload
            assert archive.read(f"{parent}/nested/deep.txt") == deep_payload
            assert not any("/." in name for name in names)  # .keep dropped

        # -- the whole workspace: same tree, and the root identity file hidden --
        response = client.get(download_dir, params=query, headers=public_headers)
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            assert f"workspace/{parent}/notes.txt" in names
            assert "workspace/AGENTS.md" not in names
            assert not any("/." in name for name in names)

        # -- a directory that is not there: 404, never a 500 --
        response = client.get(
            download_dir,
            params={**query, "path": "no_such_directory_zz"},
            headers=public_headers,
        )
        assert response.status_code == 404, response.text

        # -- remove the tree (the service's delete_tree branch) --
        response = client.delete(
            f"/api/resources/files?bot_id={bot_id}&path={parent}"
        )
        assert response.status_code == 200, response.text
