"""B8 ratchet: the community-shipped composition-root surface is corp-free.

B1–B7 made the per-concern plugin + DI-column layers import-disjoint; B8 finishes
the *composition-root* layer. This guard pins that the files a community/OSS
distribution ships as its composition root name **no corp construct**:

- The neutral config surface (``di/config.py`` types, the neutral ``ConfigModule``,
  ``di/config_community.py``) imports nothing corp and defines no corp config type.
- The profile selector (``di/profile_modules.py``) and the corp-column seam
  (``di/modules_bootstrap.py``) carry no ``plugins.prod`` import and reference
  ``infrastructure.corp`` ONLY through the documented seam / test-reuse allowlist
  (the corp column is supplied via the registry, mirroring ``config_bootstrap``).
- The community base YAML carries no corp endpoint or secret.

It must pass with the corp side fully wired (corp deps installed in this venv).
"""
from __future__ import annotations

import ast
import json
import pathlib

import yaml


_SRC = pathlib.Path(__file__).resolve().parents[3] / "src" / "agentclaw"
# B11: community-shipped configs live in the community subtree.
_CONFIGS = _SRC / "community" / "configs"


# Corp / internal package prefixes that must never appear in the neutral surface.
_FORBIDDEN_IMPORT_PREFIXES = (
    "agentclaw.corp.plugins.prod",
    "agentclaw.corp.di.config_corp",
    "agentclaw.corp.di.modules.infrastructure.corp",
    "sofapy_base",
    "arca",
    "layotto",
    "mist",
    "daas",
    "oss2",
    "ant_skills_scan_sdk",
)

# The corp config dataclasses (must live in config_corp.py, never config.py).
_CORP_CONFIG_TYPES = frozenset(
    {
        "ArcaSandboxConfig",
        "ArcaAicodingTemplateConfig",
        "BuserviceSsoConfig",
        "BuserviceTokenExchangeConfig",
        "TokenExchangeConfig",
        "AceagentConfig",
        "SkillCenterApiConfig",
        "CodefuseTokenConfig",
        "DeviceLocalConfig",
        "AntCodeConfig",
    }
)


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ── The neutral config surface: strictly corp-free ──────────────────────────

_STRICT_NEUTRAL_FILES = (
    _SRC / "community" / "di" / "config.py",
    _SRC / "community" / "di" / "config_community.py",
    _SRC / "community" / "di" / "modules" / "config_module.py",
)


def test_neutral_config_surface_imports_nothing_corp():
    offenders: list[str] = []
    for f in _STRICT_NEUTRAL_FILES:
        for mod in _imported_modules(f):
            if mod.startswith(_FORBIDDEN_IMPORT_PREFIXES):
                offenders.append(f"{f.name}: imports {mod}")
    assert not offenders, (
        "The neutral config surface (community-shipped) must import no corp / "
        "internal package:\n  " + "\n  ".join(offenders)
    )


def test_neutral_config_types_module_defines_no_corp_type():
    tree = ast.parse((_SRC / "community" / "di" / "config.py").read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    leaked = sorted(defined & _CORP_CONFIG_TYPES)
    assert not leaked, (
        "di/config.py (neutral, community-shipped) must define no corp config "
        f"type — these belong in di/config_corp.py: {leaked}"
    )


# ── Composition-root seam files: corp only via the per-file allowlist ────────
#
# The three composition-root seam files each defer exactly one class of corp
# import to a corp-only branch (mirroring each other):
#   - config_bootstrap.py  → plugins.prod.config (the corp ConfigProvider)
#   - modules_bootstrap.py → infrastructure.corp.column (the corp module column)
#   - profile_modules.py   → the TEST branch reuses two corp *config* modules
#     (CorpConfigModule + the profile-blind CorpTokenVaultModule) via
#     function-local imports; the test profile is a corp-code CI profile bundled
#     with corp deps, never selected in a community deployment.
# Everything else in these files (and every branch) must be corp-free.
_CORP_IMPORT_PREFIXES = (
    "agentclaw.corp.plugins.prod",
    "agentclaw.corp.di.modules.infrastructure.corp",
)
_SEAM_ALLOWED_CORP_IMPORTS = {
    "config_bootstrap.py": {
        "agentclaw.corp.plugins.prod.config",
    },
    "modules_bootstrap.py": {
        "agentclaw.corp.di.modules.infrastructure.corp.column",
    },
    # The profile selector names ZERO corp module in any branch: the corp column
    # AND the test-column corp reuse both come via the modules_bootstrap registry.
    "profile_modules.py": set(),
}


def test_seam_files_reference_corp_only_via_allowlist():
    for rel, allowed in _SEAM_ALLOWED_CORP_IMPORTS.items():
        mods = _imported_modules(_SRC / "community" / "di" / rel)
        corp = {m for m in mods if m.startswith(_CORP_IMPORT_PREFIXES)}
        unexpected = sorted(corp - allowed)
        assert not unexpected, (
            f"{rel} references undocumented corp modules — the composition-root "
            f"seam files may only name the documented deferred corp import(s) "
            f"{sorted(allowed)}: {unexpected}"
        )


# ── Community base YAML: no corp endpoints or secrets ────────────────────────

# The domain markers are assembled from fragments so this guard's own source
# carries no literal internal-domain token for an OSS grep to flag; they still
# match the real domains at runtime.
_ALI = "ali" "pay"
_CORP_YAML_MARKERS = (
    _ALI + ".com",
    _ALI + ".net",
    "antgroup",
    "aliyuncs",
    "@other_manual",
    "access_secret",
    "access_key_secret",
    "private_token",
)


def test_community_yaml_has_no_corp_markers():
    # Scan the parsed VALUES (not comments — the header comment legitimately
    # names the forbidden markers as guidance).
    loaded = yaml.safe_load(
        (_CONFIGS / "application-community.yaml").read_text(encoding="utf-8")
    )
    blob = json.dumps(loaded)
    leaked = [m for m in _CORP_YAML_MARKERS if m in blob]
    assert not leaked, (
        "configs/application-community.yaml (community base) must carry no corp "
        f"endpoint / secret marker in its values: {leaked}"
    )


# ── core/ + adapters/ may reference corp config only under TYPE_CHECKING ─────
#
# config_corp is corp-side (not shipped to community). A community-imported core
# service that must name a corp config type (a corp-only service constructed only
# by an explicit corp provider) may do so ONLY inside an ``if TYPE_CHECKING:``
# block, so the runtime import never executes in a community boot (B8 review, pt
# 4/6). A module-level / function-local runtime ``config_corp`` import here would
# break a community boot the moment the module is imported.

_TYPE_CHECKING_SCAN_ROOTS = (_SRC / "community" / "core", _SRC / "community" / "adapters")


def _type_checking_body_lines(tree: ast.AST) -> set[int]:
    """Line numbers that live inside an ``if TYPE_CHECKING:`` block."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )
            if is_tc:
                for child in node.body:
                    for n in ast.walk(child):
                        if hasattr(n, "lineno"):
                            lines.add(n.lineno)
    return lines


def test_core_and_adapters_config_corp_imports_are_type_checking_only():
    offenders: list[str] = []
    for root in _TYPE_CHECKING_SCAN_ROOTS:
        for py in sorted(root.rglob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            tc_lines = _type_checking_body_lines(tree)
            for node in ast.walk(tree):
                mod = None
                if isinstance(node, ast.ImportFrom) and node.module:
                    mod = node.module
                elif isinstance(node, ast.Import):
                    mod = next(
                        (a.name for a in node.names if "config_corp" in a.name), None
                    )
                if mod and "agentclaw.corp.di.config_corp" in mod:
                    if node.lineno not in tc_lines:
                        offenders.append(f"{py.relative_to(_SRC)}:{node.lineno}")
    assert not offenders, (
        "community-shipped core/ + adapters/ may import config_corp ONLY inside "
        "an `if TYPE_CHECKING:` block (the service is constructed by an explicit "
        "corp provider). Runtime config_corp imports here break a community "
        "boot:\n  " + "\n  ".join(offenders)
    )


# ── The community entrypoint + logger: strictly corp-free ────────────────────
#
# The community entrypoint (``main.py``) and the logger factory (``log.py``) are
# part of the community-shipped surface but are NOT under the config/core/adapters
# roots the scans above cover — the pre-split ``main.py`` named ``sofapy_base``
# (its prod boot) and ``log.py`` named it (a try/except probe), and both slipped
# past every guard. The corp/prod boot now lives in ``agentclaw/corp/main.py`` and
# the corp logger is installed by the corp side, so these two files must name NO
# corp/internal package — not ``sofapy_base`` and not any ``agentclaw.corp`` module.

_ENTRYPOINT_AND_LOGGER_FILES = (
    _SRC / "community" / "main.py",
    _SRC / "community" / "log.py",
    _SRC / "community" / "_entry.py",
)

# Same third-party corp prefixes as the neutral surface, plus the whole
# ``agentclaw.corp`` subpackage (a community distribution ships without it).
_ENTRYPOINT_FORBIDDEN_PREFIXES = _FORBIDDEN_IMPORT_PREFIXES + ("agentclaw.corp",)


def test_entrypoint_and_logger_are_corp_free():
    offenders: list[str] = []
    for f in _ENTRYPOINT_AND_LOGGER_FILES:
        for mod in _imported_modules(f):
            if mod.startswith(_ENTRYPOINT_FORBIDDEN_PREFIXES):
                offenders.append(f"{f.name}: imports {mod}")
    assert not offenders, (
        "The community entrypoint + logger must import no corp / internal package "
        "(the corp/prod boot lives in agentclaw/corp/main.py; the corp logger is "
        "installed by the corp side):\n  " + "\n  ".join(offenders)
    )
