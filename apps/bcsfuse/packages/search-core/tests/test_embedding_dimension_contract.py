"""Provider contract for explicit embedding dimensions."""

from __future__ import annotations

import json as jsonlib

import httpx
import pytest

from search_core.config.embedding import EmbeddingSettings
from search_core.providers.embedding import (
    EmbeddingAPIError,
    RealEmbeddingProvider,
)


def provider(dimension: int, request_dimensions: bool):
    settings = EmbeddingSettings(
        base_url="https://models.example.test/v1",
        auth_token="test-token",
        model="example-embedding",
        dimension=dimension,
        request_dimensions=request_dimensions,
    )
    instance = RealEmbeddingProvider(
        settings=settings,
        max_retries=0,
        retry_budget_ms=1000,
    )
    return instance


def test_dimension_request_can_be_enabled_for_mrl_models():
    requests: list[httpx.Request] = []
    vector = [0.25] * 1024

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=jsonlib.dumps({"data": [{"embedding": vector}]}).encode(),
        )

    instance = provider(1024, True)
    instance._client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        assert instance.embed("支付查询") == vector
    finally:
        instance.close()

    assert jsonlib.loads(requests[0].content)["dimensions"] == 1024


def test_dimension_request_is_off_by_default_for_provider_compatibility():
    requests: list[httpx.Request] = []
    vector = [0.25] * 1024

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=jsonlib.dumps({"data": [{"embedding": vector}]}).encode(),
        )

    instance = provider(1024, False)
    instance._client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        assert instance.embed("支付查询") == vector
    finally:
        instance.close()

    assert "dimensions" not in jsonlib.loads(requests[0].content)


def test_known_qwen_models_have_dimension_allowlists_and_request_contract():
    from search_core.config.embedding import EmbeddingSettings

    provider_settings = EmbeddingSettings(
        base_url="https://models.example.test/v1",
        auth_token="test-token",
        model="Qwen3-Embedding-0.6B",
        dimension=1024,
        request_dimensions=False,
    )
    assert provider_settings.dimension == 1024

    smaller = EmbeddingSettings(
        base_url="https://models.example.test/v1",
        auth_token="test-token",
        model="Qwen3-Embedding-4B",
        dimension=1024,
        request_dimensions=True,
    )
    assert smaller.dimension == 1024

    native = EmbeddingSettings(
        base_url="https://models.example.test/v1",
        auth_token="test-token",
        model="Qwen3-Embedding-8B",
        dimension=4096,
        request_dimensions=False,
    )
    assert native.dimension == 4096


def test_non_native_qwen_dimension_requires_request_dimensions():
    from search_core.config.embedding import EmbeddingSettings

    with pytest.raises(ValueError, match="request_dimensions"):
        EmbeddingSettings(
            base_url="https://models.example.test/v1",
            auth_token="test-token",
            model="Qwen3-Embedding-4B",
            dimension=1024,
            request_dimensions=False,
        )


def test_unsupported_qwen_dimension_is_rejected_before_provider_instantiation():
    from search_core.config.embedding import EmbeddingSettings

    with pytest.raises(ValueError, match="dimension"):
        EmbeddingSettings(
            base_url="https://models.example.test/v1",
            auth_token="test-token",
            model="Qwen3-Embedding-4B",
            dimension=3072,
            request_dimensions=True,
        )


def test_json_response_dimension_mismatch_fails_closed():
    requests: list[httpx.Request] = []
    mismatched_vector = [0.25] * 32

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=jsonlib.dumps(
                {"data": [{"embedding": mismatched_vector}]}
            ).encode(),
        )

    instance = provider(1024, True)
    instance._client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(EmbeddingAPIError, match="dimension mismatch"):
            instance.embed("支付查询")
    finally:
        instance.close()
    assert len(requests) == 1


def test_json_response_vector_count_mismatch_fails_closed():
    def respond(request: httpx.Request) -> httpx.Response:
        single = {"data": [{"embedding": [0.25] * 1024}]}
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=jsonlib.dumps(single).encode(),
        )

    instance = provider(1024, True)
    instance._client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(EmbeddingAPIError, match="embedding count mismatch"):
            instance.embed_batch(["one", "two"])
    finally:
        instance.close()


def test_json_response_non_finite_values_fail_closed():
    invalid = {"data": [{"embedding": [float("nan")] + [0.0] * 1023}]}
    encoded = jsonlib.dumps(invalid).encode()

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=encoded,
        )

    instance = provider(1024, True)
    instance._client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(EmbeddingAPIError, match="non-finite"):
            instance.embed("支付查询")
    finally:
        instance.close()
