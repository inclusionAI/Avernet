def test_cron_router_does_not_import_old_auth():
    """api/cron/router.py must not import from servers.web or infrastructure."""
    import ast
    import pathlib
    src = pathlib.Path(
        "src/agentclaw/community/adapters/http/cron/router.py"
    ).read_text()
    tree = ast.parse(src)
    bad_prefixes = (
        "agentclaw.servers.web",
        "agentclaw.infrastructure",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for bad in bad_prefixes:
                assert not node.module.startswith(bad), (
                    f"api/cron/router.py imports from forbidden path: {node.module}"
                )
