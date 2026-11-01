"""The composed TC embedding adapter retains the shared binary wire upgrade."""

from __future__ import annotations

import httpx
import json as jsonlib
import pytest
import struct
import struct
from prometheus_client import REGISTRY

from app.infra.embedding.config.embedding_settings import EmbeddingSettings
from app.infra.public.embedding.real_embedding_provider import RealEmbeddingProvider


def test_tc_embedding_adapter_uses_negotiated_binary_response():
    vector = [0.25, -0.5] + [0.125] * 62
    body_prefix = struct.pack("<4sBBII", b"BCFE", 1, 1, 64, 1)
    binary_body = body_prefix + b"".join(
        struct.pack("<f", value) for value in vector
    )

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/x-bcsfuse-embedding-v1"},
            content=binary_body,
        )

    settings = EmbeddingSettings(
        base_url="http://models.example.test/v1",
        auth_token="test-only",
        model="binary-composition",
        dimension=64,
    )
    instance = RealEmbeddingProvider(
        settings=settings,
        max_retries=0,
        retry_budget_ms=1000,
    )
    instance._client = httpx.Client(
        transport=httpx.MockTransport(respond),
        timeout=httpx.Timeout(5.0),
    )
    try:
        assert instance.embed("composition query") == vector
        before = REGISTRY.get_sample_value(
            "bcsfuse_embedding_wire_responses_total",
            {"model": "binary-composition", "protocol": "binary"},
        )
        assert instance.embed("composition query") == vector
        after = REGISTRY.get_sample_value(
            "bcsfuse_embedding_wire_responses_total",
            {"model": "binary-composition", "protocol": "binary"},
        )
    finally:
        instance.close()

    assert after is not None
    assert after - (before or 0.0) == 1.0


def test_tc_embedding_adapter_records_dimension_contract_violations():
    from prometheus_client import REGISTRY

    invalid_response = jsonlib.dumps(
        {"data": [{"embedding": [0.25] * 32}]}
    ).encode()

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=invalid_response,
        )

    metric_name = "bcsfuse_embedding_dimension_contract_violations_total"
    labels = {
        "model": "dimension-contract",
        "protocol": "json",
        "expected_dimension": "64",
        "actual_dimensions": "32",
    }
    before = REGISTRY.get_sample_value(metric_name, labels) or 0.0

    settings = EmbeddingSettings(
        base_url="https://models.example.test/v1",
        auth_token="test-only",
        model="dimension-contract",
        dimension=64,
    )
    instance = RealEmbeddingProvider(
        settings=settings,
        max_retries=0,
        retry_budget_ms=1000,
    )
    instance._client = httpx.Client(
        transport=httpx.MockTransport(respond),
        timeout=httpx.Timeout(5),
    )
    try:
        with pytest.raises(Exception, match="dimension mismatch"):
            instance.embed("mismatched response")
    finally:
        instance.close()

    assert REGISTRY.get_sample_value(metric_name, labels) == before + 1


def test_tc_embedding_adapter_records_binary_dimension_contract_as_explicit_reason():
    from app.application.services.semantic_failure_metadata import (
        semantic_failure_metadata,
    )
    from search_core.providers.embedding import (
        EmbeddingDimensionContractError,
    )

    vector = [0.25] * 32
    binary_body = struct.pack("<4sBBII", b"BCFE", 1, 1, 32, 1) + b"".join(
        struct.pack("<f", value) for value in vector
    )

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/x-bcsfuse-embedding-v1"},
            content=binary_body,
        )

    metric_name = "bcsfuse_embedding_dimension_contract_violations_total"
    labels = {
        "model": "binary-dimension-contract",
        "protocol": "binary",
        "expected_dimension": "64",
        "actual_dimensions": "32",
    }
    before = REGISTRY.get_sample_value(metric_name, labels) or 0.0

    settings = EmbeddingSettings(
        base_url="https://models.example.test/v1",
        auth_token="test-only",
        model="binary-dimension-contract",
        dimension=64,
    )
    instance = RealEmbeddingProvider(
        settings=settings,
        max_retries=0,
        retry_budget_ms=1000,
    )
    instance._client = httpx.Client(
        transport=httpx.MockTransport(respond),
        timeout=httpx.Timeout(5),
    )
    try:
        with pytest.raises(EmbeddingDimensionContractError) as captured:
            instance.embed("mismatched binary response")
    finally:
        instance.close()

    assert "binary response dimension mismatch" in str(captured.value)
    assert semantic_failure_metadata(captured.value) == {
        "semantic_error": "EmbeddingDimensionContractError",
        "semantic_status_code": 415,
        "semantic_retry_after_seconds": None,
        "semantic_reason_code": "embedding_dimension_contract_mismatch",
        "semantic_provider_reason_code": None,
    }
    assert REGISTRY.get_sample_value(metric_name, labels) == before + 1
