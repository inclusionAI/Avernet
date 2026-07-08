"""Enforcing guard: the community-shipped surface carries NO corp identifiers.

OSS-0 #3. Scans every file that ships in the community distribution — all of
``src/agentclaw/`` except the corp overlay and test-double columns, plus the
neutral ``application.yaml`` base (``user_config`` only) and the community
overlay — for company hostnames, secret-registry references, container-template
UUIDs, and internal tenant identifiers. Corp values legitimately live in the
corp-only files (``plugins/prod``, ``config_corp.py``, the corp DI column, the
per-env corp yaml overlays) and the test doubles (``plugins/local``, the test
infra modules) — those are NOT shipped to community and are excluded.

If this test fails, a corp identifier leaked into a file the open-source build
ships. Move the value into a corp env overlay (yaml) or read it from config/env
with a neutral default; do not add it to an allowlist unless it is genuinely a
neutral mention (and even then, prefer rewording).

Deliberately NOT flagged (low-signal / load-bearing conventions, or a separate
tracked follow-up):
- ``module_config`` in ``application.yaml`` (sofapy mesh framework config the
  community runtime never reads — deferred; we scan only ``user_config``).
- Path/namespace conventions like ``/aidesktop``, ``teclaw``, ``arca`` used as a
  bare word (not a hostname/secret) — the domain patterns below still catch any
  real corp *endpoint*.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

_BACKEND = Path(__file__).resolve().parents[3]
_SRC = _BACKEND / "src" / "agentclaw"
# B11: the community-shipped configs now live in the community subtree.
_CONFIGS = _SRC / "community" / "configs"

# B11: the shipped community surface is now the physical ``agentclaw/community/``
# subtree (see _SHIPPED_ROOT below). Corp code lives under ``agentclaw/corp/`` and
# is excluded simply by not being scanned — the old per-file corp exclusions
# (plugins/prod, di/config_corp, infrastructure/corp, arca_*, antcode) collapse
# to "everything under corp/". Only ONE community-internal exclusion remains:
_EXCLUDED_FRAGMENTS = (
    # plugins/local/ is the local-dev / singlebox simulator (ships with community
    # for its own tests, but is dev tooling, not the OSS runtime product). One
    # reviewed corp-flavored residual lives here — the local secret-registry key
    # ``other_manual_agentclaw_aiworkbench_repo_url``. An OSS-0 follow-up, tracked
    # separately; kept excluded here.
    "plugins/local/",
)
_EXCLUDED_NAME_PREFIXES = ()

# High-signal corp identifiers. Value-level (endpoints / secrets / UUIDs /
# tenants), NOT neutral block or field names.
_CORP_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\.alipay\.(?:com|net)\b", "alipay company domain"),
    (r"\bantgroup-inc\.cn\b", "antgroup-inc.cn company domain"),
    (r"\bantgroup\.com\b", "antgroup.com company domain"),
    (r"\bantfin(?:-inc)?\.com\b", "antfin company domain"),
    (r"\.aliyuncs\.com\b", "aliyun OSS domain"),
    (r"[a-z][a-z0-9]*_manual_[a-z0-9_]+", "secret-registry key reference"),
    (r"\btheta_bcsfuse\w*", "corp secret name"),
    (r"ARCA-TEMPLATE-", "ARCA container-template uuid"),
    (r"\bTEMPLATE-[0-9a-fA-F]{16,}", "container-template uuid"),
    (r"\bteam_claw\w*", "corp tenant identifier"),
    (r"\barcaagentclaw\b", "corp host path identifier"),
    (r"\bcard_[0-9a-f]{6,}\b", "corp Aix card component id"),
    # Committed-secret prefixes (a secret must never live in shipped source).
    (r"\bbcs_pa_[0-9a-fA-F]{16,}", "committed provider token"),
    (r"\bsk-lf-[0-9a-fA-F-]{16,}", "committed Langfuse secret"),
    (r"\bsk-[a-zA-Z0-9]{24,}", "committed API key"),
)

# Explicitly-reviewed neutral mentions (file-relative-path, substring) that are
# NOT corp leaks. Keep this EMPTY if possible; every entry needs justification.
_ALLOWLIST: tuple[tuple[str, str], str] = ()  # type: ignore[assignment]


def _is_excluded(path: Path) -> bool:
    rel = path.relative_to(_SRC).as_posix()
    if any(frag in rel for frag in _EXCLUDED_FRAGMENTS):
        return True
    if path.name.startswith(_EXCLUDED_NAME_PREFIXES):
        return True
    return False


def _scan_text(text: str, label: str) -> list[str]:
    hits: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern, desc in _CORP_PATTERNS:
            m = re.search(pattern, line)
            if m:
                hits.append(f"{label}:{lineno}: {desc}: {m.group(0)!r}")
    return hits


# B11: the shipped community surface IS the physical community/ subtree.
_SHIPPED_ROOT = _SRC / "community"


def _shipped_python_files() -> list[Path]:
    files = []
    for path in _SHIPPED_ROOT.rglob("*.py"):
        if _is_excluded(path):
            continue
        files.append(path)
    return files


def test_shipped_python_has_no_corp_identifiers():
    offenders: list[str] = []
    for path in _shipped_python_files():
        rel = path.relative_to(_BACKEND).as_posix()
        offenders.extend(_scan_text(path.read_text(encoding="utf-8"), rel))
    assert not offenders, (
        "Corp identifier(s) found in community-shipped Python source. Move the "
        "value into a corp env overlay or read it from config/env with a neutral "
        "default:\n  " + "\n  ".join(offenders)
    )


def test_shipped_yaml_has_no_corp_identifiers():
    offenders: list[str] = []

    # application.yaml: neutral base — scan user_config only (module_config is
    # sofapy mesh framework config the community runtime never reads; deferred).
    base = yaml.safe_load((_CONFIGS / "application.yaml").read_text(encoding="utf-8"))
    base_uc = json.dumps(base.get("user_config", {}), ensure_ascii=False)
    offenders.extend(_scan_text(base_uc, "configs/application.yaml[user_config]"))

    # community overlay: entirely neutral — scan the whole file.
    community = (_CONFIGS / "application-community.yaml").read_text(encoding="utf-8")
    offenders.extend(_scan_text(community, "application-community.yaml"))

    # test overlay (B11): community-shipped CI overlay — entirely neutral, scan whole.
    test_overlay = (_CONFIGS / "application-test.yaml").read_text(encoding="utf-8")
    offenders.extend(_scan_text(test_overlay, "application-test.yaml"))

    # NOTE: application-singlebox.yaml is NOT scanned. Although it ships in the
    # community subtree, it is the local-dev / singlebox simulator config and still
    # carries reviewed corp residuals (like plugins/local/, excluded above) — an
    # OSS-0 follow-up tracked separately, out of scope for this split.

    assert not offenders, (
        "Corp identifier(s) found in community-shipped yaml:\n  "
        + "\n  ".join(offenders)
    )
