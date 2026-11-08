"""BaaS POST response validation preserves its required object contract."""

import json
from unittest.mock import MagicMock

import httpx
import pytest
from agentclaw.community.core.service_bot.baas_service_errors import BaasServiceError
from agentclaw.community.core.service_bot.services.baas_api_response import (
    BaasApiResponseError,
    read_baas_bot_api_envelope,
)
from agentclaw.community.core.service_bot.services.baas_service import BaasService


def test_valid_envelope_preserves_identity():
    envelope = {"code": 0, "data": {"publish_id": 1}}
    assert read_baas_bot_api_envelope(lambda: envelope, action="test") is envelope


def test_envelope_preserves_absent_data():
    assert read_baas_bot_api_envelope(lambda: {"code": 0}, action="test") == {"code": 0}


@pytest.mark.parametrize(
    "envelope",
    [None, [], "text", 1],
)
def test_invalid_shapes_raise_upstream_error(envelope):
    with pytest.raises(BaasApiResponseError):
        read_baas_bot_api_envelope(lambda: envelope, action="test")


def test_invalid_json_preserves_cause():
    def read_json():
        return json.loads("private malformed JSON")

    with pytest.raises(BaasApiResponseError) as caught:
        read_baas_bot_api_envelope(read_json, action="test")
    assert isinstance(caught.value.__cause__, json.JSONDecodeError)
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("body", [{"code": 0}, {"code": 0, "data": {"publish_id": 1}}])
def test_public_post_keeps_valid_data_and_missing_data_default(body):
    service = object.__new__(BaasService)
    service._tenant = "default"
    service._http = MagicMock()
    service._http.post.return_value = httpx.Response(
        200, json=body, request=httpx.Request("POST", "https://baas.test/bots")
    )
    assert service.post_bots_api("/bots", {}, "test") == body.get("data", {})


def test_private_post_keeps_legacy_nullable_data_behavior():
    service = object.__new__(BaasService)
    service._tenant = "default"
    service._http = MagicMock()
    service._http.post.return_value = httpx.Response(
        200,
        json={"code": 0, "data": None},
        request=httpx.Request("POST", "https://baas.test/bots"),
    )
    assert service._post_bots_api("/bots", {}, "test") is None


def test_business_error_keeps_existing_message():
    with pytest.raises(BaasServiceError, match="BaaS API error: rejected"):
        read_baas_bot_api_envelope(
            lambda: {"code": 1, "message": "rejected"}, action="test"
        )


def test_unexpected_reader_error_propagates():
    def read_json():
        raise RuntimeError("unexpected reader failure")

    with pytest.raises(RuntimeError, match="unexpected reader failure"):
        read_baas_bot_api_envelope(read_json, action="test")
