"""The shared owner/lock checks behind every CLI write."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.caller_identity.contracts import (
    CallerIdentityAmbiguousError,
    CallerIdentityNotFoundError,
    CallerIdentityPermissionError,
    CallerIdentityReadOnlyError,
)
from agentclaw.community.core.caller_identity.guard import (
    load_bot,
    lock_epoch_matches,
    require_owner_of_active_service_bot,
)


class _Bots:
    def __init__(self, bot=None, error: Exception | None = None) -> None:
        self.bot = bot
        self.error = error
        self.calls: list[str] = []

    def get_by_id_and_entity(self, bot_id, entity_id):
        self.calls.append("entity")
        return self.bot

    def get_unique_by_id(self, bot_id):
        self.calls.append("unique")
        if self.error:
            raise self.error
        return self.bot


def test_entity_scoped_lookup_is_used_when_given() -> None:
    bots = _Bots({"bot_id": "b"})
    assert load_bot(bots, bot_id="b", entity_id="e") == {"bot_id": "b"}  # type: ignore[arg-type]
    assert bots.calls == ["entity"]


def test_unscoped_lookup_fails_closed_on_duplicates() -> None:
    bots = _Bots(error=BotLookupAmbiguousError("dup"))
    with pytest.raises(CallerIdentityAmbiguousError):
        load_bot(bots, bot_id="b", entity_id=None)  # type: ignore[arg-type]


def test_missing_bot_is_not_found() -> None:
    with pytest.raises(CallerIdentityNotFoundError):
        load_bot(_Bots(None), bot_id="b", entity_id=None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("lock", "epoch", "ok"),
    [
        (None, None, True),
        (None, 3, False),
        (SimpleNamespace(id=3, holder_user_id="u"), None, False),
        (SimpleNamespace(id=3, holder_user_id="u"), 3, True),
        (SimpleNamespace(id=3, holder_user_id="other"), 3, False),
        (SimpleNamespace(id=4, holder_user_id="u"), 3, False),
    ],
)
def test_lock_epoch_must_name_the_lock_the_actor_holds(lock, epoch, ok) -> None:
    keys: list[str] = []
    locks = SimpleNamespace(get_by_key=lambda key: keys.append(key) or lock)
    assert lock_epoch_matches(  # type: ignore[arg-type]
        locks, bot_id="b", owner_id="o", actor_id="u", lock_epoch=epoch
    ) is ok
    assert keys == ["b:o"]


@pytest.mark.parametrize(
    ("bot", "actor", "error"),
    [
        ({"owner_id": "o", "bot_type": "service", "status": "STOPPED"}, "x", CallerIdentityReadOnlyError),
        ({"owner_id": "o", "bot_type": "personal", "status": "ACTIVE"}, "o", CallerIdentityReadOnlyError),
        ({"owner_id": "o", "bot_type": "service", "status": "ACTIVE"}, "x", CallerIdentityPermissionError),
        ({"owner_id": "o", "bot_type": "service", "status": "ACTIVE"}, "o", None),
    ],
)
def test_the_bot_is_checked_before_the_actor(bot, actor, error) -> None:
    """A read-only bot is read-only for everyone; only then must the actor be its owner."""
    if error is None:
        require_owner_of_active_service_bot(bot, actor)
    else:
        with pytest.raises(error):
            require_owner_of_active_service_bot(bot, actor)
