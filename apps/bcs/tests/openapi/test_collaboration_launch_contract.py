"""Shared wire shapes and separate Human/capability trust boundaries."""
import sys
from pathlib import Path

BCS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BCS_ROOT))
from scripts.validate_openapi_contract import load_contract, validate_contract  # noqa: E402


def test_draft_projection_is_capability_only_and_uses_native_source_schema():
    root = BCS_ROOT / "api-contracts"
    native = load_contract(root / "collaboration-drafts", "v1.yaml")
    internal = load_contract(root / "v1", "internal.yaml")
    assert validate_contract(internal, path_prefix="/api/v1/collaboration/") == []
    base = "/api/v1/collaboration/drafts/{draftId}/revisions/{revision}"
    assert [p for p in internal["paths"] if "/drafts/" in p] == [base, base + "/validate"]
    for path, method in [(base, "get"), (base + "/validate", "post")]:
        op = internal["paths"][path][method]
        assert op["x-avernet-security"] == {}
        assert op["security"] == [{"RevisionReadToken": []}]
        assert "requestBody" not in op
        schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert set(schema["required"]) == {"code", "message", "data", "request_id"}
    schema = internal["paths"][base]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["properties"]["data"] == native["components"]["schemas"]["StoredRevision"]


def test_native_browser_slice_reuses_v1_shapes_without_gateway_or_bot_auth():
    root = BCS_ROOT / "api-contracts"
    native = load_contract(root / "collaboration-user", "v1.yaml")
    public = load_contract(root / "v1")
    assert validate_contract(native, path_prefix="/collaboration/user/") == []
    assert validate_contract(public) == []
    operations = [(p, m) for p, item in native["paths"].items() for m in item if m in {"get", "post"}]
    assert len(operations) == 4
    for path, method in operations:
        op = native["paths"][path][method]
        assert op["x-avernet-security"] == {"user": "required"}
        counterpart = public["paths"][path.replace("/collaboration/user", "/openapi/v1/collaboration")][method]
        assert op.get("requestBody") == counterpart.get("requestBody")
        for status in [s for s in op["responses"] if s.startswith("2")]:
            assert op["responses"][status] == counterpart["responses"][status]
    guard = native["paths"]["/collaboration/user/groups"]["parameters"][0]
    assert guard["name"] == "X-BCS-Expected-User" and guard["required"] is True
    assert public["paths"]["/openapi/v1/collaboration/me"]["get"]["x-avernet-security"] == {"user": "required", "app": "required"}
