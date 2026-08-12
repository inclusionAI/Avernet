"""Per-bot startup script service (issue #926).

Owns the rules the repository deliberately does not: the size cap, the
env scoping, and the "absent is not an error" contract that the payload-build
path depends on.

The concrete service does **not** inherit ``BotStartupScriptServiceProtocol``
— that would force a ``core -> api`` import the layering rule forbids.
Conformance is structural, checked by ``test_service_api_conformance.py``.
"""
from __future__ import annotations

from typing import Optional

from injector import inject

from agentclaw.community.core.bot_startup_script.repository.models import (
    BotStartupScriptRecord,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotStartupScriptRepositoryProtocol,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()

#: Maximum stored script size, in UTF-8 bytes.
#:
#: 64 KiB is far above any real provisioning script and keeps the base64 form
#: (~4/3 of this) well inside a single ``execute_command`` payload, which is how
#: the body reaches the container.
MAX_SCRIPT_BYTES = 64 * 1024


class StartupScriptTooLargeError(ValueError):
    """Raised when a submitted script exceeds :data:`MAX_SCRIPT_BYTES`.

    The message names the limit and the actual size — a caller should not have
    to guess by bisecting their script.
    """

    def __init__(self, size_bytes: int) -> None:
        super().__init__(
            f"startup script is {size_bytes} bytes, "
            f"which exceeds the {MAX_SCRIPT_BYTES}-byte limit"
        )
        self.size_bytes = size_bytes
        self.limit_bytes = MAX_SCRIPT_BYTES


class BotStartupScriptService:
    """Read, replace and clear a bot's startup script."""

    @inject
    def __init__(self, repository: BotStartupScriptRepositoryProtocol) -> None:
        self._repository = repository

    def get(
        self, *, entity_id: str, bot_id: str
    ) -> Optional[BotStartupScriptRecord]:
        """Return the stored script, or ``None`` when the bot has none."""
        return self._repository.get(
            env=get_current_env(), entity_id=entity_id, bot_id=bot_id
        )

    def put(
        self, *, entity_id: str, bot_id: str, script: str, modifier: str
    ) -> BotStartupScriptRecord:
        """Store or replace the script.

        Args:
            modifier: The acting user, resolved from the request principal.
                Never supplied by the client.

        Raises:
            StartupScriptTooLargeError: When the body exceeds the size cap.
        """
        size_bytes = len(script.encode("utf-8"))
        if size_bytes > MAX_SCRIPT_BYTES:
            raise StartupScriptTooLargeError(size_bytes)
        return self._repository.upsert(
            env=get_current_env(),
            entity_id=entity_id,
            bot_id=bot_id,
            script=script,
            size_bytes=size_bytes,
            modifier=modifier,
        )

    def delete(self, *, entity_id: str, bot_id: str) -> bool:
        """Clear the script. Idempotent — clearing an absent script succeeds."""
        return self._repository.delete(
            env=get_current_env(), entity_id=entity_id, bot_id=bot_id
        )

    def resolve_support(self, bot: dict) -> tuple[bool, str]:
        """Return ``(supported, reason)`` for a bot; ``reason`` is "" when supported.

        Lives on the service so the HTTP adapter can reach it through the
        Service API Protocol — a router importing the rule directly would cross
        the adapter/core boundary the architecture gate enforces.
        """
        from agentclaw.community.core.bot_startup_script.services._support import (
            resolve_support,
        )

        binding = bot.get("device_binding") or {}
        return resolve_support(bot, binding.get("device_provider"))

    def get_body(self, *, entity_id: str, bot_id: str) -> str:
        """Return the script body, or ``""`` when the bot has none.

        The payload-build path composes a shell string and must never branch on
        ``None``; an empty body is the "no script" signal there, and
        ``_get_start_cmd`` returns its unchanged chain for it.
        """
        record = self.get(entity_id=entity_id, bot_id=bot_id)
        return record.script if record is not None else ""
