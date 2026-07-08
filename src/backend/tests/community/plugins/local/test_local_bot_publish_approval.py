"""LocalBotPublishApproval delegates straight to publish_directly."""
from unittest.mock import MagicMock

from agentclaw.community.plugin_api.bot_publish_approval import BotPublishCallbacks
from agentclaw.community.plugins.local.bot_publish_approval import LocalBotPublishApproval


def _make_callbacks(publish_directly):
    return BotPublishCallbacks(
        publish_directly=publish_directly,
        archive_approval=MagicMock(),
        update_with_notification=MagicMock(),
        handle_approval_callback=MagicMock(),
        refetch_bot=MagicMock(),
        build_approval_context=MagicMock(),
    )


def test_publish_invokes_publish_directly_with_full_kwargs():
    captured = {}

    def publish_directly(**kwargs):
        captured.update(kwargs)
        return {"id": 42, "public": "1"}

    plugin = LocalBotPublishApproval()
    result = plugin.publish(
        bot={"id": 42}, ext={"foo": "bar"},
        bot_id="b1", owner_id="o1", operator_id="op1",
        operator=None,
        public="1", permission_owner="owner",
        friend_approval="0", access_mode="RESTRICTED",
        callbacks=_make_callbacks(publish_directly),
    )

    assert result == {"id": 42, "public": "1"}
    assert captured["bot_id"] == "b1"
    assert captured["owner_id"] == "o1"
    assert captured["public"] == "1"
    assert captured["permission_owner"] == "owner"
    assert captured["friend_approval"] == "0"
    assert captured["access_mode"] == "RESTRICTED"
