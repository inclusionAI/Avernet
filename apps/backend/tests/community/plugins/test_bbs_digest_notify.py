from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.di.bbs_digest_config import BbsDingTalkConfig
from agentclaw.community.plugin_api.notify_sender import NotifyMessage
from agentclaw.community.plugins.community.bbs_digest_notify import (
    BbsDingTalkNotifyProvider,
)


@dataclass(frozen=True)
class Secret:
    secret_user: str
    secret_value: str


class FakeSecretResolver:
    def __init__(self, secret: object) -> None:
        self._secret = secret
        self.calls: list[str] = []

    def get_secret(self, secret_name: str) -> object:
        self.calls.append(secret_name)
        return self._secret


def _message() -> NotifyMessage:
    return NotifyMessage(
        title="BBS 高价值内容待确认",
        body="summary",
        recipient="111111",
        deep_link="https://frontend.example/session",
    )


def test_unconfigured_bbs_dingtalk_provider_fails_closed() -> None:
    provider = BbsDingTalkNotifyProvider(
        BbsDingTalkConfig(),
        FakeSecretResolver(Secret("", "unused")),
    )

    assert provider.send(_message()) is None


def test_configured_bbs_dingtalk_sender_resolves_its_named_secret() -> None:
    config = BbsDingTalkConfig(
        ak_id="bbs-ak-id",
        ak_secret_name="bbs_dingtalk_ak_secret",
        robot_code="bbs-robot-code",
        card_template_id="bbs-template.schema",
    )
    resolver = FakeSecretResolver(Secret("", "bbs-secret"))
    provider = BbsDingTalkNotifyProvider(config, resolver)

    assert provider._sender._configured() is True
    assert resolver.calls == ["bbs_dingtalk_ak_secret"]


def test_bbs_sender_uses_owner_over_task_discovery_recipient(monkeypatch) -> None:
    config = BbsDingTalkConfig(
        ak_id="bbs-ak-id",
        ak_secret_name="bbs_dingtalk_ak_secret",
        robot_code="bbs-robot-code",
        card_template_id="bbs-template.schema",
    )
    provider = BbsDingTalkNotifyProvider(
        config,
        FakeSecretResolver(Secret("", "bbs-secret")),
    )
    monkeypatch.setenv("TASK_DISCOVERY_DINGTALK_ACCOUNT_ID", "task-discovery-user")

    assert provider._sender._recipient_account_id(_message()) == "111111"


def test_configured_bbs_dingtalk_sender_returns_card_result(monkeypatch) -> None:
    config = BbsDingTalkConfig(
        ak_id="bbs-ak-id",
        ak_secret_name="bbs_dingtalk_ak_secret",
        robot_code="bbs-robot-code",
        card_template_id="bbs-template.schema",
    )
    provider = BbsDingTalkNotifyProvider(
        config,
        FakeSecretResolver(Secret("", "bbs-secret")),
    )

    monkeypatch.setattr(
        provider._sender,
        "_send_dingtalk_card",
        lambda _message: "bbs-card-123",
    )

    assert provider.send(_message()) == "bbs-card-123"


def test_bbs_dingtalk_provider_registered_as_production_plugin() -> None:
    from agentclaw.community.plugin_api.bbs_digest_notify import BbsDigestNotifyProvider
    from agentclaw.community.plugin_api.impl_registry import IMPL_REGISTRY, Mode

    entries = [entry for entry in IMPL_REGISTRY if entry.cls is BbsDingTalkNotifyProvider]
    assert len(entries) == 1
    assert entries[0].protocol is BbsDigestNotifyProvider
    assert entries[0].mode is Mode.PROD
    assert entries[0].flavor is None
