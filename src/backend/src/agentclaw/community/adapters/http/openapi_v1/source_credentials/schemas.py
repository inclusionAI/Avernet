"""Request/response models for the source-credentials group (W3, #1471).

Docstrings and field descriptions publish verbatim into the OpenAPI
document — caller-facing prose only; rationale lives in comments.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum
from agentclaw.community.core.bot_config_manifest.credentials.models import (
    CredentialType as _DomainCredentialType,
)

# The published spelling of the domain's CredentialType: the same closed
# value set, member-documented for generated clients. Mirrored, not
# imported, because core does not depend on this layer; the assert at the
# bottom holds the two spellings together member-for-member.


class CredentialType(_DocumentedEnum):
    """The mechanism that presents this credential's secret."""

    HEADER = "header"
    OSS_AKSK = "oss_aksk"
    BASIC = "basic"

    # value -> caller-facing meaning; the schema-documentation gate reads it.
    __descriptions__ = {
        "header": "The platform presents the secret in an HTTP header "
        "(header_name) while fetching — the only implemented mechanism.",
        "oss_aksk": "Reserved for a future OSS AK/SK mechanism; refused at "
        "write today so the stored type is real from day one.",
        "basic": "Reserved for a future HTTP Basic mechanism; refused at "
        "write today so the stored type is real from day one.",
    }


assert {m.value for m in CredentialType} == {m.value for m in _DomainCredentialType}, (
    "the published CredentialType drifted from the domain vocabulary in "
    "core/bot_config_manifest/credentials/models.py"
)


# Bodies reject unknown keys: a typo'd or future field must fail validation
# rather than be silently dropped (the group contract across this surface).
_STRICT = ConfigDict(extra="forbid")

_CREDENTIAL_NAME_DESC = (
    "Identifier of the credential, tenant-scoped. Nothing about it is "
    "derived — the name is caller-chosen and referenced from a manifest by "
    "that name alone."
)
_PREFIX_DESC = (
    "Authorized presentation scopes: absolute HTTPS prefixes this "
    "credential may be presented to, matched on whole path segments "
    "(https://host/team/content authorizes that tree, not "
    ".../team/content-secret). At least one; empty list is a refusal."
)


from typing import Annotated
from fastapi import Path

CREDENTIAL_NAME_PATH = Annotated[
    str,
    Path(description=_CREDENTIAL_NAME_DESC),
]


class SourceCredentialWrite(BaseModel):
    """Register or rotate one named credential.

    One body schema for every mechanism the endpoint will ever carry: the
    discriminating key is the authentication mechanism (type), and the
    storage type is not the source type (git/oss/url is a *source* axis).
    Rotation is the same request under the same name — no apply runs and
    the next fetch uses the new value. Fails (422) when the type is a
    reserved future mechanism or the input is invalid.
    """

    model_config = _STRICT

    type: CredentialType = Field(
        default=CredentialType.HEADER,
        description="Authentication mechanism for presenting the secret. "
        "'header' is the only implemented mechanism; 'oss_aksk' and 'basic' "
        "are reserved for future support and are refused at write.",
    )
    header_name: str | None = Field(
        default=None,
        description="Header the secret is presented under, required when "
        "type is 'header' (e.g. PRIVATE-TOKEN for a git host token, "
        "Authorization for bearer-style tokens).",
    )
    secret: str = Field(
        description="The secret value itself — stored encrypted, never "
        "readable back through the API, never shown in logs or apply "
        "reports (only the credential name appears there).",
    )
    allowed_prefixes: list[str] = Field(
        description=_PREFIX_DESC,
    )


class SourceCredential(BaseModel):
    """Masked metadata for one named credential.

    The secret never appears: read paths answer whether one is stored,
    under which header it would be presented, and where it may be
    presented — never the value.
    """

    name: str = Field(description=_CREDENTIAL_NAME_DESC)
    has_secret: bool = Field(
        default=True,
        description="Whether a secret is stored under this name.",
    )
    updated_at: datetime = Field(
        description="Last write, server clock.",
    )


class SourceCredentialDetail(SourceCredential):
    """Resolve-time shape for a named credential."""

    type: CredentialType = Field(description="Authentication mechanism.")
    header_name: str | None = Field(
        description="Header the secret presents under; null if the "
        "mechanism does not use one."
    )
    allowed_prefixes: list[str] = Field(
        description="Absolute HTTPS prefixes this credential's "
        "presentation is scoped to.",
    )
    owner_app_id: int = Field(
        description="The owning application (registry id): the one whose "
        "PUT created this name. Rotation and delete are its calls alone; "
        "every application of the tenant may read this metadata.",
    )
