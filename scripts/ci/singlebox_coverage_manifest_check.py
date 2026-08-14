#!/usr/bin/env python3
"""Validate singlebox module paths and offline Plugin API evidence."""

from __future__ import annotations

import argparse
import ast
import functools
import sys
from pathlib import Path
from typing import Any

import yaml


@functools.lru_cache(maxsize=None)
def _parse_ast(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _get_base_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _get_base_name(node.value)
    return ""


def _class_methods(path: Path) -> dict[str, set[str]]:
    if not path.is_file():
        return {}
    tree = _parse_ast(path)
    return {
        node.name: {
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }


def _class_bases(path: Path) -> dict[str, set[str]]:
    if not path.is_file():
        return {}
    tree = _parse_ast(path)
    return {
        node.name: {_get_base_name(base) for base in node.bases} - {""}
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }


def _plugin_protocols(backend_root: Path) -> dict[str, set[str]]:
    protocols: dict[str, set[str]] = {}
    plugin_api_root = backend_root / "src/agentclaw/community/plugin_api"
    for path in plugin_api_root.glob("*.py"):
        tree = _parse_ast(path)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {_get_base_name(base) for base in node.bases} - {""}
            if "Plugin" not in base_names:
                continue
            protocols[node.name] = {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return protocols


def validate_manifest(
    manifest: dict[str, Any], *, backend_root: Path
) -> list[str]:
    errors: list[str] = []
    protocols = _plugin_protocols(backend_root)
    modules = manifest.get("modules") or {}
    if not isinstance(modules, dict):
        return ["coverage manifest modules must be a mapping"]
    for module_name, module in modules.items():
        if not isinstance(module, dict):
            errors.append(f"{module_name}: module config must be a mapping")
            continue
        for target in module.get("acceptance_targets") or []:
            if not (backend_root / str(target)).exists():
                errors.append(f"{module_name}: missing acceptance target {target}")
        for core_path in module.get("core_paths") or []:
            if not (backend_root / str(core_path)).exists():
                errors.append(f"{module_name}: missing core path {core_path}")
        plugin_api = module.get("plugin_api") or {}
        if plugin_api.get("status", "applicable") == "not_applicable":
            continue
        for item in plugin_api.get("items") or []:
            if isinstance(item, str):
                errors.append(
                    f"{module_name}: {item} must declare offline evidence"
                )
                continue
            if not isinstance(item, dict) or not isinstance(item.get("key"), str):
                errors.append(f"{module_name}: invalid Plugin API item {item!r}")
                continue
            key = item["key"]
            try:
                protocol_name, method_name = key.split(".", 1)
            except ValueError:
                errors.append(f"{module_name}: invalid Plugin API key {key}")
                continue
            if protocol_name not in protocols:
                errors.append(
                    f"{module_name}: {protocol_name} is not a declared Plugin Protocol"
                )
                continue
            if method_name not in protocols[protocol_name]:
                errors.append(
                    f"{module_name}: {key} is not declared by {protocol_name}"
                )
                continue
            evidence = item.get("evidence")
            if not isinstance(evidence, dict):
                errors.append(f"{module_name}: {key} must declare offline evidence")
                continue
            evidence_path = evidence.get("path")
            symbol = evidence.get("symbol")
            if not isinstance(evidence_path, str) or not isinstance(symbol, str):
                errors.append(f"{module_name}: {key} has invalid offline evidence")
                continue
            source_path = (backend_root / evidence_path).resolve()
            plugin_impl_root = (
                backend_root / "src/agentclaw/community/plugins"
            ).resolve()
            if not source_path.is_relative_to(plugin_impl_root):
                errors.append(
                    f"{module_name}: evidence source must be under "
                    "src/agentclaw/community/plugins: "
                    f"{evidence_path}"
                )
                continue
            if not source_path.is_file():
                errors.append(
                    f"{module_name}: evidence source does not exist: {evidence_path}"
                )
                continue
            symbol_parts = symbol.split(".")
            methods = _class_methods(source_path)
            if len(symbol_parts) != 2 or symbol_parts[1] not in methods.get(
                symbol_parts[0], set()
            ):
                errors.append(
                    f"{module_name}: evidence symbol does not exist: {symbol}"
                )
                continue
            if protocol_name not in _class_bases(source_path).get(
                symbol_parts[0], set()
            ):
                errors.append(
                    f"{module_name}: {symbol_parts[0]} does not implement "
                    f"{protocol_name}"
                )
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repo_root / "scripts/ci/singlebox_coverage_modules.yaml",
    )
    parser.add_argument(
        "--backend-root", type=Path, default=repo_root / "src/backend"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = yaml.safe_load(args.manifest.read_text(encoding="utf-8")) or {}
    errors = validate_manifest(manifest, backend_root=args.backend_root)
    if errors:
        print("singlebox coverage manifest validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("singlebox coverage manifest is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
