"""API Gateway permission helpers."""

from secbaas.community.config import ConfigPath, get_config, get_config_by_path

from ._models import APIKeyResponse


def _get_admin_operators() -> frozenset[str]:
    """Read admin operators from config."""
    cfg = get_config()
    operators = get_config_by_path(
        cfg, ConfigPath.API_GATEWAY_ADMIN_OPERATORS, default=[]
    )
    if not isinstance(operators, (list, tuple, set)):
        return frozenset()
    return frozenset(str(op) for op in operators)


def is_admin(operator: str) -> bool:
    """Check whether operator is in the admin list."""
    return operator in _get_admin_operators()


def parse_bot_entity_id(app_id: str) -> str | None:
    """Parse bot app_id to extract entity_id.

    app_id format: real_bot_id:entity_id

    Args:
        app_id: Application ID

    Returns:
        entity_id or None if format invalid
    """
    if ":" not in app_id:
        return None
    parts = app_id.split(":", 1)
    if len(parts) != 2 or not parts[1]:
        return None
    return parts[1]


def check_bot_permission(operator: str, app_id: str) -> bool:
    """Check whether operator has permission for a bot-type API Key.

    Validation: operator == entity_id (parsed from app_id)

    Args:
        operator: Operator staffId
        app_id: Application ID, format real_bot_id:entity_id

    Returns:
        True if permitted, False otherwise
    """
    entity_id = parse_bot_entity_id(app_id)
    if entity_id is None:
        return False
    return operator == entity_id


def check_permission(
    operator: str,
    api_key: APIKeyResponse,
) -> bool:
    """Check whether operator has permission to manage an API Key.

    Permission flow:
    1. operator is key owner → allowed
    2. app_type = bot: validate operator == entity_id
    3. Otherwise → denied

    Admin operations go through admin endpoint — admin is NOT checked here.

    Args:
        operator: Operator staffId
        api_key: API Key record

    Returns:
        True if permitted, False otherwise
    """
    if operator == api_key.owner:
        return True

    app_type = api_key.app_type

    if app_type == "bot":
        return check_bot_permission(operator, api_key.app_id)

    return False


class APIKeyPermissionChecker:
    """API Key permission checker."""

    def __init__(self, operator: str):
        self.operator = operator

    def check(self, api_key: APIKeyResponse) -> bool:
        """Check permission."""
        return check_permission(self.operator, api_key)
