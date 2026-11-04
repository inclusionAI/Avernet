"""Structural and report invariants for the one-shot repair service."""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

from agentclaw.community.kernel.bot_manager_sync import BOT_NOT_FOUND_CODE
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
        "sync_caller",
    ]
    assert [c[0] for c in repair_case.ownership.method_calls] == ["replace_ownership"]
    assert [c[0] for c in repair_case.managers.method_calls] == [
        "replace_managers"
    ]


def test_both_sync_readers_share_the_kernel_not_found_code():
    """The reader vocabulary is declared once, in the kernel."""
    for module_name in (
        "agentclaw.community.core.repository.implementations.bot.ownership_sync",
        "agentclaw.community.core.repository.implementations.bot.manager_sync",
    ):
        module = importlib.import_module(module_name)
        tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value == BOT_NOT_FOUND_CODE
        ]
        assert literals == [], f"{module_name} re-spells the kernel code"
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert "BOT_NOT_FOUND_CODE" in names, module_name


def test_report_cannot_be_built_from_three_items():
    """The four-parameter signature makes a short report unbuildable."""
    with pytest.raises(TypeError):
        _report(  # type: ignore[call-arg]
            default_repair_facts(),
            RepairItem("ownership", "succeeded"),
            RepairItem("profile", "succeeded"),
            RepairItem("managers", "succeeded"),
        )


def test_report_rejects_an_item_in_the_wrong_slot():
    """Slot order is the kernel's, so a swapped pair is a programming error."""
    with pytest.raises(ValueError, match="fixed report order"):
        _report(
            default_repair_facts(),
            RepairItem("profile", "succeeded"),
            RepairItem("ownership", "succeeded"),
            RepairItem("managers", "succeeded"),
            RepairItem("caller_config", "succeeded"),
        )
