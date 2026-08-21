"""Community ``StaffDeptPlugin`` — no staff directory.

A real, deployable impl (not a ``MockSeam`` test double). The community build
has no HR org master-data service, so it reports "no dept" — an all-``None``
:class:`StaffDeptInfo` — rather than a failure. The ``org/user`` whoami stays
available with null ``dept_*``; callers never block on a service the OSS build
cannot reach.
"""
from __future__ import annotations

from agentclaw.community.plugin_api.staff_dept import (
    DeptSearchItem,
    StaffDeptInfo,
    StaffDeptPlugin,
)


class NoStaffDept(StaffDeptPlugin):
    """Community profile: staff department info is not available."""

    def get_dept_by_work_no(self, *, work_no: str) -> StaffDeptInfo:
        return StaffDeptInfo()

    def search_depts(self, *, keyword: str) -> list[DeptSearchItem]:
        return []
