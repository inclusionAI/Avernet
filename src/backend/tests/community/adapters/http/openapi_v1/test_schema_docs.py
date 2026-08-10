"""Documentation gate for the whole public ``/openapi/v1`` surface.

This surface is published to external tenants and consumed by client
generators, so "a caller can tell what to send" has to be enforced rather than
left to reviewer diligence — the convention decays on the first hurried PR
otherwise.

The engine-runtime groups have had this gate since Track C; the rest of the
surface did not, and the difference showed. Every model outside those groups
shipped with bare fields, no request body carried an example, and internal
notes — phase numbers, service-method names, RST markup — reached the document
through handler docstrings. The narrowest symptom was a caller unable to create
a session: `SessionCreate`'s two fields are both optional, the body itself is
not, and with no example an API console rendered an empty request pane. So the
gate moved up here, and gained the request-body rules the original did not
have.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest
from fastapi import FastAPI
from pydantic import BaseModel

import agentclaw.community.adapters.http.openapi_v1 as pkg
from agentclaw.community.adapters.http.openapi_v1 import build_public_router


def _document() -> dict:
    """The served OpenAPI document for the whole public surface."""
    app = FastAPI()
    app.include_router(build_public_router())
    return app.openapi()


DOCUMENT = _document()


def _public_models() -> list[type[BaseModel]]:
    """Every Pydantic model defined anywhere under the public API package."""
    found: dict[str, type[BaseModel]] = {}
    for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        module = importlib.import_module(info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BaseModel)
                and obj is not BaseModel
                and obj.__module__.startswith(pkg.__name__)
            ):
                found[f"{obj.__module__}.{obj.__qualname__}"] = obj
    return list(found.values())


MODELS = _public_models()


def test_model_discovery_found_something():
    """Guard the guard: a discovery bug must not silently disable the checks."""
    names = {m.__name__ for m in MODELS}
    assert {"BotCreate", "SessionCreate", "RoutineCreate", "McpConfigWrite"} <= names


def test_document_covers_every_group():
    """A group dropped from assembly would take its rules out of this gate."""
    tags = {t for op in _operations() for t in op.get("tags", [])}
    assert {
        "bots",
        "identity",
        "mcp",
        "resources",
        "routines",
        "sessions",
        "skills",
    } <= tags


def _operations() -> list[dict]:
    return [op for ops in DOCUMENT["paths"].values() for op in ops.values()]


def _operation_ids() -> list[tuple[str, str, dict]]:
    return [
        (method.upper(), path, op)
        for path, ops in sorted(DOCUMENT["paths"].items())
        for method, op in ops.items()
    ]


def _resolve(ref: str) -> dict:
    node: dict = DOCUMENT
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


# ── request bodies say what to send ───────────────────────────────────────────


def _request_bodies() -> list[tuple[str, str, str, dict]]:
    """(method, path, media type, schema) for every operation taking a body."""
    out: list[tuple[str, str, str, dict]] = []
    for method, path, op in _operation_ids():
        body = op.get("requestBody")
        if not body:
            continue
        for media, entry in body["content"].items():
            schema = entry.get("schema", {})
            if "$ref" in schema:
                schema = _resolve(schema["$ref"])
            out.append((method, path, media, schema))
    return out


REQUEST_BODIES = _request_bodies()


def test_request_body_discovery_found_something():
    assert len(REQUEST_BODIES) >= 10, "body discovery broke; the rules below are inert"


@pytest.mark.parametrize(
    "method,path,media,schema",
    REQUEST_BODIES,
    ids=[f"{m} {p} ({t})" for m, p, t, _ in REQUEST_BODIES],
)
def test_every_request_body_shows_what_to_send(method, path, media, schema):
    """A schema alone is not enough to compose a request from.

    An API console renders `example` into its request pane; without one the
    caller gets an empty editor and has to infer the body from the field list —
    which is exactly how a required-but-all-optional body ends up sent as
    nothing at all.

    A binary body has no JSON example to give, so it owes a description saying
    what bytes to send and how — the mistake there is wrapping them in a
    multipart form, which no schema can warn about.
    """
    del method, path
    if media == "application/json":
        assert schema.get("example") or schema.get("examples"), (
            "JSON request body publishes no example"
        )
    else:
        assert (schema.get("description") or "").strip(), (
            "non-JSON request body publishes no description"
        )


# ── every published member is documented ──────────────────────────────────────


def _documenting_class(model: type[BaseModel]) -> type[BaseModel]:
    """The class whose docstring describes ``model``.

    Parametrising a generic model injects the concretisation (``Page[Session]``)
    into the defining module with an empty ``__doc__`` — Pydantic does not copy
    the generic's. Those concretisations are not separate published models; the
    named subclass that uses one carries the description. So the docstring
    requirement is checked against the generic origin, which keeps the gate
    honest without demanding a docstring on a class no one can write one for.
    """
    origin = getattr(model, "__pydantic_generic_metadata__", {}).get("origin")
    return origin or model


def test_every_model_field_is_described():
    """A generated client is only usable if the fields carry documentation."""
    problems: list[str] = []
    for model in MODELS:
        if not (_documenting_class(model).__doc__ or "").strip():
            problems.append(f"{model.__name__}: no docstring (schema description)")
        for name, field in model.model_fields.items():
            if not (field.description or "").strip():
                problems.append(f"{model.__name__}.{name}: no Field(description=...)")
    assert not problems, "undocumented schema members:\n  " + "\n  ".join(problems)


def test_every_operation_is_described():
    """The operation summary is the first thing a caller reads."""
    problems: list[str] = []
    for method, path, op in _operation_ids():
        if not (op.get("description") or op.get("summary") or "").strip():
            problems.append(f"{method} {path}")
    assert not problems, "undescribed operations:\n  " + "\n  ".join(problems)


# ── nothing internal reaches the published document ──────────────────────────
#
# Docstrings and Field descriptions are promoted verbatim into the OpenAPI
# document external tenants read. Rationale therefore belongs in `#` comments.
# These markers are things that leaked once and must not again.
_FORBIDDEN_IN_PUBLISHED_TEXT = (
    "src/engine",          # internal source paths
    "singlebox",           # deployment tiers
    "OCB",                 # internal component names
    "teamclaw",
    "mcporter",
    "stub",                # test/deployment scaffolding
    "on_miss",             # unpublished alias spellings
    "/api/",               # the engine's private route paths
    "EngineManager",       # internal class names
    "``",                  # RST markup — Swagger/Redoc render markdown, not RST
    ":class:",
    "device_fs",           # service-internal argument names
    "legacy",              # the surface this one replaced
    "follow-up",           # unshipped work; a caller cannot act on it
    "Phase ",              # delivery phases
    "@envelope_errors",    # decorator names
)


def _published_strings(schema: dict) -> list[tuple[str, str]]:
    """Every description/title string in a generated schema, with its path."""
    out: list[tuple[str, str]] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("description", "title") and isinstance(value, str):
                    out.append((f"{path}.{key}", value))
                else:
                    walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(schema, "")
    return out


def test_published_model_text_contains_nothing_internal():
    """Each model's own schema, including any not reachable from a route today.

    The document walk below only sees components a route references, so a model
    added ahead of the endpoint that will serve it would slip past it.
    """
    problems: list[str] = []
    for model in MODELS:
        for where, text in _published_strings(model.model_json_schema()):
            for marker in _FORBIDDEN_IN_PUBLISHED_TEXT:
                if marker.lower() in text.lower():
                    problems.append(f"{model.__name__}{where}: contains {marker!r}")
    assert not problems, "internal detail in published schema text:\n  " + "\n  ".join(
        problems
    )


def test_the_generated_document_contains_nothing_internal():
    """The whole document — operation descriptions as well as model schemas.

    FastAPI promotes every handler docstring to the operation description, so a
    note meant for the next maintainer is published to every external tenant
    unless something checks.
    """
    problems: list[str] = []
    for where, text in _published_strings(DOCUMENT["paths"]) + _published_strings(
        DOCUMENT.get("components", {})
    ):
        for marker in _FORBIDDEN_IN_PUBLISHED_TEXT:
            if marker.lower() in text.lower():
                problems.append(f"{where}: contains {marker!r}")
    assert not problems, "internal detail in the published document:\n  " + "\n  ".join(
        problems
    )
