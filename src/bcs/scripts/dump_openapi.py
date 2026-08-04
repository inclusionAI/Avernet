#!/usr/bin/env python3
"""Export the BCN OpenAPI contract as deterministic, self-contained JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

try:
    from .bundle_openapi_contract import _rewrite_discriminator_mappings
    from .validate_openapi_contract import HTTP_METHODS, load_contract, validate_contract
except ImportError:
    from bundle_openapi_contract import _rewrite_discriminator_mappings
    from validate_openapi_contract import HTTP_METHODS, load_contract, validate_contract


COLLABORATION_PREFIX = "/openapi/v1/collaboration/"
DEFAULT_CONTRACT_ROOT = Path(__file__).resolve().parents[1] / "api-contracts" / "v1"


def _validate_collaboration_prefix(contract: dict[str, object]) -> None:
    for path, path_item in contract.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        if any(method.lower() in HTTP_METHODS for method in path_item):
            if not path.startswith(COLLABORATION_PREFIX):
                raise ValueError(
                    f"OpenAPI operation path must use {COLLABORATION_PREFIX}: {path}"
                )


def dump_contract(root: Path, output: Path) -> Path:
    contract = load_contract(root)
    errors = validate_contract(contract)
    if errors:
        raise ValueError("\n".join(errors))
    _validate_collaboration_prefix(contract)
    _rewrite_discriminator_mappings(contract)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--root", type=Path, default=DEFAULT_CONTRACT_ROOT)
    args = parser.parse_args()

    try:
        output = dump_contract(args.root, args.output)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(error)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
