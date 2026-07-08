"""Architecture guard: LocalDeviceService must not import ProcessManagerProtocol.

After the BaaS migration (see docs/superpowers/specs/
2026-06-05-singlebox-local-device-service-to-baas-design.md), LocalDeviceService
delegates the "spawn local engine process" responsibility entirely to BaaS via
BaasService. Backend must never touch LocalProcessManager from LocalDeviceService.

This is a mechanical safeguard against accidental re-introduction during
refactors / merge conflicts. Pair with inventory-devices.md A*-3 which records
LocalProcessManager as out-of-scope BaaS-internal concern.
"""
import ast
from pathlib import Path


LOCAL_DEVICE_SERVICE_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "agentclaw"
    / "community"
    / "core"
    / "devices"
    / "services"
    / "local_device_service.py"
)


def test_local_device_service_does_not_import_process_manager():
    assert LOCAL_DEVICE_SERVICE_PATH.exists(), (
        f"Expected file at {LOCAL_DEVICE_SERVICE_PATH}"
    )

    src = LOCAL_DEVICE_SERVICE_PATH.read_text()
    tree = ast.parse(src)
    forbidden = {"ProcessManagerProtocol", "LocalProcessManager"}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in forbidden, (
                    f"LocalDeviceService must not import {alias.name}; "
                    "singlebox lifecycle has been migrated to BaaS. "
                    "See docs/superpowers/specs/"
                    "2026-06-05-singlebox-local-device-service-to-baas-design.md"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden, (
                    f"LocalDeviceService must not import {alias.name}; "
                    "singlebox lifecycle has been migrated to BaaS."
                )


def test_local_device_service_does_not_reference_process_manager_at_runtime():
    """Active code (not docstrings/comments) must not reference ProcessManager.

    Allows docstring/comment mentions explaining the historical migration.
    """
    src = LOCAL_DEVICE_SERVICE_PATH.read_text()
    forbidden_tokens = ("self._process_manager", "ProcessManagerProtocol")

    for token in forbidden_tokens:
        offending_lines = []
        for lineno, line in enumerate(src.splitlines(), start=1):
            stripped = line.lstrip()
            # Skip comment lines and triple-quoted docstring boundaries
            if stripped.startswith("#"):
                continue
            if stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if token in line:
                offending_lines.append(f"{lineno}: {line.rstrip()}")

        assert not offending_lines, (
            f"Found active reference to '{token}' in LocalDeviceService:\n"
            + "\n".join(offending_lines)
            + "\nSinglebox lifecycle migrated to BaaS — drop the reference."
        )
