"""Test that core/bot_management/dependencies/models.py does not import from services layer."""


def test_bot_management_models_does_not_import_services():
    """Verify models.py doesn't depend on services/ layer."""
    import ast
    import pathlib
    src = pathlib.Path(
        "src/agentclaw/community/core/bot_management/dependencies/models.py"
    ).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(
                "agentclaw.services"
            ), f"Forbidden import: {node.module}"
