"""Staff-dept concern — community binding (no staff directory)."""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin


class CommunityStaffDeptModule(Module):
    """community: no staff directory (dept stays null)."""

    def configure(self, binder: Binder) -> None:
        from agentclaw.community.plugins.community.staff_dept import NoStaffDept

        binder.bind(StaffDeptPlugin, to=NoStaffDept, scope=singleton)
