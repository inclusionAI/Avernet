"""Wave E2b result-queue density package (executor row #20).

Offlinedensity backfill for story_state_machine_result_queue: 18 frozen
RESULT-QUEUE.* contract parents plus 34 source-anchored atoms from the frozen
one-shot result-queue story (52 parsed; residual_count_gap=0).  The package
is credential-free and offline: obligations anchors verbatim story bytes,
replay projects an in-memory owned-local receipt through the frozen
dynamic-family adjudication faces, and registration_proposal is
DESCRIBE_ONLY text for the owner session.
"""
from .obligations import (
    DENSITY_CONTRACT, DENSITY_OBLIGATION_COUNT, DRIVER_TOKEN_ENV,
    EXECUTOR_ROW_INDEX, HUMAN_COOKIE_ENV, PARSED_OBLIGATION_COUNT, PARENT,
    PARENT_OBLIGATION_COUNT, RESIDUAL_COUNT_GAP, SOURCE_FILE, SOURCE_SHA256,
    ZERO_ATOM_PARENTS, density_plan, obligations)
from .registration_proposal import (
    PROPOSAL_TOKEN, describe_only_mainline_fragment, registration_proposal)
from .replay import (
    STATUS_BLOCKED_INVENTORIED, STATUS_FAIL, STATUS_NOT_RUN, STATUS_PASS,
    mock_observations, project)

__all__ = [
    "DENSITY_CONTRACT", "DENSITY_OBLIGATION_COUNT", "DRIVER_TOKEN_ENV",
    "EXECUTOR_ROW_INDEX", "HUMAN_COOKIE_ENV", "PARSED_OBLIGATION_COUNT", "PARENT",
    "PARENT_OBLIGATION_COUNT", "PROPOSAL_TOKEN", "RESIDUAL_COUNT_GAP", "SOURCE_FILE",
    "SOURCE_SHA256", "STATUS_BLOCKED_INVENTORIED", "STATUS_FAIL", "STATUS_NOT_RUN",
    "STATUS_PASS", "ZERO_ATOM_PARENTS", "density_plan",
    "describe_only_mainline_fragment", "mock_observations", "obligations", "project",
    "registration_proposal",
]
