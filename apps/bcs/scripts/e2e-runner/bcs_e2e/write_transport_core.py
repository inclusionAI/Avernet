"""WriteClient core plane: structured/core/event-subscription write primitives and their response-code maps."""
from __future__ import annotations

import json
import time
import uuid

from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request

from .correlation import new_traceparent, valid_traceparent, record_response_id
from .profile import ROUTE_BINDING_RESPONSE_BUDGET_MAXIMUM, identifier, unique_keys
from .route_bindings import require_edge_case_binding
from .write_transport_error import WriteTransportError


class WriteCoreTransportMixin:
    """Stateless mixin plane; instance state is installed by WriteClient.__init__ in the base module."""

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
