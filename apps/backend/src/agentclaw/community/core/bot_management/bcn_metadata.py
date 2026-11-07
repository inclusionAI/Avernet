"""Resolve display lookups against established BCN references without Owner rewrites."""

from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.bcn_identity import bcn_bot_reference
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.core.repository.protocols.bot import BotRepository


def resolve_metadata_pairs(
    repository: BotRepository, pairs: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    resolver = BotAddressResolver(repository)
    resolved = []
    for bot_id, locator in dict.fromkeys(pairs):
        try:
            bot = resolver.resolve(bot_id=bot_id, legacy_owner_id=locator).bot
        except BotAddressNotFoundError:
            if bot_id == "default":
                continue  # Never resolve another person's default Bot by id alone.
            # A converted service Bot can retain a reference distinct from both
            # its entity and its current Owner. Only accept the exact stored ref.
            bot = repository.get_unique_by_id(bot_id)
            if bot is None or bcn_bot_reference(bot) != f"{bot_id}:{locator}":
                continue
        resolved.append((str(bot["bot_id"]), str(bot["owner_id"])))
    return list(dict.fromkeys(resolved))
