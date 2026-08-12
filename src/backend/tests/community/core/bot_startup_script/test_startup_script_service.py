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


@pytest.fixture
def svc():
    return BotStartupScriptService(FakeRepo())


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
    """Support keys on the container provider, never on the bot type."""

    def _bot(self, engine="openclaw", provider="baas"):
        return {
            "active_engine": engine,
            "bot_type": "personal",
            "device_binding": {"device_provider": provider},
        }

    def test_baas_backed_bot_is_supported(self, svc):
        assert svc.resolve_support(self._bot()) == (True, "")

    def test_personal_and_service_bots_get_the_same_answer(self, svc):
        """They share one allocator and one payload builder."""
        personal = {**self._bot(), "bot_type": "personal"}
        service = {**self._bot(), "bot_type": "service"}
        assert svc.resolve_support(personal) == svc.resolve_support(service)

    def test_teclaw_bot_is_unsupported(self, svc):
        supported, reason = svc.resolve_support(self._bot(engine="teclaw"))
        assert supported is False
        assert "teclaw" in reason

    def test_teclaw_is_unsupported_even_on_the_baas_provider(self, svc):
        """Engine is checked first: teclaw never gets a deploy_config at all."""
        supported, reason = svc.resolve_support(
            self._bot(engine="TeClaw", provider="baas")
        )
        assert supported is False
        assert "teclaw" in reason

    def test_legacy_arca_provider_is_unsupported(self, svc):
        supported, reason = svc.resolve_support(self._bot(provider="arca"))
        assert supported is False
        assert "arca" in reason

    def test_a_bot_with_no_binding_yet_is_supported(self, svc):
        """A bot is PENDING between create and first start — the moment an owner
        most wants to attach a script. Refusing there blocks the main use case,
        and every non-teclaw bot created today is baas-backed anyway."""
        supported, reason = svc.resolve_support({"active_engine": "openclaw"})
        assert supported is True
        assert reason == ""

    def test_teclaw_with_no_binding_is_still_unsupported(self, svc):
        """Engine is checked before the provider, so the gap does not swallow it."""
        supported, reason = svc.resolve_support({"active_engine": "teclaw"})
        assert supported is False
        assert "teclaw" in reason

    def test_the_local_provider_is_supported(self, svc):
        """LocalDeviceService allocates through _build_create_bot_payload
        (local_device_service.py:252), so it delivers the script like baas."""
        assert svc.resolve_support(self._bot(provider="local")) == (True, "")
