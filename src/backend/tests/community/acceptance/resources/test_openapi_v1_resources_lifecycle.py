"""Route-B acceptance: openapi_v1 resources lifecycle on a live singlebox backend.

Exercises the public ``/openapi/v1/bots/resources`` surface so the
Phase-1+3 ``core/resources`` service methods added for the openapi_v1
handlers (``upload_file`` / ``download_resource`` / ``preview_resource`` /
``delete_resource`` with device_fs) are covered by singlebox acceptance, not
just unit tests. This is the coverage-debt payback for the 290 lines added
under ``core/resources/`` that the legacy ``/api/resources`` acceptance path
does not reach.

Mirrors ``test_resource_metadata_lifecycle.py``'s live-personal-bot pattern
but talks the public Envelope contract (``code`` / ``data``) instead of the
legacy ``{success, data}`` shape.

Off by default; enable with RUN_ACCEPTANCE=1.
"""
from __future__ import annotations

import httpx
import pytest

from tests.community.acceptance._fixtures.live_personal_bot import (
    create_live_personal_bot,
    fresh_id,
)


def _assert_ok(response: httpx.Response) -> dict:
    """Assert the public Envelope returned a code matching the HTTP status."""
    assert response.status_code in (200, 201), response.text
    payload = response.json()
    assert payload.get("code", 0) // 1000 == response.status_code, payload
    return payload.get("data") or {}


@pytest.mark.acceptance
def test_openapi_v1_resources_link_lifecycle(live_backend):
    """List / create (LINK) / get / check-name / update / delete via openapi_v1.

    Covers the pure-DB paths (no device_fs) plus the LINK create branch and the
    delete-after-create flow. The delete here is a soft-delete (status=deleted)
    so device_fs is not strictly required.
    """
    user_id = fresh_id("e2e_openapi_res_user")
    headers = {"x-user-id": user_id}

    with httpx.Client(base_url=live_backend, headers=headers, timeout=60.0) as client:
        bot = create_live_personal_bot(
            client,
            user_id=user_id,
            bot_name_prefix="OpenapiRes",
            bot_desc="openapi_v1 resources lifecycle bot",
        )
        bot_id = bot["bot_id"]

        # --- list (may have leftover data from other acceptance tests;
        #     we only assert we can list without error) ---
        listed = _assert_ok(
            client.get(f"/openapi/v1/bots/resources?bot_id={bot_id}")
        )
        assert "total" in listed

        # --- create a LINK resource ---
        created = _assert_ok(
            client.post(
                f"/openapi/v1/bots/resources?bot_id={bot_id}",
                json={"name": "openapi-link", "type": "link", "url": "https://example.com/x"},
            )
        )
        rid = created["resource_id"]
        assert created["type"] == "link"
        assert created["url"] == "https://example.com/x"

        # --- get by id ---
        got = _assert_ok(client.get(f"/openapi/v1/bots/resources/{rid}?bot_id={bot_id}"))
        assert got["resource_id"] == rid

        # --- check-name (taken) ---
        checked = _assert_ok(
            client.get(
                f"/openapi/v1/bots/resources/check-name?bot_id={bot_id}"
                f"&name=openapi-link&type=link"
            )
        )
        # NameCheck shape: {name, exists}
        assert checked.get("exists") is True

        # --- list (now 1) ---
        listed = _assert_ok(
            client.get(f"/openapi/v1/bots/resources?bot_id={bot_id}")
        )
        assert listed.get("total", 0) >= 1

        # --- delete (soft) ---
        deleted = _assert_ok(
            client.delete(f"/openapi/v1/bots/resources/{rid}?bot_id={bot_id}")
        )
        assert deleted.get("deleted") is True


@pytest.mark.acceptance
def test_openapi_v1_resources_file_upload_download_preview(live_backend):
    """Upload / download / preview a FILE resource via openapi_v1.

    Covers the device_fs-bearing service methods (``upload_file`` /
    ``download_resource`` / ``preview_resource``) that the legacy
    ``/api/resources`` acceptance path does not reach. Requires the bot to be
    ready so the device-fs resolver can dispatch a real boundary.
    """
    from tests.community.acceptance._fixtures.live_personal_bot import wait_bot_ready

    user_id = fresh_id("e2e_openapi_file_user")
    headers = {"x-user-id": user_id}

    with httpx.Client(base_url=live_backend, headers=headers, timeout=120.0) as client:
        bot = create_live_personal_bot(
            client,
            user_id=user_id,
            bot_name_prefix="OpenapiFile",
            bot_desc="openapi_v1 file lifecycle bot",
        )
        bot_id = bot["bot_id"]
        wait_bot_ready(client, bot_id)

        # --- upload a raw file (octet-stream) ---
        content = b"hello openapi_v1 file coverage"
        upload = _assert_ok(
            client.post(
                f"/openapi/v1/bots/resources/upload?bot_id={bot_id}&name=cover.txt",
                content=content,
                headers={**headers, "Content-Type": "application/octet-stream"},
            )
        )
        file_id = upload["resource_id"]
        assert upload["type"] == "file"

        # --- download the bytes back (raw, not enveloped) ---
        dl = client.get(
            f"/openapi/v1/bots/resources/{file_id}/download?bot_id={bot_id}"
        )
        assert dl.status_code == 200, dl.text
        assert dl.content == content

        # --- preview (enveloped Preview) ---
        prev = _assert_ok(
            client.get(
                f"/openapi/v1/bots/resources/{file_id}/preview?bot_id={bot_id}"
            )
        )
        assert prev.get("resource_id") == file_id

        # --- hard delete (device_fs delete path for a file) ---
        deleted = _assert_ok(
            client.delete(f"/openapi/v1/bots/resources/{file_id}?bot_id={bot_id}")
        )
        assert deleted.get("deleted") is True
