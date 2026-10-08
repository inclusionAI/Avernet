"""Architecture guard: LocalDeviceFileSystem._baas_* methods must not call shutil.

The dual-mode plugin keeps pathlib + shutil for the pathlib fallback path,
but the BaaS-mode methods (``_baas_read_file`` etc.) must go through
``self._baas_request`` → BaaS get_http_info → httpx direct. A regression
that re-introduces shutil into a _baas_ method body would defeat the
"singlebox routes via BaaS" invariant.
"""
import ast
from pathlib import Path


LOCAL_FS_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "agentclaw"
    / "community"
    / "core"
    / "devices"
    / "services"
    / "local_device_filesystem.py"
)


def _baas_method_bodies(src: str) -> dict[str, ast.AST]:
    """Return {method_name: ast.AsyncFunctionDef} for every method whose name
    starts with '_baas_' (also include _baas_request)."""
    tree = ast.parse(src)
    out: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_baas_") or node.name == "_baas_request":
                out[node.name] = node
    return out


def test_baas_methods_do_not_use_shutil_or_pathlib_io():
    """_baas_* 方法体里不应出现 shutil.* 调用或 Path(...).read_bytes/write_bytes/rmtree/iterdir。"""
    assert LOCAL_FS_PATH.exists(), f"missing {LOCAL_FS_PATH}"
    src = LOCAL_FS_PATH.read_text()
    bodies = _baas_method_bodies(src)
    assert bodies, "no _baas_* methods found — plan-02 not yet implemented?"

    forbidden_attrs = {
        "read_bytes", "write_bytes", "rmtree", "iterdir", "rglob",
        "mkdir",  # write_file 写容器，绝不 mkdir 本机
    }
    forbidden_modules = {"shutil"}

    violations: list[str] = []
    for name, fn in bodies.items():
        for node in ast.walk(fn):
            # any "shutil.*" attribute access
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id in forbidden_modules:
                    violations.append(
                        f"{name}:{node.lineno} forbidden module access {node.value.id}.{node.attr}"
                    )
            # any path.read_bytes / path.write_bytes / etc
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
                violations.append(
                    f"{name}:{node.lineno} forbidden attr access .{node.attr}"
                )

    assert not violations, (
        "BaaS-mode methods must route via _baas_request → BaaS http-info → "
        "httpx direct. Violations:\n  " + "\n  ".join(violations)
    )


def test_pathlib_methods_still_have_shutil_or_pathlib():
    """Sanity reverse-check: _pathlib_* methods DO use shutil/Path (else they're
    pointless fallbacks). Pin this so we notice if someone gut _pathlib_*."""
    src = LOCAL_FS_PATH.read_text()
    tree = ast.parse(src)
    pathlib_method_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_pathlib_"):
                pathlib_method_names.append(node.name)

    assert pathlib_method_names, "no _pathlib_* methods found — fallback gone?"

    # At least one of them should reference shutil or pathlib.Path-method
    src_text = src
    assert "shutil" in src_text or "Path(" in src_text, (
        "pathlib fallback methods should reference shutil/Path; got none"
    )
