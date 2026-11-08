from __future__ import annotations
























class _Bots:
    def get_unique_by_id(self, bot_id: str) -> dict:
        assert bot_id == "bot-1"
        return {
            "id": 1,
            "bot_id": bot_id,
            "owner_id": "true-owner",
            "env": "dev",
            "entity_id": "true-owner",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "entity_type": "staff",
            "status": "ACTIVE",
        }

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
        bot = self.get_unique_by_id(bot_id)
        return bot if owner_id == bot["owner_id"] else None

    def list_matches_by_legacy_locator(self, bot_id: str, locator: str) -> list[dict]:
        bot = self.get_by_id_and_owner(bot_id, locator)
        return [bot] if bot is not None else []


class _MissingBots:
    def get_unique_by_id(self, _bot_id: str):
        return None

    def get_by_id_and_owner(self, _bot_id: str, _owner_id: str):
        return None

    def list_matches_by_legacy_locator(self, _bot_id: str, _locator: str) -> list[dict]:
        return []


class _SharedDefaultBots:
    """A real shared ``default`` Bot namespace with no global lookup."""

    def __init__(self, owner_id: str) -> None:
        self.owner_id = owner_id
        self.lookups: list[tuple[str, str]] = []

    def get_unique_by_id(self, _bot_id: str) -> dict:
        raise AssertionError("legacy default must never use a global bot lookup")

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict | None:
        self.lookups.append((bot_id, owner_id))
        if (bot_id, owner_id) != ("default", self.owner_id):
            return None
        return {
            "id": 2,
            "bot_id": bot_id,
            "owner_id": owner_id,
            "env": "pre",
            "entity_id": owner_id,
            "active_engine": "openclaw",
            "bot_type": "personal",
            "entity_type": "staff",
            "status": "ACTIVE",
        }

    def list_matches_by_legacy_locator(self, bot_id: str, locator: str) -> list[dict]:
        bot = self.get_by_id_and_owner(bot_id, locator)
        return [bot] if bot is not None else []


class _UnsupportedBots(_Bots):
    def get_unique_by_id(self, bot_id: str) -> dict:
        bot = super().get_unique_by_id(bot_id)
        return {**bot, "bot_type": "desktop", "active_engine": "claude_code"}


class _AicodingImageBots(_Bots):
    def get_unique_by_id(self, bot_id: str) -> dict:
        return {
            **super().get_unique_by_id(bot_id),
            "active_engine": "claude_code",
            "template_type": "personalCoding",
        }


class _PlainClaudeCodeBots(_Bots):
    def get_unique_by_id(self, bot_id: str) -> dict:
        return {
            **super().get_unique_by_id(bot_id),
            "active_engine": "claude_code",
            "template_type": "normalCC",
        }


class _LiteralAicodingBots(_Bots):
    def get_unique_by_id(self, bot_id: str) -> dict:
        return {**super().get_unique_by_id(bot_id), "active_engine": "aicoding"}


class _NotReadyApplicationCodingBots(_Bots):
    def get_unique_by_id(self, bot_id: str) -> dict:
        return {
            **super().get_unique_by_id(bot_id),
            "status": "PENDING",
            "active_engine": "claude_code",
            "template_type": "applicationCoding",
        }


class _RuntimeBots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        assert (bot_id, owner_id) == ("bot-1", "true-owner")
        return {
            # Every persisted Bot has a primary key; identity lookups key on it.
            "id": 42,
            "bot_id": bot_id,
            "owner_id": owner_id,
            "entity_id": "entity-1",
            "active_engine": "openclaw",
            "bot_type": "personal",
            "entity_type": "staff",
            "env": "pre",
        }

    def list_matches_by_legacy_locator(self, bot_id: str, locator: str) -> list[dict]:
        return [self.get_by_id_and_owner(bot_id, locator)]


class _AicodingImageRuntimeBots(_RuntimeBots):
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        return {
            **super().get_by_id_and_owner(bot_id, owner_id),
            "active_engine": "claude_code",
            "template_type": "personalCoding",
        }


class _PlainClaudeCodeRuntimeBots(_RuntimeBots):
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        return {
            **super().get_by_id_and_owner(bot_id, owner_id),
            "active_engine": "claude_code",
            "template_type": "normalCC",
        }


class _HistoricalAicodingRuntimeBots(_RuntimeBots):
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        return {
            **super().get_by_id_and_owner(bot_id, owner_id),
            "active_engine": "aicoding",
        }


class _TeclawRuntimeBots(_RuntimeBots):
    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        return {
            **super().get_by_id_and_owner(bot_id, owner_id),
            "bot_type": "personal",
            "active_engine": "teclaw",
        }


class _PkLessRuntimeBots(_RuntimeBots):
    """A Bot record without a primary key — not what the projector assumes."""

    def get_by_id_and_owner(self, bot_id: str, owner_id: str) -> dict:
        record = dict(super().get_by_id_and_owner(bot_id, owner_id))
        record.pop("id")
        return record

