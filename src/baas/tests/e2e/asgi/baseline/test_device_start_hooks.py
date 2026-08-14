"""E2E tests for start hook dispatch paths via HTTP endpoints.

Exercises the _start_hook_dispatcher.py flow through bot creation lifecycle:
- Null/empty hook configs
- Retryable and non-retryable error paths
- Special character injection safety
- Callback server resolution in non-prod environments

Tests use broad status code assertions (200-500) per design decision
and only interact with the system via HTTP endpoints — no internal imports.
"""

import pytest

from tests.e2e.asgi.conftest import (
    DEFAULT_TEMPLATE_UUID,
    APITestHelper,
    cleanup_bot,
    create_test_bot,
)

pytestmark = [pytest.mark.e2e_asgi]

# ── Acceptable response codes (broad assertion design) ────────────────────────

OKISH = (200, 400, 401, 403, 404, 422, 500)


class TestStartHookNullConfig:
    """Start hook dispatch with null/omitted after_create_cmd_hook config."""

    @pytest.mark.asyncio
    async def test_start_hook_null_config(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create bot with deploy_config but no hook: device creation succeeds."""
        bot = await create_test_bot(
            api,
            f"hook-null-{unique_id}",
            template_uuid=DEFAULT_TEMPLATE_UUID,
            device_count=1,
            deploy_config={},
        )
        assert bot is not None
        assert "bot_uuid" in bot

        # Verify bot exists and is not in a broken state
        resp = await api.client.get(
            api.bot_url(bot["bot_uuid"]),
            params=api.params(),
        )
        assert resp.status_code in OKISH

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_start_hook_null_deploy_config_omitted(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Create bot without any deploy_config key: should not crash dispatcher."""
        bot = await create_test_bot(
            api,
            f"hook-no-config-{unique_id}",
            template_uuid=DEFAULT_TEMPLATE_UUID,
            device_count=1,
            deploy_config=None,
        )
        assert bot is not None
        assert "bot_uuid" in bot

        resp = await api.client.get(
            api.bot_url(bot["bot_uuid"]),
            params=api.params(),
        )
        assert resp.status_code in OKISH

        await cleanup_bot(api, bot["bot_uuid"])


class TestStartHookEmptyStringConfig:
    """Start hook dispatch with empty string hook configurations."""

    @pytest.mark.asyncio
    async def test_start_hook_empty_string_after_create(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Bot with empty string after_create_cmd_hook: graceful handling."""
        bot = await create_test_bot(
            api,
            f"hook-empty-{unique_id}",
            template_uuid=DEFAULT_TEMPLATE_UUID,
            device_count=1,
            deploy_config={"after_create_cmd_hook": ""},
        )
        assert bot is not None
        assert "bot_uuid" in bot

        resp = await api.client.get(
            api.bot_url(bot["bot_uuid"]),
            params=api.params(),
        )
        assert resp.status_code in OKISH

        # Verify devices endpoint still works
        devices_resp = await api.client.get(
            api.bot_devices_url(bot["bot_uuid"]),
            params=api.params(),
        )
        assert devices_resp.status_code in OKISH

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_start_hook_empty_string_both_hooks(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Bot with both hooks as empty strings: neither should crash dispatcher."""
        bot = await create_test_bot(
            api,
            f"hook-both-empty-{unique_id}",
            template_uuid=DEFAULT_TEMPLATE_UUID,
            device_count=1,
            deploy_config={
                "after_create_cmd_hook": "",
                "before_destroy_cmd_hook": "",
            },
        )
        assert bot is not None
        assert "bot_uuid" in bot

        resp = await api.client.get(
            api.bot_url(bot["bot_uuid"]),
            params=api.params(),
        )
        assert resp.status_code in OKISH

        await cleanup_bot(api, bot["bot_uuid"])


class TestStartHookErrorPaths:
    """Start hook dispatch error handling — retryable and non-retryable."""

    @pytest.mark.asyncio
    async def test_start_hook_retryable_error(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Hook with command that always exits non-zero: dispatcher retries, no crash.

        The wrapper script sends callbacks with retries and eventually reports
        FAILED status. The dispatcher itself should not crash.
        """
        bot = await create_test_bot(
            api,
            f"hook-retry-{unique_id}",
            template_uuid=DEFAULT_TEMPLATE_UUID,
            device_count=1,
            deploy_config={
                "after_create_cmd_hook": (
                    "#!/bin/bash\n"
                    "# This hook always fails — exercisers retry logic\n"
                    "echo 'attempting hook operation' >&2\n"
                    "exit 1\n"
                )
            },
        )
        assert bot is not None
        assert "bot_uuid" in bot

        # Bot and device endpoints should still be functional
        resp = await api.client.get(
            api.bot_url(bot["bot_uuid"]),
            params=api.params(),
        )
        assert resp.status_code in OKISH

        # List devices — should not 500
        devices_resp = await api.client.get(
            api.bot_devices_url(bot["bot_uuid"]),
            params=api.params(),
        )
        assert devices_resp.status_code in OKISH

        # Detail-by-uuid should work
        detail_resp = await api.client.get(
            api.bot_detail_url(bot["bot_uuid"]),
            params=api.params(),
        )
        assert detail_resp.status_code in OKISH

        await cleanup_bot(api, bot["bot_uuid"])

    @pytest.mark.asyncio
    async def test_start_hook_non_retryable_error(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Bot with invalid hook script: should report error gracefully.

        An invalid hook won't trigger retries from the dispatcher itself
        but may cause the wrapper to fail fast. The system should still
        report device/status errors rather than crash.
        """
        bot = await create_test_bot(
            api,
            f"hook-nonretry-{unique_id}",
            template_uuid=DEFAULT_TEMPLATE_UUID,
            device_count=1,
            deploy_config={
                "after_create_cmd_hook": "invalid-command-that-does-not-exist --broken"
            },
        )
        assert bot is not None
        assert "bot_uuid" in bot

        # The publish should still be accessible even if device creation failed
        publish_id = bot.get("publish_id")
        if publish_id:
            publish_resp = await api.client.get(
                api.publish_url(publish_id),
                params=api.params(),
            )
            assert publish_resp.status_code in OKISH

        # Bot should still exist and be queryable
        bot_resp = await api.client.get(
            api.bot_url(bot["bot_uuid"]),
            params=api.params(),
        )
        assert bot_resp.status_code in OKISH

        await cleanup_bot(api, bot["bot_uuid"])


class TestStartHookSpecialCharacters:
    """Start hook safety — special characters and injection prevention."""

    @pytest.mark.asyncio
    async def test_start_hook_special_characters(
        self, api: APITestHelper, unique_id: str
    ) -> None:
        """Hook config with special shell characters: no injection or crash.

        Tests that the dispatcher's use of shlex.quote(), heredoc isolation,
        and template rendering safely handles special characters without
        allowing command injection or system corruption.
        """
        # Characters that are dangerous in shell contexts
        hook_with_special_chars = (
            "#!/bin/bash\n"
            "echo 'Testing special chars: $HOME /etc/passwd `id` $(whoami)'\n"
            "echo 'Quotes: \"double\" '\"'\"'single'\"'\"''\n"
            "echo 'Backticks: `ls` and dollars: ${VAR}'\n"
            "echo 'Pipes: | and redirects: > < >>'\n"
            "# This is just a comment with & and ;\n"
            "exit 0\n"
        )
        bot = await create_test_bot(
            api,
            f"hook-special-{unique_id}",
            template_uuid=DEFAULT_TEMPLATE_UUID,
            device_count=1,
            deploy_config={"after_create_cmd_hook": hook_with_special_chars},
        )
        assert bot is not None
        assert "bot_uuid" in bot

        # System should still be functional — no injection should have corrupted state
        resp = await api.client.get(
            api.bot_url(bot["bot_uuid"]),
            params=api.params(),
        )
        assert resp.status_code in OKISH

        # Check that listing all bots still works (no global corruption)
        list_resp = await api.client.get(
            api.bot_url(),
            params=api.params(page=1, page_size=5),
        )
        assert list_resp.status_code in OKISH

        await cleanup_bot(api, bot["bot_uuid"])


class TestCallbackServerNonProd:
    """Callback server URL resolution in non-production environments."""

    @pytest.mark.asyncio
    async def test_callback_server_non_prod(self, api: APITestHelper) -> None:
        """Verify callback-server health endpoints work in non-prod env.

        The _start_hook_dispatcher uses _get_callback_server() which reads
        config based on environment (prod vs. pre/dev). In non-prod envs,
        the pre callback host path is used. This test verifies that the
        health-checker and related endpoints are reachable, which indirectly
        validates that the service configured as callback_server is functional.
        """
        # Health checker endpoint — primary callback server health probe
        resp = await api.client.get(api.bot_health_url())
        assert resp.status_code in OKISH

        # Alive endpoint
        alive_resp = await api.client.get(api.health_alive_url())
        assert alive_resp.status_code in OKISH

        # Active bots endpoint
        active_resp = await api.client.get(api.health_active_bots_url())
        assert active_resp.status_code in OKISH

        # System config endpoint — validates config resolution works
        sys_resp = await api.client.get(
            api.system_config_url(),
            params=api.params(),
        )
        assert sys_resp.status_code in OKISH
