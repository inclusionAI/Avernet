import json
from io import BytesIO

import pytest

from agentcompute.community.plugins.llm import OpenAICompatibleProvider


def test_requires_base_url_and_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError):
        OpenAICompatibleProvider()


def test_reads_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    provider = OpenAICompatibleProvider()
    assert provider._base_url == "https://api.example.com/v1"
    assert provider._api_key == "sk-env"
    assert provider._model == "env-model"


def test_model_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    provider = OpenAICompatibleProvider(base_url="https://api.example.com/v1", api_key="sk-x")
    assert provider._model == "gpt-4o-mini"


def test_complete_posts_and_parses(monkeypatch):
    captured = {}

    def _mock_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = dict(request.headers)
        captured["timeout"] = timeout
        payload = {"choices": [{"message": {"content": "hi there"}}]}
        return BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen)
    provider = OpenAICompatibleProvider(base_url="https://api.example.com/v1", api_key="sk-x")
    result = provider.complete("hello", temperature=0.7)
    assert result == "hi there"
    assert captured["body"]["model"] == "gpt-4o-mini"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hello"}]
    assert captured["body"]["temperature"] == 0.7
    assert captured["headers"]["Authorization"] == "Bearer sk-x"


def test_complete_raises_on_http_error(monkeypatch):
    import urllib.error

    def _raise(request, timeout):
        raise urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    provider = OpenAICompatibleProvider(base_url="https://api.example.com/v1", api_key="sk-x")
    with pytest.raises(RuntimeError, match="HTTP 401"):
        provider.complete("x")
