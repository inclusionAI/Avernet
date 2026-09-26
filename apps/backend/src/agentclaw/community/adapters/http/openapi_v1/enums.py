"""The base for published enums on the ``/openapi/v1/bots`` surface.

IMPORTANT — everything a class or field docstring says in this package is
published verbatim into the OpenAPI document that external tenants read.
Docstrings are therefore caller-facing prose only. Rationale, upstream defects,
internal component and route names, and deployment-tier details belong in ``#``
comments like these, which are not published.

Only value sets that are **genuinely closed at the source** may subclass this.
An open set — one an upstream service or deployment configuration can extend —
stays a plain ``str`` field whose description lists the known values, because
validating a response against a set the source does not enforce turns a
backward-compatible upstream change into a public 500. The engine-runtime
enums module records the per-set rulings for its groups.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class _DocumentedEnum(str, Enum):
    """Base for published enums; not itself part of the API."""

    # member value -> caller-facing meaning. Every member must be covered; the
    # schema-documentation gate fails the build otherwise.
    __descriptions__: dict[str, str] = {}

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Any:
        schema = handler(core_schema)
        if not cls.__descriptions__:
            return schema
        # OpenAPI has no native slot for per-member documentation, and the two
        # dominant generators disagree on the extension:
        #   openapi-generator reads x-enum-descriptions / x-enum-varnames as
        #     ARRAYS positionally parallel to `enum`
        #   NSwag reads x-enumNames
        # Emit all three so a generated client actually carries the docs. Member
        # order is declaration order in Pydantic, which is what makes the
        # parallel-array form well-defined.
        values = list(schema.get("enum", []))
        schema["x-enum-descriptions"] = [
            cls.__descriptions__.get(v, "") for v in values
        ]
        names = {m.value: m.name for m in cls}
        schema["x-enum-varnames"] = [names.get(v, str(v)) for v in values]
        schema["x-enumNames"] = list(schema["x-enum-varnames"])
        return schema


__all__ = ["_DocumentedEnum"]
