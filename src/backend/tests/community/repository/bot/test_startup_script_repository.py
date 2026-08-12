

class TestTheKeyIsUnambiguous:
    """The uniqueness key must be injective for *any* identifiers, not only for
    ones that honour an invariant nothing enforces."""

    def test_a_control_character_cannot_shift_the_boundary_between_ids(self):
        """`(entity_id="a\x00b", bot_id="c")` and `(entity_id="a", bot_id="b\x00c")`
        are different bots and must not share a row.

        Under a delimiter-joined key they hashed identically: one bot's write
        landed on the other's row, and with the incarnation stamp in place the
        victim's own next write was then refused as stale — locked out of its
        own script rather than clobbered once. `create_bot` takes
        caller-supplied ids and validates neither against control characters,
        so this was reachable, not theoretical.
        """
        from agentclaw.community.core.repository.implementations.bot.startup_script import (
            _script_key,
        )

        assert _script_key(env="e", entity_id="a\x00b", bot_id="c") != _script_key(
            env="e", entity_id="a", bot_id="b\x00c"
        )

    def test_the_separator_character_itself_cannot_shift_the_boundary(self):
        """The length prefix uses ':' — which is itself legal inside an id, so
        the encoding must not depend on that being unavailable either."""
        from agentclaw.community.core.repository.implementations.bot.startup_script import (
            _script_key,
        )

        assert _script_key(env="e", entity_id="1:x", bot_id="y") != _script_key(
            env="e", entity_id="1", bot_id="x:y"
        )

    def test_the_same_identifiers_still_reach_the_same_row(self):
        """Injective, not merely different every time."""
        from agentclaw.community.core.repository.implementations.bot.startup_script import (
            _script_key,
        )

        assert _script_key(env="e", entity_id="ent", bot_id="bot") == _script_key(
            env="e", entity_id="ent", bot_id="bot"
        )
