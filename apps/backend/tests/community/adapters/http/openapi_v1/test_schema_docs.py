"""Schema-documentation gate for the whole public surface.

The engine-runtime groups carry their own gate
(``engine_runtime/test_schema_docs.py``); this one holds the same bar for the
**generated document of every group**, because that document — parameters,
schema descriptions, enum member docs — is what client generators and external
tenants actually consume. "Every field is documented" has to be enforced rather
than left to reviewer diligence; the convention decays on the first hurried PR
otherwise.

Asserted against the real generated document rather than the source models, so
a model published through any group (including ones defined outside the
``openapi_v1`` package, like the Bot Logs models) is covered exactly as it is
published.
"""

from __future__ import annotations

import pytest
from agentclaw.community.adapters.http.openapi_v1.dormant.schemas import (
    BotActivateResult,
    BotRecycleOperation,
    BotRecycleResult,
)


from tests.community.adapters.http.openapi_v1.conftest import public_document

_METHODS = {"get", "put", "post", "delete", "patch", "options", "head", "trace"}


def _document() -> dict:
    return public_document()


def _operations(document: dict):
    for path, methods in document["paths"].items():
        for method, operation in methods.items():
            if method in _METHODS:
                yield path, method, operation


def _schemas(document: dict) -> dict:
    return document.get("components", {}).get("schemas", {})


def test_document_generation_found_something():
    """Guard the guard: an assembly bug must not silently disable the checks."""
    document = _document()
    assert len(list(_operations(document))) > 50
    assert len(_schemas(document)) > 50


def test_every_parameter_is_described():
    """A generated client's parameter docs are only as good as these."""
    problems: list[str] = []
    for path, method, operation in _operations(_document()):
        for param in operation.get("parameters", []):
            if not (param.get("description") or "").strip():
                where = f"{method.upper()} {path}"
                problems.append(f"{where}: {param['in']}:{param['name']}")
    assert not problems, "undocumented parameters:\n  " + "\n  ".join(problems)


def test_every_schema_and_field_is_described():
    """Every published component and every property carries a description.

    Parametrised generics (the Envelope/Page concretisations) are covered too:
    the wrappers inject a description into each concretisation's schema, so no
    exemption list is needed here — and none should ever be added.
    """
    problems: list[str] = []
    for name, schema in _schemas(_document()).items():
        if not (schema.get("description") or "").strip():
            problems.append(f"{name}: no schema description")
        for prop, spec in (schema.get("properties") or {}).items():
            if not (spec.get("description") or "").strip():
                problems.append(f"{name}.{prop}: no description")
    assert not problems, "undocumented schema members:\n  " + "\n  ".join(problems)


def test_every_published_enum_documents_its_members():
    """Component-level enums must carry the parallel x-enum-descriptions.

    Inline (Literal-typed) enums are exempt: they have no component to attach
    the extension to, so their member meanings belong in the field or
    parameter description — which the description gates above require.
    """
    problems: list[str] = []
    for name, schema in _schemas(_document()).items():
        values = schema.get("enum")
        if not values:
            continue
        docs = schema.get("x-enum-descriptions")
        if not isinstance(docs, list) or len(docs) != len(values):
            problems.append(f"{name}: missing/short x-enum-descriptions")
            continue
        for value, doc in zip(values, docs):
            if not (doc or "").strip():
                problems.append(f"{name}[{value}]: empty member description")
    assert not problems, "undocumented enums:\n  " + "\n  ".join(problems)


# ── nothing internal reaches the published document ──────────────────────────
#
# Docstrings and Field descriptions are promoted verbatim into the OpenAPI
# document external tenants read. Rationale therefore belongs in `#` comments.
# The base list mirrors the engine-runtime gate; the additions are markers that
# leaked from the other groups before this gate existed.
_FORBIDDEN_IN_PUBLISHED_TEXT = (
    "engine/adapter",      # internal source paths
    "singlebox",           # deployment tiers
    "OCB",                 # internal component names
    "teamclaw",
    "mcporter",
    "stub",                # test/deployment scaffolding
    "on_miss",             # unpublished alias spellings
    "/api/",               # private route paths
    "EngineManager",       # internal class names
    "``",                  # RST markup — Swagger/Redoc render markdown, not RST
    ":class:",
    ":func:",
    "langfuse",            # retired internal observability vendor naming
    "TODO(",               # roadmap notes are not caller documentation
)


def _published_strings(node, path: str = ""):
    """Every description/title/summary string in the document, with its path."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("description", "title", "summary") and isinstance(value, str):
                yield f"{path}.{key}", value
            else:
                yield from _published_strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _published_strings(value, f"{path}[{i}]")


def test_the_published_document_contains_nothing_internal():
    problems: list[str] = []
    for where, text in _published_strings(_document()):
        for marker in _FORBIDDEN_IN_PUBLISHED_TEXT:
            if marker.lower() in text.lower():
                problems.append(f"{where}: contains {marker!r}")
    assert not problems, "internal detail in the published document:\n  " + "\n  ".join(
        problems
    )


def test_every_published_example_satisfies_its_own_schema():
    """An example missing a required field is worse than no example.

    It is the first thing an integrator copies, and copying it produces a
    payload the schema rejects — or, on a response, a shape the client's
    generated type cannot hold. Either way the document contradicts itself and
    the reader trusts the wrong half.

    This is not hypothetical here: removing ``bot_id`` from the routines
    *create* body, correct because the bot moved to the path, also took it out
    of the ``Routine`` *response* example, where the field is still required and
    still sent. That went unnoticed for the whole feature and was found in
    review, not by the suite — so the invariant is asserted rather than
    remembered.

    Only required fields are checked. An example may legitimately omit an
    optional one, and often should, to show the minimal shape.
    """
    schemas = (_document().get("components") or {}).get("schemas") or {}
    problems: list[str] = []
    for name, schema in sorted(schemas.items()):
        example = schema.get("example")
        if not isinstance(example, dict):
            continue
        missing = sorted(set(schema.get("required") or []) - set(example))
        if missing:
            problems.append(f"{name}: example omits required {missing}")
    assert not problems, (
        "published examples that do not satisfy their own schema:\n  "
        + "\n  ".join(problems)
    )


def test_manifest_apply_publishes_only_the_ordinary_apply_contract():
    operation = _document()["paths"]["/openapi/v1/bots/{bot_id}/config-manifest/apply"]["post"]
    parameters = {item["name"] for item in operation["parameters"]}
    assert "dry_run" in parameters
    assert "recover_creation" not in parameters
    assert "202" in operation["responses"]


def test_manifest_discovery_documents_skill_references_separately_from_transport():
    """Generated clients can read all four reference forms and a valid example."""
    schemas = _schemas(_document())
    capabilities = schemas["ConfigManifestCapabilities"]
    references = capabilities["properties"]["skill_references"]
    reference_schema = schemas[references["items"]["$ref"].rsplit("/", 1)[-1]]
    assert reference_schema["properties"]["reference"]["enum"] == [
        "package", "repo_path", "skill_uuid", "skill_code",
    ]
    assert set(reference_schema["required"]) == {"reference", "supported", "reason"}
    example = capabilities["example"]
    assert [row["reference"] for row in example["skill_references"]] == [
        "package", "repo_path", "skill_uuid", "skill_code",
    ]
    assert "capabilities" in {row["name"] for row in example["constructs"]}
    assert not {"skills", "mcp"} & {row["name"] for row in example["constructs"]}
    assert not {"skills", "mcp"} & {row["category"] for row in example["source_matrix"]}


def test_manifest_put_publishes_both_success_shapes():
    document = _document()
    operation = document["paths"]["/openapi/v1/bots/{bot_id}/config-manifest"]["put"]
    parameter = next(item for item in operation["parameters"] if item["name"] == "dry_run")
    assert parameter["in"] == "query"
    assert parameter["required"] is False
    assert parameter["schema"]["default"] is False
    response = operation["responses"]["200"]["content"]["application/json"]["schema"]
    envelope = _schemas(document)[response["$ref"].rsplit("/", 1)[-1]]
    assert envelope["properties"]["data"]["anyOf"] == [
        {"$ref": "#/components/schemas/ConfigManifest"},
        {"$ref": "#/components/schemas/ConfigManifestApply"},
        {"type": "null"},
    ]

_BASE = "/openapi/v1/bots/{bot_id}"


def test_recycle_documents_admission_backup_policy_and_idempotency():
    operation = public_document()["paths"][f"{_BASE}/recycle"]["post"]
    description = operation["description"]
    for term in (
        "aicoding",
        "claude_code",
        "202",
        "operation_id",
        "changed=false",
        "fastdisk_fail_block",
        "1500",
        "NEEDS_CONFIRMATION",
        "503",
        "All supported personal",
        "Non-coding Bots skip",
    ):
        assert term in description
    assert "requestBody" not in operation
    responses = operation["responses"]
    for status in ("200", "202"):
        sample = responses[status]["content"]["application/json"]["example"]
        assert sample["code"] == int(status) * 1000
        BotRecycleResult.model_validate(sample["data"])
    assert "not completion" in responses["202"]["description"]


def test_recycle_query_documents_read_only_terminal_results():
    operation = public_document()["paths"][
        f"{_BASE}/recycle-operations/{{operation_id}}"
    ]["get"]
    description = operation["description"]
    for term in (
        "Read-only",
        "HTTP 200",
        "FAILED",
        "BACKUP_FAILED",
        "BACKUP_TIMEOUT",
        "NEEDS_CONFIRMATION",
        "404",
        "current/latest",
        "bot_status",
    ):
        assert term in description
    sample = operation["responses"]["200"]["content"]["application/json"]["example"]
    assert sample["code"] == 200000
    assert sample["data"]["phase"] == "FAILED"
    BotRecycleOperation.model_validate(sample["data"])


def test_activation_documents_recycle_conflicts_and_unchanged_polling():
    operation = public_document()["paths"][f"{_BASE}/activate"]["post"]
    description = operation["description"]
    for term in ("REACTIVATING", "ACTIVE", "NEEDS_CONFIRMATION", "409", "/status"):
        assert term in description
    assert "requestBody" not in operation
    for status in ("200", "202"):
        sample = operation["responses"][status]["content"]["application/json"][
            "example"
        ]
        assert sample["code"] == int(status) * 1000
        BotActivateResult.model_validate(sample["data"])


def test_container_restart_documents_http_200_async_progress_not_http_202():
    operation = public_document()["paths"][f"{_BASE}/restart"]["post"]
    description = operation["description"]
    for term in (
        "HTTP 200",
        "not HTTP 202",
        "ext.aicoding.restart",
        "operation_id",
        "QUEUED",
        "BACKING_UP",
        "RESTARTING",
        "WAITING_READY",
        "SUCCEEDED",
        "FAILED",
        "fastdisk_fail_block",
        "1500",
        "partial_success",
        "/status",
    ):
        assert term in description
    assert "requestBody" not in operation
    assert "200" in operation["responses"]
    assert "202" not in operation["responses"]
    assert "not completion" in operation["responses"]["200"]["description"]


def test_bot_status_documents_restart_extension_separately_from_readiness():
    operation = public_document()["paths"][f"{_BASE}/status"]["get"]
    for term in (
        "ext.aicoding.restart",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "phase",
        "is_ready",
        "backup_failed_continue",
        "recycle-operations",
    ):
        assert term in operation["description"]
    assert "200" in operation["responses"]


@pytest.mark.parametrize("model", ["BotRecycleResult", "BotRecycleOperation"])
def test_recycle_field_docs_distinguish_operation_and_bot_status(model):
    component = public_document()["components"]["schemas"][model]
    assert "NEEDS_CONFIRMATION" in component["properties"]["phase"]["description"]
    if model == "BotRecycleResult":
        assert "admission" in component["properties"]["changed"]["description"]
    else:
        assert "BACKUP_TIMEOUT" in component["properties"]["error_code"]["description"]
