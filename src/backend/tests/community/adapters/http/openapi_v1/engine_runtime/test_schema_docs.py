"""Schema-documentation gate for the engine-runtime groups (Track C, Task 5).

This surface is published and consumed by client generators, so "every field is
documented" has to be enforced rather than left to reviewer diligence — the
convention decays on the first hurried PR otherwise.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from enum import Enum

import pytest
from pydantic import BaseModel

import agentclaw.community.adapters.http.openapi_v1.engine_runtime as pkg
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    ApprovalMode,
    MessageRole,
    SocketKind,
)

def _all_enums() -> list[type]:
    """Every published enum, discovered rather than listed.

    A hand-maintained list silently skips a fourth enum added later — and
    ``_DocumentedEnum`` omits ``x-enum-descriptions`` entirely when
    ``__descriptions__`` is empty, so an undocumented enum would ship green.
    """
    from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
        _DocumentedEnum,
    )

    found: dict[str, type] = {}
    for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        module = importlib.import_module(info.name)
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, _DocumentedEnum)
                and obj is not _DocumentedEnum
                and obj.__module__.startswith(pkg.__name__)
            ):
                found[obj.__name__] = obj
    return list(found.values())


_ENUMS = _all_enums()


def test_enum_discovery_found_something():
    """Guard the guard: a discovery bug must not silently disable the checks."""
    assert {e.__name__ for e in _ENUMS} >= {
        "ApprovalMode",
        "MessageRole",
        "SocketKind",
    }


def _public_models() -> list[type[BaseModel]]:
    """Every Pydantic model defined anywhere under the engine_runtime package."""
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


# ── enums ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("enum_cls", _ENUMS, ids=lambda e: e.__name__)
def test_enum_is_str_based(enum_cls):
    """So OpenAPI emits ``type: string`` + ``enum``, not an opaque integer set."""
    assert issubclass(enum_cls, str)
    assert issubclass(enum_cls, Enum)


@pytest.mark.parametrize("enum_cls", _ENUMS, ids=lambda e: e.__name__)
def test_every_enum_member_is_documented(enum_cls):
    """OpenAPI has no per-member doc slot; x-enum-descriptions is the convention."""
    documented = set(enum_cls.__descriptions__)
    members = {m.value for m in enum_cls}
    assert members == documented, (
        f"{enum_cls.__name__}: undocumented {members - documented}, "
        f"stale {documented - members}"
    )


@pytest.mark.parametrize("enum_cls", _ENUMS, ids=lambda e: e.__name__)
def test_enum_publishes_descriptions_into_its_schema(enum_cls):
    """The descriptions must survive schema generation, not just exist in Python."""

    class _Holder(BaseModel):
        value: enum_cls  # type: ignore[valid-type]

    schema = _Holder.model_json_schema()
    defs = schema.get("$defs", {})
    assert enum_cls.__name__ in defs, "enum did not generate its own component"
    component = defs[enum_cls.__name__]
    assert component.get("type") == "string"
    assert set(component.get("enum", [])) == {m.value for m in enum_cls}
    # Arrays positionally parallel to `enum` — the form openapi-generator reads.
    # A map is ignored by it, which would make the extension decorative.
    values = component["enum"]
    assert component["x-enum-descriptions"] == [
        enum_cls.__descriptions__[v] for v in values
    ]
    names = {m.value: m.name for m in enum_cls}
    assert component["x-enum-varnames"] == [names[v] for v in values]
    # NSwag reads a different key; emit it too so either generator carries docs.
    assert component["x-enumNames"] == component["x-enum-varnames"]


def test_approval_mode_publishes_only_the_advertised_spellings():
    """The engine accepts six values but advertises three.

    ``always``/``off`` are undocumented and ``on_miss`` is a snake_case alias of
    ``on-miss``. Publishing any of them would bless two public spellings for one
    mode, permanently.
    """
    assert {m.value for m in ApprovalMode} == {"approve", "on-miss", "never"}
    for alias in ("always", "on_miss", "off", "auto"):
        assert alias not in {m.value for m in ApprovalMode}


def test_no_engine_name_enum_exists():
    """Engine names are deployment config, not a closed set.

    ``_get_engine_types()`` reads the ``ENGINE_TYPES`` environment variable, so
    an enum here would contradict a deployment that configures its own list —
    and would fail closed on a response.
    """
    from agentclaw.community.adapters.http.openapi_v1 import engine_runtime as er

    assert not hasattr(er, "EngineName")


# ── models ────────────────────────────────────────────────────────────────────


def test_model_discovery_found_something():
    """``_public_models()`` returning [] would make the gate below vacuous."""
    assert _public_models(), "no models discovered — the documentation gate is inert"


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


def test_the_generated_document_contains_nothing_internal():
    """The whole document, not just model schemas.

    FastAPI promotes every **handler docstring** to the operation description,
    and the earlier version of this gate walked only Pydantic schemas — so five
    handler docstrings shipped the engine's private route paths, internal
    rationale and RST markup into the document external tenants read, with the
    gate green.
    """
    from fastapi import FastAPI

    from agentclaw.community.adapters.http.openapi_v1 import _ENGINE_RUNTIME_GROUPS

    app = FastAPI()
    for group in _ENGINE_RUNTIME_GROUPS:
        app.include_router(group)
    document = app.openapi()

    problems: list[str] = []
    for where, text in _published_strings(document["paths"]):
        for marker in _FORBIDDEN_IN_PUBLISHED_TEXT:
            if marker.lower() in text.lower():
                problems.append(f"paths{where}: contains {marker!r}")
    assert not problems, "internal detail in the published document:\n  " + "\n  ".join(
        problems
    )


def test_published_text_contains_nothing_internal():
    """The public API document is caller-facing prose only.

    ``ApprovalMode``'s docstring once listed the very alias spellings the team
    decided never to publish, named an internal engine route, and described an
    unfixed upstream defect — all of it emitted into the schema.
    """
    problems: list[str] = []
    for model in _public_models() + list(_ENUMS):
        schema = (
            model.model_json_schema()
            if hasattr(model, "model_json_schema")
            else _enum_schema(model)
        )
        for where, text in _published_strings(schema):
            for marker in _FORBIDDEN_IN_PUBLISHED_TEXT:
                if marker.lower() in text.lower():
                    problems.append(f"{model.__name__}{where}: contains {marker!r}")
    assert not problems, "internal detail in published schema text:\n  " + "\n  ".join(
        problems
    )


def _enum_schema(enum_cls) -> dict:
    class _Holder(BaseModel):
        value: enum_cls  # type: ignore[valid-type]

    return _Holder.model_json_schema()


def test_every_model_field_is_described():
    """A generated client is only usable if the fields carry documentation."""
    problems: list[str] = []
    for model in _public_models():
        if not (model.__doc__ or "").strip():
            problems.append(f"{model.__name__}: no docstring (schema description)")
        for name, field in model.model_fields.items():
            if not (field.description or "").strip():
                problems.append(f"{model.__name__}.{name}: no Field(description=...)")
    assert not problems, "undocumented schema members:\n  " + "\n  ".join(problems)
