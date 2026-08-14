"""Stage-scoped channel → ``engine_overrides`` reader.

The single source of truth for turning a bot's active DingTalk channels into the
engine-neutral ``engine_overrides`` payload (the ``channels`` block embedded in a
:class:`BotConfigArtifact`). It is parameterized by **stage**: the config-compose
collector reads it with the draft filter (``{None, "", "draft"}``) for the live/
draft artifact, and the publish flow reads it per promoted stage (``{"verify"}`` /
``{"online"}``) so each environment ships its own channel config.

Engine-neutral: the representation is snake_case (mirroring the stored channel
``config``); each engine adapts it to whatever its runtime needs. openclaw's own
camelCase ``openclaw.json`` write path is unaffected and stays the source of truth
for openclaw runtimes.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.repository.protocols.chat import ChannelRepository


class ChannelEngineOverridesReader:
    """Reads a bot's active DingTalk channels for a set of stages and maps them to
    the neutral ``engine_overrides`` shape."""

    def __init__(self, channel_repo: ChannelRepository) -> None:
        self._channel_repo = channel_repo

    def overrides_for_stage(
        self,
        *,
        user_id: str,
        bot_id: str,
        accept_stages: set[str | None],
    ) -> dict[str, Any]:
        """Active DingTalk channels whose ``stage`` ∈ ``accept_stages``, mapped to
        ``{"channels": {"dingding": {"enabled": True, "accounts": [...]}}}``.

        Returns ``{}`` when the bot has no matching active channels, so the artifact
        keeps its default (no ``channels`` key).

        ``accept_stages`` is the explicit, per-call stage filter:
        - draft / live artifact → ``{None, "", "draft"}`` (a bot's own runtime config)
        - verify promotion → ``{"verify"}``
        - online promotion → ``{"online"}``

        Identity scoping mirrors the channel read convention (``identity_id`` of the
        acting owner plus the shared ``"aideskdingding"`` default row).
        """
        records = self._channel_repo.get_by_type_and_identity_ids(
            type="dingding",
            identity_ids=[user_id, "aideskdingding"],
            bind_bot_id=bot_id,
        )

        accounts: list[dict[str, Any]] = []
        seen_client_ids: set[str] = set()
        for record in records:
            if record.status != "1" or record.stage not in accept_stages:
                continue
            account = self._map_channel_account(record.config)
            client_id = account.get("client_id")
            if not client_id or client_id in seen_client_ids:
                continue
            seen_client_ids.add(client_id)
            accounts.append(account)

        if not accounts:
            return {}
        return {"channels": {"dingding": {"enabled": True, "accounts": accounts}}}

    @staticmethod
    def _map_channel_account(config: dict[str, Any]) -> dict[str, Any]:
        """Map a stored DingTalk channel ``config`` to the neutral account shape.

        snake_case throughout, matching openclaw's write at
        ``channel_service.py:212-242``: ``robot_code`` is always ``client_id``
        (openclaw hardcodes ``robotCode = client_id`` and ignores any stored
        ``robot_code``), and ``message_type`` defaults to ``card``/``markdown``
        per ``enable_streaming_cards``. Optional/empty fields (``client_secret``,
        the card-template keys) are omitted rather than emitted as ``null`` —
        mirroring openclaw's ``None``-skipping setter.
        """
        client_id = config.get("client_id")
        enable_streaming_cards = config.get("enable_streaming_cards", False)
        account: dict[str, Any] = {
            "client_id": client_id,
            "robot_code": client_id,
            "dm_policy": config.get("dm_policy", "open"),
            "group_policy": "open",
            "message_type": config.get(
                "message_type", "card" if enable_streaming_cards else "markdown"
            ),
            "enable_streaming_cards": enable_streaming_cards,
        }
        if config.get("client_secret"):
            account["client_secret"] = config["client_secret"]
        if config.get("card_template_id"):
            account["card_template_id"] = config["card_template_id"]
        if config.get("card_template_key"):
            account["card_template_key"] = config["card_template_key"]
        return account
