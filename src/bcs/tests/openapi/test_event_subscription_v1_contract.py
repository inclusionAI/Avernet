import sys
from pathlib import Path

BCS_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = BCS_ROOT / "api-contracts" / "v1"
sys.path.insert(0, str(BCS_ROOT))

from scripts.validate_openapi_contract import load_contract, validate_contract  # noqa: E402

BASE = "/openapi/v1/collaboration"
OPERATIONS = {
    ("post", f"{BASE}/event-subscriptions"),
    ("get", f"{BASE}/event-subscriptions"),
    ("get", f"{BASE}/event-subscriptions/{{subscription_id}}"),
    ("patch", f"{BASE}/event-subscriptions/{{subscription_id}}"),
    ("delete", f"{BASE}/event-subscriptions/{{subscription_id}}"),
    ("post", f"{BASE}/event-subscriptions/{{subscription_id}}:test"),
    ("get", f"{BASE}/event-subscriptions/{{subscription_id}}/deliveries"),
    ("get", f"{BASE}/event-deliveries/{{delivery_id}}"),
    ("post", f"{BASE}/event-deliveries/{{delivery_id}}:replay"),
    ("post", f"{BASE}/event-deliveries/{{delivery_id}}:skip"),
}


def contract():
    return load_contract(CONTRACT_ROOT)


def test_event_subscription_operations_are_versioned_and_valid() -> None:
    document = contract()
    assert validate_contract(document) == []
    actual = {
        (method, path)
        for path, item in document["paths"].items()
        for method in item
        if (method, path) in OPERATIONS
    }
    assert actual == OPERATIONS
    for method, path in OPERATIONS:
        assert document["paths"][path][method]["x-avernet-security"] == {
            "user": "required",
            "app": "required",
        }


def test_endpoint_is_write_only_and_subscription_auth_is_not_exposed() -> None:
    document = contract()
    create = document["paths"][f"{BASE}/event-subscriptions"]["post"]
    request_schema = create["requestBody"]["content"]["application/json"]["schema"]
    sink_input = request_schema["properties"]["sink"]
    assert sink_input["properties"]["url"]["writeOnly"] is True
    assert "auth" not in sink_input["properties"]

    response_schema = create["responses"]["201"]["content"]["application/json"][
        "schema"
    ]
    serialized = repr(response_schema)
    assert "'auth':" not in serialized
    assert "'url':" not in serialized


def test_create_scope_and_delivery_modes_are_limited_to_the_mvp() -> None:
    document = contract()
    create = document["paths"][f"{BASE}/event-subscriptions"]["post"]
    request_schema = create["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["properties"]["scope"]["required"] == ["type", "id"]
    assert request_schema["properties"]["scope"]["properties"]["type"]["enum"] == [
        "group"
    ]
    assert set(request_schema["properties"]) == {
        "name",
        "scope",
        "event_filters",
        "payload",
        "sink",
    }
    assert request_schema["properties"]["sink"]["properties"]["type"]["const"] == (
        "webhook"
    )


def test_revision_cursor_and_status_contracts_are_explicit() -> None:
    document = contract()
    patch = document["paths"][
        f"{BASE}/event-subscriptions/{{subscription_id}}"
    ]["patch"]
    assert {parameter["name"] for parameter in patch["parameters"]} == {
        "subscription_id",
        "If-Match",
    }
    assert "revision" in patch["requestBody"]["content"]["application/json"][
        "schema"
    ]["properties"]

    listing = document["paths"][f"{BASE}/event-subscriptions"]["get"]
    parameters = {parameter["name"]: parameter for parameter in listing["parameters"]}
    assert parameters["limit"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 20,
    }
    assert "cursor" in parameters

    replay = document["paths"][f"{BASE}/event-deliveries/{{delivery_id}}:replay"][
        "post"
    ]
    assert set(replay["responses"]) >= {"202", "404", "409"}
    skip = document["paths"][f"{BASE}/event-deliveries/{{delivery_id}}:skip"][
        "post"
    ]
    assert set(skip["responses"]) >= {"200", "404", "409"}


def test_in_flight_attempt_summary_requires_only_started_identity() -> None:
    document = contract()
    detail = document["paths"][
        f"{BASE}/event-deliveries/{{delivery_id}}"
    ]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    attempt = detail["properties"]["data"]["properties"]["attempts"]["items"]
    assert attempt["required"] == ["attempt_no", "started_at"]
    assert {
        "completed_at",
        "latency_ms",
        "result",
        "http_status",
        "error_category",
    } <= set(attempt["properties"])


def test_stable_event_error_vocabulary_is_declared() -> None:
    document = contract()
    declared = {
        code
        for method, path in OPERATIONS
        for response in document["paths"][path][method]["responses"].values()
        for code in response.get("x-error-codes", [])
    }
    assert {
        "event_subscription_not_found",
        "event_delivery_not_found",
        "invalid_event_filter",
        "invalid_event_scope",
        "invalid_webhook_url",
        "event_subscription_limit_reached",
        "event_subscription_revision_conflict",
        "event_subscription_forbidden",
        "event_delivery_not_replayable",
        "event_delivery_lane_blocked",
    } <= declared
