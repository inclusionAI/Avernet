"""Enum-documentation gate for the engine-runtime groups.

OpenAPI has no native slot for per-member documentation, so a published enum
carries its meanings in an extension the client generators read. That mechanism
is engine-runtime's, and so are the rulings about which value sets get an enum
at all — hence a gate of its own.

The model and published-text rules that used to live here now apply to the whole
public surface and moved up to ``openapi_v1/test_schema_docs.py``; they covered
these groups only, while every other group shipped undocumented.
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
