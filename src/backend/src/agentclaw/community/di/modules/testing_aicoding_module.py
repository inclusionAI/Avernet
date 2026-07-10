"""TestingAicodingModule — local-mode override for aicoding services.

⚠️ TEMP LOCAL-DEV PATCH (临时本地改动): the stub ``WorkspaceHostingService``
below has been temporarily swapped for the REAL ``WorkspaceHostingService`` so
create_bot(applicationCoding) actually calls the configured DIMA OpenAPI under
``--local`` instead of returning a synthetic ``W_STUB_<bot_id>``.

This serves BOTH the singlebox local-dev boot AND the pytest test column.
pytest tests that asserted on ``W_STUB_*`` workspace IDs will break — that's
expected for a throwaway local-dev patch. Restore from
/tmp/testing_aicoding_module.py.bak when done.

The real service's ``@inject`` constructor auto-wires the real
``WorkspaceHostingClient`` that ``TestAppServicesModule`` builds from the YAML
``dima`` block, so end-to-end workspace creation hits the configured DIMA
endpoint.
"""
from __future__ import annotations

from injector import Binder, Module, singleton

from agentclaw.community.core.bot_management.services.workspace_hosting_service import (
    WorkspaceHostingService,
)


class TestingAicodingModule(Module):
    """Bind the REAL WorkspaceHostingService (temp local-dev patch)."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            WorkspaceHostingService, to=WorkspaceHostingService, scope=singleton
        )
