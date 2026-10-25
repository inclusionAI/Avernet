"""Pin pre-B6 Legacy route order, methods, names and response contracts."""
import json
from pathlib import Path

from agentclaw.community.adapters.http.skill_center import skills, skillsets


def test_legacy_router_contract_survives_responsibility_split():
    expected = json.loads(Path(__file__).with_name("legacy_route_contract.json").read_text())
    for name, module in [("skills", skills), ("skillsets", skillsets)]:
        actual = [
            {"path": r.path, "methods": sorted(r.methods), "name": r.name,
             "response_model": getattr(r.response_model, "__name__", None),
             "status_code": r.status_code}
            for r in module.router.routes
        ]
        assert actual == expected[name]
