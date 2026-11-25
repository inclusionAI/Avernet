from __future__ import annotations

from typing import Annotated

from injector import Injector, Module, provider, singleton

from agentclaw.community.di.bbs_digest_config import BbsDingTalkConfig
from agentclaw.community.di.modules.bbs_digest_module import BbsDigestModule
from agentclaw.community.plugin_api.notify_sender import (
    QUALIFIER_BBS_DIGEST,
    NotifyMessage,
    NotifySenderPlugin,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.plugins.community.notify_sender import DingTalkNotifySender


class _Secret:
    secret_value = "bbs-secret"


class _FakeSecretResolver:
    def __init__(self, secret: object) -> None:
        self._secret = secret
        self.calls: list[str] = []

    def get_secret(self, secret_name: str) -> object:
        self.calls.append(secret_name)
        return self._secret


class _StubBbsDingTalkModule(Module):
    @singleton
    @provider
    def bbs_dingtalk_config(self) -> BbsDingTalkConfig:
        return BbsDingTalkConfig(
            ak_id="bbs-ak-id",
            ak_secret_name="bbs_dingtalk_ak_secret",
            robot_code="bbs-robot-code",
            card_template_id="bbs-template.schema",
        )

    @singleton
    @provider
    def secret_resolver(self) -> SecretResolver:
        return _FakeSecretResolver(_Secret())  # type: ignore[return-value]


def _message() -> NotifyMessage:
    return NotifyMessage(
        title="BBS digest",
        body="summary",
        recipient="111111",
        deep_link="https://frontend.example/session",
    )


def test_bbs_digest_module_builds_qualified_dingtalk_sender():
    injector = Injector([BbsDigestModule(), _StubBbsDingTalkModule()])
    resolver = injector.get(SecretResolver)
    sender = injector.get(Annotated[NotifySenderPlugin, QUALIFIER_BBS_DIGEST])

    assert isinstance(sender, DingTalkNotifySender)
    assert sender.channels == frozenset({"tc_card"})
    assert sender._configured() is True
    assert "bbs_dingtalk_ak_secret" in resolver.calls


def test_bbs_digest_sender_ignores_task_discovery_recipient(monkeypatch):
    injector = Injector([BbsDigestModule(), _StubBbsDingTalkModule()])
    sender = injector.get(Annotated[NotifySenderPlugin, QUALIFIER_BBS_DIGEST])
    monkeypatch.setenv("TASK_DISCOVERY_DINGTALK_ACCOUNT_ID", "task-discovery-user")

    assert sender._recipient_account_id(_message()) == "111111"


def test_bbs_digest_sender_returns_real_card_result(monkeypatch):
    injector = Injector([BbsDigestModule(), _StubBbsDingTalkModule()])
    sender = injector.get(Annotated[NotifySenderPlugin, QUALIFIER_BBS_DIGEST])
    monkeypatch.setattr(sender, "_send_dingtalk_card", lambda _message: "card-123")

    assert sender.send(_message(), channel="tc_card") == "card-123"


def test_bbs_digest_sender_fails_closed_for_other_channels(monkeypatch):
    injector = Injector([BbsDigestModule(), _StubBbsDingTalkModule()])
    sender = injector.get(Annotated[NotifySenderPlugin, QUALIFIER_BBS_DIGEST])
    monkeypatch.setattr(sender, "_send_dingtalk_card", lambda _message: "card-123")

    assert sender.send(_message(), channel="markdown") is None
