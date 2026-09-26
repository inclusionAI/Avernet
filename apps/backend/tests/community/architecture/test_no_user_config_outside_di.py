"""Architecture guard: ``user_config`` reads must live only in the DI layer.

Spec acceptance criterion #3:

    0 hits for ``user_config.get`` / ``sofa.sofa_config.user_config`` /
    ``sofapy_base.app.config.get_config`` outside ``di/modules/`` and
    ``core/config/sofa.py``.

Task 5 already cleaned up every offending site, so this test PASSES as
of Task 8 with a tiny allowlist (the DI layer itself + the sofa shim).
It is in place to keep future PRs from regressing — adding a fresh
``user_config.get(...)`` to a service will fail in CI rather than slip
in unnoticed.

Detection is AST-based, so the patterns must appear as real code
(attribute access, call, import). Comments and string literals
mentioning the same names — e.g. this docstring — are ignored.

The allowlist will shrink to one entry once ``EnvConfig`` is provided
through DI and the lone ``core/config/sofa.py`` shim is folded into
``config_module.py`` (tracked alongside the per-module migrations of
``arca_factory`` and ``core/storage/path``).
"""
from __future__ import annotations

import ast
import pathlib

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]  # .../src/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"


# Files where reading user_config / calling get_config IS legal — they
# are the DI layer that owns the substrate, plus the small sofa shim.
#
# The skill_center / utils / passport / git_sync entries below were
# introduced by origin/dev features that landed after Task 9d. They
# read ``user_config`` directly because their feature work pre-dated
# the DI substrate. Each will get a proper DI migration in its own
# follow-up task; the entry is removed at that point.
_ALLOWED_FILES: frozenset[str] = frozenset({
    "di/modules/config_module.py",
    "di/modules/economy_governance_module.py",
    "core/config/sofa.py",
    # The corporate ConfigProvider — this is the single sanctioned home for the
    # sofapy get_config() read (B2). core/config/sofa.py routes through it via
    # the provider registry instead of importing sofapy directly.
    "corp/plugins/prod/config.py",
    # Origin/dev additions awaiting DI migration:
    "corp/plugins/prod/arca_io.py",
    "adapters/http/skill_center/skills.py",
    "core/skill_center/services/skill_center_sync_service.py",
    "core/skill_center/services/skill_scan.py",
    "core/skill_center/services/git_sync.py",
    "corp/plugins/prod/passport.py",
})


def _relpath(file: pathlib.Path) -> str:
    rel = file.relative_to(_AGENTCLAW_ROOT).as_posix()
    # B11: layers migrate under ``agentclaw/community/<layer>``. Strip the
    # ``community/`` prefix so layer-relative allowlist keys ("core/...") match
    # whichever side of the move a file is on.
    return rel[len("community/"):] if rel.startswith("community/") else rel


def _attr_chain(node: ast.AST) -> list[str] | None:
    """Return the dotted attribute chain for ``a.b.c`` style nodes.

    Returns ``["sofapy_base", "app", "config", "get_config"]`` for
    ``sofapy_base.app.config.get_config``. Returns ``None`` when the
    chain isn't anchored at a plain ``Name``.
    """
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    parts.reverse()
    return parts


def _find_violations(tree: ast.AST) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # ``user_config.get`` — direct attribute access on a local named
        # ``user_config``.
        if isinstance(node, ast.Attribute) and node.attr == "get":
            if isinstance(node.value, ast.Name) and node.value.id == "user_config":
                out.append((node.lineno, "user_config.get"))

        # ``...sofa_config.user_config`` — any chain ending in
        # ``.sofa_config.user_config``.
        if isinstance(node, ast.Attribute) and node.attr == "user_config":
            chain = _attr_chain(node)
            if chain and len(chain) >= 2 and chain[-2] == "sofa_config":
                out.append((node.lineno, ".".join(chain)))

        # ``sofapy_base.app.config.get_config`` chained access.
        if isinstance(node, ast.Attribute):
            chain = _attr_chain(node)
            if chain == ["sofapy_base", "app", "config", "get_config"]:
                out.append((node.lineno, "sofapy_base.app.config.get_config"))

        # ``from sofapy_base.app.config import get_config``.
        if isinstance(node, ast.ImportFrom):
            if node.module == "sofapy_base.app.config":
                for alias in node.names:
                    if alias.name == "get_config":
                        out.append(
                            (node.lineno, "from sofapy_base.app.config import get_config")
                        )
    return out


def test_no_user_config_reads_outside_di_layer() -> None:
    violations: list[str] = []
    for file in _AGENTCLAW_ROOT.rglob("*.py"):
        rel = _relpath(file)
        if rel in _ALLOWED_FILES:
            continue
        try:
            tree = ast.parse(file.read_text(), filename=str(file))
        except SyntaxError:
            continue
        for lineno, kind in _find_violations(tree):
            violations.append(f"{rel}:{lineno} reads `{kind}`")
    assert not violations, (
        "user_config / get_config reads must live in di/modules/ only.\n"
        "Move the read into a typed @provider in ConfigModule and inject\n"
        "the dataclass into the consumer.\n"
        "Violations:\n  " + "\n  ".join(violations)
    )


def test_allowlist_files_exist() -> None:
    """Stale allowlist entries fail loudly so they get pruned.

    B11: corp-tolerant — a ``corp/`` allowlist entry cannot exist in a corp-absent
    tree; skip those there, still check them in the monorepo (corp present).
    """
    corp_present = (_AGENTCLAW_ROOT / "corp").is_dir()
    missing = [
        rel for rel in _ALLOWED_FILES
        if not (_AGENTCLAW_ROOT / rel).is_file()
        and not (_AGENTCLAW_ROOT / "community" / rel).is_file()
        and not (rel.startswith("corp/") and not corp_present)
    ]
    assert not missing, (
        "Allowlist references files that do not exist:\n  " + "\n  ".join(missing)
    )
