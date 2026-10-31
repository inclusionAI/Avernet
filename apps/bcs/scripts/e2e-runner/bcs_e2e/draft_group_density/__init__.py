"""Wave E2b draft-group density package (executor row #40).

Offline density backfill for story_collaboration_draft_group_launch: 17
frozen LOCAL-DRAFT-GROUP.* contract parents plus 75 source-anchored weighted
atoms from the frozen 3-bot author/driver/reviewer Draft-group launch story
(92 parsed; residual_count_gap=0).  The package is credential-free and
offline: obligations anchors verbatim story bytes, replay projects an
in-memory owned-local receipt through the frozen dynamic-family adjudication
faces, and registration_proposal is DESCRIBE_ONLY text for the owner session.
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
