"""Capability Protocol for staff department information.

Lookups up a caller's department by work number against an external staff
directory, and searches the department directory by keyword. The corp
implementation talks to the company HR org master-data service
(``Employee360Service`` / ``EmployeeDeptService`` via MOSN/Layotto); the community
implementation has no such service and reports "no dept" (all-``None``) / an empty
search result so callers fall through; the local noop returns the same shapes for
offline dev.

Dept is **optional profile data, not an auth decision**: a returned
:class:`StaffDeptInfo` whose fields are all ``None`` means "looked up, the person
genuinely has no dept" — the caller answers 200 with null, not a failure. A
:class:`DeptSearchItem` list that is empty means "no departments matched the
keyword" — also 200, not a failure. Infrastructure failure is signalled by
raising :class:`DeptLookupError`, which the caller surfaces as a 5xx (distinct
from auth 401, from no-dept 200, and from no-match 200).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agentclaw.community.plugin_api.base import Plugin


@dataclass(frozen=True)
class StaffProfileInfo:
    """Basic employee profile data returned by the staff directory."""

    work_no: str
    nick_name: str | None = None


class StaffProfileLookupError(RuntimeError):
    """Raised when the staff profile infrastructure cannot be queried."""


@dataclass(frozen=True)
class StaffDeptInfo:
    """A person's department identity, read from the staff directory.

    Every field is ``str | None``: ``None`` is an intentional contract state, not
    defensive widening — an identity provider (or the staff service) may genuinely
    not supply a value, and they are null rather than invented when absent.
    """

    dept_no: Optional[str] = None
    dept_name: Optional[str] = None
    dept_path: Optional[str] = None


class DeptSearchItem(BaseModel):
    """One department returned by a keyword search of the directory.

    All three are present: a directory entry matched by the search has a number,
    a name, and a lineage path. The list is empty when nothing matched (200), not
    when the service failed — failure raises and is answered 5xx.
    """

    model_config = {"frozen": True}

    dept_no: str = Field(
        description="The department's number (deptNo) — unique within the tenant."
    )
    dept_name: str = Field(description="The department's display name (deptName).")
    dept_path: str = Field(
        description="The department's lineage path (deptPath), root-to-this-dept."
    )


@runtime_checkable
class StaffDeptPlugin(Plugin, Protocol):
    """Resolves a caller's department by work number, and searches the directory.

    Raises :class:`DeptLookupError` only when the backing staff directory is
    unreachable or reports an infrastructure failure — so the caller can tell
    "no dept" / "no match" (data absence, 200) from "directory down" (infra
    failure, 5xx).
    """

    def get_profile_by_work_no(self, *, work_no: str) -> StaffProfileInfo:
        """Return the employee profile, or a null nickname when absent."""
        ...

    def get_dept_by_work_no(self, *, work_no: str) -> StaffDeptInfo:
        """Return the department for ``work_no``, or an all-``None`` info.

        Implementations that have no staff service (community / local) return
        ``StaffDeptInfo()`` — "no dept", not a failure. A real impl that reached
        the service and found no record / a record with no dept returns the same
        all-``None`` shape. Only an unreachable or erroring service raises.
        """
        ...

    def search_depts(self, *, keyword: str) -> list[DeptSearchItem]:
        """Fuzzy-search department names matching ``keyword``.

        Returns the matching departments (empty list when none match — 200, not a
        failure). Implementations with no staff service return ``[]``. Only an
        unreachable or erroring service raises :class:`DeptLookupError`.
        """
        ...
