#!/usr/bin/env python3
"""Export the BCN OpenAPI contract as deterministic, self-contained JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

try:
    from .bundle_openapi_contract import _rewrite_discriminator_mappings
    from .validate_openapi_contract import (
        HTTP_METHODS,
        PUBLIC_COLLABORATION_PREFIX,
        load_contract,
        validate_contract,
    )
except ImportError:
    from bundle_openapi_contract import _rewrite_discriminator_mappings
    from validate_openapi_contract import (
        HTTP_METHODS,
        PUBLIC_COLLABORATION_PREFIX,
        load_contract,
        validate_contract,
    )


DEFAULT_CONTRACT_ROOT = Path(__file__).resolve().parents[1] / "api-contracts" / "v1"


def _validate_operation_prefixes(contract: dict[str, object], prefix: str) -> None:
    prefixes = (prefix,)
    if prefix == PUBLIC_COLLABORATION_PREFIX:
        prefixes = (PUBLIC_COLLABORATION_PREFIX, "/openapi/v1/auth/")
    for path, path_item in contract.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        if any(method.lower() in HTTP_METHODS for method in path_item):
            if not any(path.startswith(item) for item in prefixes):
                joined = " or ".join(prefixes)
                raise ValueError(
                    f"OpenAPI operation path must use {joined}: {path}"
                )


def dump_contract(
    root: Path,
    output: Path,
    *,
    entrypoint: str = "openapi.yaml",
    path_prefix: str = PUBLIC_COLLABORATION_PREFIX,
) -> Path:
    contract = load_contract(root, entrypoint=entrypoint)
    errors = validate_contract(
        contract,
        path_prefix=path_prefix,
    )
    if errors:
        raise ValueError("\n".join(errors))
    _validate_operation_prefixes(contract, path_prefix)
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
    parser.add_argument("--entrypoint", default="openapi.yaml")
    parser.add_argument("--path-prefix", default=PUBLIC_COLLABORATION_PREFIX)
    args = parser.parse_args()

    try:
        output = dump_contract(
            args.root,
            args.output,
            entrypoint=args.entrypoint,
            path_prefix=args.path_prefix,
        )
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(error)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
