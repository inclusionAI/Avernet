"""Describe-only serial integration proposal for Wave-W2-#12."""
from __future__ import annotations

PROPOSAL_TOKEN = "DRAFT_DELIVERY_DENSITY_24_OBLIGATIONS_20261031"


def describe_only_mainline_fragment() -> str:
    """Return the text the mainline integrator may apply; write nothing here."""
    return (
        f"{PROPOSAL_TOKEN}: row #12 story_collaboration_draft_delivery proposes "
        "8 existing parent obligations plus 24 source-anchored density obligations; "
        "executor-migration.json notes-only integration; no new registration command "
        "and no edits to inventory.py, cli.py, artifact.py, "
        "case_command_registration.py, or coverage-map/*; retain preprod NOT_RUN, "
        "grants_release_credit=false, and named 302 product-defect fail-close."
    )
