"""Direct-conversation density backfill for the frozen Direct Chat parent.

Wave-D1 closes the clause layer of ``story_user_has_direct_agent_conversation``
(coverage-map row 13): the upstream parsed roster is 14 clauses (wave3 clause
map S13, ``stories.sh`` L776-805, with the L789 flow guard counted), while the
committed family carries five request-level DM obligations.  This subpackage:

* ``contract`` — freezes the 14 source-anchored density rows with the DM
  parent projection and the honest roster accounting (parsed 14, parents 5,
  delta 9 closed clause-by-clause, family total 19);
* ``defect`` — registers the on-file DM-TERMINAL-CANCEL-PROJECTION defect and
  the three honest gaps (idempotency not fabricated, defect disposition,
  preprod execution);
* ``profile`` — admits the dual-environment leg profiles structurally with
  environment-variable NAME references only;
* ``transport`` — the injection boundary plus the canonical defect-faithful
  offline replay (cancelled terminal carries the on-file downstream_error);
* ``run`` — the offline executor producing honest receipts: local defect-
  faithful rehearsal with an injected transport, preprod/transport-less
  NOT_RUN gap receipts, ``remote_validation=NOT_RUN`` forever;
* ``reporting`` — mechanical receipt summarization and proposed accounting
  notes;
* ``registration_proposal`` — describe-only mainline integration text: the
  density obligations ride the existing ``dual-direct-chat-run`` envelope, no
  new registered command, no shared-face edit.

No module here executes a transport, launches a process, contacts preprod,
resolves a credential, or edits a shared face.
"""

from __future__ import annotations

from .contract import (
    DENSITY_ASSERTION_COUNT,
    DENSITY_CONTRACT,
    DENSITY_OPERATIONS,
    FAMILY_OBLIGATION_TOTAL,
    MAIN_CANCEL_OPERATION,
    MAIN_READ_OPERATION,
    MAIN_SUBMIT_OPERATION,
    MAIN_TERMINAL_OPERATION,
    PARSED_CLAUSE_COUNT,
    PARSED_MINUS_PARENT_OBLIGATIONS,
    PARENT,
    PARENT_OBLIGATION_COUNT,
    ParsedDensityAssertion,
    ROSTER_PROVENANCE,
    direct_conversation_density_plan,
    parsed_density_assertions,
)
from .defect import (
    DEFECT_GATED_ASSERTION_IDS,
    DEFECT_GATED_CLAUSES,
    DEFECT_REGISTRY,
    DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID,
    DOCUMENTED_GAPS,
    DOWNSTREAM_ERROR_MARKER,
    GAP_LEDGER,
    assert_defect_gap_alignment,
    defect_gate_descriptor,
    gap_ledger_descriptor,
)
from .profile import (
    DENSITY_OPERATION_BINDINGS,
    DENSITY_PROFILE_CONTRACT,
    validate_density_profile,
)
from .registration_proposal import (
    ENVELOPE_COMMAND,
    PROPOSAL_STATUS,
    assert_anchors,
    registration_proposal,
)
from .reporting import (
    density_accounting,
    family_accounting,
    md_table,
    proposed_coverage_map_note,
    proposed_executor_migration_notes,
)
from .run import (
    ASSERTION_FAILED,
    BLOCKED_AFTER_FAILURE,
    JOURNEY_BLOCKED,
    STATUS_BLOCKED,
    STATUS_FAIL,
    STATUS_NOT_RUN,
    STATUS_PASS,
    TRANSPORT_NOT_INJECTED,
    blocked_direct_density_receipt,
    run_direct_conversation_density,
)
from .transport import (
    CANONICAL_MEMBER_BOT_ID,
    CANONICAL_RUN_ID,
    CANONICAL_SESSION_ID,
    DensityTransport,
    Observation,
    ProjectionReplayTransport,
    ReplayTransport,
    canonical_defect_faithful_observations,
)

__all__ = [
    "ASSERTION_FAILED",
    "BLOCKED_AFTER_FAILURE",
    "CANONICAL_MEMBER_BOT_ID",
    "CANONICAL_RUN_ID",
    "CANONICAL_SESSION_ID",
    "DENSITY_ASSERTION_COUNT",
    "DENSITY_CONTRACT",
    "DENSITY_OPERATION_BINDINGS",
    "DENSITY_OPERATIONS",
    "DENSITY_PROFILE_CONTRACT",
    "DEFECT_GATED_ASSERTION_IDS",
    "DEFECT_GATED_CLAUSES",
    "DEFECT_REGISTRY",
    "DM_CANCEL_TERMINAL_PROJECTION_DEFECT_ID",
    "DOCUMENTED_GAPS",
    "DOWNSTREAM_ERROR_MARKER",
    "DensityTransport",
    "ENVELOPE_COMMAND",
    "FAMILY_OBLIGATION_TOTAL",
    "GAP_LEDGER",
    "JOURNEY_BLOCKED",
    "MAIN_CANCEL_OPERATION",
    "MAIN_READ_OPERATION",
    "MAIN_SUBMIT_OPERATION",
    "MAIN_TERMINAL_OPERATION",
    "Observation",
    "PARSED_CLAUSE_COUNT",
    "PARSED_MINUS_PARENT_OBLIGATIONS",
    "PARENT",
    "PARENT_OBLIGATION_COUNT",
    "ParsedDensityAssertion",
    "ProjectionReplayTransport",
    "PROPOSAL_STATUS",
    "ReplayTransport",
    "ROSTER_PROVENANCE",
    "STATUS_BLOCKED",
    "STATUS_FAIL",
    "STATUS_NOT_RUN",
    "STATUS_PASS",
    "TRANSPORT_NOT_INJECTED",
    "assert_anchors",
    "assert_defect_gap_alignment",
    "blocked_direct_density_receipt",
    "canonical_defect_faithful_observations",
    "density_accounting",
    "direct_conversation_density_plan",
    "family_accounting",
    "gap_ledger_descriptor",
    "defect_gate_descriptor",
    "md_table",
    "parsed_density_assertions",
    "proposed_coverage_map_note",
    "proposed_executor_migration_notes",
    "registration_proposal",
    "run_direct_conversation_density",
    "validate_density_profile",
]
