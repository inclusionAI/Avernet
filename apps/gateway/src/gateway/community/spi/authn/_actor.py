"""Expected caller Bot selector, independent of the credential type."""

from collections.abc import Mapping

from gateway.community.spi.auth import AuthError

ACTOR_BOT_ID_HEADER = "x-bcn-actor-bot-id"


def expected_actor_id(headers: Mapping[str, str]) -> str | None:
    value = headers.get(ACTOR_BOT_ID_HEADER)
    if value is None:
        return None
    if (
        not value
        or len(value) > 256
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise AuthError("invalid Actor header")
    return value
