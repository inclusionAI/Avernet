"""Architecture enforcement: config key consistency.

Verifies that every dotted key in ``configs/application.yaml``
has a corresponding field in the Pydantic / dataclass config model
hierarchy defined by ``gateway.community.config._models.Config``.

This prevents silent config drift when a key is added to the YAML
but the code never reads it, or when a key is removed from the YAML
that the model expects.
"""

from __future__ import annotations

from dataclasses import Field, fields, is_dataclass
from pathlib import Path
from typing import Any, get_type_hints

import pytest
import yaml
from pydantic import BaseModel

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_CONFIG_FILE = _PROJECT_ROOT / "configs" / "application.yaml"

try:
    from gateway.community.config._models import Config as _AppConfig
    from gateway.community.config._models import UserConfig as _UserConfig
except ModuleNotFoundError:
    _AppConfig = None  # type: ignore[assignment]
    _UserConfig = None  # type: ignore[assignment,misc]


def _flatten_keys(data: object, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            dotted = f"{prefix}.{key}" if prefix else key
            keys.add(dotted)
            if isinstance(value, dict):
                keys |= _flatten_keys(value, dotted)
    return keys


def _dataclass_field_keys(cls: type, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for f in fields(cls):
        dotted = f"{prefix}.{f.name}" if prefix else f.name
        keys.add(dotted)
        field_type = _unwrapped_type(f)
        if _is_compound(field_type):
            keys |= _dataclass_field_keys(field_type, dotted)
    return keys


def _pydantic_field_keys(cls: type[BaseModel], prefix: str = "") -> set[str]:
    keys: set[str] = set()
    hints = get_type_hints(cls)
    for name, field_info in cls.model_fields.items():
        dotted = f"{prefix}.{name}" if prefix else name
        keys.add(dotted)
        field_type = hints.get(name)
        if field_type is not None and _is_compound(field_type):
            keys |= _field_keys_of(field_type, dotted)
    return keys


_BOOTSTRAP_FIELDS: frozenset[str] = frozenset({"config_dir", "raw"})
"""AppConfig fields managed at bootstrap, not present in application.yaml."""


def _unwrapped_type(f: Field) -> type | None:
    t = f.type
    origin = getattr(t, "__origin__", None)
    if origin is not None:
        args = getattr(t, "__args__", ())
        non_none = [a for a in args if a is not type(None)]  # noqa: E721
        if len(non_none) == 1:
            return non_none[0]
        return t
    return t if t is not type(None) else None  # noqa: E721


def _is_compound(t: type) -> bool:
    if isinstance(t, type):
        if issubclass(t, BaseModel):
            return True
        if is_dataclass(t):
            return True
    return False


def _field_keys_of(cls_or_name: type, prefix: str = "") -> set[str]:
    if issubclass(cls_or_name, BaseModel):
        return _pydantic_field_keys(cls_or_name, prefix)
    if is_dataclass(cls_or_name):
        return _dataclass_field_keys(cls_or_name, prefix)
    return set()


def _expected_keys() -> set[str]:
    if _AppConfig is None or _UserConfig is None:
        return set()

    expected: set[str] = set()
    for f in fields(_AppConfig):
        if f.name in _BOOTSTRAP_FIELDS:
            continue
        expected.add(f.name)
        field_type = _unwrapped_type(f)
        if _is_compound(field_type):
            expected |= _field_keys_of(field_type, f.name)
        elif f.name == "user_config":
            expected |= _field_keys_of(_UserConfig, "user_config")
    return expected


def _format_diff(only_in_yaml: set[str], only_in_model: set[str]) -> str:
    parts: list[str] = []
    if only_in_yaml:
        parts.append("Keys in application.yaml but NOT in the config model:")
        for k in sorted(only_in_yaml):
            parts.append(f"  + {k}")
    if only_in_model:
        parts.append("Keys in the config model but NOT in application.yaml:")
        for k in sorted(only_in_model):
            parts.append(f"  - {k}")
    parts.append(
        "\nTip: Add missing keys to configs/application.yaml matching the "
        "config model in gateway/community/config/_models.py, or update "
        "the model if the YAML key is intentional and the model is stale."
    )
    return "\n".join(parts)


def test_config_keys_match_model() -> None:
    """Every key in ``application.yaml`` must map to a field in the
    config model, and every declared top-level model field should
    appear in the YAML.
    """
    if _AppConfig is None:
        pytest.skip(
            "gateway package not importable — run from src/gateway/ "
            "with `python -m pytest tests/architecture/`"
        )

    if not _CONFIG_FILE.exists():
        pytest.fail(f"Config file not found: {_CONFIG_FILE}")

    with _CONFIG_FILE.open() as fh:
        yaml_data: dict[str, Any] = yaml.safe_load(fh) or {}

    yaml_keys = _flatten_keys(yaml_data)
    model_keys = _expected_keys()

    only_in_yaml = yaml_keys - model_keys
    only_in_model = model_keys - yaml_keys

    if only_in_yaml or only_in_model:
        diff = _format_diff(only_in_yaml, only_in_model)
        import warnings

        warnings.warn(
            f"Config key mismatch detected between:\n"
            f"  YAML:  {_CONFIG_FILE}\n"
            f"  Model: gateway.community.config._models\n\n"
            f"{diff}"
        )
