"""Use only the shared live stack; no acceptance-owned device/process allocation."""
import os

import pytest

from tests.community.acceptance.collaboration_repair.live_case import RepairCase


@pytest.fixture
def repair_case(live_backend):
    assert os.environ.get("SINGLEBOX_ACCEPTANCE_REUSE_LIVE") == "1", (
        "Run collaboration-repair acceptance through the shared singlebox coverage runner"
    )
    cases = []

    def create(**kwargs):
        case = RepairCase(live_backend)
        cases.append(case)
        case.seed(**kwargs)
        return case

    try:
        yield create
    finally:
        for case in reversed(cases):
            case.close()
