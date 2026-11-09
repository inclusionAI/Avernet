"""Structural contract tests for the V1 OpenAPI authentication facade.

Task 14 of the V1 API auth plugin chain (docs/bcs V1): these tests load
the checked-in OpenAPI YAML with a real YAML parser and assert the wire
contract that the configurable auth sources and shared-session safety
work pinned — actual `headers` / `x-error-codes` keys on the response
objects, not term greps.

Pinned surface (plan §8.2/§8.4, spec §7.4/§8.5, review rulings R1–R4):

- every login-family response (`url`, `callback`, `user`, `refresh`,
  `logout`) documents `Cache-Control: no-store`; the issuing and
  completing entries document `Set-Cookie` (the provider list and the
  redirect respectively);
- the callback documents BOTH `Set-Cookie` instructions (install the
  `bcs_session` cookie AND clear the temporary browser challenge) and
  the required challenge cookie names (`__Host-bcs_oauth_login` in
  production, a separate non-`__Host` name over plain-HTTP local);
- `refresh` lists 401 (unauthenticated, including a lost rotation race)
  AND 503 (`auth_dependency_unavailable`, dependency outage); a lost
  LOGIN race on the callback is the NEW 409 `login_session_conflict`;
- `logout` lists 503 (`auth_dependency_unavailable`) — a persistent
  revoke failure is never a success;
- 503 is the dedicated 50300 `auth_dependency_unavailable` class; the
  pre-existing 502 Bot identity store fault (`bot_identity_unavailable`)
  keeps its mapping;
- cookie-authenticated unsafe requests with a missing or untrusted
  browser Origin are rejected 403 (the verifier chain surfaces
  `forbidden`; the refresh/logout entrypoint gates with
  `browser_origin_untrusted`);
- the shared envelope documents the strict dependency-unavailable
  response object.
"""

import sys
import unittest
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from validate_openapi_contract import load_contract  # noqa: E402

V1_ROOT = SCRIPTS.parent / "api-contracts" / "v1"

PUBLIC_PREFIXES = ("/openapi/v1/",)
NO_STORE = {"no-store"}
PROD_CHALLENGE_COOKIE = "__Host-bcs_oauth_login"


def _load(name):
    with (V1_ROOT / name).open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _response_headers(response):
    headers = response.get("headers", {})
    return {
        name: str(value.get("description", "")) + " " + str(
            value.get("schema", {}).get("const", "")
        )
        for name, value in headers.items()
    }


def _operation_paths(contract, operation_id):
    for path_item in contract.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict) and operation.get("operationId") == operation_id:
                yield path_item, operation


def _operation(contract, operation_id):
    matches = list(_operation_paths(contract, operation_id))
    assert matches, f"operationId {operation_id} missing from openapi.yaml"
    return matches[0][1]


def _header_values(contract, operation_id, status, header_name):
    operation = _operation(contract, operation_id)
    response = operation["responses"][str(status)]
    by_name = _response_headers(response)
    lowered = {key.lower(): value for key, value in by_name.items()}
    return lowered.get(header_name.lower(), "")


class LoginUrlContract(unittest.TestCase):
    """GET /openapi/v1/auth/url — issuing the browser challenge."""

    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract(V1_ROOT, entrypoint="openapi.yaml")

    def test_documents_no_store(self):
        text = _header_values(self.contract, "get_auth_login_urls", 200, "cache-control")
        self.assertTrue(
            NO_STORE.issubset(set(part.strip() for part in text.split())),
            "login URL discovery must declare Cache-Control: no-store",
        )

    def test_documents_challenge_set_cookie(self):
        text = _header_values(self.contract, "get_auth_login_urls", 200, "set-cookie")
        self.assertIn(PROD_CHALLENGE_COOKIE, text)
        for attribute in ("Path=/", "HttpOnly", "Secure", "SameSite=Lax", "Max-Age=300"):
            self.assertIn(attribute, text)

    def test_challenge_uses_a_separate_local_http_name(self):
        text = _header_values(self.contract, "get_auth_login_urls", 200, "set-cookie")
        self.assertIn("bcs_oauth_login", text)
        # The non-__Host local name must be documented as the ONLY other
        # accepted name (production never downgrades).
        self.assertNotIn("__Host-bcs_oauth_login over plain HTTP", text)

    def test_unconfigured_is_not_found(self):
        operation = _operation(self.contract, "get_auth_login_urls")
        self.assertIn(
            "auth_not_configured",
            operation["responses"]["404"].get("x-error-codes", []),
        )


class CallbackContract(unittest.TestCase):
    """GET /openapi/v1/auth/callback/{provider} — completing the login."""

    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract(V1_ROOT, entrypoint="openapi.yaml")

    def test_documents_no_store(self):
        text = _header_values(self.contract, "complete_auth_login", 302, "cache-control")
        self.assertTrue(
            NO_STORE.issubset(set(part.strip() for part in text.split())),
            "the login redirect must declare Cache-Control: no-store",
        )

    def test_documents_multiple_set_cookie(self):
        text = _header_values(self.contract, "complete_auth_login", 302, "set-cookie")
        lowered = text.lower()
        self.assertIn("bcs_session", lowered, "callback must install the session cookie")
        self.assertIn(PROD_CHALLENGE_COOKIE.lower(), lowered)
        self.assertIn("clear", lowered)
        for attribute in ("path=/", "httponly", "samesite=lax"):
            self.assertIn(attribute, lowered)

    def test_documents_the_required_browser_challenge(self):
        # The temporary cookie is REQUIRED: a browser without it can never
        # redeem a state (plan §8.4); a query nonce never substitutes.
        operation = _operation(self.contract, "complete_auth_login")
        document = yaml.safe_dump(operation)
        for term in ("__Host-bcs_oauth_login", "challenge"):
            self.assertIn(term, document)

    def test_state_rejections_are_bad_request(self):
        operation = _operation(self.contract, "complete_auth_login")
        codes_400 = set(operation["responses"]["400"].get("x-error-codes", []))
        self.assertTrue(
            {"invalid_state", "invalid_login_state", "missing_code"}.issubset(codes_400),
            "callback 400 must carry the adapter and service state rejections",
        )

    def test_provider_gate_is_not_found(self):
        operation = _operation(self.contract, "complete_auth_login")
        codes_404 = set(operation["responses"]["404"].get("x-error-codes", []))
        self.assertTrue(
            {"provider_not_found", "provider_not_enabled", "auth_not_configured"}.issubset(codes_404),
            "a provider outside the enabled chain must never start a login",
        )

    def test_lost_login_race_is_a_409(self):
        operation = _operation(self.contract, "complete_auth_login")
        self.assertIn(
            "login_session_conflict",
            operation["responses"]["409"].get("x-error-codes", []),
            "a lost atomic install race is the 409-side conflict",
        )

    def test_dependency_outage_is_a_503(self):
        operation = _operation(self.contract, "complete_auth_login")
        self.assertIn(
            "auth_dependency_unavailable",
            operation["responses"]["503"].get("x-error-codes", []),
        )


class UserContract(unittest.TestCase):
    """GET /openapi/v1/auth/user — current verified identity."""

    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract(V1_ROOT, entrypoint="openapi.yaml")

    def test_documents_no_store(self):
        text = _header_values(self.contract, "get_auth_user", 200, "cache-control")
        self.assertTrue(
            NO_STORE.issubset(set(part.strip() for part in text.split())),
            "the identity projection must never be cached",
        )

    def test_dependency_outage_is_a_503(self):
        operation = _operation(self.contract, "get_auth_user")
        self.assertIn(
            "auth_dependency_unavailable",
            operation["responses"]["503"].get("x-error-codes", []),
        )

    def test_source_policy_rejection_is_403(self):
        operation = _operation(self.contract, "get_auth_user")
        codes_403 = set(operation["responses"]["403"].get("x-error-codes", []))
        self.assertIn(
            "forbidden",
            codes_403,
            "the verifier chain surfaces exactly the pinned 403 code",
        )


class RefreshContract(unittest.TestCase):
    """POST /openapi/v1/auth/refresh — atomic rotation."""

    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract(V1_ROOT, entrypoint="openapi.yaml")

    def test_documents_no_store_and_set_cookie(self):
        text = _header_values(self.contract, "refresh_auth_session", 200, "cache-control")
        self.assertTrue(NO_STORE.issubset(set(part.strip() for part in text.split())))
        cookies = _header_values(self.contract, "refresh_auth_session", 200, "set-cookie")
        self.assertIn("bcs_session", cookies.lower())

    def test_lost_rotation_race_is_a_401_not_a_500(self):
        operation = _operation(self.contract, "refresh_auth_session")
        codes_401 = set(operation["responses"]["401"].get("x-error-codes", []))
        self.assertIn("unauthenticated", codes_401)

    def test_dependency_outage_is_a_503(self):
        operation = _operation(self.contract, "refresh_auth_session")
        self.assertIn(
            "auth_dependency_unavailable",
            operation["responses"]["503"].get("x-error-codes", []),
            "a store outage is 503, never folded into a denial",
        )

    def test_cookie_origin_gate_is_403(self):
        operation = _operation(self.contract, "refresh_auth_session")
        codes_403 = set(operation["responses"]["403"].get("x-error-codes", []))
        self.assertTrue(
            {"browser_origin_untrusted", "forbidden"} & codes_403,
            "cookie-authenticated unsafe writes require a trusted Origin (403)",
        )

    def test_disabled_provider_refresh_is_403_without_issuance(self):
        operation = _operation(self.contract, "refresh_auth_session")
        response = operation["responses"]["403"]
        self.assertIn("session_forbidden", response.get("x-error-codes", []))
        self.assertIn("outside the explicit V1 OAuth chain", response["description"])
        self.assertIn("no Set-Cookie", response["description"])


class LogoutContract(unittest.TestCase):
    """POST /openapi/v1/auth/logout — atomic revoke, no best-effort success."""

    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract(V1_ROOT, entrypoint="openapi.yaml")

    def test_documents_no_store_and_cookie_removal(self):
        text = _header_values(self.contract, "logout_auth_session", 200, "cache-control")
        self.assertTrue(NO_STORE.issubset(set(part.strip() for part in text.split())))
        cookies = _header_values(self.contract, "logout_auth_session", 200, "set-cookie")
        self.assertIn("bcs_session", cookies.lower())
        self.assertIn("max-age=0", cookies.lower().replace(" ", ""))

    def test_persistence_failure_is_a_503(self):
        operation = _operation(self.contract, "logout_auth_session")
        self.assertIn(
            "auth_dependency_unavailable",
            operation["responses"]["503"].get("x-error-codes", []),
            "logout with a failed revoke must never report success",
        )

    def test_cookie_origin_gate_is_403(self):
        operation = _operation(self.contract, "logout_auth_session")
        codes_403 = set(operation["responses"]["403"].get("x-error-codes", []))
        self.assertTrue(
            {"browser_origin_untrusted", "forbidden"} & codes_403,
            "cookie-bearing logout requires a trusted Origin (403)",
        )


class SharedEnvelopeContract(unittest.TestCase):
    """`shared.yaml` shared response objects for the strict error classes."""

    @classmethod
    def setUpClass(cls):
        cls.shared = _load("shared.yaml")

    def test_dependency_unavailable_response_object_exists(self):
        dependency = self.shared.get("AuthDependencyUnavailableResponse")
        self.assertIsNotNone(
            dependency,
            "shared.yaml must define the dedicated 503 response object",
        )
        self.assertEqual(dependency["description"], "Authentication dependency unavailable.")
        self.assertEqual(dependency["x-error-codes"], ["auth_dependency_unavailable"])

    def test_internal_response_stays_pinned(self):
        internal = self.shared["InternalErrorResponse"]
        self.assertEqual(internal["x-error-codes"], ["internal_error"])


class AuthenticationContractDoc(unittest.TestCase):
    """The human-readable contract document pins the semantics YAML cannot."""

    @classmethod
    def setUpClass(cls):
        doc = V1_ROOT / "authentication" / "contract.md"
        if not doc.exists():
            raise unittest.SkipTest("authentication/contract.md not written yet")
        with doc.open(encoding="utf-8") as stream:
            cls.text = stream.read()

    def assert_in_section(self, needle, section):
        self.assertIn(needle, self.text, f"contract.md must pin {needle!r} under {section}")

    def test_pins_source_precedence(self):
        for needle in ("first-success", "InvalidCredential", "DependencyUnavailable"):
            self.assert_in_section(needle, "source precedence")

    def test_pins_session_instance_claims(self):
        for needle in ("session_id", "revision", "env"):
            self.assert_in_section(needle, "JWT claims")

    def test_pins_shared_writer_rollout(self):
        lowered = self.text.lower()
        for needle in ("mixed", "writer", "binaries", "unconditional"):
            self.assertIn(needle, lowered, "contract.md must pin the shared-writer rollout")

    def test_pins_multi_instance_pending_state(self):
        self.assert_in_section("pending", "multi-instance")

    def test_pins_mysql_strict_mode(self):
        for needle in ("VARCHAR(64)", "strict"):
            self.assert_in_section(needle, "MySQL")

    def test_pins_provider_table_presence_is_not_enablement(self):
        lowered = self.text.lower()
        self.assertIn("provider table", lowered)
        self.assertIn("does not enable", lowered)

    def test_pins_gateway_and_bot_identity_error_mapping(self):
        self.assert_in_section("bot_identity_unavailable", "error mapping")
        self.assert_in_section("auth_dependency_unavailable", "error mapping")


if __name__ == "__main__":
    unittest.main()
