import pytest

from gateway.community.bootstrap.plugins._plugin_core import _bot_env
from gateway.community.core.authn import authenticate
from gateway.community.plugins.authn.bot_token._strategy import extract_bot_token
from gateway.community.spi.authn import CredentialBundle


@pytest.mark.parametrize(
    "runtime,expected",
    [("prod", "prod"), ("gray", "gray"), ("prepub", "pre"), ("dev", "dev")],
)
def test_registry_environment_matches_bcn(monkeypatch, runtime, expected):
    monkeypatch.setenv("SERVER_ENV", runtime)
    assert _bot_env() == expected


def test_registry_environment_defaults_to_dev(monkeypatch):
    monkeypatch.delenv("SERVER_ENV", raising=False)
    assert _bot_env() == "dev"


def test_unconfigured_bcs_header_does_not_conflict_with_bot_token():
    creds = CredentialBundle(
        headers={"x-bcs-bot-token": "a", "x-avernet-bot-token": "b"},
        cookies={},
        query={},
    )
    assert extract_bot_token(creds, "x-avernet-bot-token") == "b"


def test_bcs_dedicated_bot_token_header_is_not_an_alias():
    creds = CredentialBundle(
        headers={"x-bcs-bot-token": "static-a"}, cookies={}, query={}
    )
    assert extract_bot_token(creds, "x-avernet-bot-token") is None


def test_dedicated_bot_token_header_remains_configurable():
    creds = CredentialBundle(
        headers={"x-custom-bot-token": "static-a", "x-avernet-bot-token": "other"},
        cookies={},
        query={},
    )
    assert extract_bot_token(creds, "x-custom-bot-token") == "static-a"
