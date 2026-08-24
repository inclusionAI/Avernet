"""Transport-agnostic policy for preparing Bot creation attributes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agentclaw.community.core.bot_management.create_errors import (
    BotCombinationUnsupportedError,
    BotTemplateInvalidError,
)

APPLICATION_CODING_ENGINES = frozenset({"claude_code"})

# Server-managed keys a caller must not supply inside a template payload.
_TEMPLATE_SERVER_RESERVED_FIELDS = frozenset(
    {
        "dima_space_id",
        "workspace_id",
        "template_uid",
        "bot_id",
        "workspace_status",
        "workspace_state",
        "start_status",
    }
)


class BotCreateDeploymentMode(StrEnum):
    """Deployment boundary relevant to Bot creation policy."""

    CLOUD = "cloud"
    LOCAL = "local"


@dataclass(frozen=True)
class BotCreateContext:
    """Caller-resolved business context required by creation policy."""

    deployment_mode: BotCreateDeploymentMode
    space_kind: str


@dataclass(frozen=True)
class PreparedBotCreate:
    """Sanitized creation attributes plus required platform capabilities."""

    template_config: dict[str, Any] | None
    requires_workspace_hosting: bool = False


def to_internal_template_config(
    value: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Reject server-managed template keys and detach the caller-owned payload."""
    if value is None:
        return None
    reserved = sorted(_TEMPLATE_SERVER_RESERVED_FIELDS.intersection(value))
    if reserved:
        raise BotTemplateInvalidError(
            f"template_config contains server-managed fields: {reserved}"
        )
    return deepcopy(value)


def _validate_application_coding_config(
    value: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate the stable outer contract while preserving vendor extensions."""
    # Legacy internal callers may intentionally omit the config; keep that
    # established contract. When a config is supplied, reject an empty or
    # malformed payload before Passport/workspace/persistence side effects.
    if value is None:
        return None
    if not value:
        raise BotTemplateInvalidError(
            "applicationCoding template_config must not be empty"
        )
    expected_types: dict[str, type | tuple[type, ...]] = {
        "devflow_workflow": (str, dict),
        "yuque_kb_repos": list,
        "code_repos": list,
        "bot_template_config": dict,
        "token": str,
    }
    for key, expected in expected_types.items():
        if key not in value:
            continue
        field_value = value[key]
        if not isinstance(field_value, expected):
            raise BotTemplateInvalidError(
                f"applicationCoding template_config.{key} has invalid type"
            )
        if key == "token" and not field_value.strip():
            raise BotTemplateInvalidError(
                "applicationCoding template_config.token cannot be empty"
            )
    return value


def prepare_bot_create(
    *,
    template_type: str | None,
    template_config: dict[str, Any] | None,
    bot_type: str,
    engine_type: str,
    context: BotCreateContext,
) -> PreparedBotCreate:
    """Validate and sanitize template-related Bot creation attributes.

    The policy is shared by all creation transports. It deliberately reports
    required capabilities instead of querying infrastructure itself, leaving
    orchestration to fail before Passport or persistence side effects.
    """
    if template_type is None:
        if template_config is not None:
            raise BotTemplateInvalidError("template_config requires template_type")
        return PreparedBotCreate(template_config=None)

    # Other template types are established internal Bot-management inputs. Their
    # own adapters/services own their field semantics; this policy only detaches
    # their optional payload and must not turn the OpenAPI template enum into a
    # global creation restriction.
    if template_type != "applicationCoding":
        return PreparedBotCreate(
            template_config=to_internal_template_config(template_config)
        )

    if context.deployment_mode is not BotCreateDeploymentMode.CLOUD:
        raise BotCombinationUnsupportedError("application coding is cloud-only")
    if engine_type not in APPLICATION_CODING_ENGINES:
        raise BotCombinationUnsupportedError(
            f"application coding does not support engine: {engine_type}"
        )
    if bot_type != "personal":
        raise BotCombinationUnsupportedError("application coding bot must be personal")
    if context.space_kind != "personal":
        raise BotCombinationUnsupportedError(
            "application coding is personal-space only"
        )

    sanitized = to_internal_template_config(template_config)
    return PreparedBotCreate(
        template_config=_validate_application_coding_config(sanitized),
        requires_workspace_hosting=True,
    )
