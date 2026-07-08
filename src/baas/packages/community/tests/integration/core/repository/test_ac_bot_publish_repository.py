"""Integration tests for AcBotPublishRepository Protocol.

Tests the get_binding_id protocol method with negative (no match) cases only.
Positive cases are skipped because ac_* tables are externally maintained by AgentClaw.

Uses ONLY Protocol types — no Zdas* concrete class references in test code.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from secbaas.core.repository.ac_bot_publish import AcBotPublishRepository

pytestmark = pytest.mark.integration


def _generate_uuid() -> str:
    return uuid4().hex


# ── Test Class ────────────────────────────────────────────────────────────


class TestAcBotPublishRepositoryProtocol:
    """Integration tests for AcBotPublishRepository Protocol.

    Only tests that don't require ac_* table data run. Positive cases
    are skipped — the ac_bots and ac_bot_publish tables are maintained
    externally by AgentClaw.
    """

    # ── Negative: nonexistent source_bot_id ────────────────────────────

    def test_get_binding_id_nonexistent(
        self,
        ac_bot_publish_repository: AcBotPublishRepository,
    ):
        """Query for a source_bot_id that does not exist — returns None."""
        result = ac_bot_publish_repository.get_binding_id(
            source_bot_id=f"nonexistent_bot_{_generate_uuid()}",
            status="success",
        )
        assert result is None

    # ── Note: Positive tests (test_get_binding_id_found, with_status_filter,
    # with_owner_id, nonexistent_status, default_status) are removed because
    # ac_bots + ac_bot_publish tables are externally maintained by AgentClaw.
    # Only negative-case tests (returns None for nonexistent data) remain.
