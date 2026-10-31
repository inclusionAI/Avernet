import pytest

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


FALSE_POSITIVE_LINES = [
    'credential = os.environ.get(profile["auth"]["driver_token_env"], "")',
    'token = _safe_token(value, key + " token")',
    'write(context, {"token": driver_token, "bot_uuid": driver_id})',
    '("POST", "/register?token={register_token}&bot-name={bot_name}"),',
    'query = urllib.parse.urlencode({"token": register_token, "bot-name": name})',
    'context = credential = request_context = None',
    'token = settings.value(code=20_000, message="OK")',
    'PUBLIC_SHARE_TOKEN = "public-share-token-v1"',
    'ADMIN_TOKEN = "synthetic-provider-admin-token"',
    "OBLIGATION_READ_TOKEN = 'DRAFT-DELIVERY.read-token-single-revision'",
    'DRIVER_TOKEN = "owned-driver-token"',
    "return {'token': 'private-registration-token'}",
    "{'missing_secret': 'CONFIRMED_404'}",
    'STATION_REGISTER_BOT_PATH = "/register?token={register_token}&bot-name={bot_name}"',
    '                token=settings.callable_token("CEO"), bot_id=self._id("CEO"))',
    '            token = settings.token(',
    '"BCS_E2E_DRIVER_BOT_TOKEN": DRIVER_BOT_PROFILE}),',
    '    self.state["platform"] = {"panel": panel, "missing_secret": "CONFIRMED_404"}',
    '    token = token_data["token"] if isinstance(token_data, dict) else None',
    '    json.dumps({"secret": constants.secret()}),',
    '    raw = json.dumps({"token": CONNECT_TOKEN}).encode(), b"")',
]


@pytest.mark.parametrize("line", FALSE_POSITIVE_LINES)
def test_indirection_templates_and_identifier_shapes_are_not_credentials(line: str) -> None:
    assert _findings(_diff(line)) == []

@pytest.mark.parametrize(
    "line",
    [
        'INTEGRATION_TOKEN = "AUTHORITY_DENSITY_42_OBLIGATIONS_20261031"',
        'COMPLETION_WAVE_TOKEN = "SERVICE_POLICY_DENSITY_15_OBLIGATIONS_20261031"',
        'PROPOSAL_TOKEN = "CHANNEL_DENSITY_14_OBLIGATIONS_20261031"',
        'WAVE_ID = "DUAL_SUBBOTS_DENSITY_D4_131_LEAVES"',
    ],
)
def test_date_stamped_uppercase_roster_markers_are_not_credentials(line: str) -> None:
    assert _findings(_diff(line)) == []


@pytest.mark.parametrize(
    "line",
    [
        'PROPOSAL_TOKEN = "aMixedCaseSecretValue_20261031"',
        'PROPOSAL_TOKEN = "f47ac10b58cc4372a910b20261031abcdef"',
        'PROPOSAL_TOKEN = "DEADBEEFCAFE1234_20261031"',
    ],
)
def test_date_suffix_alone_never_exempts_real_secret_shapes(line: str) -> None:
    findings = _findings(_diff(line))
    assert len(findings) == 1
    assert findings[0].rule == "credential-like assignment"


TRUE_POSITIVE_LINES = [
    "auth_" + "token" + ' = "gHt9Km2Qx7Zw3Rd8Yv5"',
    "pass" + "word" + ' = "8f3ka92mx01jd77q"',
    "api_" + "key" + ' = "448d1a6c-094b-44ea-a234-6df2b5439841"',
    "session_" + "secret" + ' = "aB3f9kQm2Xz8Lp4Rw"',
    "db_" + "password" + ' = "g7Kx9Lm2Qw8Rd5Zv"',
    "token" + ' = "private-12345678901234567"',
]


@pytest.mark.parametrize("line", TRUE_POSITIVE_LINES)
def test_high_entropy_blobs_and_digit_led_values_stay_rejected(line: str) -> None:
    findings = _findings(_diff(line))
    assert findings, line
    assert findings[0].rule == "credential-like assignment"


def test_string_keyed_plaintext_secret_values_stay_flagged() -> None:
    # A string-literal LHS names a field, but a plaintext dict-literal value
    # on that field is still a leak: the string-text exemption must not
    # swallow quoted credential values. Pieces keep the scanner from matching
    # this test file itself.
    payload = "return {'" + "token" + "': 'gHt9Km2Qx7Zw3Rd8Yv5'}"
    findings = _findings(_diff(payload))
    assert len(findings) == 1
    assert findings[0].rule == "credential-like assignment"
    # Name references and URL templates behind the same string key stay exempt.
    assert _findings(_diff('payload = {"auth_token": driver_token}')) == []
    assert _findings(_diff('("GET", "/register?token={register_token}"))')) == []
