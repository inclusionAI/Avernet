"""Singlebox live query of the BBS task-mode candidate roster — DEFERRED.

``list_bots_by_task_modes`` now lives on :class:`BcnService` (unified
``BcnConfig`` provider identity, ``GET /providers/{provider_id}/bots/by-task-modes``,
exercised in pre/prod), not on the BCS adapter. This singlebox live e2e is
deferred until the singlebox BBS-roster path is re-enabled; the query logic is
covered by ``test_bcn_service_list_bots_by_task_modes``.

Re-enable against ``BcnService`` (DI-injected unified provider identity) when the
singlebox roster path is revived — do NOT re-add the removed BcsHttpAdapter route.
"""
from __future__ import annotations

import pytest


@pytest.mark.skip(
    reason="singlebox roster deferred: list_bots_by_task_modes moved to "
    "BcnService (unified BcnConfig provider identity, exercised in pre/prod); "
    "singlebox path mocked/ignored"
)
def test_singlebox_queries_global_bcs_bots_by_task_modes() -> None:
    """Deferred — rewrite against ``BcnService`` to re-enable (see module docstring)."""
