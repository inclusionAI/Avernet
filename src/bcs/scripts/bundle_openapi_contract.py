#!/usr/bin/env python3
"""Bundle the BCN OpenAPI fragments into one deterministic YAML artifact."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

try:
    from .validate_openapi_contract import load_contract, validate_contract
except ImportError:
    from validate_openapi_contract import load_contract, validate_contract


def _pointer_part(value: str | int) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _rewrite_discriminator_mappings(
    value: object,
    path: tuple[str | int, ...] = (),
) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _rewrite_discriminator_mappings(item, (*path, index))
        return
    if not isinstance(value, dict):
        return

    discriminator = value.get("discriminator")
    variants = value.get("oneOf")
    if (
        isinstance(discriminator, dict)
        and isinstance(discriminator.get("mapping"), dict)
        and isinstance(discriminator.get("propertyName"), str)
        and isinstance(variants, list)
    ):
        property_name = discriminator["propertyName"]
        rewritten: dict[str, str] = {}
        for discriminator_value in discriminator["mapping"]:
            matches = [
                index
                for index, variant in enumerate(variants)
                if isinstance(variant, dict)
                and variant.get("properties", {})
                .get(property_name, {})
                .get("const")
                == discriminator_value
            ]
            if len(matches) != 1:
                raise ValueError(
                    "cannot resolve discriminator mapping "
                    f"{discriminator_value!r} at {path!r}"
                )
            target_path = (*path, "oneOf", matches[0])
            rewritten[discriminator_value] = "#/" + "/".join(
                _pointer_part(part) for part in target_path
            )
        discriminator["mapping"] = rewritten

    for key, item in value.items():
        _rewrite_discriminator_mappings(item, (*path, key))


def bundle_contract(root: Path, output_dir: Path) -> Path:
    contract = load_contract(root)
    errors = validate_contract(contract)
    if errors:
        raise ValueError("\n".join(errors))
    _rewrite_discriminator_mappings(contract)

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "bcn-openapi-v1.yaml"
    output.write_text(
        yaml.safe_dump(contract, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    try:
        output = bundle_contract(args.root, args.output_dir)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(error)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
