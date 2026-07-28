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


def bundle_contract(root: Path, output_dir: Path) -> Path:
    contract = load_contract(root)
    errors = validate_contract(contract)
    if errors:
        raise ValueError("\n".join(errors))

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
