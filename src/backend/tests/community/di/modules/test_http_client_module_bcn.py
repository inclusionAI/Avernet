"""BCN HttpClient host selection."""
from __future__ import annotations

from agentclaw.community.di import config as cfg
from agentclaw.community.di.modules.http_client_module import HttpClientModule
from agentclaw.community.plugins.http_client import HttpxClient


def test_bcn_http_client_prod_uses_base_url(monkeypatch) -> None:
    monkeypatch.setattr(
        "agentclaw.community.di.modules.http_client_module.get_current_env",
        lambda: "prod",
    )
    client = HttpClientModule().bcn_http_client(
        cfg.BcnConfig(
            base_url="https://bcn.example.test",
            base_url_pre="https://bcn-pre.example.test",
        )
    )

    assert isinstance(client, HttpxClient)
    assert client._base_url == "https://bcn.example.test"


def test_bcn_http_client_pre_uses_base_url_pre(monkeypatch) -> None:
    monkeypatch.setattr(
        "agentclaw.community.di.modules.http_client_module.get_current_env",
        lambda: "pre",
    )
    client = HttpClientModule().bcn_http_client(
        cfg.BcnConfig(
            base_url="https://bcn.example.test",
            base_url_pre="https://bcn-pre.example.test",
        )
    )

    assert isinstance(client, HttpxClient)
    assert client._base_url == "https://bcn-pre.example.test"
