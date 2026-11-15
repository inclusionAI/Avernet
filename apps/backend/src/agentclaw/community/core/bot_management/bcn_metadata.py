"""Resolve display lookups against established BCN references without Owner rewrites."""

from agentclaw.community.core.bot_management.bcn_identity import bcn_bot_reference
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.repository.protocols.bot import BotRepository


def resolve_metadata_pairs(
    repository: BotRepository, pairs: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    pairs = list(dict.fromkeys(pairs))
    candidates = {}
    # HTTP batches are capped at 100; library consumers (e.g. task display)
    # may supply more. Preserve their behavior with bounded database batches.
    for offset in range(0, len(pairs), 100):
        candidates.update(repository.list_metadata_locator_candidates(pairs[offset:offset + 100]))
    resolved = []
    for bot_id, locator in pairs:
        rows = candidates.get((bot_id, locator), [])
        matches = [row for row in rows if row["_metadata_locator_match"]]
        if len(matches) > 1:
            raise BotLookupAmbiguousError(
                f"Bot address is ambiguous: bot_id={bot_id}, owner_id={locator}"
            )
        if matches:
            bot = matches[0]
        else:
            if bot_id == "default":
                continue  # Never resolve another person's default Bot by id alone.
            if len(rows) > 1:
                raise BotLookupAmbiguousError
            # A converted service Bot can retain a reference distinct from both
            # its entity and its current Owner. Only accept the exact stored ref.
            bot = rows[0] if rows else None
            if bot is None or bcn_bot_reference(bot) != f"{bot_id}:{locator}":
                continue
        resolved.append((str(bot["bot_id"]), str(bot["owner_id"])))
    return list(dict.fromkeys(resolved))
