"""Wave idx1 agent-network density package (executor row index 1).

Offline density backfill for story_user_prepares_agent_network: 8 frozen
NETWORK.* contract parents plus 3 source-anchored leaves from the frozen
poolab parsed snapshot (11 parsed = 8 parents + 3 non-PASS records;
residual_count_gap=0).  The package is credential-free and offline:
obligations anchor verbatim shell bytes and the frozen parsed records, replay
mirrors the committed r7 citation and the owned-local LOCAL-NETWORK-01 face
in memory (the 3 density leaves are frozen record mirrors, never probes), and
registration_proposal is DESCRIBE_ONLY text for the owner session.  Note the
E2b-inherited name shadow: ``obligations`` imported from this package is the
builder function; use importlib for the module object.
"""
from .obligations import (
    DENSITY_CONTRACT, DENSITY_OBLIGATION_COUNT, DRIVER_TOKEN_ENV,
    EXECUTOR_ROW_INDEX, HUMAN_COOKIE_ENV, PARSED_OBLIGATION_COUNT, PARENT,
    PARENT_OBLIGATION_COUNT, RESIDUAL_COUNT_GAP, SOURCE_FILE, SOURCE_SHA256,
    density_plan, obligations)
from .registration_proposal import (
    PROPOSAL_TOKEN, describe_only_mainline_fragment, registration_proposal)
from .replay import (
    STATUS_FAIL, STATUS_NOT_RUN, STATUS_PASS, mock_observations, project)

__all__ = [
    "DENSITY_CONTRACT", "DENSITY_OBLIGATION_COUNT", "DRIVER_TOKEN_ENV",
    "EXECUTOR_ROW_INDEX", "HUMAN_COOKIE_ENV", "PARSED_OBLIGATION_COUNT",
    "PARENT", "PARENT_OBLIGATION_COUNT", "PROPOSAL_TOKEN", "RESIDUAL_COUNT_GAP",
    "SOURCE_FILE", "SOURCE_SHA256", "STATUS_FAIL", "STATUS_NOT_RUN",
    "STATUS_PASS", "density_plan", "describe_only_mainline_fragment",
    "mock_observations", "obligations", "project", "registration_proposal",
]
