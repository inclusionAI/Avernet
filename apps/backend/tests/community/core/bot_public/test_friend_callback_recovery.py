"""Tests for friend approval callback puid recovery."""
from agentclaw.community.core.bot_public.services.friend_callback_recovery import (
    recover_bot_friend_id,
)


class TestRecoverBotFriendId:
    def test_recovers_id_from_wellformed_friend_puid(self):
        # biz_id = f"{bot_friend_id}{%Y%m%d%H%M%S}" per _create_friend_request_approval
        puid = "antprocess-agentclaw_botpublicfriends_12320261127104210"
        assert recover_bot_friend_id(puid) == 123

    def test_rejects_publish_puid_prefix(self):
        # Publication/risk approvals use other biz types and must not resolve here.
        assert recover_bot_friend_id("antprocess-agentclaw_botpublic_12320261127104210") is None
        assert recover_bot_friend_id("antprocess-agentclaw_botpublic_risk-3f2b1a09-1111-2222-3333-444455556666") is None

    def test_rejects_bare_timestamp_without_id_prefix(self):
        assert recover_bot_friend_id("antprocess-agentclaw_botpublicfriends_20261127104210") is None

    def test_rejects_invalid_timestamp_suffix(self):
        assert recover_bot_friend_id("antprocess-agentclaw_botpublicfriends_1232099913999910") is None

    def test_rejects_zero_id_prefix(self):
        assert recover_bot_friend_id("antprocess-agentclaw_botpublicfriends_020261127104210") is None

    def test_rejects_empty_or_malformed_puid(self):
        assert recover_bot_friend_id("") is None
        assert recover_bot_friend_id(None) is None
        assert recover_bot_friend_id("AntProcess-agentclaw_botpublicfriends_12320261127104210") is None
        assert recover_bot_friend_id("antprocess-agentclaw_botpublicfriends_") is None
