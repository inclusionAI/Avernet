from agentcompute.community import get_registry
from agentcompute.community.bootstrap import Config, set_config
from agentcompute.community.plugins import register_plugins
from agentcompute.community.plugins.llm import OpenAICompatibleProvider


def test_openai_provider_resolves_from_config():
    register_plugins()
    set_config(
        Config(
            llm_provider="openai",
            options={
                "llm": {
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-test",
                    "model": "gpt-4o",
                }
            },
        )
    )
    provider = get_registry().get("llm_provider", "openai").factory()
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider._base_url == "https://api.example.com/v1"
    assert provider._api_key == "sk-test"
    assert provider._model == "gpt-4o"


def test_llm_provider_options_registered():
    register_plugins()
    names = get_registry().names("llm_provider")
    assert "stub" in names
    assert "openai" in names
