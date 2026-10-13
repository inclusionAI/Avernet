"""Device delivery catches contract exceptions, not a concrete Pool implementation."""

import ast
from pathlib import Path


def test_device_callback_error_is_imported_from_its_declared_contract():
    backend = Path(__file__).resolve().parents[3]
    router = backend / "src/agentclaw/community/adapters/http/devices/router.py"
    imports = [node for node in ast.walk(ast.parse(router.read_text())) if isinstance(node, ast.ImportFrom)]
    assert not any(
        node.module == "agentclaw.community.core.skills_pool.native_confirmation"
        for node in imports
    )
    assert any(
        node.module == "agentclaw.community.core.devices.protocols"
        and any(name.name == "LayoutInitializationConfirmationError" for name in node.names)
        for node in imports
    )
