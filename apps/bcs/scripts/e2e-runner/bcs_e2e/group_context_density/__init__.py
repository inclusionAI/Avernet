"""Offline density projection for the Group Context CLI story.

The package expands the nine committed Group Context parent obligations into
nine source-anchored response details, producing an 18-point roster without
changing the existing ``dual-group-context-run`` envelope.  Owned-local runs
use a deterministic replay; preprod remains an honest NOT_RUN gap.
"""

from .contract import (
    CASE_ID,
    CONTRACT,
    FIELD_OBLIGATION_COUNT,
    PARENT,
    PARENT_OBLIGATION_COUNT,
    PARSED_OBLIGATION_COUNT,
    RESIDUAL_COUNT_GAP,
    SOURCE_SHA256,
    Obligation,
    group_context_density_plan,
    parsed_obligations,
)
from .defect import GROUP_CONTEXT_PREPROD_GAP_ID, honesty_ledger
from .profile import PROFILE_CONTRACT, validate_density_profile
from .reporting import (
    INTEGRATION_TOKEN,
    density_accounting,
    family_accounting,
    proposed_executor_migration_notes,
)
from .registration_proposal import registration_proposal
from .run import (
    STATUS_BLOCKED_INVENTORIED,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    run_group_context_density,
)
from .transport import Observation, ReplayTransport, canonical_replay_observations

__all__ = [
    "CASE_ID", "CONTRACT", "FIELD_OBLIGATION_COUNT", "GROUP_CONTEXT_PREPROD_GAP_ID",
    "INTEGRATION_TOKEN", "Obligation", "Observation", "PARENT",
    "PARENT_OBLIGATION_COUNT", "PARSED_OBLIGATION_COUNT", "PROFILE_CONTRACT",
    "RESIDUAL_COUNT_GAP", "SOURCE_SHA256", "ReplayTransport",
    "STATUS_BLOCKED_INVENTORIED", "STATUS_FAIL", "STATUS_NOT_RUN", "STATUS_PASS",
    "canonical_replay_observations", "density_accounting", "family_accounting",
    "group_context_density_plan", "honesty_ledger", "parsed_obligations",
    "proposed_executor_migration_notes", "registration_proposal",
    "run_group_context_density", "validate_density_profile",
]
