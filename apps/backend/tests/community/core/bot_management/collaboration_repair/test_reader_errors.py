"""Unclassified reader failures must not impersonate request deadlines."""

import pytest

from tests.community.core.bot_management.collaboration_repair.factories import (
    make_repair_case,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["initial", "ownership", "managers"])
async def test_reader_timeout_error_propagates_without_deadline_expiry(phase):
    case = make_repair_case()
    reader = {
        "initial": case.reader,
        "ownership": case.ownership_reader,
        "managers": case.managers_reader,
    }[phase]
    error = TimeoutError("unclassified read error")
    reader.read_facts.side_effect = error
    with pytest.raises(TimeoutError, match="unclassified read error"):
        await case.service.repair(case.command)
    case.managers.replace_managers.assert_not_awaited()
    case.metadata.sync_caller.assert_not_awaited()
