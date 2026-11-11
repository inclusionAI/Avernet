"""Unit tests for the identity-source side of BotService.

Covers the two directions that protect an identity Bot's passport:

* deleting an inheriting Bot never touches the source's passport
* deleting a source that still has live children is refused

Assertions name exact values so a self-issued passport -- which derives an
agent code from any id -- cannot make a weak check pass.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotOperationNotAllowedError,
    BotService,
)


def _service() -> BotService:
    svc = BotService.__new__(BotService)
    svc._repository = MagicMock()
    svc._passport_plugin = MagicMock()
    svc._repository.count_identity_bot_children.return_value = 0
    svc._repository.list_by_owner.return_value = (1, [{"bot_id": "other"}])
    svc._repository.get_by_id_and_owner.return_value = {
        "bot_id": "src", "identity_bot_id": None, "owner_id": "owner-1", "ext": {},
    }
    return svc


# ── find_identity_source ─────────────────────────────────────


class TestFindIdentitySource:
    def test_returns_the_row_the_repository_found(self):
        svc = _service()
        row = {"bot_id": "src", "entity_id": "owner-1"}
        svc._repository.get_by_id_and_owner.return_value = row
        assert svc.get_identity_source_row("src", entity_id="owner-1") is row
        svc._repository.get_by_id_and_owner.assert_called_once_with("src", "owner-1")

    def test_a_missing_or_foreign_bot_is_reported_as_none(self):
        svc = _service()
        svc._repository.get_by_id_and_owner.return_value = None
        assert svc.get_identity_source_row("src", entity_id="owner-1") is None

    @pytest.mark.asyncio
    async def test_the_async_entry_point_runs_the_same_lookup(self):
        svc = _service()
        row = {"bot_id": "src", "entity_id": "owner-1"}
        svc._repository.get_by_id_and_owner.return_value = row
        assert await svc.find_identity_source("src", entity_id="owner-1") is row


# ── validate_delete_bot: refusing a source with children ─────


class TestDeleteGuard:
    def test_a_source_with_live_children_cannot_be_deleted(self):
        svc = _service()
        svc._repository.count_identity_bot_children.return_value = 3
        with pytest.raises(BotOperationNotAllowedError) as exc:
            svc.validate_delete_bot("src", user_id="owner-1")
        assert str(exc.value) == (
            "该 Bot 仍是 3 个继承 Bot 的身份来源，请先删除这些 Bot"
        )

    def test_a_source_with_no_children_is_allowed_through(self):
        svc = _service()
        svc._repository.count_identity_bot_children.return_value = 0
        svc.validate_delete_bot("src", user_id="owner-1")
        svc._repository.count_identity_bot_children.assert_called_once_with(
            "src", owner_id="owner-1"
        )

    def test_an_unanswerable_count_does_not_block_deletion(self):
        """A repository that cannot answer must not make every Bot undeletable."""
        svc = _service()
        svc._repository.count_identity_bot_children.return_value = None
        svc.validate_delete_bot("src", user_id="owner-1")


# ── delete_bot: the passport skip ────────────────────────────


class TestDeleteSkipsPassportForInheritingBot:
    def _run(self, bot: dict):
        svc = BotService.__new__(BotService)
        svc._repository = MagicMock()
        svc._passport_plugin = MagicMock()
        svc._repository.get_by_id_and_owner.return_value = bot
        svc._repository.count_identity_bot_children.return_value = 0
        svc._repository.list_by_owner.return_value = (1, [{"bot_id": "other"}])
        svc._repository.soft_delete_by_owner.return_value = True
        svc._bot_app_grant_provider = MagicMock()
        svc._cleanup_bot_associated_data = MagicMock()
        svc._release_device_for_bot = MagicMock()
        svc.delete_bot("child", "owner-1")
        return svc

    def test_an_inheriting_bot_never_destroys_a_passport(self):
        svc = self._run(
            {"bot_id": "child", "identity_bot_id": "src", "owner_id": "owner-1"}
        )
        svc._passport_plugin.destroy_passport.assert_not_called()

    def test_deleting_a_child_never_touches_the_sources_passport(self):
        """The child carries a copy; destroying it would kill the source's
        identity and every sibling that runs under it."""
        svc = self._run(
            {"bot_id": "child", "identity_bot_id": "src", "owner_id": "owner-1"}
        )
        svc._passport_plugin.destroy_passport.assert_not_called()
        # Not merely "called with a different id" -- never called at all.
        assert svc._passport_plugin.destroy_passport.call_args_list == []

    def test_an_ordinary_bot_still_destroys_its_own_passport(self):
        svc = self._run(
            {"bot_id": "child", "identity_bot_id": None, "owner_id": "owner-1"}
        )
        svc._passport_plugin.destroy_passport.assert_called_once()