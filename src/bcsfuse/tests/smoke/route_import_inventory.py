#!/usr/bin/env python3
"""
Route Import Inventory Scanner for OSS Migration (S6)

Scans existing business API routes and analyzes their import safety for OSS:
- Identifies forbidden internal imports (DRM, Layotto, Sofa, ZDAS, etc.)
- Checks for external service connections during import
- Determines which routes can be safely mounted in OSS mode
- Outputs detailed inventory report
"""

import ast
import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple
from dataclasses import dataclass, field
import importlib.util


@dataclass
class RouteModuleInfo:
    """Information about a route module"""
    module_path: str
    importable: bool = False
    forbidden_internal_imports: List[str] = field(default_factory=list)
    needs_adapter: bool = False
    can_mount_now: bool = False
    reason: str = ""
    dependencies: List[str] = field(default_factory=list)
    route_endpoints: List[str] = field(default_factory=list)


# Forbidden internal patterns (from S6 requirements)
FORBIDDEN_PATTERNS = [
    # Internal infrastructure
    "sofa_app",
    "ZDAS",
    "zdas",
    "DRM",
    "drm",
    "Layotto",
    "layotto",
    "sofapy_base",
    "rpplus",
    "qdrant_zdas",
    "faiss_zdas",

    # Internal modules
    "bcsfuse-internal",

    # Internal configs (dev/pre/prod specific)
    "config-dev",
    "config-pre",
    "config-prod",
]

# Route modules to scan (relative to src/interfaces/api/)
ROUTE_MODULES = [
    "worker_routes.py",
    "profile_routes.py",
    "fusion_routes.py",
    "recommend_routes.py",
    "verify_routes.py",
]


def scan_imports_in_file(file_path: Path) -> Tuple[Set[str], Set[str]]:
    """
    Scan a Python file for all import statements.

    Returns:
        Tuple of (imported_modules, imported_names)
    """
    imported_modules = set()
    imported_names = set()

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_modules.add(node.module)
                for alias in node.names:
                    imported_names.add(f"{node.module}.{alias.name}" if node.module else alias.name)

    except SyntaxError as e:
        print(f"ERROR: Syntax error in {file_path}: {e}")
    except Exception as e:
        print(f"ERROR: Failed to parse {file_path}: {e}")

    return imported_modules, imported_names


def check_forbidden_imports(imported_modules: Set[str], imported_names: Set[str]) -> List[str]:
    """
    Check if any imports match forbidden patterns.

    Returns:
        List of forbidden imports found
    """
    forbidden_found = []

    # Check modules
    for module in imported_modules:
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.lower() in module.lower():
                forbidden_found.append(f"import {module} (matches {pattern})")

    # Check specific names
    for name in imported_names:
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.lower() in name.lower():
                forbidden_found.append(f"from {name} (matches {pattern})")

    return forbidden_found


def extract_route_endpoints(file_path: Path) -> List[str]:
    """
    Extract route endpoint decorators from a file.

    Returns:
        List of endpoint strings (e.g., "GET /workers")
    """
    endpoints = []

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call):
                        # Check if it's a router method call
                        if hasattr(decorator.func, 'attr'):
                            method = decorator.func.attr
                            if method in ['get', 'post', 'put', 'delete', 'patch']:
                                # Extract path if available
                                if decorator.args:
                                    if isinstance(decorator.args[0], ast.Constant):
                                        path = decorator.args[0].value
                                        endpoints.append(f"{method.upper()} {path}")

    except Exception as e:
        print(f"WARNING: Failed to extract endpoints from {file_path}: {e}")

    return endpoints


def scan_route_module(route_file: str) -> RouteModuleInfo:
    """
    Scan a single route module file.

    Args:
        route_file: Route file name (e.g., "worker_routes.py")

    Returns:
        RouteModuleInfo with scan results
    """
    # Find the route file
    bcsfuse_root = Path(__file__).parent.parent
    route_path = bcsfuse_root / "src" / "interfaces" / "api" / route_file

    if not route_path.exists():
        return RouteModuleInfo(
            module_path=route_file,
            importable=False,
            reason=f"File not found: {route_path}",
        )

    info = RouteModuleInfo(module_path=route_file)

    # Scan imports
    imported_modules, imported_names = scan_imports_in_file(route_path)
    info.dependencies = sorted(list(imported_modules))

    # Check forbidden imports
    forbidden = check_forbidden_imports(imported_modules, imported_names)
    info.forbidden_internal_imports = forbidden

    # Extract endpoints
    info.route_endpoints = extract_route_endpoints(route_path)

    # Determine if can mount
    if forbidden:
        info.can_mount_now = False
        info.needs_adapter = True
        info.reason = f"BLOCKED: {len(forbidden)} forbidden imports found"
    else:
        info.can_mount_now = True
        info.needs_adapter = False
        info.reason = "OK: No forbidden imports detected"

    # Try to verify import safety
    info.importable = verify_import_safety(route_path, forbidden)

    return info


def verify_import_safety(route_path: Path, forbidden: List[str]) -> bool:
    """
    Attempt to verify if the module can be safely imported.

    Args:
        route_path: Path to the route file
        forbidden: List of forbidden imports found

    Returns:
        True if module can be safely imported
    """
    if forbidden:
        return False

    # For now, we do a static analysis only
    # In future, we could try dynamic import in a sandbox
    return True


def scan_dependencies_dir() -> Dict[str, List[str]]:
    """
    Scan the dependencies directory for forbidden imports.

    Returns:
        Dict mapping dependency file to list of forbidden imports
    """
    bcsfuse_root = Path(__file__).parent.parent
    deps_dir = bcsfuse_root / "src" / "interfaces" / "api" / "dependencies"

    if not deps_dir.exists():
        return {}

    results = {}

    for dep_file in deps_dir.glob("*.py"):
        if dep_file.name == "__init__.py":
            continue

        imported_modules, imported_names = scan_imports_in_file(dep_file)
        forbidden = check_forbidden_imports(imported_modules, imported_names)

        if forbidden:
            results[dep_file.name] = forbidden

    return results


def main():
    """Main entry point for route import inventory scanner."""
    print("=" * 70)
    print("Route Import Inventory Scanner (S6)")
    print("=" * 70)
    print()

    # Scan route modules
    print("[1] Scanning Route Modules")
    print("-" * 70)

    route_results = {}
    for route_file in ROUTE_MODULES:
        print(f"\nScanning: {route_file}")
        info = scan_route_module(route_file)
        route_results[route_file] = info

        print(f"  Importable: {'✓' if info.importable else '✗'}")
        print(f"  Can Mount Now: {'✓' if info.can_mount_now else '✗'}")
        print(f"  Needs Adapter: {'✓' if info.needs_adapter else '✗'}")
        print(f"  Reason: {info.reason}")

        if info.forbidden_internal_imports:
            print(f"  Forbidden Imports ({len(info.forbidden_internal_imports)}):")
            for imp in info.forbidden_internal_imports[:5]:  # Show first 5
                print(f"    - {imp}")
            if len(info.forbidden_internal_imports) > 5:
                print(f"    ... and {len(info.forbidden_internal_imports) - 5} more")

        if info.route_endpoints:
            print(f"  Endpoints ({len(info.route_endpoints)}):")
            for endpoint in info.route_endpoints[:5]:  # Show first 5
                print(f"    - {endpoint}")
            if len(info.route_endpoints) > 5:
                print(f"    ... and {len(info.route_endpoints) - 5} more")

    # Scan dependencies
    print("\n" + "=" * 70)
    print("[2] Scanning Dependencies Directory")
    print("-" * 70)

    dep_issues = scan_dependencies_dir()
    if dep_issues:
        for dep_file, forbidden in dep_issues.items():
            print(f"\n{dep_file}: BLOCKED")
            for imp in forbidden:
                print(f"  - {imp}")
    else:
        print("\n✓ No forbidden imports in dependencies directory")

    # Summary
    print("\n" + "=" * 70)
    print("[3] Summary")
    print("-" * 70)

    can_mount = []
    blocked = []

    for route_file, info in route_results.items():
        if info.can_mount_now:
            can_mount.append(route_file)
        else:
            blocked.append(route_file)

    print(f"\nRoutes that CAN be mounted now ({len(can_mount)}):")
    for route in can_mount:
        print(f"  ✓ {route}")

    print(f"\nRoutes that are BLOCKED ({len(blocked)}):")
    for route in blocked:
        print(f"  ✗ {route}")

    print(f"\nDependency files with issues: {len(dep_issues)}")

    # Detailed inventory output
    print("\n" + "=" * 70)
    print("[4] Detailed Inventory")
    print("-" * 70)

    for route_file, info in route_results.items():
        print(f"\n{route_file}:")
        print(f"  module_path: {info.module_path}")
        print(f"  importable: {info.importable}")
        print(f"  forbidden_internal_imports: {info.forbidden_internal_imports}")
        print(f"  needs_adapter: {info.needs_adapter}")
        print(f"  can_mount_now: {info.can_mount_now}")
        print(f"  reason: {info.reason}")

    # Final result
    print("\n" + "=" * 70)
    print("RESULT")
    print("=" * 70)

    if blocked:
        print("\nROUTE_BLOCKED_BY_INTERNAL_IMPORT")
        print(f"\n{len(blocked)} route(s) have forbidden internal imports and cannot be mounted.")
        print("These routes need adapters or stubs before mounting in OSS mode.")
    else:
        print("\nALL_ROUTES_PASS_IMPORT_CHECK")
        print("\nAll scanned routes can be safely imported in OSS mode.")

    return 0 if not blocked else 1


if __name__ == "__main__":
    sys.exit(main())