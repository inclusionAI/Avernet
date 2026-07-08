"""Community AICoding service — no AntCode workflow catalog.

The open-source build has no AntCode git integration, so the workflow catalog is
empty. ``GET /workflows`` returns ``[]`` instead of erroring. Corp-free: imports
only the neutral ``api`` Protocol shape (structural — not even that at runtime).
"""
from __future__ import annotations

from typing import List, Optional


class NoopWorkflowCatalogService:
    """Empty workflow catalog for the community build (no AntCode)."""

    async def list_workflows(self, branch: Optional[str] = None) -> List[dict]:
        return []
