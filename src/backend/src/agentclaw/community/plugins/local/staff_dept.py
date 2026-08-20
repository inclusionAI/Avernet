"""Local ``StaffDeptPlugin`` — noop impl for offline / single-box dev.

The real staff directory talks to MOSN/Layotto (the company HR org master-data
service), which isn't reachable on a dev laptop. Local impl returns an
all-``None`` :class:`StaffDeptInfo` — "no dept", not a failure — so the
``org/user`` whoami stays available in singlebox with null ``dept_*``.
"""
from __future__ import annotations

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugin_api.staff_dept import (
    DeptSearchItem,
    StaffDeptInfo,
    StaffDeptPlugin,
)
from agentclaw.community.plugins.local._mock_seam import MockSeam

logger = get_logger()


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="no I/O in method bodies",
)
class LocalStaffDeptService(MockSeam, StaffDeptPlugin):
    """No-op staff-dept service for local mode.

    No constructor deps (the prod impl needs MOSN/Layotto + a Mist secret; we
    have nothing to wire).
    """

    def get_dept_by_work_no(self, *, work_no: str) -> StaffDeptInfo:
        logger.info(
            "[LocalStaffDeptService.get_dept_by_work_no] noop — work_no=%s",
            work_no,
        )
        return StaffDeptInfo()

    def search_depts(self, *, keyword: str) -> list[DeptSearchItem]:
        logger.info(
            "[LocalStaffDeptService.search_depts] noop — keyword=%s",
            keyword,
        )
        return []
