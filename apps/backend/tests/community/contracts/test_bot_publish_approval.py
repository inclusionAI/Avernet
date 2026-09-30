"""Rule 25 conformance — BotPublishApprovalPlugin.

Consumer under test: ``BotPublicService`` calls
``bot_publish_approval.publish(..., callbacks=...)`` during the
publish flow. Seeding a full bot-publish requires bot + binding
fixtures; the contract is verified through the DI-bound plugin by
providing a stub ``BotPublishCallbacks`` that records whether
``publish_directly`` was invoked — the local strategy's contract.

Plugin-hit assertion: the local impl ``LocalBotPublishApproval``
*must* call ``callbacks.publish_directly(...)`` rather than start
antprocess (which is unreachable locally). The stub records the
invocation; observing it proves the plugin executed its branch.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.plugin_api.bot_publish_approval import (
    BotPublishApprovalPlugin,
    BotPublishCallbacks,
)


def test_local_strategy_publishes_directly(world) -> None:
    calls: dict[str, int] = {"publish_directly": 0, "archive_approval": 0}

    def publish_directly(**kwargs: Any) -> dict[str, Any]:
        calls["publish_directly"] += 1
        return {"success": True, "data": {"bot_id": kwargs.get("bot_id")}}

    def archive_approval(*args: Any, **kwargs: Any) -> None:
        calls["archive_approval"] += 1

    def update_with_notification(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}

    def _stub(*args: Any, **kwargs: Any) -> Any:
        return None

    callbacks = BotPublishCallbacks(
        publish_directly=publish_directly,
        archive_approval=archive_approval,
        update_with_notification=update_with_notification,
        handle_approval_callback=_stub,
        refetch_bot=_stub,
        build_approval_context=_stub,
    )

    plugin = world.get(BotPublishApprovalPlugin)
    result = plugin.publish(
        bot={"id": "bot_x"},
        ext={},
        bot_id="bot_x",
        owner_id="alice",
        operator_id="alice",
        operator=None,
        public="0",
        permission_owner="caller",
        friend_approval="0",
        access_mode="open",
        callbacks=callbacks,
    )
    # Plugin-hit assertion: local strategy must skip antprocess and
    # delegate to publish_directly.
    assert calls["publish_directly"] == 1
    assert result["success"] is True
