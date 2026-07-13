"""E2E tests for file transfer upload full-chain verification.

Exercises the upload happy path:
  POST upload-url API -> stub OSS write -> poller scan -> DONE

Verifies that the poller detects the uploaded file and triggers device pull
via paas_facade, and that status transitions through UPLOAD_COMPLETED -> DONE.
All verification is done exclusively through mock_ticket_repo assertions.
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


class TestUploadE2E:

    pytestmark = pytest.mark.e2e

    @pytest.mark.asyncio
    async def test_upload_full_chain(
        self,
        api: APITestHelper,
        unique_id: str,
        stub_oss_backend,
        mock_ticket_repo,
        poller,
    ) -> None:
        """Upload full chain: API -> stub OSS write -> poller scan -> DONE.

        Steps:
        1. Find or create a valid bot with active device
        2. POST /api/v1/bots/{tenant}/{bot_uuid}/files/upload-url
        3. PUT test content to stub OSS via returned upload_url
        4. Configure mock_ticket_repo for poller scan
        5. Run poller directly (await poller.run())
        6. Verify status transitions: UPLOAD_COMPLETED, DONE
        7. Verify device pull_file was triggered
        """
        from .conftest import _make_ticket_record

        # ── Step 1: Find or create a valid bot ─────────────────────────
        bot = await find_existing_bot(api)
        bot_created = False
        if bot is None:
            bot = await create_test_bot(api, name=f"ft-upload-{unique_id}")
            bot_created = True
            try:
                from ...conftest import activate_test_bot
                await activate_test_bot(api, bot)
            except Exception:
                pass  # Activation is best-effort for non-hook bots

        bot_uuid = bot["bot_uuid"]
        if not bot_uuid:
            await cleanup_bot(api, bot_uuid)
            pytest.skip("No bot available for upload E2E test")

        try:
            # ── Step 2: Call upload-url API ────────────────────────────
            response = await api.client.post(
                f"/api/v1/bots/{api.tenant}/{bot_uuid}/files/upload-url",
                params=api.params(),
                json={
                    "device_path": "/home/bot/uploads/test.txt",
                    "filename": "test.txt",
                    "expire_seconds": 3600,
                },
            )
            assert response.status_code == 200, (
                f"Upload URL request failed: {response.status_code} {response.text}"
            )
            body = response.json()
            assert "data" in body, f"Response missing 'data': {body}"
            data = body["data"]
            assert data["upload_url"], "upload_url is empty"
            assert data["transfer_id"], "transfer_id is empty"
            assert data["expires_at"], "expires_at is empty"

            # ── Step 3: PUT file to stub OSS ───────────────────────────
            test_content = b"hello e2e upload test"
            stub_oss_backend.put_content(data["upload_url"], test_content)

            # Verify the stub storage received the content
            assert stub_oss_backend.check_object_exists(
                f"file-transfers/{data['transfer_id']}/test.txt"
            ), "Stub OSS should have the uploaded file"

            # ── Step 4: Configure mock ticket_repo for poller scan ─────
            ticket = _make_ticket_record(
                transfer_id=data["transfer_id"],
                direction="UPLOAD",
                status="CREATED",
                device_path="/home/bot/uploads/test.txt",
            )
            mock_ticket_repo.list_pending_uploads.return_value = [ticket]

            # ── Step 5: Run poller directly (D-09) ─────────────────────
            await poller.run()

            # ── Step 6: Verify poller processed the ticket ─────────────
            status_transitions = [
                (c.args[0], c.args[1])
                for c in mock_ticket_repo.update_status.call_args_list
            ]
            assert (data["transfer_id"], "UPLOAD_COMPLETED") in status_transitions, (
                f"Expected UPLOAD_COMPLETED transition not found. "
                f"Got: {status_transitions}"
            )
            assert (data["transfer_id"], "DONE") in status_transitions, (
                f"Expected DONE transition not found. "
                f"Got: {status_transitions}"
            )

            # ── Step 7: Verify device pull was triggered ───────────────
            # poller fixture's paas_facade.pull_file is an AsyncMock
            poller._paas_facade.pull_file.assert_called_once()
            call_kwargs = poller._paas_facade.pull_file.call_args.kwargs
            assert "paas_device_id" in call_kwargs
            assert "source_url" in call_kwargs
            assert "device_path" in call_kwargs
            assert call_kwargs["device_path"] == "/home/bot/uploads/test.txt"

        finally:
            # ── Cleanup ────────────────────────────────────────────────
            if bot_created:
                try:
                    await cleanup_bot(api, bot_uuid)
                except Exception:
                    pass  # Ignore cleanup errors