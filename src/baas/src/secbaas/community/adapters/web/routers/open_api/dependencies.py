"""Open API dependency injection"""

from dependency_injector.wiring import Provide, inject
from fastapi import Depends, Header, HTTPException, Request, status

from secbaas.community.api.api_gateway import (
    APIKeyPolicy,
    APIKeyRecord,
    APIKeyValidator,
    parse_policy,
)
from secbaas.community.api.bot_runtime import BotChatContext
from secbaas.community.api.open_api import OpenAPICode
from secbaas.community.bootstrap import ApplicationContainer


def get_api_key_from_header(authorization: str | None = Header(None)) -> str:
    """Parse Bearer Token from the Authorization Header

    Args:
        authorization: Authorization Header value, formatted as "Bearer {api_key}"

    Returns:
        api_key: Parsed API Key

    Raises:
        HTTPException: 401 if the Header is missing or malformed
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 40101, "message": "Token missing"},
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": 40002, "message": "Parameter missing"},
        )

    return parts[1]


def get_iam_token_from_cookie(request: Request) -> str | None:
    """Parse IAM Token from Cookie (case-insensitive)

    Args:
        request: FastAPI Request object

    Returns:
        iam_token: Parsed IAM Token; returns None if not present
    """
    # Case-insensitive cookie lookup
    for key, value in request.cookies.items():
        if key.upper() == "IAM_TOKEN":
            return value if value else None

    return None


@inject
async def validate_api_key(
    api_key: str = Depends(get_api_key_from_header),
    validator: APIKeyValidator = Depends(
        Provide[ApplicationContainer.services.api_key_validator]
    ),
) -> APIKeyRecord:
    """Validate the API Key and return the associated API Key record

    Args:
        api_key: API Key
        validator: APIKeyValidator instance

    Returns:
        APIKeyRecord: Validated API Key record, containing bot_id (app_id), api_key_prefix, etc.

    Raises:
        HTTPException: 401 if the API Key is invalid
    """
    record = await validator.verify(api_key)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": 40103, "message": "Token invalid"},
        )
    return record


def get_bot_chat_context(
    api_key_record: APIKeyRecord = Depends(validate_api_key),
    iam_token: str | None = Depends(get_iam_token_from_cookie),
) -> BotChatContext:
    """Build BotChatContext from API Key record and Cookie

    Args:
        api_key_record: API Key validation record
        iam_token: IAM Token extracted from Cookie

    Returns:
        BotChatContext: Request context
    """
    return BotChatContext.from_api_key(
        api_key_prefix=api_key_record.api_key_prefix,
        app_id=api_key_record.app_id,
        app_type=api_key_record.app_type or "UNKNOWN",
        iam_token=iam_token,
        tenant=api_key_record.tenant or "",
    )


def normalize_bot_id(bot_id: str) -> str:
    """Compatibility handling for bot IDs whose employee number is zero-padded. bot_id format is ``<real_bot_id>:<entity_id>``; strips leading zeros from entity_id."""
    if ":" in bot_id:
        real_bot_id, entity_id = bot_id.rsplit(":", 1)
        entity_id = entity_id.lstrip("0") or "0"
        return f"{real_bot_id}:{entity_id}"
    return bot_id


def resolve_bot_id_from_api_key(api_key_record: APIKeyRecord) -> str:
    """Resolve bot_id from the API Key record, handling zero-padded employee numbers

    Args:
        api_key_record: API Key validation record

    Returns:
        str: Parsed and normalized bot_id
    """
    return normalize_bot_id(api_key_record.app_id)


def parse_bot_id(bot_id: str) -> tuple[str, str]:
    """Parse bot_id into real_bot_id and entity_id

    Args:
        bot_id: bot ID, formatted as <real_bot_id>:<entity_id>

    Returns:
        tuple[str, str]: (real_bot_id, entity_id)
    """
    parts = bot_id.split(":", 1)
    real_bot_id = parts[0] if parts else ""
    entity_id = parts[1] if len(parts) == 2 else ""
    return real_bot_id, entity_id


def match_allowed_bots(target_bot_id: str, allowed_bots: list[str]) -> str | None:
    """Validate whether target_bot_id is in the allowed_bots list

    Args:
        target_bot_id: Target bot ID, formatted as <real_bot_id>:<entity_id>
        allowed_bots: List of bot IDs allowed for access

    Returns:
        str | None: The matched bot_id from allowed_bots (authoritative format); returns None if no match
    """
    if not allowed_bots:
        return None

    target_real_bot_id, target_entity_id = parse_bot_id(target_bot_id)

    for allowed in allowed_bots:
        allowed_real_bot_id, allowed_entity_id = parse_bot_id(allowed)

        if target_real_bot_id != "default":
            # Non-default mode: only real_bot_id needs to match
            if target_real_bot_id == allowed_real_bot_id:
                return allowed
        else:
            # Default mode: requires full match (real_bot_id:entity_id)
            if (
                target_real_bot_id == allowed_real_bot_id
                and target_entity_id == allowed_entity_id
            ):
                return allowed

    return None


def validate_policy(
    api_key_record: APIKeyRecord,
    target_bot_id: str,
) -> str:
    """Validate access permission to the target bot based on the API Key's policy

    Policy semantics (after parse_policy normalization):
    - allowed_bots contains "*": allows access to all bots (explicit configuration).
    - allowed_bots is empty (including legacy ["NONE"] sentinel / unconfigured policy / missing allowed_bots key): denies all bots (fail-closed).
    - Otherwise: whitelist match; only matched bots are permitted.

    Args:
        api_key_record: API Key record
        target_bot_id: Target bot ID, formatted as <real_bot_id>:<entity_id>

    Returns:
        str: The bot_id to use after successful validation.
              Returns the original target_bot_id when explicit allow-all;
              Returns the matched authoritative format from allowed_bots when whitelist-matched.

    Raises:
        HTTPException: 403 if no permission
    """
    policy = parse_policy(api_key_record.policy)

    if APIKeyPolicy.ALL in policy.allowed_bots:
        # Explicit allow-all
        return target_bot_id

    matched = match_allowed_bots(target_bot_id, policy.allowed_bots)
    if matched is None:
        # Whitelist miss / empty (including NONE) denies all bots (fail-closed)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": OpenAPICode.FORBIDDEN,
                "message": f"API Key does not have permission to access bot: {target_bot_id}",
            },
        )
    return matched
