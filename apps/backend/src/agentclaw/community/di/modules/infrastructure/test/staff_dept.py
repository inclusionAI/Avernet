"""Staff-dept concern — test / singlebox binding (local noop)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin


class TestStaffDeptModule(Module):
    """test / singlebox: local noop staff-dept (dept stays null — no MOSN)."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.local.staff_dept import LocalStaffDeptService

        binder.bind(StaffDeptPlugin, to=LocalStaffDeptService, scope=singleton)
