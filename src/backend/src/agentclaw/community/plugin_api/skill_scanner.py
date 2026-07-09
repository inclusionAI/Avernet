"""Capability Protocol for static skill scanning.

A skill scanner statically analyses skill packages to extract their MCP
dependencies and risk tags (written back to skill metadata). The concrete
scanner is a vendor SDK in the corp build; this Protocol keeps that dependency
out of ``core/skill_center`` — core obtains an opaque scanner handle via
``create_sdk()`` and never imports the SDK.

The community build has no scanner (``is_available()`` is ``False``,
``create_sdk()`` returns ``None``), so auto-detection is simply skipped — skills
may still declare their MCP dependencies in their own metadata.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class SkillScannerPlugin(Plugin, Protocol):
    """Provides an initialized skill-scanner SDK handle (or none)."""

    def is_available(self) -> bool:
        """True iff a usable scanner is configured (SDK installed + credential)."""
        ...

    def create_sdk(self) -> Any | None:
        """Return an initialized scanner SDK, or ``None`` when unavailable.

        The returned object is used duck-typed by the scan service
        (``scan`` / ``scan_git_repo`` / ``get_mcp_dependencies`` /
        ``start_scheduler`` / ``add_scheduled_git_scan``). ``None`` disables
        scanning.
        """
        ...
