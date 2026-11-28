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


def test_agentswarm_public_protocol_label_is_not_a_credential() -> None:
    field = "_ZERO_CHECK_OPEN_API_" + "TOKEN"
    label = "OPEN_API:app:agentswarm"
    for path in ["example.py", "example.yaml", "example.rs"]:
        assert _findings(_diff(f'{field} = "{label}"', path=path)) == []


def test_protocol_label_exception_is_exact_and_does_not_hide_credentials() -> None:
    label = "OPEN_API:app:agentswarm"
    for value in [label + ":secret-123456789", "OPEN_API:app:other-application",
                  label.lower(), "prefix:" + label]:
        assert _findings(_diff('auth_' + 'token = "' + value + '"'))
    secret = "ghp_" + "a" * 30
    findings = _findings(_diff('auth_' + 'token = "' + label + '"; other = "' + secret + '"'))
    assert findings and findings[0].rule == "GitHub token"
    assert secret not in findings[0].content


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


def test_rust_array_elements_are_not_exempted_as_index_keys() -> None:
    secret = "a-real-looking-" + "secret-value-12345"
    for expression in [
        'String::from(["VALUE"][0])',
        'String::from([["VALUE"]][0][0])',
        'String::from(vec!["VALUE"][0])',
        'String::from({ return ["VALUE"]; })',
    ]:
        line = 'let token = ' + expression.replace('VALUE', secret) + ';'
        findings = _findings(_diff(line, path="example.rs"))
        assert findings, expression
        assert all(secret not in finding.content for finding in findings)


def test_rust_credential_named_declarations_are_types_not_secrets() -> None:
    declarations = [
        "    session_SLOT: Option<&str>,",
        "    async fn named_provider(&self,name:&str,SLOT:&str)->ServiceResult<ProviderBasicInfo> {",
        "pub struct Registration {pub card:Card,pub bot_runtime_SLOT:Option<String>}",
    ]
    for line in declarations:
        assert _findings(_diff(line.replace("SLOT", "token"), path="example.rs")) == []
    literal = 'pub struct Record { SLOT: "a-real-looking-' + 'secret-value-12345" }'
    assert _findings(_diff(literal.replace("SLOT", "token"), path="example.rs"))


def test_python_runtime_credential_lookup_does_not_treat_field_label_as_value() -> None:
    line = 'SLOT = registration["bot_runtime_SLOT"]'.replace("SLOT", "token")
    assert _findings(_diff(line)) == []
    secret = "ghp_" + "a" * 30
    # Known credentials stay forbidden even when used as lookup keys.
    assert _findings(_diff('SLOT = registration["' + secret + '"]'))
    literal = 'SLOT = "a-real-looking-' + 'secret-value-12345"'
    assert _findings(_diff(literal.replace("SLOT", "token")))


def test_rust_neighbor_type_does_not_exempt_a_credential_value() -> None:
    source = 'pub struct Record {card:Card, SLOT: ' + 'a-real-looking-secret-value-12345 }'
    assert _findings(_diff(source.replace("SLOT", "token"), path="example.rs"))


def test_rust_initializer_field_is_not_a_type_declaration() -> None:
    source = 'let config = Config { SLOT: ' + 'AbCdEf0123456789XYZ };'
    assert _findings(_diff(source.replace("SLOT", "token"), path="example.rs"))


def test_rust_prior_struct_does_not_exempt_a_later_initializer() -> None:
    source = 'pub struct Empty {} fn f() { let config = Config { SLOT: ' + 'AbCdEf0123456789XYZ }; }'
    assert _findings(_diff(source.replace("SLOT", "token"), path="example.rs"))


def test_python_getattr_credential_field_is_a_reference_but_default_is_not() -> None:
    line = 'proxy_SLOT = getattr(info, "a2a_proxy_SLOT", "")'.replace("SLOT", "token")
    assert _findings(_diff(line)) == []
    secret = "AbCdEf" + "0123456789XYZ-secret-value"
    for expression in [
        'getattr(info, "a2a_proxy_SLOT", "VALUE")',
        'getattr(info, "VALUE", "")',
    ]:
        line = 'proxy_SLOT = ' + expression.replace("VALUE", secret)
        findings = _findings(_diff(line.replace("SLOT", "token")))
        assert findings
        assert all(secret not in finding.content for finding in findings)


def test_python_header_mapping_preserves_runtime_and_literal_distinction() -> None:
    line = 'headers={"X-PROXYPASS-SLOT": proxy_SLOT} if proxy_SLOT else {},'.replace("SLOT", "token")
    assert _findings(_diff(line)) == []
    secret = "AbCdEf" + "0123456789XYZ-secret-value"
    for expression in ['"VALUE"', 'wrap("VALUE")']:
        line = 'headers={"X-PROXYPASS-SLOT": ' + expression + '} if proxy_SLOT else {},'
        findings = _findings(_diff(line.replace("SLOT", "token").replace("VALUE", secret)))
        assert findings
        assert all(secret not in finding.content for finding in findings)


def test_python_call_can_end_in_unchanged_diff_context() -> None:
    diff = "\n".join([
        "+++ b/example.py", "@@ -1,3 +1,4 @@",
        "-    result = resolve_proxy_face(info,",
        "+    base, withheld, proxy_SLOT = resolve_proxy_face(",
        "+        info, engine_type=engine_type,",
        "         connection_type=connection_type,", "     )",
    ]).replace("SLOT", "token")
    assert _findings(diff) == []
    # A literal on a retained argument line must not gain an exemption either.
    secret = "AbCdEf" + "0123456789XYZ-secret-value"
    assert _findings(diff.replace("connection_type=connection_type", 'connection_type="' + secret + '"'))
    # Context never becomes a new finding by itself.
    assert _findings(_diff('result = runtime_value') + '\n password="' + secret + '"') == []


def _python_diff(source: str) -> str:
    lines = source.replace("SLOT", "token").splitlines()
    return "\n".join([
        "+++ b/example.py",
        f"@@ -0,0 +1,{len(lines)} @@",
        *("+" + line for line in lines),
    ])


def test_python_enclosing_delimiters_do_not_consume_later_statements() -> None:
    expressions = [
        'result = request(client, SLOT=False)',
        'params = {"fencing_SLOT": fence}',
        'result = request(json={"fencing_SLOT": fence})',
        'binder.bind(Issuer, to=Issuer(secret=b"s"))',
        'async def files(self, fencing_SLOT: str) -> dict[str, bytes]:',
        'def __init__(self, SLOT: str):',
        'request(SLOT=runtime_value, note="unrelated-long-diagnostic-message")',
    ]
    for expression in expressions:
        # This is a later statement, not a literal inside the credential RHS.
        source = expression + '\n    message = "unrelated-long-diagnostic-message"'
        assert _findings(_python_diff(source)) == [], expression


def test_python_short_constants_and_runtime_conditional_are_not_credentials() -> None:
    for expression in ['False', 'None', 'b"s"', '"test-token"',
                       'issuer.issue(worker_id) if valid else "invalid"']:
        assert _findings(_python_diff('SLOT = ' + expression)) == [], expression


def test_python_multiline_runtime_call_ignores_mapping_keys_not_values() -> None:
    source = '''SLOT = signer.encode(
    claims | overrides, key,
    algorithm="HS256", headers=headers,
)
params = {"fencing_SLOT": fence}
'''
    assert _findings(_python_diff(source)) == []


def test_python_nested_literals_still_block_after_bounded_rhs_parsing() -> None:
    credential = "a-real-looking-" + "secret-value-12345"
    expressions = [
        'request(SLOT="VALUE")',
        'request(SLOT=wrap("VALUE"))',
        'request(SLOT=wrap(b"VALUE"))',
        'params = {"SLOT": wrap("VALUE")}',
        'SLOT = wrap(\n    "VALUE",\n)',
        'SLOT = wrap(\n    # a continuation comment\n    "VALUE",\n)',
        'SLOT = wrap(\n    {"field_name": "VALUE"},\n)',
        'SLOT = str("VALUE")',
        'SLOT = wrap(claims | {"password": "VALUE"})',
        'SLOT = os.environ.get("AUTH_SLOT", "VALUE")',
        'SLOT = wrap("VALUE") if valid else runtime_value',
        'SLOT = runtime_value if valid else wrap("VALUE")',
        'def fn(SLOT: str = "VALUE"):',
        'SLOT = unknown_syntax@("VALUE")',
        'SLOT = runtime_value, "VALUE"',
        'other, SLOT = runtime_value, "VALUE"',
    ]
    for expression in expressions:
        findings = _findings(_python_diff(expression.replace("VALUE", credential)))
        assert findings, expression
        assert all(credential not in finding.content for finding in findings)


def test_python_same_line_sibling_credential_is_still_detected() -> None:
    credential = "a-real-looking-" + "secret-value-12345"
    for source in [
        'request(SLOT=False, password="VALUE")',
        'data = {"fencing_SLOT": fence, "password": "VALUE"}',
        'SLOT = runtime_value; password = "VALUE"',
    ]:
        findings = _findings(_python_diff(source.replace("VALUE", credential)))
        assert findings, source
        assert all(credential not in finding.content for finding in findings)


def test_python_known_provider_tokens_and_private_keys_are_always_blocked() -> None:
    values = ["ghp_" + "a" * 30, "Bearer " + "b" * 30,
              "-----BEGIN " + "PRIVATE KEY-----"]
    for value in values:
        for source in ['message = "VALUE"', 'request(SLOT=False, note="VALUE")',
                       'SLOT = wrap("VALUE")']:
            findings = _findings(_python_diff(source.replace("VALUE", value)))
            assert findings, source
            assert all(value not in finding.content for finding in findings)


def test_python_long_numeric_credentials_are_not_short_constants() -> None:
    credential = "1234567890" * 3
    assert _findings(_python_diff("SLOT = " + credential))
