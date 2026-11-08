"""Requests and refusal assertions for the app-only admission ratchet."""

def _data(response):
    assert response.status_code == 200, response.json()
    return response.json()["data"]


_UNGRANTED_APP_CASES = {
    ("GET", "/openapi/v1/bots/skills/repository"): {
        "request": lambda client: client.get("/openapi/v1/bots/skills/repository"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/skills/repository/tree"): {
        "request": lambda client: client.get("/openapi/v1/bots/skills/repository/tree"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/skills/repository/{skill_id}"): {
        "request": lambda client: client.get("/openapi/v1/bots/skills/repository/1"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/skills/{skill_id}/readme"): {
        "request": lambda client: client.get("/openapi/v1/bots/skills/1/readme"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/skills/repository/sync"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/skills/repository/sync"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/market/skill-center/sync"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/market/skill-center/sync"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots"): {
        "request": lambda client: client.get("/openapi/v1/bots"),
        "assert_starved": lambda response: (
            _data(response)["items"] == [] and _data(response)["total"] == 0
        ),
    },
    ("GET", "/openapi/v1/bots/collaborations"): {
        "request": lambda client: client.get("/openapi/v1/bots/collaborations"),
        "assert_starved": lambda response: (
            _data(response)["items"] == [] and _data(response)["total"] == 0
        ),
    },
    ("GET", "/openapi/v1/bots/authorized"): {
        "request": lambda client: client.get("/openapi/v1/bots/authorized"),
        "assert_starved": lambda response: (
            _data(response)["items"] == [] and _data(response)["total"] == 0
        ),
    },
    ("GET", "/openapi/v1/bots/all"): {
        "request": lambda client: client.get("/openapi/v1/bots/all"),
        "assert_starved": lambda response: (
            _data(response)["items"] == [] and _data(response)["total"] == 0
        ),
    },
    ("GET", "/openapi/v1/bots/local"): {
        "request": lambda client: client.get("/openapi/v1/bots/local"),
        "assert_starved": lambda response: (
            _data(response)["items"] == [] and _data(response)["total"] == 0
        ),
    },
    ("GET", "/openapi/v1/bots/local/devices"): {
        "request": lambda client: client.get("/openapi/v1/bots/local/devices"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/local/devices/{machine_id}/files"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/local/devices/machine-1/files"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/ceiling"): {
        "request": lambda client: client.get("/openapi/v1/bots/ceiling"),
        # USER_GATED: no delegation, no relationship — masked as not-found, so
        # a stranger app cannot read a person's quota by naming them.
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/routines/all"): {
        "request": lambda client: client.get("/openapi/v1/bots/routines/all"),
        # USER_GATED, the ceiling's exact shape: the aggregate reads the named
        # user's whole routine fleet, so an app with no delegation from them is
        # answered as if the user did not exist. The cron service stays
        # untouched — it is bound to `_UnexpectedService` in the fixture, so a
        # regression that asks it anyway fails here rather than leaking rows.
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces"): {
        "request": lambda client: client.get("/openapi/v1/bots/spaces"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/members"): {
        "request": lambda client: client.get("/openapi/v1/bots/spaces/1/members"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills"): {
        "request": lambda client: client.get("/openapi/v1/bots/spaces/1/skills"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/consumable"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/consumable"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}"): {
        "request": lambda client: client.get("/openapi/v1/bots/spaces/1/skills/1"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/draft/files"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/draft/files/{path:path}",
    ): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/draft/files/SKILL.md"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/versions"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}",
    ): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/versions/1"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files",
    ): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/versions/1/files"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    (
        "GET",
        "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/versions/{version}/files/{path:path}",
    ): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/versions/1/files/SKILL.md"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}/grants"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/spaces/1/skills/1/grants"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/spaces/{space_id}/market-favorites"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/spaces/1/market-favorites",
            json={
                "market_source": "SKILLCENTER",
                "target_type": "SKILL",
                "target_code": "skill-1",
            },
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/spaces/{space_id}/market-favorites/cancel"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/spaces/1/market-favorites/cancel",
            json={
                "market_source": "SKILLCENTER",
                "target_type": "SKILL",
                "target_code": "skill-1",
            },
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/spaces/{space_id}/market-favorites/search"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/spaces/1/market-favorites/search", json={}
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/spaces/{space_id}/market-favorites/status"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/spaces/1/market-favorites/status",
            json={
                "market_source": "SKILLCENTER",
                "target_type": "SKILL",
                "target_codes": ["skill-1"],
            },
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/{bot_id}/editor-request-policy"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/bot-1/editor-request-policy",
            params={"entity_id": "owner-1"},
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("PATCH", "/openapi/v1/bots/{bot_id}/editor-request-policy"): {
        "request": lambda client: client.patch(
            "/openapi/v1/bots/bot-1/editor-request-policy",
            params={"entity_id": "owner-1"},
            json={"auto_approve": True},
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/{bot_id}/editor-requests"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/bot-1/editor-requests",
            params={"owner_id": "owner-1"},
            json={"reason": "joint editing"},
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/work-orders"): {
        "request": lambda client: client.get("/openapi/v1/bots/work-orders"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/work-orders/{work_order_id}"): {
        "request": lambda client: client.get("/openapi/v1/bots/work-orders/1"),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/work-orders/events"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/work-orders/events",
            json={
                "event_category": "NOTICE",
                "biz_type": "TEST",
                "biz_id": "1",
                "event_type": "SPACE_JOIN_REVIEWED",
                "recipient_user_ids": ["u-2"],
                "title": "notice",
            },
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/work-orders/{work_order_id}/approval"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/work-orders/1/approval",
            json={"decision": "APPROVED", "review_remark": "approve"},
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/work-order-notifications/unread-count"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/work-order-notifications/unread-count"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("GET", "/openapi/v1/bots/work-order-notifications/{notification_id}"): {
        "request": lambda client: client.get(
            "/openapi/v1/bots/work-order-notifications/1"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/work-order-notifications/read-all"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/work-order-notifications/read-all"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
    ("POST", "/openapi/v1/bots/work-order-notifications/{notification_id}/read"): {
        "request": lambda client: client.post(
            "/openapi/v1/bots/work-order-notifications/1/read"
        ),
        "assert_starved": lambda response: response.status_code == 404,
    },
}
