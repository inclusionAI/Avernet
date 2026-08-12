"""Unit tests for BotStartupScriptService — the rules the repository doesn't own."""
import pytest

from agentclaw.community.core.bot_startup_script.services.startup_script_service import (
    MAX_SCRIPT_BYTES,
    BotStartupScriptService,
    StartupScriptTooLargeError,
)


class FakeRepo:
    """In-memory stand-in keyed exactly like the real one."""

    def __init__(self):
        self.rows = {}

    def get(self, *, env, entity_id, bot_id):
        return self.rows.get((env, entity_id, bot_id))

    def upsert(self, *, env, entity_id, bot_id, script, size_bytes, modifier):
        from agentclaw.community.core.bot_startup_script.repository.models import (
            BotStartupScriptRecord,
        )

        rec = BotStartupScriptRecord(
            id=1, env=env, entity_id=entity_id, bot_id=bot_id,
            script=script, size_bytes=size_bytes, modifier=modifier,
        )
        self.rows[(env, entity_id, bot_id)] = rec
        return rec

    def delete(self, *, env, entity_id, bot_id):
        return self.rows.pop((env, entity_id, bot_id), None) is not None


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
    assert svc.get(entity_id="ent", bot_id="bot") is None


def test_put_computes_size_in_utf8_bytes_not_characters(svc):
    """A multibyte body must be measured in bytes — the cap is a byte budget."""
    body = "echo 你好"  # 8 chars, 11 bytes
    rec = svc.put(entity_id="ent", bot_id="bot", script=body, modifier="u1")
    assert rec.size_bytes == len(body.encode("utf-8"))
    assert rec.size_bytes != len(body)


def test_put_records_the_modifier_it_is_given(svc):
    rec = svc.put(entity_id="ent", bot_id="bot", script="echo a", modifier="alice")
    assert rec.modifier == "alice"


def test_put_rejects_over_limit_naming_the_limit(svc):
    oversize = "x" * (MAX_SCRIPT_BYTES + 1)
    with pytest.raises(StartupScriptTooLargeError) as exc:
        svc.put(entity_id="ent", bot_id="bot", script=oversize, modifier="u1")
    assert str(MAX_SCRIPT_BYTES) in str(exc.value)
    assert exc.value.limit_bytes == MAX_SCRIPT_BYTES
    assert exc.value.size_bytes == MAX_SCRIPT_BYTES + 1


def test_put_accepts_a_body_exactly_at_the_limit(svc):
    """The cap is inclusive — off-by-one here would reject a legal script."""
    at_limit = "x" * MAX_SCRIPT_BYTES
    rec = svc.put(entity_id="ent", bot_id="bot", script=at_limit, modifier="u1")
    assert rec.size_bytes == MAX_SCRIPT_BYTES


def test_put_rejects_before_storing_anything(svc):
    oversize = "x" * (MAX_SCRIPT_BYTES + 1)
    with pytest.raises(StartupScriptTooLargeError):
        svc.put(entity_id="ent", bot_id="bot", script=oversize, modifier="u1")
    assert svc.get(entity_id="ent", bot_id="bot") is None


def test_delete_is_idempotent(svc):
    assert svc.delete(entity_id="ent", bot_id="bot") is False
    svc.put(entity_id="ent", bot_id="bot", script="echo a", modifier="u1")
    assert svc.delete(entity_id="ent", bot_id="bot") is True
    assert svc.delete(entity_id="ent", bot_id="bot") is False


def test_get_body_returns_empty_string_when_unset(svc):
    """The payload path composes a shell string and must never see None."""
    assert svc.get_body(entity_id="ent", bot_id="bot") == ""


def test_get_body_returns_the_stored_body(svc):
    svc.put(entity_id="ent", bot_id="bot", script="echo hello", modifier="u1")
    assert svc.get_body(entity_id="ent", bot_id="bot") == "echo hello"


def test_get_body_returns_empty_string_after_delete(svc):
    svc.put(entity_id="ent", bot_id="bot", script="echo hello", modifier="u1")
    svc.delete(entity_id="ent", bot_id="bot")
    assert svc.get_body(entity_id="ent", bot_id="bot") == ""


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
