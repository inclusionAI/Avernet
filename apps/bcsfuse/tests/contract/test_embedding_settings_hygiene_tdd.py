"""TDD: embedding credentials and service identifiers are normalized once."""

from __future__ import annotations

from search_core.config.embedding import EmbeddingSettings


def test_directly_supplied_embedding_configuration_is_trimmed() -> None:
    settings = EmbeddingSettings(
        base_url="  https://embedding.invalid/v1  ",
        auth_token="  test-token  ",
        model="  Qwen3-Embedding-4B  ",
        dimension=1024,
        request_dimensions=True,
    )

    assert settings.base_url == "https://embedding.invalid/v1"
    assert settings.auth_token == "test-token"
    assert settings.model == "Qwen3-Embedding-4B"
    assert settings.is_configured() is True
    assert settings.missing_config() == []


def test_blank_embedding_values_behave_like_missing_values() -> None:
    settings = EmbeddingSettings(base_url="   ", auth_token="")

    assert settings.base_url is None
    assert settings.auth_token is None
    assert settings.is_configured() is False
    assert settings.missing_config() == [
        "EMBEDDING_BASE_URL",
        "EMBEDDING_AUTH_TOKEN",
        "EMBEDDING_MODEL",
    ]
