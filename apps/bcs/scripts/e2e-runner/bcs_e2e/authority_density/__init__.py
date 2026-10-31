"""Offline density projection for the Bot-authority user journey (Wave-B #18)."""

from .contract import (
    CALL_GROUP_COUNT, CASE_ID, CONTRACT, PARSED_OBLIGATION_COUNT, PARENT,
    RESIDUAL_COUNT_GAP, SHELL_STATUS_ASSERTIONS, SHELL_VALUE_ASSERTIONS,
    SOURCE_SHA256, SOURCE_REVISION, Obligation, authority_density_plan,
    call_groups, parsed_obligations,
)
from .defect import (
    IDENTITY_PRESEED_OPERATIONS, PREPROD_COLUMN, PREPROD_GAP_IDS,
    PRODUCT_DEFECTS, admission_diagnosis_basis, assert_honesty_ledger,
    defect_gap_ledger, gap_attribution, preprod_admission_gaps,
)
from .profile import PROFILE_CONTRACT, validate_density_profile
from .registration_proposal import EXECUTOR_MIGRATION_ROW, EXECUTOR_ROW, registration_proposal
from .reporting import (
    INTEGRATION_TOKEN, density_accounting, family_accounting, md_table,
    proposed_executor_migration_notes,
)
from .run import (
    ASSERTION_FAILED, BLOCKED_AFTER_ASSERTION_FAILURE,
    ENVELOPE_PRECONDITION_FAILED, PREPROD_ADMISSION_BLOCKED, STATUS_BLOCKED_INVENTORIED,
    STATUS_FAIL, STATUS_NOT_RUN, STATUS_PASS, run_authority_density,
)
from .transport import (
    Observation, ReplayTransport, canonical_replay_observations,
    canonical_replay_transport,
)

__all__ = [
    "ASSERTION_FAILED", "BLOCKED_AFTER_ASSERTION_FAILURE",
    "CALL_GROUP_COUNT", "CASE_ID", "CONTRACT", "ENVELOPE_PRECONDITION_FAILED",
    "EXECUTOR_MIGRATION_ROW", "EXECUTOR_ROW", "IDENTITY_PRESEED_OPERATIONS",
    "INTEGRATION_TOKEN", "Obligation", "Observation", "PARENT",
    "PARSED_OBLIGATION_COUNT", "PREPROD_ADMISSION_BLOCKED", "PREPROD_COLUMN",
    "PREPROD_GAP_IDS", "PROFILE_CONTRACT", "PRODUCT_DEFECTS",
    "RESIDUAL_COUNT_GAP", "ReplayTransport", "SHELL_STATUS_ASSERTIONS",
    "SHELL_VALUE_ASSERTIONS", "SOURCE_SHA256", "SOURCE_REVISION",
    "STATUS_BLOCKED_INVENTORIED", "STATUS_FAIL", "STATUS_NOT_RUN",
    "STATUS_PASS", "admission_diagnosis_basis", "assert_honesty_ledger",
    "authority_density_plan", "call_groups", "canonical_replay_observations",
    "canonical_replay_transport", "defect_gap_ledger", "density_accounting",
    "family_accounting", "gap_attribution", "md_table", "parsed_obligations",
    "preprod_admission_gaps", "proposed_executor_migration_notes",
    "registration_proposal", "run_authority_density", "validate_density_profile",
]
