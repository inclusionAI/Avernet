"""Native-async HTTP embedding batch contract."""

from __future__ import annotations

import asyncio

from search_core.providers.embedding import AsyncHttpEmbedding


class _Client:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def post_json(self, url: str, *, headers: dict[str, str], json: dict):
        self.calls.append({"url": url, "payload": json})
        return {
            "data": [
                {"index": index, "embedding": [index, 0.5]}
                for index in range(len(json["input"]))
            ]
        }


class _Settings:
    base_url = "https://example.test/v1"
    auth_token = "test-only"
    model = "contract-model"
    dimension = 2
    timeout_ms = 100


def test_http_embeddings_batch_emits_one_request_and_validates_results() -> None:
    provider = AsyncHttpEmbedding.__new__(AsyncHttpEmbedding)
    provider._settings = _Settings()
    provider._client = _Client()

    vectors = asyncio.run(provider.embed_batch(["one", "two", "three"]))

    assert vectors == [[0, 0.5], [1, 0.5], [2, 0.5]]
    assert len(provider._client.calls) == 1
    assert provider._client.calls[0]["payload"]["input"] == ["one", "two", "three"]


def test_async_batch_requests_mrl_dimension_when_enabled() -> None:
    class DimensionSettings(_Settings):
        request_dimensions = True

    provider = AsyncHttpEmbedding.__new__(AsyncHttpEmbedding)
    provider._settings = DimensionSettings()
    provider._client = _Client()

    asyncio.run(provider.embed_batch(["one", "two"]))

    assert provider._client.calls[0]["payload"]["dimensions"] == 2


def test_async_batch_omits_dimension_when_not_requested() -> None:
    class NativeDimensionSettings(_Settings):
        request_dimensions = False

    provider = AsyncHttpEmbedding.__new__(AsyncHttpEmbedding)
    provider._settings = NativeDimensionSettings()
    provider._client = _Client()

    asyncio.run(provider.embed_batch(["one", "two"]))

    assert "dimensions" not in provider._client.calls[0]["payload"]
