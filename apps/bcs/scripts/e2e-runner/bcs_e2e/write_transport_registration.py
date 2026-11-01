"""WriteClient registration plane: bound driver-Bot lifecycle patches and the reviewed public-v1 network registration create."""
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
from .profile import browser_headers, identifier, unique_keys
from .route_bindings import NETWORK_REGISTER_CREATE_PATH, require_edge_case_binding
from .write_transport_error import WriteTransportError


class WriteRegistrationTransportMixin:
    """Stateless mixin plane; instance state is installed by WriteClient.__init__ in the base module."""

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
