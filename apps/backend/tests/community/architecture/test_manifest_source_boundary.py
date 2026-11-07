"""Manifest execution consumes neutral bindings, not Expert business policy."""
import ast
from pathlib import Path


def test_manifest_implementation_does_not_interpret_expert_provenance():
    from agentclaw.community.core import bot_config_manifest

    for path in Path(bot_config_manifest.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "expert_origin":
                raise AssertionError(f"{path}: Expert provenance belongs to Bot creation")
            if isinstance(node, ast.ImportFrom):
                assert "expert" not in (node.module or ""), str(path)
                assert not (node.module or "").endswith(".creation_source"), str(path)
                assert all(alias.name != "ExpertOrigin" for alias in node.names), str(path)


def test_ordinary_manifest_http_submission_has_no_expert_parameter():
    import inspect
    from agentclaw.community.adapters.http.openapi_v1.bots.create_with_manifest import submit_manifest_creation

    assert "expert_origin" not in inspect.signature(submit_manifest_creation).parameters
