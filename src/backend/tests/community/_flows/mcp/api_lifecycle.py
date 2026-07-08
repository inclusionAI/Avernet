"""MCP route-A API flow definitions for singlebox."""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep


MCP_FACADE_FLOWS: list[FlowCase] = [
    FlowCase(
        name="singlebox-mcp-local-facade-and-adapter-sync",
        covers=["mcp"],
        steps=[
            FlowStep(method="GET", path="/api/mcp/market/list", expect={"success": True, "data": []}),
            FlowStep(method="GET", path="/api/mcp/tenants", expect={"success": True, "data": []}),
            FlowStep(
                method="GET",
                path="/api/mcp/market/permission",
                query={"server_code": "mcp.singlebox.e2e", "user_id": "e2e_user"},
                expect={"success": True, "has_permission": False},
            ),
            FlowStep(
                method="POST",
                path="/api/mcp/market/permission/apply",
                body={"server_code": "mcp.singlebox.e2e", "tool_list": ["search"], "reason": "e2e"},
                expect={"success": True, "server_code": "mcp.singlebox.e2e"},
                extract={"mcp_server_code": "server_code"},
            ),
            FlowStep(
                method="GET",
                path="/api/mcp/market/permission",
                query={"server_code": "{mcp_server_code}", "user_id": "e2e_user"},
                expect={"success": True},
            ),
        ],
    )
]


MCP_USER_CONFIG_FANOUT_FLOWS: list[FlowCase] = [
    FlowCase(
        name="singlebox-mcp-user-config-fanout",
        covers=["mcp"],
        steps=[
            FlowStep(
                method="POST",
                path="/api/mcp/user/config",
                query={"entity_id": "e2e_user", "entity_type": "staff"},
                body={
                    "server_code": "mcp.singlebox.fanout",
                    "headers": {"x-singlebox-token": "updated"},
                    "endpoint_env": "PRE",
                    "transport_protocol": "STREAMABLE_HTTP",
                },
                expect={
                    "success": True,
                    "data": {
                        "server_code": "mcp.singlebox.fanout",
                        "endpoint_env": "PRE",
                        "transport_protocol": "STREAMABLE_HTTP",
                        "sync_results": [
                            {"bot_id": "bot-mcp-fanout-a", "synced": True},
                            {"bot_id": "bot-mcp-fanout-b", "synced": True},
                            {"bot_id": "bot-mcp-fanout-c", "synced": False},
                        ],
                    },
                },
            ),
            FlowStep(
                method="GET",
                path="/api/mcp/user/config",
                query={"server_code": "mcp.singlebox.fanout"},
                expect={
                    "success": True,
                    "data": {
                        "server_code": "mcp.singlebox.fanout",
                        "headers": {"x-singlebox-token": "updated"},
                        "endpoint_env": "PRE",
                        "transport_protocol": "STREAMABLE_HTTP",
                        "has_config": True,
                    },
                },
            ),
        ],
    )
]


MCP_SKILLSET_SYNC_FLOWS: list[FlowCase] = [
    FlowCase(
        name="singlebox-skillset-add-mcp-sync",
        covers=["skill_center", "mcp"],
        steps=[
            FlowStep(
                method="POST",
                path="/api/skillsets",
                body={
                    "name": "E2E MCP SkillSet",
                    "user_id": "e2e_user",
                    "bot_id": "bot-mcp-skillset",
                },
                expect_status=200,
                expect={"success": True, "data": {"name": "E2E MCP SkillSet"}},
                extract={"skill_set_id": "data.id"},
            ),
            FlowStep(
                method="POST",
                path="/api/skillsets/{skill_set_id}/mcps",
                query={
                    "entity_id": "e2e_user",
                    "entity_type": "staff",
                    "bot_id": "bot-mcp-skillset",
                    "engine_type": "openclaw",
                },
                body={"server_code": "mcp.singlebox.skillset", "user_id": "e2e_user"},
                expect={
                    "success": True,
                    "server_code": "mcp.singlebox.skillset",
                },
            ),
            FlowStep(
                method="GET",
                path="/api/skillsets/{skill_set_id}/mcps",
                query={
                    "entity_id": "e2e_user",
                    "entity_type": "staff",
                    "bot_id": "bot-mcp-skillset",
                    "engine_type": "openclaw",
                },
                expect={
                    "success": True,
                    "data": [{"server_code": "mcp.singlebox.skillset"}],
                },
            ),
        ],
    )
]


MCP_DEVICE_ALIVE_FLOWS: list[FlowCase] = [
    FlowCase(
        name="singlebox-device-alive-mcp-resync",
        covers=["devices", "mcp"],
        steps=[
            FlowStep(
                method="POST",
                path="/api/v1/devices/callback/alive",
                body={"device_id": "local-bot-mcp-alive"},
                headers={"Authorization": "Bearer token-mcp-alive"},
                expect={"success": True, "error_code": 200},
            ),
        ],
    )
]


MCP_LIVE_SYNC_FLOWS: list[FlowCase] = [
    FlowCase(
        name="singlebox-mcp-live-user-config-sync",
        covers=["mcp", "devices"],
        steps=[
            FlowStep(
                method="POST",
                path="/api/mcp/user/config",
                query={
                    "entity_id": "{entity_id}",
                    "entity_type": "staff",
                    "bot_id": "{bot_id}",
                },
                body={
                    "server_code": "{server_code}",
                    "headers": {"x-singlebox-token": "updated"},
                    "endpoint_env": "PRE",
                    "transport_protocol": "STREAMABLE_HTTP",
                },
                expect={"success": True},
                extract={"sync_results": "data.sync_results"},
            ),
        ],
    )
]


MCP_FLOWS: list[FlowCase] = [
    *MCP_FACADE_FLOWS,
    *MCP_USER_CONFIG_FANOUT_FLOWS,
    *MCP_SKILLSET_SYNC_FLOWS,
    *MCP_DEVICE_ALIVE_FLOWS,
    *MCP_LIVE_SYNC_FLOWS,
]
