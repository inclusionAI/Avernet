"""Risk domain services depend on the persistence contract, not SQLAlchemy."""

import ast
from pathlib import Path


def test_risk_services_do_not_import_concrete_repository():
    source = Path(__file__).parents[3] / "src/agentclaw/community/core"
    paths = list((source / "bot_public_risk").glob("*.py"))
    paths += [source / "bot_public/services/bot_public_service.py"]
    for path in paths:
        if path.name == "repository.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert "agentclaw.community.core.bot_public_risk.repository" not in imports, path
