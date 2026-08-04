#!/usr/bin/env python3
"""Load and validate the checked-in BCN OpenAPI contract."""

from __future__ import annotations

import argparse
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


HTTP_METHODS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "trace",
}
ROUTING_ONLY_OPERATION_ID_PARTS = {"collaboration", "bcn", "openapi"}
ENVELOPE_FIELDS = {"code", "message", "data", "request_id"}


def _json_pointer(document: Any, pointer: str) -> Any:
    current = document
    if not pointer:
        return current
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer: #{pointer}")
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise ValueError(f"unresolved JSON pointer: #{pointer}") from error
    return current


def _read_yaml(path: Path, cache: dict[Path, Any]) -> Any:
    resolved = path.resolve()
    if resolved not in cache:
        with resolved.open(encoding="utf-8") as stream:
            cache[resolved] = yaml.safe_load(stream)
    return cache[resolved]


def _resolve(
    value: Any,
    *,
    current_file: Path,
    cache: dict[Path, Any],
    stack: tuple[tuple[Path, str], ...] = (),
) -> Any:
    if isinstance(value, list):
        return [
            _resolve(item, current_file=current_file, cache=cache, stack=stack)
            for item in value
        ]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        reference = value["$ref"]
        file_part, separator, pointer = reference.partition("#")
        target_file = (
            (current_file.parent / file_part).resolve()
            if file_part
            else current_file.resolve()
        )
        target_key = (target_file, pointer)
        if target_key in stack:
            raise ValueError(f"cyclic $ref is not supported: {reference}")
        target_document = _read_yaml(target_file, cache)
        target = _json_pointer(target_document, pointer if separator else "")
        resolved = _resolve(
            deepcopy(target),
            current_file=target_file,
            cache=cache,
            stack=(*stack, target_key),
        )
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        if siblings:
            if not isinstance(resolved, dict):
                raise ValueError(f"$ref siblings require an object target: {reference}")
            resolved.update(
                _resolve(
                    siblings,
                    current_file=current_file,
                    cache=cache,
                    stack=stack,
                )
            )
        return resolved
    return {
        key: _resolve(item, current_file=current_file, cache=cache, stack=stack)
        for key, item in value.items()
    }


def load_contract(root: Path) -> dict[str, Any]:
    entrypoint = root / "openapi.yaml"
    cache: dict[Path, Any] = {}
    document = _read_yaml(entrypoint, cache)
    resolved = _resolve(document, current_file=entrypoint, cache=cache)
    if not isinstance(resolved, dict):
        raise ValueError("OpenAPI entrypoint must contain an object")
    return resolved


def _iter_operations(contract: dict[str, Any]):
    for path, path_item in contract.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() in HTTP_METHODS:
                yield method.lower(), path, operation


def _response_schema(response: dict[str, Any]) -> dict[str, Any] | None:
    content = response.get("content", {})
    media = content.get("application/json")
    if not isinstance(media, dict):
        return None
    schema = media.get("schema")
    return schema if isinstance(schema, dict) else None


def validate_contract(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    operation_ids: set[str] = set()

    for method, path, operation in _iter_operations(contract):
        location = f"{method.upper()} {path}"
        if not path.startswith("/openapi/v1/"):
            errors.append(f"{location}: path is outside /openapi/v1/**")
        if path.startswith("/openapi/v1/internal/"):
            errors.append(f"{location}: Internal API is not in the first batch")

        operation_id = operation.get("operationId")
        if not operation_id:
            errors.append(f"{location}: missing operationId")
        elif operation_id in operation_ids:
            errors.append(f"{location}: duplicate operationId {operation_id}")
        else:
            operation_ids.add(operation_id)
            if not re.fullmatch(r"[a-z][a-z0-9_]*", operation_id):
                errors.append(f"{location}: operationId must be snake_case")
            parts = set(operation_id.split("_"))
            forbidden = parts & ROUTING_ONLY_OPERATION_ID_PARTS
            if forbidden or re.search(r"(^|_)v[0-9]+($|_)", operation_id):
                errors.append(
                    f"{location}: operationId contains routing/version-only naming"
                )

        if "x-avernet-security" not in operation:
            errors.append(f"{location}: missing x-avernet-security")

        responses = operation.get("responses", {})
        if not responses:
            errors.append(f"{location}: missing responses")
        for status, response in responses.items():
            websocket_upgrade = (
                operation.get("x-avernet-protocol") == "websocket"
                and str(status) == "101"
            )
            schema = _response_schema(response)
            if schema is None and not websocket_upgrade:
                errors.append(f"{location} {status}: missing JSON response schema")
            elif schema is not None:
                required = set(schema.get("required", []))
                if not ENVELOPE_FIELDS.issubset(required):
                    errors.append(f"{location} {status}: response is not an envelope")
            if (
                not str(status).startswith("2")
                and not websocket_upgrade
                and not response.get("x-error-codes")
            ):
                errors.append(f"{location} {status}: missing x-error-codes")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()

    try:
        contract = load_contract(args.root)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"OpenAPI contract load failed: {error}")
        return 1

    errors = validate_contract(contract)
    if errors:
        for error in errors:
            print(error)
        return 1

    operation_count = sum(1 for _ in _iter_operations(contract))
    print(f"{operation_count} operations validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
