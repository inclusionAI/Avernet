from search_core.core.log_context import received_body_fields, search_request_body


def test_raw_business_body_retains_wire_shape_and_redacts_bounded_copy():
    assert received_body_fields(set()) == {}
    body = {
        "query": "mail foo@example.com",
        "filters": [
            {"key": "labels.visibility", "values": ["test"]},
            {"key": "private.field", "values": ["private value"]},
        ],
        "goal": {"id": "legacy"},
        "context": {
            "task": None,
            "goal": {"steps": [2, {"accessToken": "private value"}, False, None]},
        },
        "task": {"description": "x" * 300, "ids": list(range(150))},
    }
    token = search_request_body.set(body)
    try:
        fields = received_body_fields({"labels.visibility"})
    finally:
        search_request_body.reset(token)
    request = fields["request_body"]
    assert fields["has_goal"] and fields["has_task"]
    assert fields["request_body_truncated"] is True
    assert request["query"] == "mail [redacted]"
    assert request["filters"][0] == body["filters"][0]
    assert request["filters"][1]["values"] == "[redacted]"
    assert request["goal"] == {"id": "legacy"}
    assert request["context"] == {
        "task": None,
        "goal": {"steps": [2, {"accessToken": "[redacted]"}, False, None]},
    }
    assert request["task"]["description"] == "x" * 256
    assert 0 < len(request["task"]["ids"]) < len(body["task"]["ids"])
    assert body["context"]["goal"]["steps"][1] == {"accessToken": "private value"}
    assert received_body_fields(set()) == {}
