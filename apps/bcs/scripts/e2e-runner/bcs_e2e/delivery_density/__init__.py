"""Dual-environment field-level density slice for the Delivery Policy story.

The package projects the 36 reviewed obligation sites of
``story_human_manages_delivery_policy`` (ten coarse parent obligations aligned
with the committed delivery-policy migration contract plus 26 field-level
density obligations projected from the 13 anchored upstream field sites -
response fields, the explicit 409 error envelope, and the persistence
read-backs) and executes the projection offline on admitted profiles.  Every
obligation anchors to its ``stories.sh`` line with the upstream quote; anchor
drift fails closed.  Owned-local Singlebox演算s through the deterministic
replay fixture; preprod offline fail-closes to the named-gap
BLOCKED_INVENTORIED receipt with every obligation NOT_RUN.  The
``dual-delivery-policy-density-run`` wrapper constant and handler are
registered by the mainline serial wave; the projection stays offline.  No
credential value ever leaves the package.
"""

from .contract import (
    CASE_ID,
    DENSITY_ASSERTION_COUNT,
    DENSITY_FIELD_COUNT,
    DENSITY_PARENT_COUNT,
    DUAL_DELIVERY_POLICY_DENSITY_COMMAND,
    PROJECTION_CONTRACT,
    REPORT_CONTRACT,
    delivery_density_plan,
)
from .obligations import (
    COARSE_PARENT_COUNT,
    FIELD_OBLIGATION_COUNT,
    FIELD_SITE_COUNT,
    NOT_CHARGED_SOURCE_PROBES,
    PARSED_OBLIGATION_COUNT,
    PARENT,
    build_delivery_density_obligations,
    delivery_density_reconciliation,
)
from .profile import PROFILE_CONTRACT, validate_delivery_density_profile
from .reporting import (
    RESOURCE_DISPOSITION,
    dual_delivery_policy_density_case_run,
    delivery_density_credential_kind,
)
from .run import (
    GAP_ID,
    Observation,
    ReplayTransport,
    STATUS_BLOCKED_INVENTORIED,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    owned_local_replay_observations,
    owned_local_replay_transport,
    run_delivery_density,
)

__all__ = [
    "CASE_ID",
    "COARSE_PARENT_COUNT",
    "DENSITY_ASSERTION_COUNT",
    "DENSITY_FIELD_COUNT",
    "DENSITY_PARENT_COUNT",
    "DUAL_DELIVERY_POLICY_DENSITY_COMMAND",
    "FIELD_OBLIGATION_COUNT",
    "FIELD_SITE_COUNT",
    "GAP_ID",
    "NOT_CHARGED_SOURCE_PROBES",
    "PARSED_OBLIGATION_COUNT",
    "PARENT",
    "PROJECTION_CONTRACT",
    "PROFILE_CONTRACT",
    "REPORT_CONTRACT",
    "RESOURCE_DISPOSITION",
    "STATUS_BLOCKED_INVENTORIED",
    "STATUS_FAIL",
    "STATUS_NOT_RUN",
    "STATUS_PASS",
    "Observation",
    "ReplayTransport",
    "build_delivery_density_obligations",
    "delivery_density_credential_kind",
    "delivery_density_plan",
    "delivery_density_reconciliation",
    "dual_delivery_policy_density_case_run",
    "owned_local_replay_observations",
    "owned_local_replay_transport",
    "run_delivery_density",
    "validate_delivery_density_profile",
]
