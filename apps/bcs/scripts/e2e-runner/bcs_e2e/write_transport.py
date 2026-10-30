"""Allowlisted write transport for the pilot; no arbitrary URL/method escape hatch."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, ProxyHandler, HTTPSHandler, build_opener

from .profile import (ROUTE_BINDING_RESPONSE_BUDGET_MAXIMUM, browser_headers,
                       identifier, origin, unique_keys)
from .transport import tls_context
from .correlation import RequestFailure, new_traceparent, valid_traceparent, record_response_id
from .route_bindings import (
    NETWORK_REGISTER_CREATE_PATH, require_edge_case_binding,
)


class WriteTransportError(RequestFailure):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WriteClient:
    def __init__(self, profile: dict, *, local_auth=None):
        self.profile = profile
        self.base = origin(profile["entrypoint"]["origin"], profile["environment"]["tier"])
        self.local_auth = local_auth
        owned_local = profile["auth"] == {"kind": "owned-local"}
        if owned_local or local_auth is not None:
            from .local_auth import require_local_auth
            require_local_auth(profile, local_auth)
        self.headers = browser_headers(profile)
        for header, ref in profile["auth"].get("header_env", {}).items():
            value = __import__("os").environ.get(ref, "")
            if not value or not value.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise WriteTransportError("missing or invalid authentication reference")
            self.headers[header] = value
        if profile["auth"].get("authorization_scheme") == "bearer" and "Authorization" in self.headers:
            token = self.headers["Authorization"]
            if token and not token.startswith("Bearer "):
                self.headers["Authorization"] = "Bearer " + token
        self.opener = build_opener(ProxyHandler({}), NoRedirect()) if owned_local else build_opener(HTTPSHandler(context=tls_context()), NoRedirect())

    def patch_bot_status(self, target_actor_id: str, status: str) -> tuple[dict, dict]:
        """Patch only the bound dedicated driver Bot between raw lifecycle states."""
        if status not in {"hidden", "online"}:
            raise WriteTransportError("actor-status mutation accepts only hidden or online")
        binding = require_edge_case_binding(self.profile, "network_actor_status.write")
        driver = self.profile["actors"]["driver"]["bot_id"]
        if target_actor_id != driver or binding.get("target_actor_id") != driver:
            raise WriteTransportError("actor-status target differs from the dedicated driver Bot")
        if binding.get("path") != ("/openapi/v1/collaboration/bots/" + quote(driver, safe="")):
            raise WriteTransportError("actor-status route differs from its reviewed target binding")
        request_id = identifier("e2e-step-" + uuid.uuid4().hex, "request_id")
        auth_headers = {header: value for header, value in self.headers.items()
                        if header != "Authorization"}
        traceparent = (new_traceparent() if binding["traceparent"] == "w3c" else None)
        headers = {**auth_headers, "Accept": "application/json",
                   "Content-Type": "application/json", "X-Request-ID": request_id}
        if traceparent is not None:
            headers["traceparent"] = traceparent
        body = json.dumps({"status": status}, separators=(",", ":")).encode()
        request = Request(self.base + binding["path"], method="PATCH",
                          headers=headers, data=body)
        started = time.monotonic()
        record = {"operation": "network.actor_status.write", "method": "PATCH",
                  "path": binding["path"], "target_actor_id": driver,
                  "requested_status": status, "submitted_request_id": request_id,
                  "http_status": None, "credentials_sent": "Cookie" in auth_headers,
                  "authorization_sent": "Authorization" in auth_headers,
                  "traceparent_mode": binding["traceparent"]}
        if traceparent is not None:
            record["submitted_traceparent"] = traceparent
        try:
            try:
                response = self.opener.open(request, timeout=self.profile["policy"]["timeout_seconds"])
            except HTTPError as error:
                response = error
            with response:
                record["http_status"] = response.code
                header_id = response.headers.get("x-request-id", "")
                record["request_id_echoed"] = header_id == request_id
                record_response_id(record, header_id, source="HEADER",
                                   sensitive_values=auth_headers.values())
                if response.code != 200:
                    raise WriteTransportError(f"actor-status update returned HTTP {response.code}")
                raw = response.read(self.profile["policy"]["max_response_bytes"] + 1)
                if len(raw) > self.profile["policy"]["max_response_bytes"]:
                    raise WriteTransportError("actor-status response exceeds configured size limit")
                payload = json.loads(raw, object_pairs_hook=unique_keys)
                if (not isinstance(payload, dict)
                        or type(payload.get("code")) is not int or payload.get("code") != 20000
                        or not isinstance(payload.get("message"), str)
                        or not isinstance(payload.get("request_id"), str)
                        or not payload.get("request_id") or "data" not in payload):
                    raise WriteTransportError("public actor-status response envelope is invalid")
                if not record_response_id(record, payload["request_id"], source="ENVELOPE",
                                            sensitive_values=auth_headers.values()):
                    raise WriteTransportError("public actor-status response request ID is invalid")
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise WriteTransportError("actor-status response data must be an object")
                if data.get("bot_id") != driver or data.get("status") != status:
                    raise WriteTransportError("actor-status response did not acknowledge the exact target state")
                return data, record
        except WriteTransportError as error:
            raise error.with_record(record)
        except (OSError, URLError, ValueError, UnicodeError):
            raise WriteTransportError("network, TLS, timeout or JSON decoding error").with_record(record) from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)

    def _friend_connection_post(self, *, operation: str, binding_name: str,
                                 path: str, body: dict, expected_status: int,
                                 direction: str) -> tuple[dict, dict]:
        """POST only one reviewed friend-connection decision route, without fallback."""
        binding = require_edge_case_binding(self.profile, binding_name)
        request_id = identifier("e2e-friend-" + uuid.uuid4().hex, "request_id")
        auth_headers = {header: value for header, value in self.headers.items()
                        if header != "Authorization"}
        headers = {**auth_headers, "Accept": "application/json",
                   "Content-Type": "application/json", "X-Request-ID": request_id}
        payload = json.dumps(body, separators=(",", ":")).encode()
        request = Request(self.base + path, method="POST", data=payload,
                          headers=headers)
        started = time.monotonic()
        record = {"operation": operation, "method": "POST", "path": path,
                  "requested_direction": direction, "submitted_request_id": request_id,
                  "http_status": None, "credentials_sent": "Cookie" in auth_headers,
                  "authorization_sent": "Authorization" in auth_headers,
                  "traceparent_mode": binding["traceparent"]}
        try:
            try:
                response = self.opener.open(request, timeout=self.profile["policy"]["timeout_seconds"])
            except HTTPError as error:
                response = error
            with response:
                record["http_status"] = response.code
                header_id = response.headers.get("x-request-id", "")
                record["request_id_echoed"] = header_id == request_id
                record_response_id(record, header_id, source="HEADER",
                                    sensitive_values=auth_headers.values())
                if response.code != expected_status:
                    raise WriteTransportError(
                        f"friend-connection decision returned HTTP {response.code}")
                raw = response.read(self.profile["policy"]["max_response_bytes"] + 1)
                if len(raw) > self.profile["policy"]["max_response_bytes"]:
                    raise WriteTransportError("friend-connection response exceeds its byte budget")
                envelope = json.loads(raw, object_pairs_hook=unique_keys)
                expected_code = 20100 if expected_status == 201 else 20000
                if (not isinstance(envelope, dict)
                        or type(envelope.get("code")) is not int
                        or envelope["code"] != expected_code
                        or not isinstance(envelope.get("message"), str)
                        or not isinstance(envelope.get("request_id"), str)
                        or not envelope["request_id"] or "data" not in envelope):
                    raise WriteTransportError("friend-connection response envelope is invalid")
                if not record_response_id(record, envelope["request_id"], source="ENVELOPE",
                                           sensitive_values=auth_headers.values()):
                    raise WriteTransportError("friend-connection response request ID is invalid")
                data = envelope["data"]
                if not isinstance(data, dict):
                    raise WriteTransportError("friend-connection response data must be an object")
                return data, record
        except WriteTransportError as error:
            raise error.with_record(record)
        except (OSError, URLError, ValueError, UnicodeError):
            raise WriteTransportError(
                "friend-connection decision network or decoding failed").with_record(record) from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)

    @staticmethod
    def _friend_actor(actor_id: str, expected_role: str) -> dict:
        return {"type": "bot", "id": identifier(actor_id, f"{expected_role} actor ID")}

    def _friend_mutation_request(self, *, operation: str, binding_name: str,
                                  path: str, evidence_path: str | None = None,
                                  body: dict | None = None,
                                  expected: tuple[int, ...] = (200,),
                                  admit_error: bool = False,
                                  response_mode: str = "public-envelope",
                                  sensitive_values: tuple[str, ...] = ()) -> tuple[dict, dict]:
        """Execute one reviewed friend-mutation route with fail-closed parsing.

        Negative mutation assertions are opt-in through ``admit_error``. An
        admitted error is projected to code/message/data/error only; no other
        response body fields can enter evidence or be normalized to success.
        """
        binding = require_edge_case_binding(self.profile, binding_name)
        if not expected or any(type(status) is not int or status < 200 or status > 599
                               for status in expected):
            raise WriteTransportError("friend-mutation expected statuses are invalid")
        if response_mode != binding["response"]:
            raise WriteTransportError("friend-mutation response mode differs from its binding")
        if evidence_path is None:
            evidence_path = path
        if binding["auth"] == "human_cookie":
            auth_headers = {header: value for header, value in self.headers.items()
                            if header != "Authorization"}
        elif binding["auth"] == "register_token":
            auth_headers = {header: value for header, value in self.headers.items()
                            if header != "Authorization"}
        elif binding["auth"] == "human_cookie_owned_bot":
            auth_headers = dict(self.headers)
        else:
            raise WriteTransportError("friend-mutation route has an unsupported identity")
        request_id = identifier("e2e-friend-mutation-" + uuid.uuid4().hex, "request_id")
        headers = {**auth_headers, "Accept": "application/json",
                    "X-Request-ID": request_id}
        traceparent = new_traceparent() if binding["traceparent"] == "w3c" else None
        if traceparent is not None:
            headers["traceparent"] = traceparent
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode()
        request = Request(self.base + path, method=binding["method"],
                            headers=headers, data=data)
        started = time.monotonic()
        record = {"operation": operation, "method": binding["method"],
                    "path": evidence_path, "submitted_request_id": request_id,
                    "http_status": None,
                    "credentials_sent": "Cookie" in auth_headers,
                    "authorization_sent": "Authorization" in auth_headers,
                    "traceparent_mode": binding["traceparent"]}
        if traceparent is not None:
            record["submitted_traceparent"] = traceparent
        try:
            try:
                response = self.opener.open(request, timeout=self.profile["policy"]["timeout_seconds"])
            except HTTPError as error:
                response = error
            with response:
                record["http_status"] = response.code
                header_id = response.headers.get("x-request-id", "")
                record["request_id_echoed"] = header_id == request_id
                record_response_id(record, header_id, source="HEADER",
                                    sensitive_values=(*auth_headers.values(), *sensitive_values))
                if response.code not in expected:
                    raise WriteTransportError(
                        f"friend-mutation operation {operation} returned HTTP {response.code}")
                raw = response.read(self.profile["policy"]["max_response_bytes"] + 1)
                if len(raw) > self.profile["policy"]["max_response_bytes"]:
                    raise WriteTransportError(
                        "friend-mutation response exceeds configured size limit")
                payload = json.loads(raw, object_pairs_hook=unique_keys)
                if response_mode == "public-envelope":
                    status_code = {200: 20000, 201: 20100, 202: 20200,
                                    400: 40000, 403: 40300, 404: 40400,
                                    500: 50000}.get(response.code)
                    if (status_code is None or not isinstance(payload, dict)
                            or type(payload.get("code")) is not int
                            or payload["code"] != status_code
                            or not isinstance(payload.get("message"), str)
                            or "data" not in payload
                            or not isinstance(payload["data"], dict)):
                        raise WriteTransportError("friend-mutation response envelope is invalid")
                    response_id = payload.get("request_id")
                    if isinstance(response_id, str) and response_id:
                        if not record_response_id(record, response_id, source="ENVELOPE",
                                                    sensitive_values=(*auth_headers.values(), *sensitive_values)):
                            raise WriteTransportError(
                                "friend-mutation response request ID is invalid")
                    elif response.code < 400:
                        raise WriteTransportError("friend-mutation success response request ID is invalid")
                    if response.code >= 400:
                        if not admit_error:
                            raise WriteTransportError(
                                "negative friend-mutation assertion requires explicit error admission")
                        data_object = payload["data"]
                        projected = {"code": payload["code"],
                                        "message": payload["message"],
                                        "data": data_object}
                        error_code = data_object.get("error_code")
                        if isinstance(error_code, str) and error_code:
                            projected["error"] = error_code
                        return projected, record
                    return payload["data"], record
                if not isinstance(payload, dict):
                    raise WriteTransportError("friend-mutation station response must be an object")
                return payload, record
        except WriteTransportError as error:
            raise error.with_record(record)
        except (OSError, URLError, ValueError, UnicodeError):
            raise WriteTransportError(
                "friend-mutation network or decoding failed").with_record(record) from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)

    def create_edge_friend_request(self, from_actor_id: str, to_actor_id: str, *,
                                    expected: tuple[int, ...] = (201,),
                                    admit_error: bool = False) -> tuple[dict, dict]:
        """Create exactly one runtime-bound friend request on the public face."""
        binding = require_edge_case_binding(
            self.profile, "friend_connection_mutation.create")
        from_actor_id = identifier(from_actor_id, "friend-mutation from actor ID")
        to_actor_id = identifier(to_actor_id, "friend-mutation to actor ID")
        # The reviewed self-add negative branch must reach BCS: actor
        # distinctness is domain policy owned by the server (400
        # cannot_add_self), never a client-side transport precondition.
        body = {
            "from_actor": self._friend_actor(from_actor_id, "from"),
            "to_actor": self._friend_actor(to_actor_id, "to"),
        }
        return self._friend_mutation_request(
            operation="friend_connection_mutation.create",
            binding_name="friend_connection_mutation.create",
            path=binding["path"], body=body, expected=expected,
            admit_error=admit_error, response_mode="public-envelope",
        )

    def decide_edge_friend_request(self, action: str, request_id: str,
                                    from_actor_id: str, to_actor_id: str, *,
                                    expected: tuple[int, ...] = (200,),
                                    admit_error: bool = False) -> tuple[dict, dict]:
        """Accept, reject, or cancel exactly one runtime-bound friend request."""
        if action not in {"accept", "reject", "cancel"}:
            raise WriteTransportError("unsupported friend-mutation decision")
        binding_name = f"friend_connection_mutation.{action}"
        binding = require_edge_case_binding(self.profile, binding_name)
        if isinstance(request_id, int) and type(request_id) is int:
            request_id = str(request_id)
        request_id = identifier(request_id, "friend-mutation request ID")
        from_actor_id = identifier(from_actor_id, "friend-mutation from actor ID")
        to_actor_id = identifier(to_actor_id, "friend-mutation to actor ID")
        if from_actor_id == to_actor_id:
            raise WriteTransportError("friend-mutation pair must be distinct")
        path = binding["path"].replace("{request_id}", quote(request_id, safe=""))
        return self._friend_mutation_request(
            operation=binding_name, binding_name=binding_name,
            path=path, body={}, expected=expected,
            admit_error=admit_error, response_mode="public-envelope",
        )

    def revoke_edge_friend_connection(self, target_actor_id: str) -> tuple[dict, dict]:
        """Revoke one runtime-bound edge through the reviewed public query route."""
        binding = require_edge_case_binding(
            self.profile, "friend_connection_mutation.revoke")
        target_actor_id = identifier(target_actor_id, "friend-mutation target actor ID")
        path = binding["path"].replace(
            "{target_actor_id}", quote(target_actor_id, safe=""))
        return self._friend_mutation_request(
            operation="friend_connection_mutation.revoke",
            binding_name="friend_connection_mutation.revoke", path=path,
        )

    def patch_bot_friend_profile(self, target_actor_id: str, *, visibility: str | None = None,
                                    friend_check_in_strategy: str | None = None) -> tuple[dict, dict]:
        """Patch only the admitted visibility/profile fields of a runtime Bot."""
        binding = require_edge_case_binding(
            self.profile, "friend_connection_mutation.visibility.write")
        target_actor_id = identifier(target_actor_id, "friend-mutation target actor ID")
        body: dict[str, str] = {}
        if visibility is not None:
            body["visibility"] = visibility
        if friend_check_in_strategy is not None:
            body["friend_check_in_strategy"] = friend_check_in_strategy
        if not body:
            raise WriteTransportError("friend-mutation profile patch requires a field")
        path = binding["path"].replace(
            "{bot_id}", quote(target_actor_id, safe=""))
        return self._friend_mutation_request(
            operation="friend_connection_mutation.visibility.write",
            binding_name="friend_connection_mutation.visibility.write",
            path=path, body=body,
        )

    def station_register_bot(self, token: str, bot_name: str) -> tuple[dict, dict]:
        """Register a station Bot while redacting its token from evidence."""
        binding = require_edge_case_binding(
            self.profile, "friend_connection_mutation.register_bot.create")
        if (not isinstance(token, str) or not 1 <= len(token) <= 4096
                or not token.isascii()
                or any(ord(char) <= 32 or ord(char) == 127 for char in token)):
            raise WriteTransportError("station register token is outside its reviewed bounds")
        if (not isinstance(bot_name, str) or not 2 <= len(bot_name) <= 64
                or not bot_name.isascii()
                or any(ord(char) <= 32 or ord(char) == 127 for char in bot_name)):
            raise WriteTransportError("station registered Bot name is outside its reviewed bounds")
        request_path = (binding["path"]
                        .replace("{register_token}", quote(token, safe=""))
                        .replace("{bot_name}", quote(bot_name, safe="")))
        evidence_path = (binding["path"]
                        .replace("{register_token}", "[REDACTED-register-token]")
                        .replace("{bot_name}", quote(bot_name, safe="")))
        data, record = self._friend_mutation_request(
            operation="friend_connection_mutation.register_bot.create",
            binding_name="friend_connection_mutation.register_bot.create",
            path=request_path, evidence_path=evidence_path,
            response_mode="station-register-json",
            sensitive_values=(token,),
        )
        bot_uuid = data.get("bot_uuid")
        bot_token = data.get("bot_token")
        if (data.get("bot_name") != bot_name
                or not isinstance(bot_uuid, str)
                or re.fullmatch(r"[A-Za-z0-9_.:@-]{1,128}", bot_uuid or "") is None
                or not isinstance(bot_token, str)
                or not 1 <= len(bot_token) <= 4096
                or not bot_token.isascii()
                or any(ord(char) <= 32 or ord(char) == 127 for char in bot_token)):
            raise WriteTransportError("station register response is outside its reviewed contract")
        record["register_token_sha256"] = hashlib.sha256(token.encode()).hexdigest()
        return data, record

    def station_legacy_revoke(self, target_actor_id: str) -> tuple[dict, dict]:
        """Revoke all station edges to one owner-bound peer with dual identity."""
        binding = require_edge_case_binding(
            self.profile, "friend_connection_mutation.legacy_revoke")
        target_actor_id = identifier(target_actor_id, "friend-mutation target actor ID")
        path = binding["path"].replace("{actor}", quote(target_actor_id, safe=""))
        data, record = self._friend_mutation_request(
            operation="friend_connection_mutation.legacy_revoke",
            binding_name="friend_connection_mutation.legacy_revoke",
            path=path, response_mode="station-revoke-json",
        )
        edges = data.get("data", {}).get("revoked_edges")
        if (data.get("success") is not True
                or data.get("error") is not None
                or not isinstance(edges, list)
                or any(type(edge) is not int for edge in edges)):
            raise WriteTransportError("station revoke response is outside its reviewed contract")
        return data, record

    def station_bot_delete(self, bot_uuid: str) -> tuple[dict, dict]:
        """Retire one runtime-registered temporary station Bot with dual identity."""
        binding = require_edge_case_binding(
            self.profile, "friend_connection_mutation.station_bot_delete")
        bot_uuid = identifier(bot_uuid, "station Bot dispose target")
        path = binding["path"].replace("{bot_id}", quote(bot_uuid, safe=""))
        data, record = self._friend_mutation_request(
            operation="friend_connection_mutation.station_bot_delete",
            binding_name="friend_connection_mutation.station_bot_delete",
            path=path, response_mode="station-bot-delete-json",
        )
        if (data.get("left") is not True or data.get("bot_uuid") != bot_uuid):
            raise WriteTransportError(
                "station Bot dispose response is outside its reviewed contract")
        return data, record

    def station_bot_read(self, bot_uuid: str) -> tuple[dict, dict]:
        """Read back one retired station Bot and require the exact 404 tombstone."""
        binding = require_edge_case_binding(
            self.profile, "friend_connection_mutation.station_bot_read")
        bot_uuid = identifier(bot_uuid, "station Bot dispose target")
        path = binding["path"].replace("{bot_id}", quote(bot_uuid, safe=""))
        data, record = self._friend_mutation_request(
            operation="friend_connection_mutation.station_bot_read",
            binding_name="friend_connection_mutation.station_bot_read",
            path=path, expected=(404,), response_mode="station-bot-read-json",
        )
        if data.get("code") != "bot_not_found":
            raise WriteTransportError(
                "station retired Bot read-back differs from its reviewed tombstone")
        return data, record

    def create_friend_connection_request(self, from_actor_id: str,
                                         to_actor_id: str) -> tuple[dict, dict]:
        driver = self.profile["actors"]["driver"]["bot_id"]
        binding_name = ("friend_connection_decision.reject.create"
                        if from_actor_id == driver
                        else "friend_connection_decision.accept.create")
        binding = require_edge_case_binding(self.profile, binding_name)
        from_actor_id = identifier(from_actor_id, "friend-connection from actor")
        to_actor_id = identifier(to_actor_id, "friend-connection to actor")
        if (binding["from_actor_id"] != from_actor_id
                or binding["to_actor_id"] != to_actor_id):
            raise WriteTransportError("friend-connection create target differs from its binding")
        body = {
            "from_actor": self._friend_actor(from_actor_id, "from"),
            "to_actor": self._friend_actor(to_actor_id, "to"),
        }
        operation = ("friend.decision.create_member_to_driver"
                     if binding_name.endswith("accept.create")
                     else "friend.decision.create_driver_to_member")
        return self._friend_connection_post(
            operation=operation, binding_name=binding_name,
            path=binding["path"], body=body, expected_status=201,
            direction=f"{from_actor_id}->{to_actor_id}",
        )

    def create_public_friend_connection_request(self, from_actor_id: str,
                                                to_actor_id: str) -> tuple[dict, dict]:
        binding = require_edge_case_binding(
            self.profile, "friend_connection_public_accept.create"
        )
        from_actor_id = identifier(from_actor_id, "friend-connection from actor")
        to_actor_id = identifier(to_actor_id, "friend-connection to actor")
        if (binding["from_actor_id"] != from_actor_id
                or binding["to_actor_id"] != to_actor_id):
            raise WriteTransportError("public auto-acceptance target differs from its binding")
        body = {
            "from_actor": self._friend_actor(from_actor_id, "from"),
            "to_actor": self._friend_actor(to_actor_id, "to"),
        }
        return self._friend_connection_post(
            operation="friend.public.create",
            binding_name="friend_connection_public_accept.create",
            path=binding["path"], body=body, expected_status=201,
            direction=f"{from_actor_id}->{to_actor_id}",
        )

    def decision_friend_connection_request(self, *, decision: str, request_id: str,
                                           from_actor_id: str,
                                           to_actor_id: str) -> tuple[dict, dict]:
        if decision not in {"accept", "reject"}:
            raise WriteTransportError("unsupported friend-connection decision")
        binding_name = f"friend_connection_decision.{decision}"
        binding = require_edge_case_binding(self.profile, binding_name)
        request_id = identifier(request_id, "returned friend-connection request ID")
        from_actor_id = identifier(from_actor_id, "from actor ID")
        to_actor_id = identifier(to_actor_id, "to actor ID")
        if (binding["from_actor_id"] != from_actor_id
                or binding["to_actor_id"] != to_actor_id):
            raise WriteTransportError("friend-connection decision pair differs from its binding")
        path = binding["path"].replace("{request_id}", quote(request_id, safe=""))
        response, record = self._friend_connection_post(
            operation=f"friend.decision.{decision}",
            binding_name=binding_name, path=path, body={},
            expected_status=200, direction=f"{from_actor_id}->{to_actor_id}",
        )
        if response.get("request_id") != request_id:
            raise WriteTransportError("friend-connection decision did not return the exact request")
        if response.get("status") != ("approved" if decision == "accept" else "rejected"):
            raise WriteTransportError("friend-connection decision status differs")
        return response, record

    _STRUCTURED_SUCCESS_CODES = {200: 20000, 201: 20100, 202: 20200}
    _STRUCTURED_ERROR_CODES = {400: 40000, 404: 40400, 409: 40900}

    def structured_call(self, operation: str, *, group_id: str = "",
                         run_id: str = "", body: dict | None = None,
                         expected: tuple[int, ...] = (200,),
                         parse_error: bool = False) -> tuple[dict | None, dict]:
        """Execute exactly one reviewed structured-collaboration write route.

        Negative assertions (the unpublished-definition upgrade, the bot-task
        Human response and the aborted-run rerun rejection) require explicit
        ``parse_error`` admission so the bounded diagnostic projection is
        retrievable; an unadmitted error status is a transport failure and
        never a silent pass.
        """
        if not operation.startswith("structured."):
            raise WriteTransportError("structured call requires a reviewed structured route")
        try:
            binding = require_edge_case_binding(self.profile, operation)
        except ValueError:
            raise WriteTransportError(
                "structured call requires a reviewed structured route") from None
        method, path = binding["method"], binding["path"]
        if method == "GET":
            raise WriteTransportError("structured call is write-only; structured reads belong to the read transport")
        if not isinstance(group_id, str) or not isinstance(run_id, str):
            raise WriteTransportError("structured identifiers must be strings")
        if "{group_id}" in path:
            if not group_id:
                raise WriteTransportError("structured group route requires a bound group ID")
            path = path.replace("{group_id}", quote(group_id, safe=""))
        if "{run_id}" in path:
            if not run_id:
                raise WriteTransportError("structured run route requires a bound run ID")
            path = path.replace("{run_id}", quote(run_id, safe=""))
        if "{driver_actor_id}" in path:
            driver_id = self.profile.get("actors", {}).get("driver", {}).get("bot_id", "")
            if not driver_id:
                raise WriteTransportError("structured group delete requires the dedicated driver Bot")
            path = path.replace("{driver_actor_id}", quote(driver_id, safe=""))
        request_id = identifier("e2e-structured-" + uuid.uuid4().hex, "request_id")
        auth_headers = self.local_auth.headers(self.profile) if self.local_auth is not None else self.headers
        if binding["auth"] == "human_cookie":
            # Human-scoped structured routes authenticate with the Human Cookie only.
            auth_headers = {header: value for header, value in auth_headers.items()
                            if header != "Authorization"}
        elif binding["auth"] != "authenticated_bot":
            raise WriteTransportError("structured route must bind a Human Cookie or the dedicated authenticated Bot")
        headers = {**auth_headers, "Accept": "application/json", "X-Request-ID": request_id}
        traceparent = new_traceparent() if binding["traceparent"] == "w3c" else None
        if traceparent is not None:
            headers["traceparent"] = traceparent
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path, method=method, headers=headers, data=data)
        started = time.monotonic()
        record = {"operation": operation, "method": method, "path": path,
                  "submitted_request_id": request_id, "http_status": None,
                  "credentials_sent": "Cookie" in auth_headers,
                  "authorization_sent": "Authorization" in auth_headers,
                  "traceparent_mode": binding["traceparent"]}
        if traceparent is not None:
            record["submitted_traceparent"] = traceparent
        try:
            try:
                response = self.opener.open(request, timeout=self.profile["policy"]["timeout_seconds"])
            except HTTPError as error:
                response = error
            with response:
                record["http_status"] = response.code
                header_id = response.headers.get("x-request-id", "")
                record["request_id_echoed"] = header_id == request_id
                record_response_id(record, header_id, source="HEADER",
                                   sensitive_values=auth_headers.values())
                if traceparent is not None:
                    record["submitted_traceparent"] = traceparent
                    effective = response.headers.get("traceparent", "")
                    if effective:
                        if not valid_traceparent(effective):
                            raise WriteTransportError("response traceparent is invalid")
                        record["effective_traceparent"] = effective
                if response.code not in expected:
                    raise WriteTransportError(
                        f"structured operation {operation} returned HTTP {response.code}")
                raw = response.read(self.profile["policy"]["max_response_bytes"] + 1)
                if len(raw) > self.profile["policy"]["max_response_bytes"]:
                    raise WriteTransportError("structured response exceeds configured size limit")
                if response.code == 204 or not raw:
                    return None, record
                payload = json.loads(raw, object_pairs_hook=unique_keys)
                if binding["response"] == "plain-json":
                    if not isinstance(payload, dict):
                        raise WriteTransportError("structured station response must be an object")
                    if response.code >= 400:
                        if not parse_error:
                            raise WriteTransportError(
                                "negative structured assertion requires explicit error admission")
                        if not isinstance(payload.get("message"), str) or not payload["message"]:
                            raise WriteTransportError("structured station error message is invalid")
                    return payload, record
                envelope = payload
                expected_code = (self._STRUCTURED_SUCCESS_CODES.get(response.code)
                                 if response.code < 400
                                 else self._STRUCTURED_ERROR_CODES.get(response.code))
                if (expected_code is None
                        or not isinstance(envelope, dict)
                        or type(envelope.get("code")) is not int
                        or envelope.get("code") != expected_code
                        or not isinstance(envelope.get("message"), str)
                        or not envelope.get("message")
                        or not isinstance(envelope.get("request_id"), str)
                        or not envelope.get("request_id") or "data" not in envelope):
                    raise WriteTransportError("structured response envelope is invalid")
                if not record_response_id(record, envelope["request_id"], source="ENVELOPE",
                                           sensitive_values=auth_headers.values()):
                    raise WriteTransportError("structured response request ID is invalid")
                payload = envelope.get("data")
                if response.code >= 400:
                    if not parse_error:
                        raise WriteTransportError(
                            "negative structured assertion requires explicit error admission")
                    # Bounded diagnostic projection: the envelope message and the
                    # structured data are both part of the reviewed negative
                    # response, and neither carries credentials.
                    return {"message": envelope["message"], "data": payload}, record
                if not isinstance(payload, dict):
                    raise WriteTransportError("structured success response data must be an object")
                return payload, record
        except WriteTransportError as error:
            raise error.with_record(record)
        except (OSError, URLError, ValueError, UnicodeError):
            raise WriteTransportError("network, TLS, timeout or JSON decoding error").with_record(record) from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)

    def call(self, operation: str, *, group_id: str = "", session_id: str = "",
             invite_token: str = "", body: dict | None = None,
             expected: tuple[int, ...] = (200,), request_id: str = "",
             parse_error: bool = False) -> tuple[dict | None, dict]:
        try:
            binding = self.profile["route_bindings"][operation]
            method, path = binding["method"], binding["path"]
            trace_mode = binding["traceparent"]
        except (KeyError, TypeError):
            raise WriteTransportError("operation has no validated route binding") from None
        byte_budget = binding.get("max_response_bytes")
        if byte_budget is None:
            byte_budget = self.profile["policy"]["max_response_bytes"]
        elif (type(byte_budget) is not int
                or not 1 <= byte_budget <= ROUTE_BINDING_RESPONSE_BUDGET_MAXIMUM):
            raise WriteTransportError(
                "route binding response budget is outside the reviewed bound")
        if "{group_id}" in path:
            if not group_id:
                raise WriteTransportError("group operation requires a bound group ID")
            path = path.replace("{group_id}", quote(group_id, safe=""))
        if "{session_id}" in path:
            if not session_id:
                raise WriteTransportError("session operation requires a bound session ID")
            path = path.replace("{session_id}", quote(session_id, safe=""))
        if "{driver_actor_id}" in path:
            driver_id = self.profile.get("actors", {}).get("driver", {}).get("bot_id", "")
            if not driver_id:
                raise WriteTransportError("session CLI cleanup requires the dedicated driver Bot")
            path = path.replace("{driver_actor_id}", quote(driver_id, safe=""))
        if "{member_actor_id}" in path:
            member_id = self.profile.get("actors", {}).get("member", {}).get("bot_id", "")
            if not member_id:
                raise WriteTransportError("session CLI member route requires the dedicated member Bot")
            path = path.replace("{member_actor_id}", quote(member_id, safe=""))
        if "{qa_actor_id}" in path:
            qa_id = self.profile.get("actors", {}).get("qa", {}).get("bot_id", "")
            if not qa_id:
                raise WriteTransportError("incident participant route requires the dedicated QA Bot")
            path = path.replace("{qa_actor_id}", quote(qa_id, safe=""))
        if "{invite_token}" in path:
            if not invite_token:
                raise WriteTransportError("invite operation requires the exact minted token")
            request_path = path.replace("{invite_token}", quote(invite_token, safe=""))
            evidence_path = path.replace("{invite_token}", "[REDACTED]")
        else:
            request_path, evidence_path = path, path
        request_id = identifier(request_id, "request_id") if request_id else "e2e-step-" + uuid.uuid4().hex
        if self.local_auth is not None:
            auth_headers = self.local_auth.headers(self.profile)
        elif self.profile.get("auth", {}).get("principal_signing_key_env"):
            # Public-v1 lifecycle routes authenticate with an exact Gateway
            # Principal JWT minted from the operator-provisioned deployment
            # signing key, never with a Cookie or a Bot token.
            from .local_auth import minted_principal_headers
            auth_headers = minted_principal_headers(self.profile)
        else:
            auth_headers = self.headers
        headers = {**auth_headers, "Accept": "application/json", "X-Request-ID": request_id}
        traceparent = new_traceparent() if trace_mode == "w3c" else None
        if traceparent is not None:
            headers["traceparent"] = traceparent
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base + request_path, method=method, headers=headers, data=data)
        started = time.monotonic()
        record = {"operation": operation, "method": method, "path": evidence_path,
                  "submitted_request_id": request_id, "http_status": None,
                  "traceparent_mode": trace_mode,
                  "response_budget_bytes": byte_budget,
                  "credentials_sent": "Cookie" in auth_headers,
                  "authorization_sent": "Authorization" in auth_headers,
                  "principal_sent": "X-Avernet-Principal" in auth_headers}
        if traceparent is not None:
            record["submitted_traceparent"] = traceparent
        try:
            try:
                response = self.opener.open(request, timeout=self.profile["policy"]["timeout_seconds"])
            except HTTPError as error:
                response = error
            with response:
                record["http_status"] = response.code
                header_id = response.headers.get("x-request-id", "")
                record["request_id_echoed"] = header_id == request_id
                record_response_id(record, header_id, source='HEADER', sensitive_values=auth_headers.values())
                if traceparent is not None:
                    record["submitted_traceparent"] = traceparent
                    effective = response.headers.get("traceparent", "")
                    if effective:
                        if not valid_traceparent(effective):
                            raise WriteTransportError("response traceparent is invalid")
                        record["effective_traceparent"] = effective
                if response.code not in expected:
                    raise WriteTransportError(f"operation {operation} returned HTTP {response.code}")
                # Expected negative verification (for example GET after delete -> 404)
                # is an assertion result, not a JSON success envelope.
                if response.code >= 400 and not parse_error:
                    return None, record
                raw = response.read(byte_budget + 1)
                if len(raw) > byte_budget:
                    raise WriteTransportError("response exceeds configured size limit")
                if response.code == 204 or not raw:
                    return None, record
                payload = json.loads(raw)
                if self.profile["entrypoint"]["api_face"] == "public-v1":
                    expected_code = {200: 20000, 201: 20100}.get(response.code)
                    if (expected_code is None or not isinstance(payload, dict) or type(payload.get("code")) is not int
                            or payload.get("code") != expected_code
                            or not isinstance(payload.get("message"), str)
                            or not isinstance(payload.get("request_id"), str)
                            or not payload.get("request_id") or "data" not in payload):
                        raise WriteTransportError("public API envelope is invalid")
                    if not record_response_id(record, payload["request_id"], source='ENVELOPE',
                                              sensitive_values=auth_headers.values()):
                        raise WriteTransportError('public API response request ID is invalid')
                    payload = payload.get("data")
                if operation in {"session.messages", "session_cli.session.messages"}:
                    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                        raise WriteTransportError("session message response must be an array of objects")
                    record["response_projection"] = (
                        "public-message-array-to-items"
                        if operation == "session.messages"
                        else "legacy-session-message-array-to-items"
                    )
                    return {"items": payload}, record
                if (operation in {"session_cli.message.deliveries.read",
                                  "session_cli.message.deliveries.query"}
                        and isinstance(payload, list)):
                    # The reviewed station withdrawals surface projects the
                    # delivery batch as a bare JSON array (empty when no
                    # delivery exists); pass arrays through verbatim instead
                    # of forcing the object envelope. Object payloads keep
                    # the generic contract below.
                    if not all(isinstance(item, dict) for item in payload):
                        raise WriteTransportError(
                            "message delivery response must be an array of "
                            "objects")
                    record["response_projection"] = (
                        "cli-delivery-array-passthrough")
                    return payload, record
                if payload is not None and not isinstance(payload, dict):
                    raise WriteTransportError("expected object response data")
                return payload, record
        except WriteTransportError as error:
            raise error.with_record(record)
        except (OSError, URLError, ValueError, UnicodeError):
            raise WriteTransportError("network, TLS, timeout or JSON decoding error").with_record(record) from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)


    def event_subscription_call(self, operation: str, *,
                                     subscription_id: str = "",
                                     body: dict | None = None,
                                     expected: tuple[int, ...] = (200,)
                                     ) -> tuple[dict | None, dict]:
        """Execute exactly one reviewed Event Subscription lifecycle write.

        The revoked disable, re-enable and tombstoning delete belong to
        the reviewed optimistic-concurrency lifecycle; the synchronous
        subscription test is a write-shaped observation of the same
        principal boundary. Negative statuses are transport failures:
        this slice has no admitted negative assertion.
        """
        if not operation.startswith("webhook.subscription."):
            raise WriteTransportError("event subscription call requires a reviewed webhook route")
        try:
            binding = require_edge_case_binding(self.profile, operation)
        except ValueError:
            raise WriteTransportError("event subscription call requires a reviewed webhook route") from None
        method, path = binding["method"], binding["path"]
        if method == "GET":
            raise WriteTransportError("event subscription call is write-only; subscription reads belong to the read transport")
        if "{subscription_id}" in path:
            if not isinstance(subscription_id, str) or not subscription_id:
                raise WriteTransportError("event subscription route requires a bound subscription ID")
            path = path.replace("{subscription_id}", quote(subscription_id, safe=""))
        request_id = identifier("e2e-webhook-" + uuid.uuid4().hex, "request_id")
        if self.local_auth is not None:
            auth_headers = self.local_auth.headers(self.profile)
        elif self.profile.get("auth", {}).get("principal_signing_key_env"):
            from .local_auth import minted_principal_headers
            auth_headers = minted_principal_headers(self.profile)
        else:
            raise WriteTransportError("Event Subscription routes require the Gateway Principal authorization")
        headers = {**auth_headers, "Accept": "application/json", "X-Request-ID": request_id}
        traceparent = new_traceparent() if binding["traceparent"] == "w3c" else None
        if traceparent is not None:
            headers["traceparent"] = traceparent
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path, method=method, headers=headers, data=data)
        started = time.monotonic()
        record = {"operation": operation, "method": method, "path": path,
                  "submitted_request_id": request_id, "http_status": None,
                  "principal_sent": True, "credentials_sent": False,
                  "authorization_sent": False,
                  "traceparent_mode": binding["traceparent"]}
        if traceparent is not None:
            record["submitted_traceparent"] = traceparent
        try:
            try:
                response = self.opener.open(request, timeout=self.profile["policy"]["timeout_seconds"])
            except HTTPError as error:
                response = error
            with response:
                record["http_status"] = response.code
                header_id = response.headers.get("x-request-id", "")
                record["request_id_echoed"] = header_id == request_id
                record_response_id(record, header_id, source="HEADER",
                                   sensitive_values=auth_headers.values())
                if traceparent is not None:
                    record["submitted_traceparent"] = traceparent
                    effective = response.headers.get("traceparent", "")
                    if effective:
                        if not valid_traceparent(effective):
                            raise WriteTransportError("response traceparent is invalid")
                        record["effective_traceparent"] = effective
                if response.code not in expected:
                    raise WriteTransportError(
                        f"event subscription operation {operation} returned HTTP {response.code}")
                raw = response.read(self.profile["policy"]["max_response_bytes"] + 1)
                if len(raw) > self.profile["policy"]["max_response_bytes"]:
                    raise WriteTransportError("event subscription response exceeds configured size limit")
                if response.code == 204 or not raw:
                    return None, record
                payload = json.loads(raw)
                if self.profile["entrypoint"]["api_face"] == "public-v1":
                    expected_code = {200: 20000, 201: 20100}.get(response.code)
                    if (expected_code is None or not isinstance(payload, dict)
                            or type(payload.get("code")) is not int
                            or payload.get("code") != expected_code
                            or not isinstance(payload.get("message"), str)
                            or not isinstance(payload.get("request_id"), str)
                            or not payload.get("request_id") or "data" not in payload):
                        raise WriteTransportError("public API envelope is invalid")
                    if not record_response_id(record, payload["request_id"], source="ENVELOPE",
                                               sensitive_values=auth_headers.values()):
                        raise WriteTransportError("public API response request ID is invalid")
                    payload = payload.get("data")
                if payload is not None and not isinstance(payload, dict):
                    raise WriteTransportError("expected object response data")
                return payload, record
        except WriteTransportError as error:
            raise error.with_record(record)
        except (OSError, URLError, ValueError, UnicodeError):
            raise WriteTransportError("network, TLS, timeout or JSON decoding error").with_record(record) from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)

    def register_bot_create(self, *, token: str,
                                 bot_name: str) -> tuple[dict, dict]:
        """Execute exactly one reviewed public-v1 Bot registration create.

        The reviewed create is deliberately anonymous: the minted
        register token is its sole credential and travels in the
        query string. Neither the register token nor the returned
        Bot token is ever journaled; only their SHA-256 digests and
        the redacted request path are recorded.
        """
        binding = require_edge_case_binding(self.profile, "register.bot.create")
        if (binding["auth"] != "register_token"
                or binding["method"] != "POST"
                or binding["path"] != NETWORK_REGISTER_CREATE_PATH):
            raise WriteTransportError(
                "register create is not the reviewed anonymous route")
        if (not isinstance(token, str) or not 1 <= len(token) <= 4096
                or not token.isascii()
                or any(ord(char) <= 32 or ord(char) == 127 for char in token)):
            raise WriteTransportError("register token is outside its reviewed bounds")
        if (not isinstance(bot_name, str) or not 2 <= len(bot_name) <= 64
                or not bot_name.isascii()
                or any(ord(char) <= 32 or ord(char) == 127 for char in bot_name)):
            raise WriteTransportError("registered Bot name is outside its reviewed bounds")
        request_path = (NETWORK_REGISTER_CREATE_PATH + "?token=" + quote(token, safe="")
                        + "&bot-name=" + quote(bot_name, safe=""))
        evidence_path = (NETWORK_REGISTER_CREATE_PATH
                         + "?token=[REDACTED-register-token]&bot-name="
                         + quote(bot_name, safe=""))
        request_id = identifier("e2e-register-" + uuid.uuid4().hex, "request_id")
        # The public-v1 product route stays anonymous: the minted register
        # token is its product credential.  Through the deployed preprod
        # Gateway, ACE rejects access without a browser Cookie even for an
        # anonymous downstream route.  Carry that Cookie (and, when bound,
        # Origin/Referer) as explicit platform ingress context; never send a
        # Bot Authorization header with it.
        platform_gateway = (
            self.profile["entrypoint"]["kind"] == "gateway"
            and self.profile["environment"]["tier"] == "preprod"
        )
        if platform_gateway:
            platform_headers = {
                **browser_headers(self.profile),
                **{header: value for header, value in self.headers.items()
                   if header != "Authorization"},
            }
        else:
            platform_headers = {}
        request = Request(self.base + request_path, method="POST", headers={
            **platform_headers,
            "Accept": "application/json", "X-Request-ID": request_id,
        })
        started = time.monotonic()
        record = {"operation": "register.bot.create", "method": "POST",
                  "path": evidence_path, "submitted_request_id": request_id,
                  "http_status": None,
                  "credentials_sent": "Cookie" in platform_headers,
                  "authorization_sent": "Authorization" in platform_headers,
                  "platform_gateway_browser_context_sent": platform_gateway,
                  "browser_origin_context_sent": bool(browser_headers(self.profile)),
                  "register_token_sha256": hashlib.sha256(token.encode()).hexdigest()}
        try:
            try:
                response = self.opener.open(request, timeout=self.profile["policy"]["timeout_seconds"])
            except HTTPError as error:
                response = error
            with response:
                record["http_status"] = response.code
                header_id = response.headers.get("x-request-id", "")
                record["request_id_echoed"] = header_id == request_id
                record_response_id(record, header_id, source="HEADER",
                                   sensitive_values=())
                if response.code != 201:
                    raise WriteTransportError(
                        f"register create returned HTTP {response.code}")
                raw = response.read(self.profile["policy"]["max_response_bytes"] + 1)
                if len(raw) > self.profile["policy"]["max_response_bytes"]:
                    raise WriteTransportError(
                        "register response exceeds configured size limit")
                payload = json.loads(raw, object_pairs_hook=unique_keys)
                if (not isinstance(payload, dict)
                        or type(payload.get("code")) is not int
                        or payload["code"] != 20100
                        or not isinstance(payload.get("message"), str)
                        or not isinstance(payload.get("request_id"), str)
                        or not payload["request_id"] or "data" not in payload):
                    raise WriteTransportError(
                        "register create response envelope is invalid")
                if not record_response_id(record, payload["request_id"],
                                           source="ENVELOPE",
                                           sensitive_values=()):
                    raise WriteTransportError(
                        "register create response request ID is invalid")
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise WriteTransportError(
                        "register create response data must be an object")
                bot_uuid = data.get("bot_uuid")
                bot_token = data.get("bot_token")
                if (data.get("bot_name") != bot_name
                        or not isinstance(bot_uuid, str)
                        or re.fullmatch(r"[A-Za-z0-9_.:@-]{1,128}", bot_uuid or "") is None
                        or not isinstance(bot_token, str)
                        or not 1 <= len(bot_token) <= 4096
                        or not bot_token.isascii()
                        or any(ord(char) <= 32 or ord(char) == 127
                               for char in bot_token)):
                    raise WriteTransportError(
                        "register create response is outside its reviewed contract")
                record["bot_token_sha256"] = hashlib.sha256(
                    bot_token.encode()).hexdigest()
                return data, record
        except WriteTransportError as error:
            raise error.with_record(record)
        except (OSError, URLError, ValueError, UnicodeError):
            raise WriteTransportError(
                "register create network or decoding failed").with_record(record) from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
