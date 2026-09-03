"""Tenant source credentials (W3, #1471): models, policy, service, binding."""
from agentclaw.community.core.bot_config_manifest.credentials.errors import (
    CredentialError,
    CredentialNotFoundError,
    MasterKeyUnavailableError,
)
from agentclaw.community.core.bot_config_manifest.credentials.models import (
    SourceCredentialModel,
    SourceCredentialRecord,
)
from agentclaw.community.core.bot_config_manifest.credentials.policy import (
    CanonicalPrefix,
    PrefixAuthorizationError,
    PrefixAuthorizationPolicy,
    validate_prefixes,
)
from agentclaw.community.core.bot_config_manifest.credentials.service import (
    SourceCredentialBinding,
    SourceCredentialService,
)
from agentclaw.community.core.bot_config_manifest.credentials.service_protocol import (  # noqa: F401  re-export
    SourceCredentialServiceProtocol,
)

__all__ = [
    "CanonicalPrefix",
    "CredentialError",
    "CredentialNotFoundError",
    "MasterKeyUnavailableError",
    "PrefixAuthorizationError",
    "PrefixAuthorizationPolicy",
    "SourceCredentialBinding",
    "SourceCredentialModel",
    "SourceCredentialRecord",
    "SourceCredentialService",
    "SourceCredentialServiceProtocol",
    "validate_prefixes",
]
