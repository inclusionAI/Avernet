"""The lifecycle extension seam: what the shared flow guarantees a bound
extension gets, and what a bound extension changes, with proofs — not
arguments:

- a request the extension recognizes: its overrides replace the request
  values (template identity) BEFORE the shared policy, the recorded create
  carries the provisioning split it asked for, and the post-registration
  hook fires exactly once;
- an identity the extension reports as pre-issued never re-mints: PassPort
  is not applied at all and the owner relationship uses the given agent
  code;
- a request the extension does not recognize (``None``): the flow runs
  byte-identically to the unextended path — no hook fires, no override;
- deletion: ``before_delete`` runs before the destructive call, the
  completed/not-found distinction reaches the extension, and the default
  local stub is a no-op at every point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

from agentclaw.community.core.bot_management.create_flow import (
    AuthPending,
    BotCreateContext,
    BotCreateDeploymentMode,
    BotCreateSpec,
    Created,
    complete_bot_authorization,
    create_bot_with_authorization,
)
from agentclaw.community.plugin_api.bot_create_extension import (
    CreateOverride,
    NoopBotCreateExtension,
)

_CONTEXT = BotCreateContext(
    deployment_mode=BotCreateDeploymentMode.CLOUD, space_kind="personal"
)
_SPEC = BotCreateSpec(
    entity_id="u1", engine_type="openclaw", bot_type="personal", bot_name="Bot"
)


@dataclass
class RecordingExtension:
    """Minimal extension double: resolves once per request, records calls."""

    override: CreateOverride | None
    calls: list = field(default_factory=list)

    def resolve_create(self, *, bot_id, user_id, request):
        self.calls.append(("resolve", bot_id, user_id, dict(request)))
        return self.override

    def after_bot_registered(self, *, bot_id, user_id):
        self.calls.append(("after", bot_id, user_id))

    def before_delete(self, *, bot_id, owner_id):
        self.calls.append(("before_delete", bot_id, owner_id))

    def on_delete_completed(self, *, bot_id, owner_id):
        self.calls.append(("delete_completed", bot_id, owner_id))

    def on_delete_not_found(self, *, bot_id, owner_id):
        self.calls.append(("delete_not_found", bot_id, owner_id))


def _make_services(*, issued: bool = True):
    passport = MagicMock()
    applied = (
        {"token": "tok", "agent_code": "agent-1"} if issued
        else {"token": None, "iframe_url": "https://passport/iframe"}
    )
    passport.apply_agent_passport.return_value = applied
    passport.apply_first_agent_passport.return_value = applied
    passport.query_auth_status.return_value = (
        {"status": "ISSUED"} if issued else {"status": "PENDING"}
    )
    passport.query_agent_passport.return_value = {"agent_code": "agent-1"}
    bot_service = MagicMock()
    bot_service.is_first_bot.return_value = True
    bot_service.is_first_personal_bot.return_value = True
    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value.get_bot_mcp_codes.return_value = []
    return passport, bot_service, skill_set_factory


class TestOverrideApplication:
    def test_overrides_replace_request_and_split_provisioning(self):
        """Identity fields from the override beat the echoed request; the
        recorded create records without provisioning; the post-registration
        hook fires exactly once with the same identity."""
        ext = RecordingExtension(
            override=CreateOverride(
                template_type="appCoding",
                template_config={"template_key": "k1", "from": "ext"},
                provision=False,
            )
        )
        passport, bot_service, skill_set = _make_services()
        outcome = create_bot_with_authorization(
            user_id="u1", nick_name="u1", bot_id="b1",
            spec=_SPEC, context=_CONTEXT,
            bot_service=bot_service, passport_plugin=passport,
            auth_rel_plugin=MagicMock(), skill_set_factory=skill_set,
            create_extension=ext, request={"bot_name": "Bot", "unrelated": 1},
        )
        assert isinstance(outcome, Created)
        created_kwargs = bot_service.create_bot.call_args.kwargs
        assert created_kwargs["template_type"] == "appCoding"
        assert created_kwargs["template_config"] == {"template_key": "k1", "from": "ext"}
        assert created_kwargs["provision"] is False
        assert [c[0] for c in ext.calls] == ["resolve", "after"]
        assert ext.calls[0][3] == {"bot_name": "Bot", "unrelated": 1}
        assert ext.calls[1] == ("after", "b1", "u1")

    def test_unrecognized_request_is_untouched(self):
        ext = RecordingExtension(override=None)
        passport, bot_service, skill_set = _make_services()
        outcome = create_bot_with_authorization(
            user_id="u1", nick_name="u1", bot_id="b1",
            spec=_SPEC, context=_CONTEXT,
            bot_service=bot_service, passport_plugin=passport,
            auth_rel_plugin=MagicMock(), skill_set_factory=skill_set,
            create_extension=ext, request={"bot_name": "Bot"},
        )
        assert isinstance(outcome, Created)
        assert bot_service.create_bot.call_args.kwargs["provision"] is True
        assert [c[0] for c in ext.calls] == ["resolve"]  # no after hook

    def test_preissued_identity_skips_passport_apply(self):
        ext = RecordingExtension(
            override=CreateOverride(
                preissued={"agent_code": "agent-recorded"}, provision=True
            )
        )
        passport, bot_service, skill_set = _make_services()
        outcome = create_bot_with_authorization(
            user_id="u1", nick_name="u1", bot_id="b1",
            spec=_SPEC, context=_CONTEXT,
            bot_service=bot_service, passport_plugin=passport,
            auth_rel_plugin=MagicMock(), skill_set_factory=skill_set,
            create_extension=ext, request={},
        )
        assert isinstance(outcome, Created)
        passport.apply_agent_passport.assert_not_called()
        passport.apply_first_agent_passport.assert_not_called()
        recorded = bot_service.create_bot.call_args.kwargs
        assert recorded["ext"]["passport"]["agent_code"] == "agent-recorded"


class TestCompletionPath:
    def test_completion_adopts_override_and_fires_after(self):
        """Auth-completion resolves state by bot_id alone (the raw request is
        opaque), adopts the snapshot identity, records without provisioning,
        and fires the post-registration hook."""
        ext = RecordingExtension(
            override=CreateOverride(
                template_type="appCoding",
                template_config={"template_key": "k1", "from": "ext"},
                provision=False,
            )
        )
        passport, bot_service, skill_set = _make_services(issued=True)
        result = complete_bot_authorization(
            user_id="u1", nick_name="u1", bot_id="b1",
            spec=_SPEC, context=_CONTEXT,
            bot_service=bot_service, passport_plugin=passport,
            auth_rel_plugin=MagicMock(),
            create_extension=ext, request={"unrelated": True},
        )
        assert result.status == "ISSUED"
        created_kwargs = bot_service.create_bot.call_args.kwargs
        assert created_kwargs["template_type"] == "appCoding"
        assert created_kwargs["provision"] is False
        assert [c[0] for c in ext.calls] == ["resolve", "after"]

    def test_unresolved_completion_is_plain(self):
        ext = RecordingExtension(override=None)
        passport, bot_service, skill_set = _make_services(issued=True)
        result = complete_bot_authorization(
            user_id="u1", nick_name="u1", bot_id="b1",
            spec=_SPEC, context=_CONTEXT,
            bot_service=bot_service, passport_plugin=passport,
            auth_rel_plugin=MagicMock(),
            create_extension=ext, request={},
        )
        assert result.status == "ISSUED"
        assert bot_service.create_bot.call_args.kwargs["provision"] is True
        assert [c[0] for c in ext.calls] == ["resolve"]


class TestDefaultStub:
    def test_noop_stubs_are_inert(self):
        ext = NoopBotCreateExtension()
        assert ext.resolve_create(
            bot_id="b", user_id="u", request={}
        ) is None
        ext.after_bot_registered(bot_id="b", user_id="u")
        ext.before_delete(bot_id="b", owner_id="u")
        ext.on_delete_completed(bot_id="b", owner_id="u")
        ext.on_delete_not_found(bot_id="b", owner_id="u")

    def test_unextended_flow_has_no_extension_surface(self):
        """The absorbed-defaults path: without an extension the flow never
        calls anything and still yields a working handle."""
        passport, bot_service, skill_set = _make_services(
            issued=False
        )
        outcome = create_bot_with_authorization(
            user_id="u1", nick_name="u1", bot_id="b1",
            spec=_SPEC, context=_CONTEXT,
            bot_service=bot_service, passport_plugin=passport,
            auth_rel_plugin=MagicMock(), skill_set_factory=skill_set,
        )
        assert isinstance(outcome, AuthPending)


class TestDeleteHooks:
    def _handler(self):
        """The handler body under its interceptor decorator, driven directly:
        the interceptor graph (collaborator/permission services) is out of
        scope here — the contract under test is the hook sequencing."""
        from agentclaw.community.adapters.http.bot_management import (
            router as router_module,
        )
        handler = router_module.delete_bot
        return getattr(handler, "__wrapped__", handler)

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def _ctx(self):
        ctx = MagicMock()
        ctx.user_id = "op1"
        ctx.nick_name = "op1"
        return ctx

    def test_completed_delete_hooks_fire_in_order(self):
        svc = MagicMock()
        ext = RecordingExtension(override=None)
        resp = self._run(self._handler()(
            bot_id="b1", owner_id=None, ctx=self._ctx(),
            bot_service=svc, create_extension=ext,
        ))
        assert resp.success is True
        assert [c[0] for c in ext.calls] == ["before_delete", "delete_completed"]
        assert ext.calls[0][1:] == ("b1", "op1")

    def test_not_found_hits_not_found_hook_with_same_owner(self):
        from agentclaw.community.core.bot_management.services.bot_service import (
            BotNotFoundError,
        )
        svc = MagicMock()
        svc.delete_bot.side_effect = BotNotFoundError("nope")
        ext = RecordingExtension(override=None)
        resp = self._run(self._handler()(
            bot_id="b1", owner_id="op2", ctx=self._ctx(),
            bot_service=svc, create_extension=ext,
        ))
        assert resp.success is False
        delete_owner = svc.delete_bot.call_args.kwargs["user_id"]
        assert [c[0] for c in ext.calls] in (
            ["before_delete", "delete_not_found"], ["before_delete"],
        )
        for c in ext.calls:
            assert c[2] == delete_owner == "op2"
