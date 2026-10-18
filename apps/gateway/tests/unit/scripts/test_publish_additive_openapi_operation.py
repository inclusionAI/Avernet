"""The selective publisher carries only generated, additive OpenAPI entries."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_GATEWAY = Path(__file__).resolve().parents[3]
_SCRIPTS = _GATEWAY / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location(
    "publish_additive_openapi_operation",
    _SCRIPTS / "publish_additive_openapi_operation.py",
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
additive_document = _mod.additive_document
publish = _mod.publish

_PATH = "/openapi/v1/bots/{bot_id}/skills/{skill_id}/security-observations"


def _published() -> dict:
    return {
        "openapi": "3.1.0",
        "paths": {"/openapi/v1/bots/old": {"get": {"operationId": "old"}}},
        "components": {"schemas": {"Shared": {"type": "string"}}},
    }


def _candidate() -> dict:
    return {
        "openapi": "3.1.0",
        # This unrelated removal would fail a full-dump publish.
        "paths": {
            _PATH: {
                "get": {
                    "operationId": "security_observations",
                    "responses": {
                        "200": {"$ref": "#/components/schemas/ObservationEnvelope"}
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "ObservationEnvelope": {
                    "properties": {"data": {"$ref": "#/components/schemas/Observation"}}
                },
                "Observation": {
                    "properties": {"shared": {"$ref": "#/components/schemas/Shared"}}
                },
                "Shared": {"type": "string"},
            }
        },
    }


def test_publish_only_generated_operation_and_transitive_components(
    tmp_path: Path,
) -> None:
    published = tmp_path / "bots.openapi.json"
    candidate = tmp_path / "backend.openapi.json"
    old = _published()
    published.write_text(json.dumps(old), encoding="utf-8")
    candidate.write_text(json.dumps(_candidate()), encoding="utf-8")

    assert publish(published, candidate, "GET", _PATH)

    result = json.loads(published.read_text(encoding="utf-8"))
    assert set(result["paths"]) == {"/openapi/v1/bots/old", _PATH}
    assert (
        result["paths"]["/openapi/v1/bots/old"] == old["paths"]["/openapi/v1/bots/old"]
    )
    assert set(result["components"]["schemas"]) == {
        "Shared",
        "Observation",
        "ObservationEnvelope",
    }
    assert publish(published, candidate, "GET", _PATH) is False


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda doc: doc["paths"].update({_PATH: {"get": {}}}),
            "published path differs",
        ),
        (
            lambda doc: doc["components"]["schemas"].update(
                {"Observation": {"type": "integer"}}
            ),
            "published component differs",
        ),
    ],
)
def test_refuses_to_overwrite_existing_contract(change, message: str) -> None:
    old = _published()
    change(old)
    with pytest.raises(ValueError, match=message):
        additive_document(old, _candidate(), "GET", _PATH)


def test_refuses_missing_or_unsupported_component_reference() -> None:
    candidate = _candidate()
    del candidate["components"]["schemas"]["Observation"]
    with pytest.raises(ValueError, match="missing component"):
        additive_document(_published(), candidate, "GET", _PATH)

    candidate = _candidate()
    candidate["components"]["schemas"]["Observation"]["properties"]["shared"][
        "$ref"
    ] = "#/components/schemas/Shared/properties/x"
    with pytest.raises(ValueError, match="unsupported component reference"):
        additive_document(_published(), candidate, "GET", _PATH)


def test_refuses_missing_operation_other_methods_and_version_mismatch() -> None:
    with pytest.raises(ValueError, match="no GET"):
        additive_document(_published(), _candidate(), "GET", "/absent")

    # The other-methods refusal guards an ALREADY-PUBLISHED path: publishing
    # a named method there must not silently change the unnamed sibling verbs.
    published_with_path = _published()
    published_with_path["paths"][_PATH] = _candidate()["paths"][_PATH]
    for name, item in _candidate()["components"]["schemas"].items():
        published_with_path["components"]["schemas"].setdefault(name, item)
    candidate = _candidate()
    candidate["paths"][_PATH]["post"] = {}
    with pytest.raises(ValueError, match="other methods"):
        additive_document(published_with_path, candidate, "GET", _PATH)

    candidate = _candidate()
    candidate["openapi"] = "3.0.3"
    with pytest.raises(ValueError, match="version differs"):
        additive_document(_published(), candidate, "GET", _PATH)


def test_brand_new_multi_verb_path_publishes_jointly() -> None:
    candidate = _candidate()
    candidate["paths"][_PATH]["post"] = {
        "operationId": "security_observations_post",
        "responses": {"200": {"$ref": "#/components/schemas/ObservationEnvelope"}},
    }
    merged = additive_document(_published(), candidate, "GET", _PATH)
    assert set(merged["paths"][_PATH]) == {"get", "post"}
    assert merged["paths"][_PATH]["post"] == candidate["paths"][_PATH]["post"]


def test_rejection_does_not_modify_published_artifact(tmp_path: Path) -> None:
    published = tmp_path / "bots.openapi.json"
    candidate = tmp_path / "backend.openapi.json"
    published.write_text(json.dumps(_published()), encoding="utf-8")
    bad = _candidate()
    bad["components"]["schemas"]["Shared"] = {"type": "integer"}
    candidate.write_text(json.dumps(bad), encoding="utf-8")
    before = published.read_bytes()

    with pytest.raises(ValueError, match="published component differs"):
        publish(published, candidate, "GET", _PATH)

    assert published.read_bytes() == before
    assert not list(tmp_path.glob(".openapi-additive-*"))


def test_regular_gate_must_pass_before_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published = tmp_path / "bots.openapi.json"
    candidate = tmp_path / "backend.openapi.json"
    published.write_text(json.dumps(_published()), encoding="utf-8")
    candidate.write_text(json.dumps(_candidate()), encoding="utf-8")
    before = published.read_bytes()

    def reject(_published: Path, _candidate: Path, *, allow_breaking: bool) -> None:
        assert allow_breaking is False
        raise SystemExit(1)

    monkeypatch.setattr(_mod, "gate", reject)
    with pytest.raises(SystemExit):
        publish(published, candidate, "GET", _PATH)

    assert published.read_bytes() == before
    assert not list(tmp_path.glob(".openapi-additive-*"))
