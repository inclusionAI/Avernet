"""Structural and report invariants for the one-shot repair service."""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

from agentclaw.community.kernel.collaboration_repair import RepairItem

from tests.community.core.bot_management.collaboration_repair.factories import (
    default_repair_facts,
    make_repair_case,
)

_SERVICE_MODULE = (
    "agentclaw.community.core.bot_management.collaboration_repair.service"
)


@pytest.fixture
def repair_case():
    return make_repair_case()


def _service_module():
    """The service module object (structural-import inspection target)."""
    return importlib.import_module(_SERVICE_MODULE)


def _report(*items):
    """The service's fixed-order report builder, for the invariant pins."""
    from agentclaw.community.core.bot_management.collaboration_repair.service import (
        CollaborationRepairService,
    )

    return CollaborationRepairService._report(*items)


def test_service_module_never_imports_admission_or_writers():
    """No queue submission surface, no repository writers, no plugin impls."""
    service_path = pathlib.Path(_service_module().__file__)
    tree = ast.parse(service_path.read_text(encoding="utf-8"))
    forbidden = (
        "agentclaw.community.api",
        "agentclaw.community.core.repository.implementations",
        "agentclaw.community.core.bot_collaborator.manager_sync.dispatcher",
        "agentclaw.community.core.bot_collaborator.manager_sync.lifecycle",
        "agentclaw.community.core.bot_collaborator.manager_sync.notifications",
        "agentclaw.community.core.bot_management.ownership_sync.notifications",
        "agentclaw.community.plugins",
        "agentclaw.community.di",
    )
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            imported.append(module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    hits = [module for module in imported if module.startswith(forbidden)]
    assert hits == [], f"forbidden admission/write imports: {hits}"


@pytest.mark.asyncio
async def test_no_unexpected_calls_beyond_the_contract(repair_case):
    """Only the declared contract methods are touched on every dependency."""
    await repair_case.service.repair(repair_case.command)
    assert [c[0] for c in repair_case.reader.method_calls] == ["read_facts"]
    assert [c[0] for c in repair_case.ownership_reader.method_calls] == ["read_facts"]
    assert [c[0] for c in repair_case.managers_reader.method_calls] == ["read_facts"]
    assert [c[0] for c in repair_case.metadata.method_calls] == [
        "exists",
        "update_profile",
    ]
    assert [c[0] for c in repair_case.ownership.method_calls] == ["replace_ownership"]
    assert [c[0] for c in repair_case.managers.method_calls] == [
        "replace_managers"
    ]


def test_report_rejects_an_item_in_the_wrong_slot():
    """Slot order is the kernel's, so a swapped pair is a programming error."""
    with pytest.raises(ValueError, match="fixed report order"):
        _report(
            default_repair_facts(),
            RepairItem("profile", "succeeded"),
            RepairItem("ownership", "succeeded"),
            RepairItem("managers", "succeeded"),
        )
