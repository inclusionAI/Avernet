from devops.hook.check_secrets import _findings


def _diff(line: str, *, path: str = "example.py") -> str:
    return "\n".join([
        f"+++ b/{path}",
        "@@ -0,0 +1 @@",
        f"+{line}",
    ])


def test_runtime_relay_token_references_are_not_plaintext_credentials() -> None:
    assert _findings(_diff('relay_token=graph.extend_props["relay_token"],')) == []
    assert _findings(_diff('current_token = str(props.get("relay_token") or "")')) == []
    assert _findings(_diff('f"relay_token={relay_token} (execution generation)"')) == []


def test_relay_test_generation_is_an_explicit_placeholder() -> None:
    assert _findings(_diff('"relay_token": "generation-1",')) == []


def test_real_high_entropy_token_assignment_is_still_rejected() -> None:
    findings = _findings(_diff('auth_token = "a-real-looking-secret-value-12345"'))
    assert len(findings) == 1
    assert findings[0].rule == "credential-like assignment"
