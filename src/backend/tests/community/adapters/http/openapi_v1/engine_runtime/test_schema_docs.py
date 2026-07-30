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

_ENUMS = [ApprovalMode, MessageRole, SocketKind]


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
    assert component.get("x-enum-descriptions") == dict(enum_cls.__descriptions__)


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
