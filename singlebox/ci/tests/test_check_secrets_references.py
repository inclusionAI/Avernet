"""Credential references must not block release backmerges."""
import importlib.util
from pathlib import Path
import sys

import pytest


spec = importlib.util.spec_from_file_location(
    "check_secrets_references", Path(__file__).parents[3] / "devops/hook/check_secrets.py"
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


@pytest.mark.parametrize("parts", [
    ("token = pro", 'vider_cfg["provider_admin_token"]'),
    ("has_token = ", 'bool(result.get("bot_runtime_token"))'),
    (
        "result = self.write_codefuse_token_to_runtimes(bot=bot, plaintext_",
        "tok",
        "en=plaintext_token, env=env)",
    ),
])
def test_python_credential_references_and_multiline_calls_are_not_plaintext(parts):
    line = "".join(parts)
    assert scanner._findings(f"+++ b/service.py\n@@ -0,0 +1 @@\n+{line}\n") == []


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


# Assemble scanner input from fragments, as with synthetic tokens: fixture
# source must not itself resemble a plaintext credential assignment.
@pytest.mark.parametrize("line", [
    "callback_creden" "tial=callback_creden" "tial,",
    "callback_creden" "tial: WorkOrderCallbackCredential,",
    "creden" "tial=WorkOrderCallbackCredential(headers={}),",
    'creden' 'tial = WorkOrderCallbackCredential(headers={"Authorization": "Bearer token"})',
    "creden" "tial=WorkOrderCallbackCredential(headers=runtime_headers),",
    "creden" "tial=WorkOrderCallbackCredential(headers=[runtime_headers]),",
    "creden" "tial=WorkOrderCallbackCredential(headers=(runtime_headers,)),",
    "creden" "tial=WorkOrderCallbackCredential(enabled=True, count=1),",
    "creden" "tial=WorkOrderCallbackCredential(headers={'Authorization': b'test'}),",
    "creden" "tial=runtime.callback_creden" "tial,",
])
def test_python_reference_and_constructor_are_not_plaintext(line):
    assert scanner._findings(f"+++ b/service.py\n@@ -0,0 +1 @@\n+{line}\n") == []


@pytest.mark.parametrize("template", [
    'callback_creden' 'tial = "{value}"',
    'creden' 'tial=WorkOrderCallbackCredential(headers={{"Authorization": "{value}"}}),',
    'creden' 'tial=WorkOrderCallbackCredential("{value}"),',
    'creden' 'tial=WorkOrderCallbackCredential(headers=["{value}"]),',
    'creden' 'tial=WorkOrderCallbackCredential(headers=b"{value}"),',
    'creden' 'tial=runtime.callback_creden' 'tial; password="{value}"',
])
def test_literal_secrets_are_not_hidden_by_python_expression_handling(template):
    value = "AbCdEf" + "0123456789XYZ-secret-value"
    line = template.format(value=value)
    findings = scanner._findings(f"+++ b/service.py\n@@ -0,0 +1 @@\n+{line}\n")
    assert findings
    assert all(value not in finding.content for finding in findings)


@pytest.mark.parametrize("path,line", [
    ("config.env", "callback_creden" "tial=callback_creden" "tial"),
    ("config.yaml", "callback_creden" "tial: WorkOrderCallbackCredential"),
    ("service.py", "creden" "tial=WorkOrderCallbackCredential("),
    ("service.py", "creden" "tial=WorkOrderCallbackCredential(headers=unknown[0]),"),
])
def test_non_python_values_and_unrecognized_fragments_remain_conservative(path, line):
    assert scanner._findings(f"+++ b/{path}\n@@ -0,0 +1 @@\n+{line}\n")


def test_known_token_inside_constructor_still_blocks_and_is_redacted():
    value = "ghp_" + "aB3dE5fG7hI9jK1lM3nO5pQ7"
    line = 'creden' 'tial=WorkOrderCallbackCredential(headers={"Authorization": "' + value + '"})'
    findings = scanner._findings(f"+++ b/service.py\n@@ -0,0 +1 @@\n+{line}\n")
    assert len(findings) == 1
    assert findings[0].rule == "GitHub token"
    assert value not in findings[0].content


def test_cli_passes_clean_diff(monkeypatch, capsys):
    monkeypatch.setattr(scanner, "_git_diff", lambda base, head: "")
    assert scanner.main(["--base", "base", "--head", "head"]) == 0
    assert "scan passed" in capsys.readouterr().out


def test_cli_blocks_literal_and_masks_output(monkeypatch, capsys):
    value = "AbCdEf" + "0123456789XYZ-secret-value"
    diff = '+++ b/config.env\n@@ -0,0 +1 @@\n+SERVICE_API_KEY=' + value + '\n'
    monkeypatch.setattr(scanner, "_git_diff", lambda base, head: diff)
    assert scanner.main(["--base", "base", "--head", "head"]) == 1
    output = capsys.readouterr().err
    assert "config.env:1" in output
    assert value not in output


def test_cli_fails_closed_when_git_fails(monkeypatch, capsys):
    def fail(base, head):
        raise RuntimeError("git unavailable")
    monkeypatch.setattr(scanner, "_git_diff", fail)
    assert scanner.main(["--base", "base", "--head", "head"]) == 2
    assert "could not inspect Git range" in capsys.readouterr().err


@pytest.mark.parametrize("returncode,stderr", [(0, ""), (1, "invalid revision"), (1, "")])
def test_git_diff_uses_argument_list_and_propagates_failure(monkeypatch, returncode, stderr):
    from types import SimpleNamespace
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=returncode, stdout="diff", stderr=stderr)

    monkeypatch.setattr(scanner.subprocess, "run", run)
    if returncode:
        with pytest.raises(RuntimeError, match=stderr or "git diff failed"):
            scanner._git_diff("base", "head")
    else:
        assert scanner._git_diff("base", "head") == "diff"
    assert calls[0][0][-3:] == ["base", "head", "--"]
    assert not calls[0][1].get("shell", False)


@pytest.mark.parametrize("line", [
    'creden' 'tial=WorkOrderCallbackCredential(headers=f"{dynamic}"),',
    'creden' 'tial=WorkOrderCallbackCredential(headers="unfinished',
])
def test_partial_or_dynamic_literals_do_not_crash_diagnostics(line):
    assert scanner._redact_content(line)


@pytest.mark.parametrize("constructor", ["keyring.PasswordCredential", "str"])
def test_multiline_constructor_literals_remain_blocked_and_redacted(constructor):
    value = "AbCdEf" + "0123456789XYZ-secret-value"
    source = ["creden" "tial = " + constructor + "(", '    "' + value + '",', ")"]
    diff = "+++ b/service.py\n@@ -0,0 +1,3 @@\n" + "".join("+" + line + "\n" for line in source)
    findings = scanner._findings(diff)
    assert findings
    assert all(value not in finding.content for finding in findings)


@pytest.mark.parametrize("template", [
    'creden' 'tial = WorkOrderCallbackCredential("{value}")',
    'creden' 'tial = WorkOrderCallbackCredential(headers={{"Authorization": "{value}"}})',
    'creden' 'tial = WorkOrderCallbackCredential(headers=["{value}"])',
    'creden' 'tial = WorkOrderCallbackCredential(headers=b"{value}")',
    'creden' 'tial = WorkOrderCallbackCredential(headers=settings.get("callback_token", "{value}"))',
])
@pytest.mark.parametrize("suffix", ["_password", "_token"])
def test_field_name_suffix_does_not_exempt_literal_argument_or_value(template, suffix):
    value = "AbCdEf" + "0123456789XYZ" + suffix
    source = template.format(value=value)
    findings = scanner._findings("+++ b/service.py\n@@ -0,0 +1 @@\n+" + source + "\n")
    assert findings
    assert all(value not in finding.content for finding in findings)


@pytest.mark.parametrize("source", [
    ["runtime_to" "ken = self._extract_engine_runtime_token(", "    result=result,", ")"],
    ["creden" "tial = keyring.PasswordCredential(", "    runtime_headers,", ")"],
    ["creden" "tial = keyring.PasswordCredential(", '    headers={"Authorization": runtime_headers},', ")"],
])
def test_complete_added_multiline_references_are_not_plaintext(source):
    diff = "+++ b/service.py\n@@ -0,0 +1,3 @@\n" + "".join("+" + line + "\n" for line in source)
    assert scanner._findings(diff) == []


@pytest.mark.parametrize("source", [
    ["runtime_to" "ken = self._extract_engine_runtime_token("],
    ["creden" "tial = keyring.PasswordCredential("],
    ["creden" "tial = keyring.PasswordCredential(", "    runtime_headers,"],
])
def test_unresolved_multiline_fragments_fail_closed(source):
    diff = "+++ b/service.py\n@@ -0,0 +1,3 @@\n" + "".join("+" + line + "\n" for line in source)
    assert scanner._findings(diff)


@pytest.mark.parametrize("source", [
    ['provider_id, to' 'ken = value.get("provider_id"), value.get("provider_admin_token")'],
    ['return {"provider_admin_to' 'ken": token}', '', 'raise ValueError("SomeUnrelatedHighEntropyMessage")'],
    ['assert config.provider_admin_to' 'ken == ""', '', 'raise ValueError("SomeUnrelatedHighEntropyMessage")'],
])
def test_python_reference_fragments_do_not_consume_unrelated_statements(source):
    diff = "+++ b/service.py\n@@ -0,0 +1,3 @@\n" + "".join("+" + line + "\n" for line in source)
    assert scanner._findings(diff) == []


@pytest.mark.parametrize("template", [
    'outer( creden' 'tial=str("{value}"))',
    'outer(inner( creden' 'tial=str("{value}")), flag=True)',
    'outer( creden' 'tial=str("{value}"), flag=True)',
])
def test_nested_keyword_arguments_do_not_hide_literals_after_short_callees(template):
    value = "AbCdEf" + "0123456789XYZ_password"
    source = template.format(value=value)
    findings = scanner._findings("+++ b/service.py\n@@ -0,0 +1 @@\n+" + source + "\n")
    assert findings
    assert all(value not in finding.content for finding in findings)
