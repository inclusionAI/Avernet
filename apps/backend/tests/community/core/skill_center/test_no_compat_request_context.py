"""Verify RequestContext and storage_path are no longer imported from compat."""
import ast
import inspect


def _get_import_names(module) -> set[str]:
    """Return all names imported in the module (excluding docstrings/comments)."""
    source = inspect.getsource(module)
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imported = alias.asname if alias.asname else alias.name
                names.add(imported)
    return names


def _get_compat_imports(module) -> dict[str, list[str]]:
    """Return {module_path: [imported_names]} for imports from compat."""
    source = inspect.getsource(module)
    tree = ast.parse(source)
    result = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "compat" in node.module:
            names = [a.asname or a.name for a in node.names]
            result[node.module] = names
    return result


class TestNoCompatRequestContext:
    def test_skills_no_compat_request_context(self):
        from agentclaw.community.adapters.http.skill_center import skills as mod
        compat_imports = _get_compat_imports(mod)
        for mod_path, names in compat_imports.items():
            assert "get_request_context" not in names
            assert "RequestContext" not in names

    def test_skillsets_no_compat_request_context(self):
        from agentclaw.community.adapters.http.skill_center import skillsets as mod
        compat_imports = _get_compat_imports(mod)
        for mod_path, names in compat_imports.items():
            assert "get_request_context" not in names
            assert "RequestContext" not in names
            assert "storage_path" not in names

    def test_skill_auth_no_compat(self):
        from agentclaw.community.adapters.http.skill_center import skill_auth as mod
        compat_imports = _get_compat_imports(mod)
        assert len(compat_imports) == 0, f"skill_auth still imports from compat: {compat_imports}"

    def test_compat_module_deleted(self):
        """compat.py should no longer exist — all consumers migrated."""
        import importlib
        mod = importlib.util.find_spec("agentclaw.community.core.skill_center.compat")
        assert mod is None, "compat.py still exists — should have been deleted"
