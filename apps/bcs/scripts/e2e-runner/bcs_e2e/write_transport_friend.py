"""WriteClient friend plane: edge friend-request mutations, station Bot register/delete/read, and the shared friend-connection writes."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request

from .correlation import new_traceparent, record_response_id
from .profile import identifier, unique_keys
from .route_bindings import require_edge_case_binding
from .write_transport_error import WriteTransportError


class WriteFriendTransportMixin:
    """Stateless mixin plane; instance state is installed by WriteClient.__init__ in the base module."""

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
