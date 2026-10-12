import pytest

from gateway.community.bootstrap.plugins._plugin_core import _bot_env
from gateway.community.core.authn import authenticate
from gateway.community.plugins.authn.bot_token._strategy import extract_bot_token
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import CredentialBundle


@pytest.mark.parametrize(
    "runtime,expected",
    [("prod", "prod"), ("gray", "gray"), ("prepub", "pre"), ("dev", "dev")],
)
def test_registry_environment_matches_bcn(monkeypatch, runtime, expected):
    monkeypatch.setenv("SERVER_ENV", runtime)
    assert _bot_env(None) == expected
    assert _bot_env("custom") == "custom"


def test_conflicting_dedicated_bot_tokens_are_rejected():
    creds = CredentialBundle(
        headers={"x-bcs-bot-token": "a", "x-avernet-bot-token": "b"},
        cookies={},
        query={},
    )
    with pytest.raises(AuthError):
        extract_bot_token(creds, "x-avernet-bot-token")


def test_legacy_dedicated_bot_token_header_is_supported():
    creds = CredentialBundle(
        headers={"x-bcs-bot-token": "static-a"}, cookies={}, query={}
    )
    assert extract_bot_token(creds, "x-avernet-bot-token") == "static-a"
