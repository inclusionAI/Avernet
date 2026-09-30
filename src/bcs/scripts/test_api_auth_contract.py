#!/usr/bin/env python3
"""HTTP contract assertions for the BCS V1 authentication facade.

These tests read the checked-in OpenAPI YAML (the same `openapi.yaml`
entrypoint `validate_openapi_contract.py` loads) and assert that the
auth-facade routes document the security upgrade introduced by the
2026-09-18 v1-api-auth-plugin-chain design:

- `/auth/url` response carries a `Set-Cookie` header (the temporary
  `__Host-bcs_oauth_login` browser-binding challenge cookie) and
  `Cache-Control: no-store`.
- `/auth/callback/{provider}` documents the temporary-cookie
  requirement on entry, returns multiple `Set-Cookie` headers on 302
  (the fresh `bcs_session` plus the cleared challenge cookie), and
  surfaces `invalid_state` on 400.
- `/auth/refresh` covers the CAS-conflict path with a 401 (in addition
  to the missing/malformed/unbound 401) and a 503 for dependency faults.
- `/auth/logout` covers the 503 persistence-fault path; persistence
  failure must not be reported as success.

The YAML is loaded with `yaml.safe_load` and `$ref`s are resolved through
the same helper as `validate_openapi_contract.py`. Every assertion walks
the parsed structure; none of them grep for substrings inside the raw
YAML text.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = REPO_ROOT / "api-contracts" / "v1"
OPENAPI_ENTRY = CONTRACT_ROOT / "openapi.yaml"
AUTH_YAML = CONTRACT_ROOT / "openapi" / "auth.yaml"


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def _resolve_ref(value: Any, *, current_file: Path, cache: dict[Path, Any]) -> Any:
    """Resolve a single value following $ref pointers (no sibling merge)."""
    if isinstance(value, list):
        return [
            _resolve_ref(item, current_file=current_file, cache=cache)
            for item in value
        ]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        reference = value["$ref"]
        file_part, separator, pointer = reference.partition("#")
        target_file = (
            (current_file.parent / file_part).resolve()
            if file_part
            else current_file.resolve()
        )
        target_document = _load_yaml_resolved(target_file, cache)
        target = _json_pointer(target_document, pointer if separator else "")
        return _resolve_ref(target, current_file=target_file, cache=cache)
    return {
        key: _resolve_ref(item, current_file=current_file, cache=cache)
        for key, item in value.items()
    }


def _json_pointer(document: Any, pointer: str) -> Any:
    current = document
    if not pointer:
        return current
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer: #{pointer}")
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise ValueError(f"unresolved JSON pointer: #{pointer}") from error
    return current


def _load_yaml_resolved(path: Path, cache: dict[Path, Any]) -> Any:
    resolved = path.resolve()
    if resolved not in cache:
        with resolved.open(encoding="utf-8") as stream:
            cache[resolved] = yaml.safe_load(stream)
        cache[resolved] = _resolve_ref(
            cache[resolved], current_file=resolved, cache=cache
        )
    return cache[resolved]


def load_openapi() -> dict[str, Any]:
    """Load the v1 OpenAPI entrypoint with $refs resolved into the document."""
    cache: dict[Path, Any] = {}
    document = _load_yaml(OPENAPI_ENTRY)
    return _resolve_ref(document, current_file=OPENAPI_ENTRY, cache=cache)


def _path_item(contract: dict[str, Any], path: str) -> dict[str, Any]:
    paths = contract.get("paths", {})
    if path not in paths:
        raise AssertionError(f"OpenAPI is missing path {path!r}")
    return paths[path]


def _operation(path_item: dict[str, Any], method: str) -> dict[str, Any]:
    if method not in path_item:
        raise AssertionError(
            f"path is missing {method.upper()} operation; have {sorted(path_item)}"
        )
    return path_item[method]


def _response(operation: dict[str, Any], status: str) -> dict[str, Any]:
    responses = operation.get("responses", {})
    if status not in responses:
        raise AssertionError(
            f"operation {operation.get('operationId')} is missing {status} response"
        )
    return responses[status]


def _headers(response: dict[str, Any]) -> dict[str, Any]:
    headers = response.get("headers", {})
    if not isinstance(headers, dict):
        raise AssertionError(f"response.headers must be an object; got {headers!r}")
    return headers


def _header_value_const(headers: dict[str, Any], name: str, expected: str) -> bool:
    header = headers.get(name)
    if not isinstance(header, dict):
        return False
    schema = header.get("schema", {})
    if not isinstance(schema, dict):
        return False
    return schema.get("const") == expected


def _has_header(headers: dict[str, Any], name: str) -> bool:
    return isinstance(headers.get(name), dict)


def _error_codes(response: dict[str, Any]) -> set[str]:
    codes = response.get("x-error-codes", [])
    if codes is None:
        return set()
    if not isinstance(codes, list):
        raise AssertionError(f"x-error-codes must be a list; got {codes!r}")
    return set(codes)


class AuthUrlContractTests(unittest.TestCase):
    PATH = "/openapi/v1/auth/url"

    def setUp(self) -> None:
        self.contract = load_openapi()
        self.op = _operation(_path_item(self.contract, self.PATH), "get")

    def test_200_documents_set_cookie_challenge_header(self) -> None:
        response = _response(self.op, "200")
        headers = _headers(response)
        self.assertTrue(
            _has_header(headers, "Set-Cookie"),
            "GET /auth/url 200 must document the Set-Cookie header for the "
            "browser-binding challenge cookie",
        )

    def test_200_documents_no_store_cache_control(self) -> None:
        response = _response(self.op, "200")
        headers = _headers(response)
        self.assertTrue(
            _header_value_const(headers, "Cache-Control", "no-store"),
            "GET /auth/url 200 must declare Cache-Control: no-store",
        )

    def test_200_describes_browser_challenge_cookie(self) -> None:
        # The description must name the challenge cookie (`__Host-bcs_oauth_login`)
        # so clients know which cookie is set on this response.
        response = _response(self.op, "200")
        headers = _headers(response)
        set_cookie = headers.get("Set-Cookie", {})
        description = set_cookie.get("description", "") or ""
        self.assertIn(
            "__Host-bcs_oauth_login",
            description,
            "Set-Cookie description must name the __Host-bcs_oauth_login challenge cookie",
        )


class AuthCallbackContractTests(unittest.TestCase):
    PATH = "/openapi/v1/auth/callback/{provider}"

    def setUp(self) -> None:
        self.contract = load_openapi()
        self.op = _operation(_path_item(self.contract, self.PATH), "get")

    def test_documents_temporary_cookie_requirement(self) -> None:
        description = self.op.get("description", "") or self.op.get("summary", "")
        self.assertIn(
            "__Host-bcs_oauth_login",
            description,
            "callback operation description must document the temporary "
            "__Host-bcs_oauth_login cookie requirement",
        )

    def test_302_documents_set_cookie(self) -> None:
        response = _response(self.op, "302")
        headers = _headers(response)
        self.assertTrue(
            _has_header(headers, "Set-Cookie"),
            "callback 302 must document Set-Cookie",
        )
        description = headers["Set-Cookie"].get("description", "") or ""
        self.assertTrue(
            "multiple" in description.lower() or "bcs_session" in description.lower(),
            "callback 302 Set-Cookie description must mention multiple Set-Cookie "
            "values (fresh bcs_session + cleared challenge cookie)",
        )

    def test_400_includes_duplicated_query_error(self) -> None:
        self.assertIn("invalid_request", _error_codes(_response(self.op, "400")))

    def test_400_includes_invalid_state(self) -> None:
        response = _response(self.op, "400")
        codes = _error_codes(response)
        self.assertIn(
            "invalid_state",
            codes,
            "callback 400 must include the invalid_state error code",
        )


class AuthUserContractTests(unittest.TestCase):
    PATH = "/openapi/v1/auth/user"

    def setUp(self) -> None:
        self.contract = load_openapi()
        self.op = _operation(_path_item(self.contract, self.PATH), "get")

    def test_terminal_errors_have_codes_and_v1_envelopes(self) -> None:
        for status, code in (
            ("401", "unauthenticated"),
            ("403", "forbidden"),
            ("503", "unavailable"),
        ):
            with self.subTest(status=status):
                response = _response(self.op, status)
                self.assertIn(code, _error_codes(response))
                schema = response["content"]["application/json"]["schema"]
                self.assertEqual(
                    set(schema["required"]), {"code", "message", "data", "request_id"}
                )
                self.assertIn("error_code", schema["properties"]["data"]["properties"])

    def test_verified_non_human_is_a_forbidden_identity(self) -> None:
        response = _response(self.op, "403")
        self.assertIn("human", response["description"].lower())
        self.assertNotIn("does not resolve to a human", _response(self.op, "401")["description"])


class AuthRefreshContractTests(unittest.TestCase):
    PATH = "/openapi/v1/auth/refresh"

    def setUp(self) -> None:
        self.contract = load_openapi()
        self.op = _operation(_path_item(self.contract, self.PATH), "post")

    def test_401_includes_cas_conflict_code(self) -> None:
        response = _response(self.op, "401")
        codes = _error_codes(response)
        # CAS-conflict on refresh must surface as 401 (spec §8.5). The
        # distinct error code separates CAS conflicts from plain missing
        # / malformed / unbound cookies.
        self.assertTrue(
            codes.intersection({"session_conflict", "session_replaced"}),
            f"refresh 401 must include a CAS-conflict error code; got {codes}",
        )

    def test_503_documents_dependency_fault(self) -> None:
        response = _response(self.op, "503")
        codes = _error_codes(response)
        self.assertIn(
            "unavailable",
            codes,
            "refresh 503 must include the unavailable error code for dependency faults",
        )


class AuthLogoutContractTests(unittest.TestCase):
    PATH = "/openapi/v1/auth/logout"

    def setUp(self) -> None:
        self.contract = load_openapi()
        self.op = _operation(_path_item(self.contract, self.PATH), "post")

    def test_401_documents_malformed_cookie_rejection(self) -> None:
        response = _response(self.op, "401")
        self.assertIn("unauthenticated", _error_codes(response))
        schema = response["content"]["application/json"]["schema"]
        self.assertEqual(
            set(schema["required"]), {"code", "message", "data", "request_id"}
        )
        self.assertIn("error_code", schema["properties"]["data"]["properties"])

    def test_503_documents_persistence_fault(self) -> None:
        response = _response(self.op, "503")
        codes = _error_codes(response)
        self.assertIn(
            "unavailable",
            codes,
            "logout 503 must include the unavailable error code for persistence faults",
        )


class AuthPathIntentContractTests(unittest.TestCase):
    """Sanity: the auth.yaml entrypoints still exist in the OpenAPI tree."""

    def test_auth_paths_resolve(self) -> None:
        contract = load_openapi()
        for path in (
            "/openapi/v1/auth/url",
            "/openapi/v1/auth/callback/{provider}",
            "/openapi/v1/auth/user",
            "/openapi/v1/auth/refresh",
            "/openapi/v1/auth/logout",
        ):
            self.assertIn(path, contract.get("paths", {}), f"missing auth path {path}")


if __name__ == "__main__":
    unittest.main()
