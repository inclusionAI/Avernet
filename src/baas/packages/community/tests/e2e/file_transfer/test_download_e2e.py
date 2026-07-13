"""E2E tests for file transfer download full-chain verification.

Exercises the download happy path:
  POST download-url API -> Dispatcher push -> stub OSS write ->
  poller scan -> DONE + download_url written

The download dir…tion uses an async ticket model (D-05): the API returns only
transfer_id (no download_url).  The Dispatcher triggers push_file to stub OSS.
The poller detects the uploaded file, transitions PUSHING -> DONE, and writes
download_url.  All poller-behavior verification is done exclusively through
mock_ticket_repo assertions — the mock poller repo is separate from the real
OrmTicketRepository in the running BaaS process.
"""

from __future__ import annotations

import pytest

from ...conftest import (
    APITestHelper,
    cleanup_bot,
    create_test_bot,
    find_existing_bot,
)

pytestmark = [pytest.mark.e2e, pytest.mark.sync]


class TestDownloadE2E:

    pytestmark = pytest.mark.e2e

    @pytest.mark.asyncio
    async def test_download_full_chain(
        self,
        api: APITestHelper,
        unique_id: str,
        stub_oss_backend,
        mock_ticket_repo,
        mock_paas_service,
        poller,
    ) -> None:
        """Download full chain: API -> Dispatcher push -> stub OSS write ->
        poller scan -> DONE + download_url (verified via mock_ticket_repo).

        Steps:
        1. Find or create a valid bot with active device
        2. POST /api/v1/bots/{tenant}/{bot_uuid}/files/download-url
        3. Verify response: transfer_id present, download_url absent
        4. Simulate device push to stub OSS at expected staging path
        5. Configure mock_ticket_repo for poller scan
        6. Run poller directly (await poller.run())
        7. Verify status transitions: PUSHING, DONE
        8. Verify download_url was written via update_urls call
        """
        from .conftest import _make_ticket_record

        # ── Step 1: Find or create a valid bot ─────────────────────────
        bot = await find_existing_bot(api)
        bot_created = False
        if bot is None:
            bot = await create_test_bot(api, name=f"ft-download-{unique_id}")
            bot_created = True
            try:
                from ...conftest import activate_test_bot
                await activate_test_bot(api, bot)
            except Exception:
                pass  # Activation is best-effort for non-hook bots

        bot_uuid = bot["bot_uuid"]
        if not bot_uuid:
            await cleanup_bot(api, bot_uuid)
            pytest.skip("No bot available for download E2E test")

        try:
            # ── Step 2: Call download-url API ──────────────────────────
            response = await api.client.post(
                f"/api/v1/bots/{api.tenant}/{bot_uuid}/files/download-url",
                params=api.params(),
                json={
                    "device_path": "/home/bot/test_download.txt",
                    "expire_seconds": 3600,
                },
            )
            assert response.status_code == 200, (
                f"Download URL request failed: {response.status_code} {response.text}"
            )
            body = response.json()
            assert "data" in body, f"Response missing 'data': {body}"
            data = body["data"]
            # Async ticket model (D-05): no download_url in response
            assert "download_url" not in data, (
                f"Download response should NOT contain download_url "
                f"(async ticket model): {data}"
            )
            transfer_id = data["transfer_id"]
            assert transfer_id, "transfer_id is empty"
            assert data["expires_at"], "expires_at is empty"

            # ── Step 3: Simulate device push to stub OSS ───────────────
            # The Dispatcher constructs staging_path as:
            #   file-transfers/{transfer_id}/{filename}
            # where filename = Path(device_path).name = "test_download.txt"
            staging_path = f"file-transfers/{transfer_id}/test_download.txt"
            test_content = b"hello e2e download test"

            # Generate the stub upload URL and simulate the device push
            put_url = stub_oss_backend.generate_upload_url(staging_path, 3600)
            stub_oss_backend.put_content(put_url, test_content)

            # Verify the stub storage received the content
            assert stub_oss_backend.check_object_exists(staging_path), (
                "Stub OSS should have the downloaded file after simulated push"
            )

            # ── Step 4: Configure mock ticket_repo for poller scan ─────
            ticket = _make_ticket_record(
                transfer_id=transfer_id,
                direction="DOWNLOAD",
                status="CREATED",
                device_path="/home/bot/test_download.txt",
                filename="test_download.txt",
                fileservice_staging_path=staging_path,
            )
            mock_ticket_repo.list_pending_uploads.return_value = [ticket]

            # ── Step 5: Run poller directly (D-09) ─────────────────────
            await poller.run()

            # ── Step 6: Verify poller processed the download ticket ────
            status_transitions = [
                (c.args[0], c.args[1])
                for c in mock_ticket_repo.update_status.call_args_list
            ]
            assert (transfer_id, "PUSHING") in status_transitions, (
                f"Expected PUSHING transition not found. "
                f"Got: {status_transitions}"
            )
            assert (transfer_id, "DONE") in status_transitions, (
                f"Expected DONE transition not found. "
                f"Got: {status_transitions}"
            )

            # ── Step 7: Verify download_url was written ────────────────
            # update_urls is called with download_url=... by the poller
            download_url_written = None
            for c in mock_ticket_repo.update_urls.call_args_list:
                if c.kwargs.get("download_url"):
                    download_url_written = c.kwargs["download_url"]
                    break

            assert download_url_written is not None, (
                "Poller did not write download_url via update_urls. "
                f"update_urls calls: {mock_ticket_repo.update_urls.call_args_list}"
            )
            # Should be a stub-download URL from the StubFileTransferBackend
            assert download_url_written.startswith("stub-download://"), (
                f"download_url should be a stub-download URL, got: {download_url_written}"
            )

            # Verify the download content can be retrieved
            retrieved = stub_oss_backend.get_content(download_url_written)
            assert retrieved == test_content, (
                "Downloaded content should match original test content"
            )

        finally:
            # ── Cleanup ────────────────────────────────────────────────
            if bot_created:
                try:
                    await cleanup_bot(api, bot_uuid)
                except Exception:
                    pass  # Ignore cleanup errors