"""Runtime embedding defaults must match the selected efficient public model."""

from __future__ import annotations

import pytest

from app.bootstrap.embedding_defaults import resolve_embedding_runtime_defaults
from app.infra.embedding.config.embedding_settings import EmbeddingSettings


def test_runtime_defaults_use_qwen3_4b_with_1024_dimensions() -> None:
    resolved = resolve_embedding_runtime_defaults({}, {})

    assert resolved.model == "Qwen3-Embedding-4B"
    assert resolved.dimension == 1024
    assert resolved.request_dimensions is True


def test_runtime_defaults_compose_to_a_valid_model_dimension_contract() -> None:
    resolved = resolve_embedding_runtime_defaults({}, {})

    settings = EmbeddingSettings(
        base_url="https://example.invalid/v1",
        auth_token="test-token",
        model=resolved.model,
        dimension=resolved.dimension,
        request_dimensions=resolved.request_dimensions,
    )

    assert settings.model == "Qwen3-Embedding-4B"
    assert settings.dimension == 1024
    assert settings.request_dimensions is True


@pytest.mark.parametrize(
    ("env", "config", "expected"),
    [
        (
            {"EMBEDDING_MODEL": "Qwen3-Embedding-8B", "EMBEDDING_DIMENSION": "4096"},
            {"embedding": {"request_dimensions": False}},
            ("Qwen3-Embedding-8B", 4096, False),
        ),
        (
            {},
            {
                "embedding": {
                    "model": "Qwen3-Embedding-4B",
                    "dimension": 1024,
                }
            },
            ("Qwen3-Embedding-4B", 1024, True),
        ),
        (
            {},
            {
                "embedding": {
                    "model": "Qwen3-Embedding-4B",
                    "dimension": 2560,
                    "request_dimensions": False,
                }
            },
            ("Qwen3-Embedding-4B", 2560, False),
        ),
    ],
)
def test_explicit_configuration_overrides_the_efficient_defaults(
    env: dict[str, str],
    config: dict,
    expected: tuple[str, int, bool],
) -> None:
    resolved = resolve_embedding_runtime_defaults(config, env)

    assert (
        resolved.model,
        resolved.dimension,
        resolved.request_dimensions,
    ) == expected


def test_environment_overrides_application_config_for_request_dimensions() -> None:
    resolved = resolve_embedding_runtime_defaults(
        {"embedding": {"request_dimensions": True}},
        {
            "EMBEDDING_MODEL": "Qwen3-Embedding-8B",
            "EMBEDDING_DIMENSION": "4096",
            "EMBEDDING_REQUEST_DIMENSIONS": "false",
        },
    )

    assert (
        resolved.model,
        resolved.dimension,
        resolved.request_dimensions,
    ) == ("Qwen3-Embedding-8B", 4096, False)


def test_environment_overrides_application_config_for_model_and_dimension() -> None:
    resolved = resolve_embedding_runtime_defaults(
        {
            "embedding": {
                "model": "Qwen3-Embedding-0.6B",
                "dimension": 1024,
                "request_dimensions": True,
            }
        },
        {"EMBEDDING_MODEL": "Qwen3-Embedding-4B", "EMBEDDING_DIMENSION": "2048"},
    )

    assert resolved.model == "Qwen3-Embedding-4B"
    assert resolved.dimension == 2048
    assert resolved.request_dimensions is True
