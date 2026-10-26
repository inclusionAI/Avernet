from devops.hook.check_secrets import _findings


def _diff(line: str, *, path: str = "example.py") -> str:
    return "\n".join([
        f"+++ b/{path}",
        "@@ -0,0 +1 @@",
        f"+{line}",
    ])


def test_runtime_relay_token_references_are_not_plaintext_credentials() -> None:
    relay_field = "relay_" + "token"
    assert _findings(_diff(f'{relay_field}=graph.extend_props["{relay_field}"],')) == []
    assert _findings(_diff('current_token = str(props.get("relay_token") or "")')) == []
    assert _findings(_diff('f"relay_token={relay_token} (execution generation)"')) == []


def test_relay_test_generation_is_an_explicit_placeholder() -> None:
    assert _findings(_diff('"relay_token": "generation-1",')) == []


def test_real_high_entropy_token_assignment_is_still_rejected() -> None:
    credential_line = "auth_" + "token" + ' = "a-real-looking-secret-value-12345"'
    findings = _findings(_diff(credential_line))
    assert len(findings) == 1
    assert findings[0].rule == "credential-like assignment"


def test_rust_types_runtime_values_and_redaction_are_not_credentials() -> None:
    # Build source fixtures so the scanner does not mistake test source text
    # for actual assignments while scanning its own diff.
    lines = [
        'pub SLOT: ExecutionToken,',
        'pub execution_SLOT: Option<super::caller_execution::ExecutionToken>,',
        'let SLOT = ext["caller_execution_SLOT"]["SLOT"].as_str().unwrap();',
        'let SLOT = msg.execution_SLOT.as_ref().filter(|t| !t.expose().trim().is_empty()).ok_or_else(invalid)?;',
        'SLOT: SLOT.clone(), SLOT_type: SLOT.SLOT_type().to_string(),',
        'SLOT: c.SLOT.expose().to_string() };',
        '*SLOT = serde_json::json!("<redacted>");',
        'message.execution_SLOT = value.map(|v| ExecutionToken::new(v.into(), "iam-SLOT".into()));',
        'SLOT: ExecutionToken::new("test-SLOT".into(), "iam-SLOT".into()),',
        'extensions.insert("caller_execution_SLOT".into(), serde_json::json!({"type": record.SLOT_type, "SLOT": record.SLOT}));',
    ]
    for line in lines:
        assert _findings(_diff(line.replace("SLOT", "token"), path="src/example.rs")) == [], line


def test_rust_nested_credentials_are_detected_and_redacted() -> None:
    secret = "a-real-looking-" + "secret-value-12345"
    expressions = [
        '"VALUE"',
        'ExecutionToken::new("VALUE".into())',
        'Some(Token::new(r##"VALUE"##))',
        'serde_json::json!({"token": "VALUE"})',
        'Token::new(b"VALUE")',
        'Token::new("\\x61-real-looking-secret-value-12345")',
    ]
    for expression in expressions:
        line = 'let token = ' + expression.replace('VALUE', secret) + ';'
        findings = _findings(_diff(line, path="tests/example.rs"))
        assert findings, line
        assert all(secret not in finding.content for finding in findings)


def test_rust_unknown_syntax_and_bare_values_remain_conservative() -> None:
    for expression in ['abcdefghijklmnop1234567890', 'unknown_expression@unsupported']:
        assert _findings(_diff('token = ' + expression, path="example.rs"))


def test_known_credentials_are_not_exempted_by_rust_expression_detection() -> None:
    for secret in ['ghp_' + 'a' * 30, 'Bearer ' + 'b' * 30,
                   'eyJ' + 'a' * 12 + '.' + 'b' * 12 + '.' + 'c' * 12]:
        line = 'let token = Token::new("' + secret + '");'
        findings = _findings(_diff(line, path="example.rs"))
        assert findings
        assert all(secret not in finding.content for finding in findings)
