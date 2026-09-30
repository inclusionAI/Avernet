from __future__ import annotations

import re
from pathlib import Path

# this file: .../src/engine/src/engine/community/tests/architecture/<f>.py
# parents[2] == .../src/engine/src/engine/community  (community package root)
# Post-hoist: core/api/manager.py live under community/, so ROOT resolves to
# community/ and the targets below scan the real OSS-shipped layers.
ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = re.compile(
    r"alipay\.com|alipay\.net|antgroup-inc|antfin|aivision\.alipay|devapi\.alipay|"
    r"project\.alipay|teamclaw\.alipay|mcpnexus\.alipay|apzero|"
    r"alipay_antdingopensdk|alibabacloud"
)


def _python_files(path: Path):
    if path.is_file():
        yield path
        return
    yield from path.rglob("*.py")


def test_no_internal_vendor_or_domain_in_core_api_manager():
    targets = [ROOT / "core", ROOT / "api", ROOT / "manager.py"]
    leaks: list[str] = []
    for target in targets:
        for file in _python_files(target):
            if "__pycache__" in file.parts:
                continue
            text = file.read_text(encoding="utf-8")
            if FORBIDDEN.search(text):
                leaks.append(str(file.relative_to(ROOT)))
    assert leaks == []
