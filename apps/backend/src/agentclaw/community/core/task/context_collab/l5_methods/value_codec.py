"""Closed dataclass value codec. Only caller-supplied installed types may decode.

Not an object loader: no import paths, executable values, coercion or unknown keys.
Array order is preserved, including orders that influence scheduling.
"""

from dataclasses import fields, is_dataclass
from functools import lru_cache
from enum import Enum
from types import UnionType
from typing import Literal, Union, get_args, get_origin, get_type_hints

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)


_type_hints = lru_cache(maxsize=128)(get_type_hints)


def encode(value):
    from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
        MethodProfile,
        get_profile,
    )

    if type(value) is MethodProfile:
        if value != get_profile(value.method_kind, value.profile_version):
            raise ContextCollabValidationError(
                "cannot redefine an installed MethodProfile"
            )
        return {
            "type": "MethodProfile",
            "method": value.method_kind,
            "version": value.profile_version,
        }
    if is_dataclass(value):
        return {
            "type": (
                "ProductProfileDefinition"
                if type(value).__name__ == "LegacyProductConfiguration"
                else type(value).__name__
            ),
            **{f.name: encode(getattr(value, f.name)) for f in fields(value) if f.init},
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [encode(item) for item in value]
    if value is None or type(value) in (str, int, bool):
        return value
    raise ContextCollabValidationError(
        f"unsupported definition value: {type(value).__name__}"
    )


def decode(data, expected, registry, method_resolver=None):
    origin, args = get_origin(expected), get_args(expected)
    if origin is Literal:
        if not any(type(data) is type(option) and data == option for option in args):
            raise ContextCollabValidationError("unknown definition literal")
        return data
    if origin in (Union, UnionType):
        for option in args:
            try:
                return decode(data, option, registry, method_resolver)
            except ContextCollabValidationError:
                continue
        raise ContextCollabValidationError(
            "definition union type/discriminator mismatch"
        )
    if origin is tuple:
        if type(data) is not list or len(args) != 2 or args[1] is not Ellipsis:
            raise ContextCollabValidationError("definition requires an array")
        return tuple(decode(item, args[0], registry, method_resolver) for item in data)
    if expected in (str, int, bool, type(None)):
        if type(data) is not expected:
            raise ContextCollabValidationError(
                f"definition requires {expected.__name__}"
            )
        return data
    if isinstance(expected, type) and issubclass(expected, Enum):
        try:
            return expected(data)
        except (TypeError, ValueError) as exc:
            raise ContextCollabValidationError("unknown definition enum") from exc
    if expected not in registry:
        raise ContextCollabValidationError("uninstalled definition type")
    if method_resolver is not None and expected.__name__ == "MethodProfile":
        if (
            type(data) is not dict
            or set(data) != {"type", "method", "version"}
            or data["type"] != "MethodProfile"
        ):
            raise ContextCollabValidationError(
                "MethodProfile must be an exact installed reference"
            )
        if type(data["method"]) is not str or type(data["version"]) is not str:
            raise ContextCollabValidationError("invalid MethodProfile reference")
        return method_resolver(data["method"], data["version"])
    if type(data) is dict:
        subtype = next(
            (
                t
                for t in registry
                if (
                    "ProductProfileDefinition"
                    if t.__name__ == "LegacyProductConfiguration"
                    else t.__name__
                )
                == data.get("type")
            ),
            None,
        )
        if subtype is not None and issubclass(subtype, expected):
            expected = subtype
    names = {f.name for f in fields(expected) if f.init}
    if (
        type(data) is not dict
        or set(data) != names | {"type"}
        or data["type"]
        != (
            "ProductProfileDefinition"
            if expected.__name__ == "LegacyProductConfiguration"
            else expected.__name__
        )
    ):
        raise ContextCollabValidationError(
            f"invalid fields/discriminator for {expected.__name__}"
        )
    hints = _type_hints(expected)
    try:
        return expected(
            **{
                name: decode(data[name], hints[name], registry, method_resolver)
                for name in names
            }
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise ContextCollabValidationError(
            f"invalid {expected.__name__} definition"
        ) from exc
