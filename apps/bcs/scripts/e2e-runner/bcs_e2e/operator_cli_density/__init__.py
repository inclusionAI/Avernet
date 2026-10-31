"""Wave idx9 operator-cli density package (executor row #9).

Offline density backfill for story_operator_coordinates_with_cli: the 13
retained OPERATOR-CLI.* contract parents of operator-cli-migration-contract.json
plus 15 source-anchored shell assertion atoms (OPCLI.F01..OPCLI.F15 under the
OPERATOR-CLI.* site envelope; 28 parsed = 13 legacy parents + 15 atoms;
residual_count_gap=0).  The story is a shell harness story, so the anchors are
SHA-256-frozen (file, line_start, line_end) spans inside stories.sh / cli.sh /
cli-stories.sh that density rebuilds verbatim from disk bytes and fails closed
on any drift.  replay projects credential-free in-memory observations into a
28-row honest receipt (local green mirror; preprod NOT_RUN with four named
PREPROD_* gaps), and registration_proposal is DESCRIBE_ONLY text for the owner
session.  The package is credential-free and offline.
"""
from .contract import (
    ADAPTER_METHODS, CLI_OPERATIONS, COMPANION_SOURCES, COVERAGE_ROW_NO,
    DENSITY_CONTRACT, DENSITY_OBLIGATION_COUNT, DUAL_COMMAND, DUAL_SUITE,
    EXECUTOR_ROW_INDEX, FAMILY, PARSED_OBLIGATION_COUNT, PARENT_OBLIGATION_COUNT,
    PARENT_STORY, PREPROD_GAPS, PROFILE_CONTRACT, REQUIREMENTS, RESIDUAL_COUNT_GAP,
    REPORT_CONTRACT, SOURCE_FILE, SOURCE_SHA256, STORY_LINE_SPAN, ZERO_ATOM_PARENTS)
from .density import (
    ASSERTION_IDS, EXPECTED_LEAF_COUNTS, SITE_IDS, Obligation, density_plan,
    obligations)
from .registration_proposal import (
    EXPECTED_ROW_FACE, MD_ROW, NOTE, NAMED_GAP_DETAILS, PROPOSAL_TOKEN, STORY_ID,
    describe_only_mainline_fragment, registration_proposal)
from .replay import (
    STATUS_BLOCKED_INVENTORIED, STATUS_FAIL, STATUS_NOT_RUN, STATUS_PASS,
    mock_observations, project)

__all__ = [
    "ADAPTER_METHODS", "ASSERTION_IDS", "CLI_OPERATIONS", "COMPANION_SOURCES",
    "COVERAGE_ROW_NO", "DENSITY_CONTRACT", "DENSITY_OBLIGATION_COUNT",
    "DUAL_COMMAND", "DUAL_SUITE", "EXPECTED_LEAF_COUNTS", "EXPECTED_ROW_FACE",
    "EXECUTOR_ROW_INDEX", "FAMILY", "MD_ROW", "NOTE", "NAMED_GAP_DETAILS",
    "Obligation", "PARSED_OBLIGATION_COUNT", "PARENT_OBLIGATION_COUNT",
    "PARENT_STORY", "PREPROD_GAPS", "PROFILE_CONTRACT", "PROPOSAL_TOKEN",
    "REQUIREMENTS", "RESIDUAL_COUNT_GAP", "REPORT_CONTRACT", "SITE_IDS",
    "SOURCE_FILE", "SOURCE_SHA256", "STATUS_BLOCKED_INVENTORIED", "STATUS_FAIL",
    "STATUS_NOT_RUN", "STATUS_PASS", "STORY_ID", "STORY_LINE_SPAN",
    "ZERO_ATOM_PARENTS", "density_plan", "describe_only_mainline_fragment",
    "mock_observations", "obligations", "project", "registration_proposal",
]
