"""Dual-environment field-level density slice for the Retired service policy story.

The package projects the forty reviewed obligation sites of
``story_retired_service_policy`` (eight coarse parent obligations aligned
with the committed service-policy migration contract plus thirty-two
field-level density obligations projected from the thirty-two anchored
upstream field sites - the legacy double-encoding persistence gaps, the 410
retired settings surface, the two distinct service_invocation lifecycles,
the running reactivation guard, the completed chat Session conversion guard,
and the six-disposition teardown landmark) and executes the projection
offline on admitted profiles.  Every obligation anchors to its
``service_policy_story.py`` line with the exact upstream quote; anchor drift
fails closed.  Owned-local Singlebox演算s through the deterministic replay
fixture; preprod offline fail-closes to the named-gap BLOCKED_INVENTORIED
receipt with every obligation NOT_RUN.  The
``dual-service-policy-density-run`` wrapper constant and handler are
registered by the mainline serial wave; the projection stays offline.  No
credential value ever leaves the package.
"""

from .contract import (
    BOOTSTRAP_OPERATION_ROWS,
    BUSINESS_OPERATION_ROWS,
    CASE_ID,
    DENSITY_ASSERTION_COUNT,
    DENSITY_FIELD_COUNT,
    DENSITY_OPERATION_ROWS,
    DENSITY_PARENT_COUNT,
    DUAL_SERVICE_POLICY_DENSITY_COMMAND,
    PROJECTION_CONTRACT,
    REPORT_CONTRACT,
    TEARDOWN_DERIVED_OPERATION,
    service_policy_density_plan,
)
from .obligations import (
    COARSE_PARENT_COUNT,
    FIELD_OBLIGATION_COUNT,
    FIELD_SITE_COUNT,
    NOT_CHARGED_SOURCE_PROBES,
    PARSED_OBLIGATION_COUNT,
    PARENT,
    build_service_policy_density_obligations,
    service_policy_density_reconciliation,
)
from .profile import PROFILE_CONTRACT, validate_service_policy_density_profile
from .reporting import (
    RESOURCE_DISPOSITION,
    dual_service_policy_density_case_run,
    service_policy_density_credential_kind,
)
from .run import (
    GAP_ID,
    LOCAL_TEARDOWN_DISPOSITIONS,
    PREPROD_TEARDOWN_DISPOSITIONS,
    Observation,
    ReplayTransport,
    STATUS_BLOCKED_INVENTORIED,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    owned_local_replay_observations,
    owned_local_replay_transport,
    run_service_policy_density,
)

__all__ = [
    "BOOTSTRAP_OPERATION_ROWS",
    "BUSINESS_OPERATION_ROWS",
    "CASE_ID",
    "COARSE_PARENT_COUNT",
    "DENSITY_ASSERTION_COUNT",
    "DENSITY_FIELD_COUNT",
    "DENSITY_OPERATION_ROWS",
    "DENSITY_PARENT_COUNT",
    "DUAL_SERVICE_POLICY_DENSITY_COMMAND",
    "FIELD_OBLIGATION_COUNT",
    "FIELD_SITE_COUNT",
    "GAP_ID",
    "LOCAL_TEARDOWN_DISPOSITIONS",
    "NOT_CHARGED_SOURCE_PROBES",
    "PARSED_OBLIGATION_COUNT",
    "PARENT",
    "PREPROD_TEARDOWN_DISPOSITIONS",
    "PROJECTION_CONTRACT",
    "PROFILE_CONTRACT",
    "REPORT_CONTRACT",
    "RESOURCE_DISPOSITION",
    "STATUS_BLOCKED_INVENTORIED",
    "STATUS_FAIL",
    "STATUS_NOT_RUN",
    "STATUS_PASS",
    "TEARDOWN_DERIVED_OPERATION",
    "Observation",
    "ReplayTransport",
    "build_service_policy_density_obligations",
    "dual_service_policy_density_case_run",
    "owned_local_replay_observations",
    "owned_local_replay_transport",
    "run_service_policy_density",
    "service_policy_density_credential_kind",
    "service_policy_density_plan",
    "service_policy_density_reconciliation",
    "validate_service_policy_density_profile",
]
