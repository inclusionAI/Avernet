"""Storage schema v1 for complete immutable ProductProfiles (not executable code)."""

import json
from hashlib import sha256

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    MethodProfile,
    get_profile,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    OpenMethodPolicy,
    SwarmMethodPolicy,
    SwarmWorkflowPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    OpenAdmissionPolicy,
    BotSelfApplicationAdmission,
    OpenPublicationProfile,
    DesignatedOpenAdmission,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow import (
    WORKFLOW_TYPES,
)
from agentclaw.community.core.task.context_collab.l5_methods.value_codec import (
    encode,
    decode,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    LegacyProductConfiguration,
    VersionedProfileRef,
    MethodRelationTemplate,
    MethodTemplate,
    MethodCompositionTemplate,
    ScenarioRole,
    DomainMapping,
    DomainProfile,
    TaskAction,
    WorkflowProfile,
    GovernanceRequirements,
    CompatibilityRequirements,
)
from agentclaw.community.core.task.context_collab.l6_products.participant_scope import (
    ParticipantScope,
)
from agentclaw.community.core.task.context_collab.l6_products.validation import (
    validate_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.relation_storage import (
    from_legacy_relation_fields,
    to_legacy_relation_fields,
)

SCHEMA_VERSION = 1
MAX_DEFINITION_BYTES = 1024 * 1024
_TYPES = (
    *WORKFLOW_TYPES,
    AgentRef,
    MethodProfile,
    OpenMethodPolicy,
    SwarmMethodPolicy,
    SwarmWorkflowPolicy,
    OpenAdmissionPolicy,
    BotSelfApplicationAdmission,
    OpenPublicationProfile,
    DesignatedOpenAdmission,
    LegacyProductConfiguration,
    VersionedProfileRef,
    MethodRelationTemplate,
    MethodTemplate,
    MethodCompositionTemplate,
    ScenarioRole,
    DomainMapping,
    DomainProfile,
    TaskAction,
    WorkflowProfile,
    GovernanceRequirements,
    CompatibilityRequirements,
    ParticipantScope,
)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContextCollabValidationError(f"duplicate definition key: {key}")
        result[key] = value
    return result


def _nonfinite(value):
    raise ContextCollabValidationError(f"non-finite definition number: {value}")


def decode_profile(value: str) -> LegacyProductConfiguration:
    if type(value) is not str or len(value.encode("utf-8")) > MAX_DEFINITION_BYTES:
        raise ContextCollabValidationError(
            "profile definition exceeds 1 MiB or is not JSON text"
        )
    try:
        data = json.loads(value, object_pairs_hook=_object, parse_constant=_nonfinite)
        if (
            type(data) is not dict
            or set(data) != {"schema_version", "definition"}
            or type(data["schema_version"]) is not int
            or data["schema_version"] != SCHEMA_VERSION
        ):
            raise ContextCollabValidationError("unknown ProductProfile storage schema")
        profile = decode(
            from_legacy_relation_fields(data["definition"]),
            LegacyProductConfiguration,
            _TYPES,
            get_profile,
        )
        if to_legacy_relation_fields(encode(profile)) != data["definition"]:
            raise ContextCollabValidationError(
                "noncanonical or normalized ProductProfile input"
            )
        validate_product_profile(profile)
        if len(profile.ref.profile_id) > 128 or profile.ref.version > 2147483647:
            raise ContextCollabValidationError(
                "profile identity exceeds storage bounds"
            )
        return profile
    except (ValueError, TypeError, RecursionError) as exc:
        raise ContextCollabValidationError("invalid ProductProfile JSON") from exc


def encode_profile(profile: LegacyProductConfiguration) -> str:
    value = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "definition": to_legacy_relation_fields(encode(profile)),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    # Both authoring and untrusted storage use exactly the same strict contract.
    if decode_profile(value) != profile:
        raise ContextCollabValidationError("noncanonical ProductProfile definition")
    return value


def profile_digest(profile: LegacyProductConfiguration) -> str:
    return sha256(encode_profile(profile).encode("utf-8")).hexdigest()
