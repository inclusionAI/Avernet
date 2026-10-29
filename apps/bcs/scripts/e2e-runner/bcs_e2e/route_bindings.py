"""Finite route and optional cross-environment Case bindings."""
from __future__ import annotations

import re

ROUTES = {
    "group.create": ("POST", "/openapi/v1/collaboration/groups", "human_or_owned_bot"),
    "group.read": ("GET", "/openapi/v1/collaboration/groups/{group_id}", "human_or_owned_bot"),
    "group.update": ("PATCH", "/openapi/v1/collaboration/groups/{group_id}", "human_or_owned_bot"),
    "group.delete": ("DELETE", "/openapi/v1/collaboration/groups/{group_id}", "human_or_owned_bot"),
    "group.verify_deleted": ("GET", "/openapi/v1/collaboration/groups/{group_id}", "human_or_owned_bot"),
    "session.create": ("POST", "/openapi/v1/collaboration/groups/{group_id}/sessions", "human_or_owned_bot"),
    "session.read": ("GET", "/openapi/v1/collaboration/sessions/{session_id}", "human_or_owned_bot"),
    "session.update": ("PATCH", "/openapi/v1/collaboration/sessions/{session_id}", "human_or_owned_bot"),
    "session.messages": ("GET", "/openapi/v1/collaboration/sessions/{session_id}/messages?limit=50", "human_or_owned_bot"),
    "session.token": ("POST", "/openapi/v1/collaboration/sessions/{session_id}/token", "human_or_owned_bot"),
    "session.delete": ("DELETE", "/openapi/v1/collaboration/sessions/{session_id}", "human_or_owned_bot"),
    "session.verify_deleted": ("GET", "/openapi/v1/collaboration/sessions/{session_id}", "human_or_owned_bot"),
}


def validate_route_bindings(value: object, *, api_face: str) -> dict:
    from .profile import ConfigError, object_keys, text
    if not isinstance(value, dict) or set(value) != set(ROUTES):
        raise ConfigError("route_bindings: all initial pilot operations are required")
    result = {}
    for operation, (method, path, auth) in ROUTES.items():
        binding = object_keys(value[operation], {"method", "path", "api_face", "auth", "response", "traceparent"},
                              f"route_bindings.{operation}")
        if binding["method"] != method or binding["path"] != path:
            raise ConfigError(f"route_bindings.{operation}: method/path is not a declared contract")
        if (binding["api_face"] != api_face or binding["auth"] != auth
                or binding["response"] != "public-envelope"
                or binding["traceparent"] not in {"disabled", "w3c"}):
            raise ConfigError(f"route_bindings.{operation}: identity or response contract mismatch")
        text(binding["path"], f"route_bindings.{operation}.path")
        result[operation] = dict(binding)
    return result


FRIEND_REQUESTS_READ_PATH = "/openapi/v1/collaboration/friend-connections/requests"
FRIEND_CONNECTIONS_READ_PATH = "/openapi/v1/collaboration/friend-connections"
NETWORK_BOTS_QUERY_READ_PATH = "/openapi/v1/collaboration/bots/query"
NETWORK_BOTS_MINE_READ_PATH = "/openapi/v1/collaboration/bots/mine?offset=0&limit=20"
NETWORK_ACTOR_LIST_READ_PREFIX = "/openapi/v1/collaboration/bots/"
NETWORK_ACTOR_LIST_READ_SUFFIX = "/candidates?purpose=discovery&offset=0&limit=20"
NETWORK_ACTOR_SEARCH_READ_PREFIX = "/api/v1/collaboration/bots/"
NETWORK_ACTOR_SEARCH_READ_SUFFIX = "/candidates/search?q=%E4%BA%A7%E5%93%81&purpose=discovery"
NETWORK_BOTS_PAGED_READ_PREFIX = "/openapi/v1/collaboration/bots/"
NETWORK_BOTS_PAGED_READ_SUFFIX = "/candidates?purpose=discovery&offset=0&limit=5"
NETWORK_ACTOR_STATUS_PATH_PREFIX = "/openapi/v1/collaboration/bots/"
CHANNEL_BINDINGS_PATH = "/openapi/v1/collaboration/channels/bindings"
CHANNEL_BINDINGS_BY_TARGET_PREFIX = (
    "/openapi/v1/collaboration/channels/bindings/by-target?"
    "target_type=bot&target_id="
)
STRUCTURED_TEMPLATES_LIST_PATH = "/api/v1/collaboration/templates?lang=zh-CN"
STRUCTURED_TEMPLATES_DETAIL_PATH = (
    "/api/v1/collaboration/templates/single-bot-guided-answer?lang=zh-CN&format=json"
)
STRUCTURED_GROUPS_CREATE_PATH = "/api/v1/groups"
STRUCTURED_GROUP_PATH = "/api/v1/groups/{group_id}"
STRUCTURED_GROUP_DEFINITION_PATH = (
    "/api/v1/groups/{group_id}/collaboration-definition"
)
STRUCTURED_GROUP_DEFINITION_UPGRADE_PATH = (
    "/api/v1/groups/{group_id}/collaboration-definition/upgrade"
)
STRUCTURED_GROUP_RUN_START_PATH = (
    "/api/v1/groups/{group_id}/state-machine-runs"
)
STRUCTURED_RUN_PATH = "/api/v1/state-machine-runs/{run_id}"
STRUCTURED_RUN_GRAPH_PATH = "/api/v1/state-machine-runs/{run_id}/graph"
STRUCTURED_RUN_NODE_PATH = (
    "/api/v1/state-machine-runs/{run_id}/nodes/answer"
)
STRUCTURED_RUN_PENDING_HUMAN_PATH = (
    "/api/v1/state-machine-runs/{run_id}/pending-human-nodes"
)
STRUCTURED_RUN_HUMAN_RESPOND_PATH = (
    "/api/v1/state-machine-runs/{run_id}/nodes/answer/respond"
)
STRUCTURED_RUN_CANCEL_PATH = "/api/v1/state-machine-runs/{run_id}/cancel"
STRUCTURED_RUN_RERUN_PATH = "/api/v1/state-machine-runs/{run_id}/reruns"
STRUCTURED_GROUP_DELETE_PATH = (
    "/api/v1/groups/{group_id}?bot_id={driver_actor_id}"
)
STRUCTURED_DIRECT_GROUPS_CREATE_PATH = "/groups"
STRUCTURED_DIRECT_GROUP_PATH = "/groups/{group_id}"
STRUCTURED_DIRECT_GROUP_DEFINITION_PATH = (
    "/groups/{group_id}/collaboration-definition"
)
STRUCTURED_DIRECT_GROUP_DEFINITION_UPGRADE_PATH = (
    "/groups/{group_id}/collaboration-definition/upgrade"
)
STRUCTURED_DIRECT_GROUP_RUN_START_PATH = "/groups/{group_id}/state-machine-runs"
STRUCTURED_DIRECT_RUN_PATH = "/state-machine-runs/{run_id}"
STRUCTURED_DIRECT_RUN_GRAPH_PATH = "/state-machine-runs/{run_id}/graph"
STRUCTURED_DIRECT_RUN_NODE_PATH = "/state-machine-runs/{run_id}/nodes/answer"
STRUCTURED_DIRECT_RUN_PENDING_HUMAN_PATH = (
    "/state-machine-runs/{run_id}/pending-human-nodes"
)
STRUCTURED_DIRECT_RUN_HUMAN_RESPOND_PATH = (
    "/state-machine-runs/{run_id}/nodes/answer/respond"
)
STRUCTURED_DIRECT_RUN_CANCEL_PATH = "/state-machine-runs/{run_id}/cancel"
STRUCTURED_DIRECT_RUN_RERUN_PATH = "/state-machine-runs/{run_id}/reruns"
STRUCTURED_DIRECT_GROUP_DELETE_PATH = (
    "/groups/{group_id}?bot_id={driver_actor_id}"
)
STRUCTURED_DIRECT_PATHS = {
    "structured.group.create": STRUCTURED_DIRECT_GROUPS_CREATE_PATH,
    "structured.group.read": STRUCTURED_DIRECT_GROUP_PATH,
    "structured.definition.read": STRUCTURED_DIRECT_GROUP_DEFINITION_PATH,
    "structured.definition.patch": STRUCTURED_DIRECT_GROUP_DEFINITION_PATH,
    "structured.definition.upgrade.missing": STRUCTURED_DIRECT_GROUP_DEFINITION_UPGRADE_PATH,
    "structured.run.start": STRUCTURED_DIRECT_GROUP_RUN_START_PATH,
    "structured.run.read": STRUCTURED_DIRECT_RUN_PATH,
    "structured.run.graph.read": STRUCTURED_DIRECT_RUN_GRAPH_PATH,
    "structured.run.node.read": STRUCTURED_DIRECT_RUN_NODE_PATH,
    "structured.run.pending.human.nodes": STRUCTURED_DIRECT_RUN_PENDING_HUMAN_PATH,
    "structured.run.human.response.denied": STRUCTURED_DIRECT_RUN_HUMAN_RESPOND_PATH,
    "structured.run.cancel": STRUCTURED_DIRECT_RUN_CANCEL_PATH,
    "structured.run.rerun.aborted.denied": STRUCTURED_DIRECT_RUN_RERUN_PATH,
    "structured.group.delete": STRUCTURED_DIRECT_GROUP_DELETE_PATH,
}

def structured_case_path(profile: dict, operation: str) -> str | None:
    """Return the exact station face for a reviewed structured Case operation."""
    if not operation.startswith("structured."):
        return None
    if profile.get("entrypoint", {}).get("kind") == "bcs-direct":
        return STRUCTURED_DIRECT_PATHS[operation]
    return _EDGE_CASES[operation][1]

def structured_case_response(profile: dict, operation: str) -> str | None:
    """Return the reviewed wire response mode for a structured Case operation."""
    if not operation.startswith("structured."):
        return None
    if profile.get("entrypoint", {}).get("kind") == "bcs-direct":
        return "plain-json"
    return "public-envelope"

EDGE_BOT_SEARCH_NAME_READ_PREFIX = "/api/v1/collaboration/bots/"
EDGE_BOT_SEARCH_NAME_READ_SUFFIX = "/candidates/search?q=CEO&purpose=discovery"
EDGE_BOT_SEARCH_EMPTY_READ_SUFFIX = "/candidates/search?purpose=discovery"
EDGE_ADMISSION_PATH_PREFIX = "/bots/"
EDGE_ADMISSION_PATH_SUFFIX = "/admission"
NETWORK_PLATFORM_ASSET_PREFIX = "/api/v1/collaboration/assets/bcsPanel/"
FRIEND_CONNECTION_DECISION_CREATE_PATH = "/openapi/v1/collaboration/friend-connections/requests"
FRIEND_CONNECTION_REQUESTS_READ_TEMPLATE = (
    "/openapi/v1/collaboration/friend-connections/requests"
    "?actor_type=bot&actor_id={actor_id}"
    "&direction=received&status=pending&page=1&page_size=20"
)
FRIEND_CONNECTION_REJECT_PATH = (
    "/openapi/v1/collaboration/friend-connections/requests/{request_id}/reject"
)
FRIEND_CONNECTION_ACCEPT_PATH = (
    "/openapi/v1/collaboration/friend-connections/requests/{request_id}/accept"
)
FRIEND_CONNECTION_DRIVER_CONNECTIONS_TEMPLATE = (
    "/openapi/v1/collaboration/friend-connections"
    "?actor_type=bot&actor_id={actor_id}"
    "&target_type=bot&page=1&page_size=20"
)
FRIEND_CONNECTION_PUBLIC_ACCEPT_CREATE_PATH = FRIEND_CONNECTION_DECISION_CREATE_PATH
NETWORK_REGISTER_TOKEN_PATH = "/openapi/v1/collaboration/register/token"
NETWORK_REGISTER_CREATE_PATH = "/openapi/v1/collaboration/register"
NETWORK_REGISTER_READBACK_PATH = "/openapi/v1/collaboration/bots/{bot_uuid}"
EVENT_SUBSCRIPTION_LIST_PATH = (
    "/openapi/v1/collaboration/event-subscriptions"
    "?scope_type=group&scope_id={group_id}&limit=20")
EVENT_SUBSCRIPTION_PATH = (
    "/openapi/v1/collaboration/event-subscriptions/{subscription_id}")
EVENT_SUBSCRIPTION_TEST_PATH = (
    "/openapi/v1/collaboration/event-subscriptions/{subscription_id}:test")
EVENT_SUBSCRIPTION_DELIVERIES_PATH = (
    "/openapi/v1/collaboration/event-subscriptions/{subscription_id}"
    "/deliveries?status=succeeded&limit=20")
EVENT_DELIVERY_PATH = (
    "/openapi/v1/collaboration/event-deliveries/{delivery_id}")
EVENT_SUBSCRIPTION_DELETE_PATH = (
    "/openapi/v1/collaboration/event-subscriptions/{subscription_id}"
    "?revision=3")
FRIEND_MUTATION_CREATE_PATH = (
    "/openapi/v1/collaboration/friend-connections/requests"
)
FRIEND_MUTATION_ACCEPT_PATH = (
    "/openapi/v1/collaboration/friend-connections/requests/{request_id}/accept"
)
FRIEND_MUTATION_REJECT_PATH = (
    "/openapi/v1/collaboration/friend-connections/requests/{request_id}/reject"
)
FRIEND_MUTATION_CANCEL_PATH = (
    "/openapi/v1/collaboration/friend-connections/requests/{request_id}/cancel"
)
FRIEND_MUTATION_REQUESTS_SENT_PATH = (
    "/openapi/v1/collaboration/friend-connections/requests"
    "?actor_type=bot&actor_id={actor_id}&direction=sent"
    "&status=pending&page=1&page_size=20"
)
FRIEND_MUTATION_REQUESTS_RECEIVED_PATH = (
    "/openapi/v1/collaboration/friend-connections/requests"
    "?actor_type=bot&actor_id={actor_id}&direction=received"
    "&status=pending&page=1&page_size=20"
)
FRIEND_MUTATION_REQUESTS_ALL_PATH = (
    "/openapi/v1/collaboration/friend-connections/requests"
    "?actor_type=bot&actor_id={actor_id}&direction=all"
    "&status=pending&page=1&page_size=20"
)
FRIEND_MUTATION_CONNECTIONS_PATH = (
    "/openapi/v1/collaboration/friend-connections"
    "?actor_type=bot&actor_id={actor_id}&target_type=bot&page=1&page_size=20"
)
FRIEND_MUTATION_BOT_DETAIL_PATH = "/openapi/v1/collaboration/bots/{bot_uuid}"
FRIEND_MUTATION_DISCOVER_PATH = "/openapi/v1/bots/discover?collaborate_bot={target_actor_id}"
FRIEND_MUTATION_REVOKE_PATH = (
    "/openapi/v1/collaboration/friend-connections"
    "?target_actor_type=bot&target_actor_id={target_actor_id}"
)
STATION_REGISTER_TOKEN_PATH = "/register/token"
STATION_REGISTER_BOT_PATH = "/register?token={register_token}&bot-name={bot_name}"
STATION_LEGACY_REVOKE_PATH = "/collaboration/friend-connections/{actor}"
STATION_BOT_DISPOSE_PATH = "/bots/{bot_id}"
STATION_ADMISSION_PATH = "/bots/{bot_uuid}/admission?actor={actor_id}"
STATION_DISCOVER_COLLABORATE_PATH = "/bots/discover?collaborate_bot={target_actor_id}"
STATION_DISCOVER_ROLE_PATH = "/bots/discover?role=contributor"
STATION_DISCOVER_ORGANIZATION_PATH = "/bots/discover?organization_code=DEMO"

_FRIEND_MUTATION_GATEWAY_OPS = {
    "friend_connection_mutation.create",
    "friend_connection_mutation.accept",
    "friend_connection_mutation.reject",
    "friend_connection_mutation.cancel",
    "friend_connection_mutation.requests_sent.read",
    "friend_connection_mutation.requests_received.read",
    "friend_connection_mutation.requests_all.read",
    "friend_connection_mutation.connections.read",
    "friend_connection_mutation.bot_detail.read",
    "friend_connection_mutation.visibility.write",
    "friend_connection_mutation.revoke",
    "friend_connection_mutation.discover_denied.read",
}
_FRIEND_MUTATION_STATION_OPS = {
    "friend_connection_mutation.register_token.read",
    "friend_connection_mutation.register_bot.create",
    "friend_connection_mutation.legacy_revoke",
    "friend_connection_mutation.station_bot_delete",
    "friend_connection_mutation.station_bot_read",
    "friend_connection_mutation.station_admission.read",
    "friend_connection_mutation.station_discover_collaborate.read",
    "friend_connection_mutation.station_discover_role_no_org.read",
    "friend_connection_mutation.station_discover_org_no_requester.read",
}
_FRIEND_MUTATION_PAIR_OPS = {
    "friend_connection_mutation.create",
    "friend_connection_mutation.accept",
    "friend_connection_mutation.reject",
    "friend_connection_mutation.cancel",
}
_FRIEND_MUTATION_ACTOR_OPS = {
    "friend_connection_mutation.requests_sent.read",
    "friend_connection_mutation.requests_received.read",
    "friend_connection_mutation.requests_all.read",
    "friend_connection_mutation.connections.read",
}
_FRIEND_MUTATION_TARGET_OPS = {
    "friend_connection_mutation.bot_detail.read",
    "friend_connection_mutation.visibility.write",
    "friend_connection_mutation.revoke",
    "friend_connection_mutation.discover_denied.read",
    "friend_connection_mutation.legacy_revoke",
    "friend_connection_mutation.station_discover_collaborate.read",
}


def actor_list_read_path(profile: dict) -> str:
    """Return the fixed Human-managed candidate page for the dedicated driver Bot."""
    from urllib.parse import quote
    from .profile import identifier
    driver = identifier(profile["actors"]["driver"]["bot_id"], "actors.driver.bot_id")
    return NETWORK_ACTOR_LIST_READ_PREFIX + quote(driver, safe="") + NETWORK_ACTOR_LIST_READ_SUFFIX


def actor_search_read_path(profile: dict) -> str:
    """Return the exact station internal candidate-search route and original query."""
    from urllib.parse import quote
    from .profile import identifier
    driver = identifier(profile["actors"]["driver"]["bot_id"], "actors.driver.bot_id")
    return NETWORK_ACTOR_SEARCH_READ_PREFIX + quote(driver, safe="") + NETWORK_ACTOR_SEARCH_READ_SUFFIX


def bots_paged_read_path(profile: dict) -> str:
    """Return the station Bot page from the exact owned Human-actor perspective."""
    from urllib.parse import quote
    from .profile import identifier
    user_id = identifier(profile["human"]["user_id"], "human.user_id")
    return NETWORK_BOTS_PAGED_READ_PREFIX + quote("human_" + user_id, safe="") + NETWORK_BOTS_PAGED_READ_SUFFIX


def actor_status_path(profile: dict) -> str:
    """Return the exact Human-owned driver-Bot lifecycle status route."""
    from urllib.parse import quote
    from .profile import identifier
    driver = identifier(profile["actors"]["driver"]["bot_id"], "actors.driver.bot_id")
    return NETWORK_ACTOR_STATUS_PATH_PREFIX + quote(driver, safe="")


def _edge_admission_path(profile: dict, *, actor_kind: bool) -> str:
    """Return one exact dedicated-driver/Bare-Human admission route."""
    from urllib.parse import quote
    from .profile import identifier
    driver = identifier(profile["actors"]["driver"]["bot_id"], "actors.driver.bot_id")
    human = identifier(profile["human"]["user_id"], "human.user_id")
    actor = human if actor_kind else "human_" + human
    return (EDGE_ADMISSION_PATH_PREFIX + quote(driver, safe="")
            + EDGE_ADMISSION_PATH_SUFFIX + "?actor=" + quote(actor, safe="")
            + ("&actor_kind=human" if actor_kind else ""))


def _actor_path(template: str, actor_id: str) -> str:
    from urllib.parse import quote
    from .profile import identifier
    return template.replace("{actor_id}", quote(identifier(actor_id, "bound actor ID"), safe=""))


def friend_connection_member_requests_path(profile: dict) -> str:
    return _actor_path(FRIEND_CONNECTION_REQUESTS_READ_TEMPLATE,
                       profile["actors"]["member"]["bot_id"])


def friend_connection_driver_requests_path(profile: dict) -> str:
    return _actor_path(FRIEND_CONNECTION_REQUESTS_READ_TEMPLATE,
                       profile["actors"]["driver"]["bot_id"])


def friend_connection_reject_path(profile: dict) -> str:
    return FRIEND_CONNECTION_REJECT_PATH


def friend_connection_accept_path(profile: dict) -> str:
    return FRIEND_CONNECTION_ACCEPT_PATH


def friend_connection_driver_connections_path(profile: dict) -> str:
    return _actor_path(FRIEND_CONNECTION_DRIVER_CONNECTIONS_TEMPLATE,
                       profile["actors"]["driver"]["bot_id"])


def friend_connection_public_accept_driver_connections_path(profile: dict) -> str:
    return _actor_path(FRIEND_CONNECTION_DRIVER_CONNECTIONS_TEMPLATE,
                       profile["actors"]["driver"]["bot_id"])


def edge_admission_human_actor_path(profile: dict) -> str:
    return _edge_admission_path(profile, actor_kind=False)


def edge_admission_actor_kind_path(profile: dict) -> str:
    return _edge_admission_path(profile, actor_kind=True)


def channel_bindings_by_target_path(profile: dict) -> str:
    """Return the exact dedicated-driver and uninstalled-channel lookup."""
    from urllib.parse import quote
    from .profile import identifier
    driver = identifier(profile["actors"]["driver"]["bot_id"], "actors.driver.bot_id")
    from .local_channel_public_contract import UNINSTALLED_CHANNEL_TYPE
    return (CHANNEL_BINDINGS_BY_TARGET_PREFIX + quote(driver, safe="")
            + "&channel_type=" + quote(UNINSTALLED_CHANNEL_TYPE, safe=""))


def edge_bot_search_name_path(profile: dict) -> str:
    """Return the reviewed name-fuzzy Edge candidate-search route."""
    from urllib.parse import quote
    from .profile import identifier
    driver = identifier(profile["actors"]["driver"]["bot_id"], "actors.driver.bot_id")
    return EDGE_BOT_SEARCH_NAME_READ_PREFIX + quote(driver, safe="") + EDGE_BOT_SEARCH_NAME_READ_SUFFIX


def edge_bot_search_empty_path(profile: dict) -> str:
    """Return the reviewed empty-query Edge candidate-search route."""
    from urllib.parse import quote
    from .profile import identifier
    driver = identifier(profile["actors"]["driver"]["bot_id"], "actors.driver.bot_id")
    return EDGE_BOT_SEARCH_NAME_READ_PREFIX + quote(driver, safe="") + EDGE_BOT_SEARCH_EMPTY_READ_SUFFIX


_EDGE_CASES = {
    "friend_requests.read": ("GET", FRIEND_REQUESTS_READ_PATH, "authenticated_bot"),
    "friend_connections.read": ("GET", FRIEND_CONNECTIONS_READ_PATH, "authenticated_bot"),
    "edge_admission_human_actor.read": ("GET", None, "unauthenticated"),
    "edge_admission_actor_kind.read": ("GET", None, "unauthenticated"),
    "network_bot_query.read": ("POST", NETWORK_BOTS_QUERY_READ_PATH, "human_cookie"),
    "network_bot_mine.read": ("GET", NETWORK_BOTS_MINE_READ_PATH, "human_cookie"),
    "network_actor_list.read": ("GET", None, "human_cookie"),
    "network_actor_search.read": ("GET", None, "human_cookie"),
    "network_bots_paged.read": ("GET", None, "human_cookie"),
    "network_actor_status.read": ("GET", None, "human_cookie"),
    "network_actor_status.write": ("PATCH", None, "human_cookie"),
    "channel_bindings.create_denied": ("POST", CHANNEL_BINDINGS_PATH, "human_cookie"),
    "channel_bindings.list": ("GET", CHANNEL_BINDINGS_PATH, "human_cookie"),
    "channel_bindings.by_target": ("GET", None, "human_cookie"),
    "structured_templates.list": ("GET", STRUCTURED_TEMPLATES_LIST_PATH, "human_cookie"),
    "structured_templates.detail": ("GET", STRUCTURED_TEMPLATES_DETAIL_PATH, "human_cookie"),
    "structured.group.create": ("POST", STRUCTURED_GROUPS_CREATE_PATH, "authenticated_bot"),
    "structured.group.read": ("GET", STRUCTURED_GROUP_PATH, "human_cookie"),
    "structured.definition.read": ("GET", STRUCTURED_GROUP_DEFINITION_PATH, "human_cookie"),
    "structured.definition.patch": ("PATCH", STRUCTURED_GROUP_DEFINITION_PATH, "human_cookie"),
    "structured.definition.upgrade.missing": ("POST", STRUCTURED_GROUP_DEFINITION_UPGRADE_PATH, "human_cookie"),
    "structured.run.start": ("POST", STRUCTURED_GROUP_RUN_START_PATH, "human_cookie"),
    "structured.run.read": ("GET", STRUCTURED_RUN_PATH, "human_cookie"),
    "structured.run.graph.read": ("GET", STRUCTURED_RUN_GRAPH_PATH, "human_cookie"),
    "structured.run.node.read": ("GET", STRUCTURED_RUN_NODE_PATH, "human_cookie"),
    "structured.run.pending.human.nodes": ("GET", STRUCTURED_RUN_PENDING_HUMAN_PATH, "human_cookie"),
    "structured.run.human.response.denied": ("POST", STRUCTURED_RUN_HUMAN_RESPOND_PATH, "human_cookie"),
    "structured.run.cancel": ("POST", STRUCTURED_RUN_CANCEL_PATH, "human_cookie"),
    "structured.run.rerun.aborted.denied": ("POST", STRUCTURED_RUN_RERUN_PATH, "human_cookie"),
    "structured.group.delete": ("DELETE", STRUCTURED_GROUP_DELETE_PATH, "human_cookie"),
    "edge_bot_search_name.read": ("GET", None, "human_cookie"),
    "edge_bot_search_empty.read": ("GET", None, "human_cookie"),
    "network_platform_asset.read": ("GET", None, "unauthenticated"),
    "friend_connection_decision.reject.create": ("POST", FRIEND_CONNECTION_DECISION_CREATE_PATH, "human_cookie"),
    "friend_connection_decision.accept.create": ("POST", FRIEND_CONNECTION_DECISION_CREATE_PATH, "human_cookie"),
    "friend_connection_decision.member_requests.read": ("GET", None, "human_cookie"),
    "friend_connection_decision.reject": ("POST", None, "human_cookie"),
    "friend_connection_decision.driver_requests.read": ("GET", None, "human_cookie"),
    "friend_connection_decision.accept": ("POST", None, "human_cookie"),
    "friend_connection_decision.driver_connections.read": ("GET", None, "human_cookie"),
    "friend_connection_public_accept.create": ("POST", FRIEND_CONNECTION_PUBLIC_ACCEPT_CREATE_PATH, "human_cookie"),
    "friend_connection_public_accept.driver_connections.read": ("GET", None, "human_cookie"),
    "register.token.read": ("GET", NETWORK_REGISTER_TOKEN_PATH, "human_cookie"),
    "register.bot.create": ("POST", NETWORK_REGISTER_CREATE_PATH, "register_token"),
    "register.bot.readback": ("GET", NETWORK_REGISTER_READBACK_PATH, "human_cookie"),
    "webhook.subscription.list": ("GET", EVENT_SUBSCRIPTION_LIST_PATH, "gateway_principal"),
    "webhook.subscription.read": ("GET", EVENT_SUBSCRIPTION_PATH, "gateway_principal"),
    "webhook.subscription.test": ("POST", EVENT_SUBSCRIPTION_TEST_PATH, "gateway_principal"),
    "webhook.subscription.disable": ("PATCH", EVENT_SUBSCRIPTION_PATH, "gateway_principal"),
    "webhook.subscription.enable": ("PATCH", EVENT_SUBSCRIPTION_PATH, "gateway_principal"),
    "webhook.subscription.deliveries.list": ("GET", EVENT_SUBSCRIPTION_DELIVERIES_PATH, "gateway_principal"),
    "webhook.delivery.read": ("GET", EVENT_DELIVERY_PATH, "gateway_principal"),
    "webhook.subscription.delete": ("DELETE", EVENT_SUBSCRIPTION_DELETE_PATH, "gateway_principal"),
    "friend_connection_mutation.create": ("POST", FRIEND_MUTATION_CREATE_PATH, "human_cookie"),
    "friend_connection_mutation.accept": ("POST", FRIEND_MUTATION_ACCEPT_PATH, "human_cookie"),
    "friend_connection_mutation.reject": ("POST", FRIEND_MUTATION_REJECT_PATH, "human_cookie"),
    "friend_connection_mutation.cancel": ("POST", FRIEND_MUTATION_CANCEL_PATH, "human_cookie"),
    "friend_connection_mutation.requests_sent.read": ("GET", FRIEND_MUTATION_REQUESTS_SENT_PATH, "human_cookie"),
    "friend_connection_mutation.requests_received.read": ("GET", FRIEND_MUTATION_REQUESTS_RECEIVED_PATH, "human_cookie"),
    "friend_connection_mutation.requests_all.read": ("GET", FRIEND_MUTATION_REQUESTS_ALL_PATH, "human_cookie"),
    "friend_connection_mutation.connections.read": ("GET", FRIEND_MUTATION_CONNECTIONS_PATH, "human_cookie"),
    "friend_connection_mutation.bot_detail.read": ("GET", FRIEND_MUTATION_BOT_DETAIL_PATH, "human_cookie"),
    "friend_connection_mutation.visibility.write": ("PATCH", FRIEND_MUTATION_BOT_DETAIL_PATH, "human_cookie"),
    "friend_connection_mutation.revoke": ("DELETE", FRIEND_MUTATION_REVOKE_PATH, "human_cookie"),
    "friend_connection_mutation.discover_denied.read": ("GET", FRIEND_MUTATION_DISCOVER_PATH, "human_cookie"),
    "friend_connection_mutation.register_token.read": ("GET", STATION_REGISTER_TOKEN_PATH, "human_cookie"),
    "friend_connection_mutation.register_bot.create": ("POST", STATION_REGISTER_BOT_PATH, "register_token"),
    "friend_connection_mutation.legacy_revoke": ("DELETE", STATION_LEGACY_REVOKE_PATH, "human_cookie_owned_bot"),
    "friend_connection_mutation.station_bot_delete": ("DELETE", STATION_BOT_DISPOSE_PATH, "human_cookie_owned_bot"),
    "friend_connection_mutation.station_bot_read": ("GET", STATION_BOT_DISPOSE_PATH, "human_cookie_owned_bot"),
    "friend_connection_mutation.station_admission.read": ("GET", STATION_ADMISSION_PATH, "human_cookie"),
    "friend_connection_mutation.station_discover_collaborate.read": ("GET", STATION_DISCOVER_COLLABORATE_PATH, "human_cookie"),
    "friend_connection_mutation.station_discover_role_no_org.read": ("GET", STATION_DISCOVER_ROLE_PATH, "human_cookie"),
    "friend_connection_mutation.station_discover_org_no_requester.read": ("GET", STATION_DISCOVER_ORGANIZATION_PATH, "human_cookie"),
}
_EDGE_BINDING_FIELDS = {
    "method", "path", "api_face", "auth", "response", "traceparent",
}
_CALLER_BINDING_FIELDS = _EDGE_BINDING_FIELDS | {"caller_actor_id"}
_TARGETS_BINDING_FIELDS = _EDGE_BINDING_FIELDS | {"target_actor_ids"}
_TARGET_STATUS_BINDING_FIELDS = _EDGE_BINDING_FIELDS | {"target_actor_id"}
_CHANNEL_TARGET_BINDING_FIELDS = _EDGE_BINDING_FIELDS | {"target_actor_id"}
_PLATFORM_ASSET_BINDING_FIELDS = _EDGE_BINDING_FIELDS | {"asset_name", "asset_sha256"}
_FRIEND_CREATE_BINDING_FIELDS = _EDGE_BINDING_FIELDS | {"from_actor_id", "to_actor_id"}
_FRIEND_READER_BINDING_FIELDS = _EDGE_BINDING_FIELDS | {"actor_id"}
_FRIEND_DECISION_BINDING_FIELDS = _EDGE_BINDING_FIELDS | {"from_actor_id", "to_actor_id"}
_FRIEND_MUTATION_PAIR_FIELDS = _EDGE_BINDING_FIELDS | {"from_actor_id", "to_actor_id"}
_FRIEND_MUTATION_ACTOR_FIELDS = _EDGE_BINDING_FIELDS | {"actor_id"}
_FRIEND_MUTATION_TARGET_FIELDS = _EDGE_BINDING_FIELDS | {"target_actor_id"}
_ADMISSION_MUTATION_FIELDS = _EDGE_BINDING_FIELDS | {"target_actor_id", "actor_id"}


def _validate_platform_asset_url(value: str) -> None:
    """Bind only the reviewed same-origin asset route or an explicit HTTPS URL."""
    from urllib.parse import urlsplit
    from .profile import ConfigError
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise ConfigError("edge_case_bindings.network_platform_asset.read.path: invalid asset URL") from None
    if value.startswith("/"):
        if (not value.startswith(NETWORK_PLATFORM_ASSET_PREFIX) or ".." in value
                or parsed.query or parsed.fragment or not value.endswith(".js")
                or len(value) <= len(NETWORK_PLATFORM_ASSET_PREFIX)):
            raise ConfigError("edge_case_bindings.network_platform_asset.read.path: asset route is invalid")
        return
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or not parsed.path.endswith(".js") or ".." in parsed.path):
        raise ConfigError("edge_case_bindings.network_platform_asset.read.path: asset URL is invalid")


def validate_edge_case_bindings(value: object, profile: dict) -> dict:
    """Validate optional opt-in cross-environment Case bindings."""
    from .profile import ConfigError, identifier, object_keys, text
    entrypoint = profile["entrypoint"]
    if entrypoint["kind"] not in {"singlebox", "gateway", "bcs-direct"}:
        raise ConfigError("edge_case_bindings: only public-v1 Singlebox, gateway, or station-direct profiles support cross-environment Case bindings")
    if not isinstance(value, dict) or not value or set(value) - set(_EDGE_CASES):
        raise ConfigError("edge_case_bindings: unsupported cross-environment Case declaration")
    result = {}
    for operation, (method, path, auth) in _EDGE_CASES.items():
        if operation not in value:
            continue
        where = f"edge_case_bindings.{operation}"
        # The station Gateway front door resolves the dedicated Bot actor from
        # the browser context, the exact actor_id query or both. The community
        # Singlebox contract still uses the Bot Authorization bearer and its
        # caller-derived route.
        if (operation in {"friend_requests.read", "friend_connections.read",
                          "edge_admission_human_actor.read", "edge_admission_actor_kind.read"}
                and profile["environment"].get("tier") == "preprod"):
            auth = "human_cookie"
        if (operation == "structured.group.create"
                and profile["environment"].get("tier") == "preprod"):
            # Station-internal preprod ACE enforces the owning Human identity
            # on the structured Group create route: a dedicated-Bot bearer
            # only receives an SSO LOGIN envelope (decisionBy=ACE), while the
            # Human cookie creates the Group for real. The community Singlebox
            # contract keeps the authenticated Bot bearer (wire evidence
            # 20261031; probe Groups were deleted and 404-verified).
            auth = "human_cookie"
        if operation in {"structured_templates.list", "structured_templates.detail"}:
            expected_path = path
        elif operation.startswith("structured."):
            # The reviewed structured routes are Run-scoped templates; the
            # exact group/run IDs are substituted only at execution time and
            # every substitution is validated against the frozen template.
            expected_path = structured_case_path(profile, operation)
        elif operation == "network_actor_list.read":
            expected_path = actor_list_read_path(profile)
        elif operation == "network_actor_search.read":
            expected_path = actor_search_read_path(profile)
        elif operation == "network_bots_paged.read":
            expected_path = bots_paged_read_path(profile)
        elif operation in {"network_actor_status.read", "network_actor_status.write"}:
            expected_path = actor_status_path(profile)
        elif operation == "channel_bindings.by_target":
            expected_path = channel_bindings_by_target_path(profile)
        elif operation == "edge_bot_search_name.read":
            expected_path = edge_bot_search_name_path(profile)
        elif operation == "edge_bot_search_empty.read":
            expected_path = edge_bot_search_empty_path(profile)
        elif operation == "edge_admission_human_actor.read":
            expected_path = edge_admission_human_actor_path(profile)
        elif operation == "edge_admission_actor_kind.read":
            expected_path = edge_admission_actor_kind_path(profile)
        elif operation == "friend_connection_decision.member_requests.read":
            expected_path = friend_connection_member_requests_path(profile)
        elif operation == "friend_connection_decision.driver_requests.read":
            expected_path = friend_connection_driver_requests_path(profile)
        elif operation == "friend_connection_decision.reject":
            expected_path = friend_connection_reject_path(profile)
        elif operation == "friend_connection_decision.accept":
            expected_path = friend_connection_accept_path(profile)
        elif operation == "friend_connection_decision.driver_connections.read":
            expected_path = friend_connection_driver_connections_path(profile)
        elif operation == "friend_connection_public_accept.driver_connections.read":
            expected_path = friend_connection_public_accept_driver_connections_path(profile)
        elif operation == "register.bot.readback":
            # The reviewed registered-Bot read-back route carries the
            # registered Bot ID; the exact substitution is validated at
            # execution time against the frozen template.
            expected_path = path
        else:
            expected_path = path
        if operation in {"friend_requests.read", "friend_connections.read",
                         "network_actor_search.read", "edge_bot_search_name.read",
                         "edge_bot_search_empty.read"}:
            fields = _CALLER_BINDING_FIELDS
        elif operation == "structured.group.create":
            fields = _CALLER_BINDING_FIELDS
        elif operation == "network_bot_query.read":
            fields = _TARGETS_BINDING_FIELDS
        elif operation in {"network_actor_status.read", "network_actor_status.write"}:
            fields = _TARGET_STATUS_BINDING_FIELDS
        elif operation in {"channel_bindings.create_denied", "channel_bindings.by_target"}:
            fields = _CHANNEL_TARGET_BINDING_FIELDS
        elif operation == "network_platform_asset.read":
            fields = _PLATFORM_ASSET_BINDING_FIELDS
        elif operation in {"friend_connection_decision.reject.create",
                           "friend_connection_decision.accept.create",
                           "friend_connection_public_accept.create"}:
            fields = _FRIEND_CREATE_BINDING_FIELDS
        elif operation in {"friend_connection_decision.member_requests.read",
                           "friend_connection_decision.driver_requests.read",
                           "friend_connection_decision.driver_connections.read",
                           "friend_connection_public_accept.driver_connections.read"}:
            fields = _FRIEND_READER_BINDING_FIELDS
        elif operation in {"friend_connection_decision.reject",
                           "friend_connection_decision.accept"}:
            fields = _FRIEND_DECISION_BINDING_FIELDS
        elif operation in _FRIEND_MUTATION_PAIR_OPS:
            fields = _FRIEND_MUTATION_PAIR_FIELDS
        elif operation in _FRIEND_MUTATION_ACTOR_OPS:
            fields = _FRIEND_MUTATION_ACTOR_FIELDS
        elif operation in _FRIEND_MUTATION_TARGET_OPS:
            fields = _FRIEND_MUTATION_TARGET_FIELDS
        elif operation == "friend_connection_mutation.station_admission.read":
            fields = _ADMISSION_MUTATION_FIELDS
        else:
            fields = _EDGE_BINDING_FIELDS
        binding = object_keys(value[operation], fields, where)
        if operation in _FRIEND_MUTATION_GATEWAY_OPS:
            expected_api_face = "public-v1"
        elif operation in _FRIEND_MUTATION_STATION_OPS:
            expected_api_face = "station-legacy"
        else:
            expected_api_face = ("internal-v1"
                            if operation.startswith("structured.")
                            or operation in {"network_actor_search.read",
                                              "edge_bot_search_name.read",
                                              "edge_bot_search_empty.read",
                                              "network_platform_asset.read",
                                              "structured_templates.list",
                                              "structured_templates.detail",
                                              "edge_admission_human_actor.read",
                                              "edge_admission_actor_kind.read"}
                            else "public-v1")
        if operation in _FRIEND_MUTATION_GATEWAY_OPS:
            expected_response = "public-envelope"
        elif operation == "friend_connection_mutation.register_token.read":
            expected_response = "plain-json"
        elif operation == "friend_connection_mutation.register_bot.create":
            expected_response = "station-register-json"
        elif operation == "friend_connection_mutation.legacy_revoke":
            expected_response = "station-revoke-json"
        elif operation == "friend_connection_mutation.station_bot_delete":
            expected_response = "station-bot-delete-json"
        elif operation == "friend_connection_mutation.station_bot_read":
            expected_response = "station-bot-read-json"
        elif operation == "friend_connection_mutation.station_admission.read":
            expected_response = "admission-json"
        elif operation in {
                "friend_connection_mutation.station_discover_collaborate.read",
                "friend_connection_mutation.station_discover_role_no_org.read",
                "friend_connection_mutation.station_discover_org_no_requester.read"}:
            expected_response = "plain-json"
        else:
            expected_response = (
                "javascript-asset" if operation == "network_platform_asset.read"
                else "admission-json" if operation in {
                    "edge_admission_human_actor.read", "edge_admission_actor_kind.read"}
                else structured_case_response(profile, operation) or "public-envelope"
            )
        path_matches = (operation == "network_platform_asset.read"
                        or binding["path"] == expected_path)
        if (binding["method"] != method
                or not path_matches
                or binding["api_face"] != expected_api_face
                or binding["auth"] != auth
                or binding["response"] != expected_response
                or binding["traceparent"] not in {"disabled", "w3c"}):
            raise ConfigError(f"{where}: binding is not the declared API contract")
        if operation == "network_platform_asset.read":
            text(binding["path"], where + ".path")
            if (binding["asset_name"] != "bcsPanel"
                    or not re.fullmatch(r"[0-9a-f]{64}", binding["asset_sha256"] or "")):
                raise ConfigError(f"{where}: platform asset name or SHA-256 is invalid")
            _validate_platform_asset_url(binding["path"])
        if operation in {"edge_admission_human_actor.read", "edge_admission_actor_kind.read"}:
            if (entrypoint["api_face"] != "public-v1"
                    or entrypoint["manifest_face"] != "internal-v1"):
                raise ConfigError(f"{where}: native admission requires a public-v1 entrypoint with internal-v1 native face")
        elif operation in {"network_actor_search.read", "edge_bot_search_name.read",
                          "edge_bot_search_empty.read"}:
            # These reviewed internal search slices keep a public-v1
            # Gateway/Singlebox entrypoint and use the same origin's explicit
            # internal-v1 face from the dedicated driver Bot perspective.
            if entrypoint["api_face"] != "public-v1" or entrypoint["manifest_face"] != "internal-v1":
                raise ConfigError(f"{where}: internal search requires a public-v1 entrypoint with internal-v1 manifest face")
            if binding["caller_actor_id"] != profile["actors"]["driver"]["bot_id"]:
                raise ConfigError(f"{where}: viewpoint must be the dedicated driver Bot")
        elif operation in {"structured_templates.list", "structured_templates.detail"}:
            # The reviewed station template catalog is on the same origin's explicit
            # internal-v1 face. It is a read-only catalog slice and binds no Bot ID.
            if entrypoint["api_face"] != "public-v1" or entrypoint["manifest_face"] != "internal-v1":
                raise ConfigError(f"{where}: structured templates require a public-v1 entrypoint with internal-v1 manifest face")
        elif operation.startswith("structured."):
            # Gateway/Singlebox keep the explicit internal-v1 face. The station
            # direct deployment serves the same reviewed slice as native legacy
            # JSON under `/groups`, `/state-machine-runs`, and related routes.
            if (entrypoint["api_face"] != "public-v1"
                    or entrypoint["manifest_face"] not in {"internal-v1", "legacy"}
                    or (entrypoint["manifest_face"] == "legacy"
                        and entrypoint["kind"] != "bcs-direct")):
                raise ConfigError(f"{where}: structured collaboration routes require a public-v1 entrypoint with internal-v1 or station-direct legacy face")
        elif operation == "network_platform_asset.read":
            auth_headers = profile["auth"].get("header_env") if isinstance(profile["auth"], dict) else None
            if (entrypoint["api_face"] != "public-v1"
                    or entrypoint["manifest_face"] != "internal-v1"
                    or not isinstance(auth_headers, dict) or "Cookie" not in auth_headers):
                raise ConfigError(f"{where}: platform asset requires a Cookie-bound public-v1/internal-v1 profile")
        elif operation in _FRIEND_MUTATION_GATEWAY_OPS:
            # Both community Singlebox and the deployed Gateway expose this
            # public-v1 slice. The station-direct deployment is a separate
            # profile and must never receive these routes.
            if entrypoint["kind"] not in {"gateway", "singlebox"} or entrypoint["api_face"] != "public-v1":
                raise ConfigError(f"{where}: gateway mutation requires a public-v1 Gateway or Singlebox profile")
        elif operation in _FRIEND_MUTATION_STATION_OPS:
            # The station still carries the pilot's public-v1 entrypoint face;
            # its legacy product face is identified by the manifest and the
            # binding has a separate station-legacy wire-face literal.
            if entrypoint["kind"] != "bcs-direct" or entrypoint["manifest_face"] != "legacy":
                raise ConfigError(f"{where}: station mutation requires a legacy station-direct profile")
        elif binding["api_face"] != entrypoint["api_face"]:
            raise ConfigError(f"{where}: API face differs from the entrypoint")
        headers = profile["auth"].get("header_env") if isinstance(profile["auth"], dict) else None
        if not isinstance(headers, dict):
            headers = {}
        if auth == "unauthenticated":
            pass
        elif auth == "gateway_principal":
            # The reviewed public-v1 Event Subscription routes sit behind
            # the Gateway Principal middleware: only a minted principal
            # JWT authenticates them, never a Cookie or a Bot token. The
            # owned-local Singlebox runtime mints from its own runtime key;
            # a gateway profile must declare an operator-provisioned
            # deployment signing-key environment reference.
            if profile["auth"] != {"kind": "owned-local"}:
                key_ref = profile["auth"].get("principal_signing_key_env")
                if (not isinstance(key_ref, str) or not key_ref
                        or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,100}", key_ref)):
                    raise ConfigError(
                        f"{where}: gateway principal requires the owned-local "
                        "runtime or an operator-provisioned principal signing "
                        "key environment reference")
        elif auth == "register_token":
            if operation == "friend_connection_mutation.register_bot.create":
                # The station front door still requires the Human Cookie for
                # ingress. The register token remains the product credential
                # and no Bot Authorization bearer may be sent by this slice.
                if "Cookie" not in headers:
                    raise ConfigError(f"{where}: station register requires a Cookie reference")
            # The public register route is deliberately anonymous: its minted
            # token is the sole product credential, with no Cookie and no Bot
            # Authorization bearer.
        elif auth == "authenticated_bot":
            if binding["caller_actor_id"] != profile["actors"]["driver"]["bot_id"]:
                raise ConfigError(f"{where}: caller must be the dedicated driver Bot")
            if "Authorization" not in headers:
                raise ConfigError(f"{where}: authenticated Bot requires an Authorization reference")
        elif auth == "human_cookie_owned_bot":
            if "Cookie" not in headers or "Authorization" not in headers:
                raise ConfigError(f"{where}: dual-identity route requires Cookie and Authorization references")
            for header in ("Cookie", "Authorization"):
                reference = headers[header]
                if (not isinstance(reference, str)
                        or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,100}", reference)):
                    raise ConfigError(f"{where}: dual-identity header environment reference is invalid")
        else:
            if operation == "network_bot_query.read":
                targets = binding["target_actor_ids"]
                expected = [profile["actors"][role]["bot_id"]
                            for role in ("driver", "member")]
                if (not isinstance(targets, list) or len(targets) != 2
                        or any(not isinstance(target, str) or not target for target in targets)
                        or len(set(targets)) != 2 or targets != expected):
                    raise ConfigError(f"{where}: targets must be the dedicated driver and member Bots")
                for target in targets:
                    identifier(target, where + ".target_actor_ids")
            if "Cookie" not in headers:
                raise ConfigError(f"{where}: human Cookie context requires a Cookie reference")
        if operation in {"network_actor_status.read", "network_actor_status.write",
                          "channel_bindings.create_denied", "channel_bindings.by_target"}:
            target = binding["target_actor_id"]
            if (not isinstance(target, str) or target != profile["actors"]["driver"]["bot_id"]):
                raise ConfigError(f"{where}: target must be the dedicated driver Bot")
            identifier(target, f"{where}.target_actor_id")
        if operation == "friend_connection_public_accept.create":
            driver = profile["actors"]["driver"]["bot_id"]
            member = profile["actors"]["member"]["bot_id"]
            actors = (binding["from_actor_id"], binding["to_actor_id"])
            if actors != (driver, member):
                raise ConfigError(f"{where}: public auto-acceptance must bind driver to member")
            for actor in actors:
                identifier(actor, f"{where}.actor_id")
        elif operation == "friend_connection_public_accept.driver_connections.read":
            driver = profile["actors"]["driver"]["bot_id"]
            actor = binding["actor_id"]
            if actor != driver:
                raise ConfigError(f"{where}: connection read must bind the driver")
            identifier(actor, f"{where}.actor_id")
        elif operation in _FRIEND_MUTATION_PAIR_OPS:
            actors = (binding["from_actor_id"], binding["to_actor_id"])
            if not all(isinstance(actor, str) and actor for actor in actors) or len(set(actors)) != 2:
                raise ConfigError(f"{where}: pair must contain two distinct nonempty actor IDs")
            for actor in actors:
                identifier(actor, f"{where}.actor_id")
        elif operation in _FRIEND_MUTATION_ACTOR_OPS:
            identifier(binding["actor_id"], f"{where}.actor_id")
        elif operation in _FRIEND_MUTATION_TARGET_OPS:
            identifier(binding["target_actor_id"], f"{where}.target_actor_id")
        elif operation == "friend_connection_mutation.station_admission.read":
            identifier(binding["target_actor_id"], f"{where}.target_actor_id")
            identifier(binding["actor_id"], f"{where}.actor_id")
        elif operation.startswith("friend_connection_decision."):
            driver = profile["actors"]["driver"]["bot_id"]
            member = profile["actors"]["member"]["bot_id"]
            if operation == "friend_connection_decision.reject.create":
                actors = (binding["from_actor_id"], binding["to_actor_id"])
                if actors != (driver, member):
                    raise ConfigError(f"{where}: rejection create must bind driver to member")
            elif operation == "friend_connection_decision.accept.create":
                actors = (binding["from_actor_id"], binding["to_actor_id"])
                if actors != (member, driver):
                    raise ConfigError(f"{where}: acceptance create must bind member to driver")
            elif operation == "friend_connection_decision.member_requests.read":
                actors = (binding["actor_id"], None)
                if actors != (member, None):
                    raise ConfigError(f"{where}: request read must bind the member receiver")
            elif operation == "friend_connection_decision.driver_requests.read":
                actors = (binding["actor_id"], None)
                if actors != (driver, None):
                    raise ConfigError(f"{where}: request read must bind the driver receiver")
            elif operation == "friend_connection_decision.driver_connections.read":
                actors = (binding["actor_id"], None)
                if actors != (driver, None):
                    raise ConfigError(f"{where}: connection read must bind the driver")
            elif operation == "friend_connection_decision.reject":
                actors = (binding["from_actor_id"], binding["to_actor_id"])
                if actors != (driver, member):
                    raise ConfigError(f"{where}: reject must bind driver to member")
            elif operation == "friend_connection_decision.accept":
                actors = (binding["from_actor_id"], binding["to_actor_id"])
                if actors != (member, driver):
                    raise ConfigError(f"{where}: accept must bind member to driver")
            else:
                raise ConfigError(f"{where}: unknown friendship decision binding")
            for actor in actors:
                if actor is not None:
                    identifier(actor, f"{where}.actor_id")
        result[operation] = dict(binding)
    return result


def require_edge_case_binding(profile: dict, operation: str) -> dict:
    if operation not in _EDGE_CASES:
        raise ValueError("unsupported Edge operation")
    from .profile import ConfigError
    bindings = profile.get("edge_case_bindings")
    if not isinstance(bindings, dict):
        raise ConfigError(f"edge_case_bindings.{operation}: declared binding is absent")
    validated = validate_edge_case_bindings(bindings, profile)
    if operation not in validated:
        raise ConfigError(f"edge_case_bindings.{operation}: declared binding is absent")
    return validated[operation]


def require_friend_requests_binding(profile: dict) -> dict:
    return require_edge_case_binding(profile, "friend_requests.read")
