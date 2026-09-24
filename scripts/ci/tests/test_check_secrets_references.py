"""Credential references must not block release backmerges."""
import importlib.util
from pathlib import Path
import sys

import pytest


spec = importlib.util.spec_from_file_location(
    "check_secrets_references", Path(__file__).parents[1] / "check_secrets.py"
)
scanner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scanner
spec.loader.exec_module(scanner)


@pytest.mark.parametrize("path,line", [
    ("service.py", 'provider_admin_token = (config.provider_admin_token or "").strip()'),
    ("config.json", '"skill_center_internal_token": "other_manual_agentclaw_skill_center_internal_token",'),
])
def test_secret_references_are_not_credentials(path, line):
    diff = f"+++ b/{path}\n@@ -0,0 +1 @@\n+{line}\n"
    assert scanner._findings(diff) == []


@pytest.mark.parametrize("path,value", [
    ("service.py", '"config.provider_admin_token"'),
    ("config.yaml", 'abcde.0123456789.secret-value'),
    ("service.py", '"AbCdEf0123456789XYZ-secret-value"'),
])
def test_literal_credentials_remain_blocked(path, value):
    separator = ": " if path.endswith(".yaml") else " = "
    line = "provider_admin_token" + separator + value
    diff = f"+++ b/{path}\n@@ -0,0 +1 @@\n+{line}\n"
    assert scanner._findings(diff)
