"""Architecture guard: local device impls must not bypass the device seam
for FS ops, must not hardcode 127.0.0.1/localhost.

Scans ``plugins/local/*.py`` plus the local impls demoted to ``core`` in B9
(``EXTRA_SCAN_FILES``) — those left ``plugins/local/`` but keep the same rules.

Single-file guards (test_local_filesystem_baas_only.py / test_local_health_probe_baas_only.py)
cover specific plugins; this is the catch-all that scans the entire
``plugins/local/`` directory so a regression in any other local plugin gets
caught.

Rules (per spec §5.5):
  1. No ``import shutil`` (or ``shutil.*`` attribute access) in BaaS-mode
     method bodies. Pathlib-fallback methods named ``_pathlib_*`` are the
     designed exception (plan-02/03 dual-mode pattern) and stay allowed.
  2. No ``Path(...).rmtree / iterdir / symlink_to / rglob / mkdir`` etc.
     in BaaS-mode method bodies.
  3. No ``127.0.0.1`` / ``localhost`` string literals (outside comments and
     docstrings).

Allow-listed files (intentional, documented exceptions):
  - ``process_manager.py``: legacy LocalProcessManager kept for the
    aicoding/health legacy path until the dedicated cleanup round.
    Tracked in spec §1.4.
  - ``_mock_seam.py``: test infrastructure mixin, not a real plugin.
  - ``__init__.py``: package marker.
"""
import ast
from pathlib import Path


LOCAL_PLUGINS_DIR = (
    Path(__file__).resolve().parents[3]
    / "src" / "agentclaw" / "community" / "plugins" / "local"
)

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "agentclaw"

# Files that used to live under ``plugins/local/`` and were demoted to ``core``
# (B9) but must keep obeying the same dual-mode rules (BaaS-mode routes via the
# device seam; pathlib only inside ``_pathlib_*``; no hardcoded localhost). They
# left ``LOCAL_PLUGINS_DIR`` so the glob no longer sees them — scan them here so
# the catch-all coverage doesn't silently narrow.
EXTRA_SCAN_FILES = [
    _SRC_ROOT / "community" / "core" / "devices" / "services" / "local_device_filesystem.py",
]

# Files exempt from the FS-ops rules (full-file allowlist):
ALLOWLIST_FILES = {
    "process_manager.py",     # legacy LocalProcessManager (spec §1.4 cleanup)
    "_mock_seam.py",          # test infrastructure
    "__init__.py",            # pkg marker
    "baas_service.py",        # added by upstream BaaS refactor (post-merge)
}

# These method-name prefixes are designed to KEEP shutil/pathlib FS ops
# (plan-02/03 dual-mode ctor pattern — pathlib fallback for contract tests).
PATHLIB_METHOD_PREFIXES = ("_pathlib_",)

# Specific method names exempt from FS-op rules — host-side helpers that
# pre-existed plan-02/03/04 BaaS 改造 and intentionally操作 the developer's
# host filesystem (e.g., rsyncing local skills-repo into ~/.openclaw/workspace/).
# These are local-dev simulator helpers with no BaaS counterpart.
HOST_HELPER_METHOD_NAMES = frozenset({
    # device_sync.py
    "_ensure_skills_repo",        # local-dev fallback: creates empty skills-repo dir
                                  # under host ~/.openclaw/workspace/skills when no
                                  # source is found; otherwise rsyncs source into it
    # skill_repo_sync.py
    "sync",                       # LocalSkillRepoSyncPlugin.sync(): scans host
                                  # skills-repo source dir for child skill subtrees
})

FORBIDDEN_ATTRS = {
    "rmtree", "iterdir", "symlink_to", "rglob", "mkdir",
    "read_bytes", "write_bytes",
}
FORBIDDEN_MODULES = {"shutil"}


def _scan_files() -> list[Path]:
    """Return every .py file under plugins/local/ (plus the demoted-to-core
    local impls in EXTRA_SCAN_FILES) that is NOT allow-listed."""
    files = []
    for p in sorted(LOCAL_PLUGINS_DIR.glob("*.py")):
        if p.name in ALLOWLIST_FILES:
            continue
        files.append(p)
    files.extend(f for f in EXTRA_SCAN_FILES if f.exists())
    return files


def _enclosing_method_name(tree: ast.Module, target: ast.AST) -> str | None:
    """Walk the AST to find the FunctionDef / AsyncFunctionDef that contains
    ``target``. Returns the method name or None if at module level."""
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(parent):
                if child is target:
                    return parent.name
    return None


def _is_in_exempt_method(tree: ast.Module, target: ast.AST) -> bool:
    """True if target node is inside:
      - a _pathlib_* method (plan-02/03 dual-mode pattern), or
      - a specifically allow-listed host-side helper method
        (HOST_HELPER_METHOD_NAMES).
    """
    name = _enclosing_method_name(tree, target)
    if name is None:
        return False
    if any(name.startswith(prefix) for prefix in PATHLIB_METHOD_PREFIXES):
        return True
    if name in HOST_HELPER_METHOD_NAMES:
        return True
    return False


def test_no_shutil_or_pathlib_fs_in_baas_mode():
    """plugins/local/*.py 的 BaaS-mode 代码不能调用 shutil.* 或 Path(...).<FS-op>。"""
    violations: list[str] = []

    for path in _scan_files():
        src = path.read_text()
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            violations.append(f"{path.name}: parse error: {e}")
            continue

        for node in ast.walk(tree):
            # shutil.* attribute access
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id in FORBIDDEN_MODULES:
                    if not _is_in_exempt_method(tree, node):
                        violations.append(
                            f"{path.name}:{node.lineno} forbidden "
                            f"{node.value.id}.{node.attr} (move to _pathlib_* fallback or remove)"
                        )
            # .read_bytes / .write_bytes / .rmtree / .iterdir / etc on any expr
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
                if not _is_in_exempt_method(tree, node):
                    violations.append(
                        f"{path.name}:{node.lineno} forbidden attribute "
                        f".{node.attr} (BaaS mode must route via DeviceFileSystemPlugin)"
                    )

    assert not violations, (
        "plugins/local/ contains FS ops outside _pathlib_* fallback methods. "
        "Move to _pathlib_* or route via DeviceFileSystemPlugin:\n  "
        + "\n  ".join(violations)
    )


def test_no_localhost_literals():
    """plugins/local/*.py 不能有 127.0.0.1 / localhost 字符串字面量
    （注释/docstring 允许，因为它们不影响运行）。"""
    forbidden_substrings = ["127.0.0.1", "localhost"]
    violations: list[str] = []

    for path in _scan_files():
        src = path.read_text()
        for forbidden in forbidden_substrings:
            for i, line in enumerate(src.splitlines(), start=1):
                if forbidden not in line:
                    continue
                stripped = line.lstrip()
                # skip pure comments and obvious docstring lines
                if stripped.startswith("#"):
                    continue
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    continue
                violations.append(
                    f"{path.name}:{i} '{forbidden}' literal — "
                    f"singlebox routes via BaaS-supplied URLs (line: {stripped[:80]})"
                )

    assert not violations, (
        "plugins/local/ contains hardcoded localhost/127.0.0.1 outside "
        "comments. Route via BaaS get_http_info instead:\n  "
        + "\n  ".join(violations)
    )


def test_allowlist_files_actually_exist():
    """Sanity: every allow-listed file actually exists. Catches stale entries."""
    for name in ALLOWLIST_FILES:
        if name == "baas_service.py":
            # baas_service.py is allow-listed in anticipation of the upstream
            # BaaS refactor merge; before that merge it doesn't exist here.
            continue
        assert (LOCAL_PLUGINS_DIR / name).exists(), (
            f"allow-listed {name} does not exist under {LOCAL_PLUGINS_DIR}; "
            f"remove from ALLOWLIST_FILES"
        )
