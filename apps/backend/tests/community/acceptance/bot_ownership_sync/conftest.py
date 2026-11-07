"""Use only the shared live stack; no acceptance-owned device/process allocation."""
import os

import pytest

from tests.community.acceptance.bot_ownership_sync.live_case import OwnershipCase


@pytest.fixture
def owner_case(live_backend):
    assert os.environ.get("SINGLEBOX_ACCEPTANCE_REUSE_LIVE") == "1", (
        "Run ownership acceptance through the shared singlebox coverage runner"
    )
    cases = []

    def create(**kwargs):
        case = OwnershipCase(live_backend)
        cases.append(case)
        case.seed(**kwargs)
        return case

    try:
        yield create
    finally:
        for case in reversed(cases):
            case.close()
