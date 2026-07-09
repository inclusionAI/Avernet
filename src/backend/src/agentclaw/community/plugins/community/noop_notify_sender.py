"""``NoopGovernanceNotifySender`` — the community/neutral no-op notify sender.

The community distribution ships no DingTalk integration (that is a corp channel
via ``plugins/prod/dingtalk_sender.py``). Governance notifications therefore
degrade to a no-op: both send methods return ``None`` — the same graceful
outcome the corp empty-credentials path already produces. Satisfies the Rule-21
requirement that every plugin Protocol has a Noop implementation.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.log import get_logger

logger = get_logger()


class NoopGovernanceNotifySender:
    """No-op ``GovernanceNotifySender`` — every send returns ``None``."""

    def send_markdown(self, user_id: str, title: str, content: str) -> str | None:
        logger.debug("[NoopGovernanceNotifySender] send_markdown → no-op")
        return None

    def send_tc_card(
        self,
        user_id: str,
        reason: str,
        detail_link: str,
        bot_id: str,
        card_id: str,
        notification_data: dict[str, Any],
        out_track_id_prefix: str = "dingtalk",
    ) -> str | None:
        logger.debug("[NoopGovernanceNotifySender] send_tc_card → no-op")
        return None
