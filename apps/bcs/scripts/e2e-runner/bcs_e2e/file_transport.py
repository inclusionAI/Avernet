"""Session-file internal V1 transport; it is not a public-prefix alias.

Authority: api-contracts/v1/internal.yaml and openapi/session-files.yaml.
JSON envelopes, raw content and token-only shared content are distinct contracts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import copy
import json
import os
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlsplit
from urllib.request import Request, ProxyHandler, HTTPSHandler, build_opener

from .profile import browser_headers, origin, unique_keys
from .transport import tls_context
from .correlation import RequestFailure, record_response_id
from .write_transport import NoRedirect
from .upload_targets import proxy_target, upload_parts


class FileTransportError(RequestFailure):
    pass


PREPROD_SHARE_SSO = "preprod-sso-share-token-v2"


@dataclass
class _FileResponse:
    body: bytes
    record: dict
    location: str  # Empty for non-redirect responses; never exposed in reports.
    sensitive_values: tuple[str, ...] = field(default=(), repr=False)


def _segment(value: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise FileTransportError("file operation requires a bound resource ID")
    return quote(value, safe="")


class RemoteFileClient:
    def __init__(self, profile: dict, *, local_auth=None):
        self.profile = profile
        self.base = origin(profile["entrypoint"]["origin"], profile["environment"]["tier"])
        self.base_parts = urlsplit(self.base)
        self.local_auth = local_auth
        # Station gateways expose the internal V1 file router at /sessions;
        # owned-local product services expose it under the public V1 prefix.
        self.station_gateway = (profile["environment"]["tier"] == "preprod"
                                and profile["entrypoint"]["kind"] == "gateway")
        self.file_path_prefix = "" if self.station_gateway else "/api/v1/collaboration"
        owned_local = profile["auth"] == {"kind": "owned-local"}
        if owned_local or local_auth is not None:
            from .local_auth import require_local_auth
            require_local_auth(profile, local_auth)
        self.headers = browser_headers(profile)
        for header, ref in profile["auth"].get("header_env", {}).items():
            value = os.environ.get(ref, "")
            if not value or not value.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise FileTransportError("missing or invalid authentication reference")
            self.headers[header] = value
        self.opener = (build_opener(ProxyHandler({}), HTTPSHandler(context=tls_context()), NoRedirect())
                       if owned_local else build_opener(HTTPSHandler(context=tls_context()), NoRedirect()))
        self.max_bytes = profile["policy"]["max_response_bytes"]
        self.timeout = profile["policy"]["timeout_seconds"]
        self.storage_origins = {
            self._origin_key(origin(value, profile["environment"]["tier"]))
            for value in profile.get("file_transfer", {}).get("allowed_storage_origins", [])
        }
        self.share_origins = {
            self._origin_key(origin(value, profile["environment"]["tier"]))
            for value in profile.get("file_transfer", {}).get("allowed_share_origins", [])
        }
        self.prepared: dict[tuple[str, str], dict] = {}
        self.share_targets: dict[str, str] = {}

    def _path(self, session_id: str, suffix: str = "") -> str:
        return f"{self.file_path_prefix}/sessions/{_segment(session_id)}/files{suffix}"

    @staticmethod
    def _origin_key(target: str) -> tuple:
        parsed = urlsplit(target)
        return parsed.scheme, parsed.hostname, parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)

    def _same_origin(self, target: str) -> bool:
        return self._origin_key(target) == self._origin_key(self.base)

    def _url(self, path_or_url: str, *, storage: bool = False, share: bool = False) -> str:
        if (not isinstance(path_or_url, str) or not path_or_url.isascii()
                or any(ord(c) <= 32 or ord(c) == 127 for c in path_or_url)):
            raise FileTransportError("invalid file target URL")
        if not storage and path_or_url.startswith("/") and not path_or_url.startswith("//"):
            target = self.base + path_or_url
        else:
            target = path_or_url
        try:
            parts = urlsplit(target)
            if (parts.scheme not in {"https", "http"} or not parts.hostname
                    or parts.username is not None or parts.password is not None or parts.fragment):
                raise ValueError
            if storage:
                if self._origin_key(target) not in self.storage_origins:
                    raise FileTransportError("storage target requires an explicit egress allowlist")
            elif share:
                if (self._origin_key(target) not in self.share_origins
                        and not self._same_origin(target)):
                    raise FileTransportError("share target requires an explicit egress allowlist")
            elif not self._same_origin(target):
                raise FileTransportError("product target must match the configured origin")
        except ValueError:
            raise FileTransportError("invalid file target URL") from None
        return target

    def _send(self, method: str, path_or_url: str, *, operation: str,
                 body: bytes | None = None, content_type: str | None = None,
                 expected: tuple[int, ...] = (200,), include_auth: bool = True,
                 storage: bool = False, share: bool = False) -> _FileResponse:
        # Enforced at the transport boundary, not just at individual callers.
        if body is not None and (not isinstance(body, bytes) or len(body) > self.max_bytes):
            raise FileTransportError("request body exceeds the small-file runner budget")
        if storage and include_auth:
            raise FileTransportError("storage requests cannot carry product credentials")
        url = self._url(path_or_url, storage=storage, share=share)
        parts = urlsplit(url)
        request_id = "e2e-file-" + uuid.uuid4().hex
        request_headers = {"Accept": "*/*" if storage else "application/json"}
        if not storage:
            request_headers["X-Request-ID"] = request_id
        if self.local_auth is not None:
            self.local_auth.require_bound(self.profile)
        auth_headers = (self.local_auth.headers(self.profile) if self.local_auth is not None else self.headers) if include_auth else {}
        request_headers.update(auth_headers)
        # Private per-request context, not part of evidence or mutable client state.
        # Include the actual local Principal and a token-only shared URL as well.
        sensitive_values = (*self.headers.values(), *auth_headers.values(),
                            *(value for key, value in parse_qsl(parts.query, keep_blank_values=True) if key == 'token'))
        if content_type:
            if not content_type.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in content_type):
                raise FileTransportError("invalid upload MIME type")
            request_headers["Content-Type"] = content_type
        if body is not None:
            request_headers["Content-Length"] = str(len(body))
        request = Request(url, method=method, headers=request_headers, data=body)
        record = {"operation": operation, "method": method,
                  "path": "[storage-target]" if storage else parts.path + ("?[REDACTED]" if parts.query else ""),
                  "target_kind": "storage" if storage else "product",
                  "submitted_request_id": None if storage else request_id, "http_status": None,
                  "request_id_echoed": False}
        response = None
        started = time.monotonic()
        try:
            try:
                response = self.opener.open(request, timeout=self.timeout)
            except HTTPError as error:
                response = error
            record["http_status"] = response.code
            record["request_id_echoed"] = not storage and response.headers.get("x-request-id", "") == request_id
            if not storage:
                record_response_id(record, response.headers.get('x-request-id', ''), source='HEADER',
                                   sensitive_values=sensitive_values)
            if response.code not in expected:
                raise FileTransportError(f"file operation returned HTTP {response.code}")
            raw = response.read(self.max_bytes + 1)
            if len(raw) > self.max_bytes:
                raise FileTransportError("file response exceeds configured size limit")
            return _FileResponse(raw, record, response.headers.get("Location", "") or response.headers.get("location", ""),
                                 sensitive_values)
        except FileTransportError as error:
            raise error.with_record(record)
        except (OSError, URLError, UnicodeError):
            raise FileTransportError("file network, TLS or timeout failure").with_record(record) from None
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            if response is not None:
                response.close()

    def _request(self, method: str, path_or_url: str, **kwargs) -> tuple[bytes, dict]:
        response = self._send(method, path_or_url, **kwargs)
        return response.body, response.record

    def _download(self, path: str, *, operation: str, include_auth: bool,
                  share: bool = False) -> tuple[bytes, dict]:
        records = []
        target = path
        try:
            for hop in range(4):
                response = self._send("GET", target, operation=operation,
                                      expected=(200, 302, 303), include_auth=include_auth if hop == 0 else False,
                                      storage=hop > 0, share=share and hop == 0)
                records.append(response.record)
                if response.record["http_status"] == 200:
                    record = {**records[0],
                              "http_status": response.record["http_status"],
                              "hops": records,
                              "elapsed_ms": round(sum(item["elapsed_ms"] for item in records), 2)}
                    return response.body, record
                if not response.location or hop == 3:
                    raise FileTransportError("download redirect is missing or exceeds the hop limit")
                target = self._url(response.location, storage=True)
            raise FileTransportError("download did not return bytes")
        except FileTransportError as error:
            raise error.with_prior_records(records)

    @staticmethod
    def _envelope(raw: bytes) -> dict:
        try:
            payload = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError):
            raise FileTransportError("file response is not valid JSON") from None
        if (not isinstance(payload, dict) or type(payload.get("code")) is not int
                or not isinstance(payload.get("message"), str)
                or not isinstance(payload.get("request_id"), str) or not payload["request_id"]
                or not isinstance(payload.get("data"), dict)):
            raise FileTransportError("file V1 response envelope is invalid")
        return payload

    def _decode_json_data(self, raw: bytes, record: dict, *, sensitive_values=()):
        try:
            payload = self._envelope(raw)
            expected_code = {200: 20000, 201: 20100, 202: 20000}.get(record.get("http_status"))
            if expected_code is None or payload["code"] != expected_code:
                raise FileTransportError("file V1 envelope did not indicate success")
            if not record_response_id(record, payload['request_id'], source='ENVELOPE',
                                      sensitive_values=(*self.headers.values(), *sensitive_values)):
                raise FileTransportError('file response request ID is invalid')
            return payload["data"], record
        except FileTransportError as error:
            raise error.with_record(record)

    def _json(self, method: str, path: str, *, operation: str, body: dict | None = None,
              expected: tuple[int, ...] = (200,)) -> tuple[dict, dict]:
        response = self._send(
            method, path, operation=operation,
            body=None if body is None else json.dumps(body, separators=(",", ":")).encode(),
            content_type="application/json" if body is not None else None, expected=expected,
        )
        if self.station_gateway:
            try:
                data = json.loads(response.body, object_pairs_hook=unique_keys)
            except (UnicodeError, json.JSONDecodeError):
                raise FileTransportError("station file response is not valid JSON")                     .with_record(response.record) from None
            if not isinstance(data, dict):
                raise FileTransportError("station file response is not an object")                     .with_record(response.record) from None
            return data, response.record
        return self._decode_json_data(response.body, response.record, sensitive_values=response.sensitive_values)

    def probe_anonymous_route(self) -> tuple[dict, dict]:
        """Reach token validation without a valid capability or Human credentials.

        An invalid token must produce the reviewed BCS not-found envelope. A
        gateway login response (even HTTP 200) is not anonymous route readiness.
        This never follows redirects, reads a real file or mints a share token.
        """
        response = self._send('GET', self._shared_path('e2e-invalid-preflight'),
                              operation='file.anonymous_route_probe',
                              expected=(404,), include_auth=False)
        try:
            payload = self._envelope(response.body)
            if payload['code'] != 40400 or payload['data'].get('error_code') != 'shared_file_not_found':
                raise FileTransportError('anonymous file route did not reach reviewed token validation')
            if not record_response_id(response.record, payload['request_id'], source='ENVELOPE',
                                      sensitive_values=response.sensitive_values):
                raise FileTransportError('anonymous file route returned an invalid request ID')
            return {'status': 'TOKEN_VALIDATION_REACHED', 'file_access': 'NOT_TESTED'}, response.record
        except FileTransportError as error:
            raise error.with_record(response.record)

    def _share_sso_required(self) -> bool:
        return (self.profile["environment"]["tier"] == "preprod"
                and self.profile["entrypoint"]["kind"] == "gateway"
                and set(self.profile["auth"].get("header_env", {})) == {"Cookie"}
                and self.local_auth is None)

    @staticmethod
    def _login_required_projection(raw: bytes) -> dict:
        try:
            payload = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError):
            return {}
        if (not isinstance(payload, dict)
                or payload.get("actionType") != "LOGIN"
                or payload.get("buserviceErrorCode") != "USER_NOT_LOGIN"):
            return {}
        result = {"anonymous_route": "LOGIN_REQUIRED"}
        if payload.get("decisionBy") == "ACE":
            result["decision_by"] = "ACE"
        return result

    def probe_share_login_required(self, token: str) -> tuple[dict, dict]:
        if not self._share_sso_required():
            raise FileTransportError("preprod shared file SSO requires a gateway Cookie profile")
        response = self._send(
            "GET", self._share_url(token),
            operation="file.share_login_required",
            expected=(200, 401, 403), include_auth=False, share=True,
        )
        status = response.record["http_status"]
        if status == 200 and self._login_required_projection(response.body) != {
            "anonymous_route": "LOGIN_REQUIRED", "decision_by": "ACE",
        }:
            raise FileTransportError("no-Cookie share request did not prove the reviewed login denial").with_record(response.record)
        denial_kind = {200: "ACE_LOGIN", 401: "HTTP_401", 403: "HTTP_403"}[status]
        response.record["response_projection"] = {
            "share_access_contract": PREPROD_SHARE_SSO,
            "unauthenticated_access": "DENIED",
            "denial_kind": denial_kind,
        }
        return {"status": "EXPECTED_LOGIN_DENIAL", "file_access": "DENIED"}, response.record

    def shared_download_sso(self, token: str) -> tuple[bytes, dict]:
        if not self._share_sso_required():
            raise FileTransportError("preprod shared file SSO requires a gateway Cookie profile")
        if token not in self.share_targets:
            raise FileTransportError("SSO shared download requires this client's issued share token")
        _, denial = self.probe_share_login_required(token)
        data, record = self._download(self._share_url(token), operation="file.shared_download",
                                      include_auth=True, share=True)
        record["access_checks"] = [denial]
        record["response_projection"] = {
            "share_access_contract": PREPROD_SHARE_SSO,
            "unauthenticated_access": "DENIED",
            "auth_mode": "SSO_AND_SHARE_TOKEN",
        }
        return data, record

    def list(self, session_id: str):
        return self._json("GET", self._path(session_id), operation="file.list")

    def prepare(self, session_id: str, body: dict):
        data, record = self._json("POST", self._path(session_id), operation="file.prepare", body=body, expected=(201,))
        file_id = data.get("file_id")
        if isinstance(file_id, str) and file_id:
            # Defer target checks to upload: the caller must first journal the ID.
            self.prepared[(session_id, file_id)] = {"target": copy.deepcopy(data),
                                                  "size": body.get("size"), "mime_type": body.get("mime_type")}
        return data, record

    def upload(self, session_id: str, file_id: str, content: bytes):
        prepared = self.prepared.get((session_id, file_id))
        if prepared is None:
            raise FileTransportError("upload requires the exact prepared session/file binding")
        if not isinstance(content, bytes) or len(content) > self.max_bytes:
            raise FileTransportError("upload exceeds the small-file runner budget")
        mime = prepared["mime_type"]
        if not isinstance(mime, str) or not mime:
            raise FileTransportError("prepared MIME type is missing")
        parts = upload_parts(prepared["target"], content, prepared["size"])
        expected_path = self._path(session_id, f"/{_segment(file_id)}/content")
        targets = []
        for part in parts:
            # Classify only exact owned proxy routes; all other URLs are storage.
            try:
                proxy = self._same_origin(part.url) and proxy_target(part.url, expected_path, part.part_number)
            except ValueError:
                raise FileTransportError("invalid upload target URL") from None
            url = self._url(part.url, storage=not proxy)
            targets.append((part, url, proxy))
        records = []
        try:
            for part, url, proxy in targets:
                response = self._send("PUT", url, operation="file.upload", body=part.content,
                                           content_type=mime, expected=(202,) if proxy else tuple(range(200, 300)),
                                           include_auth=proxy, storage=not proxy)
                record = response.record
                records.append(record)
                if proxy:
                    data, _ = self._decode_json_data(response.body, record, sensitive_values=response.sensitive_values)
                    if data.get("file_id") != file_id or data.get("status") != "pending":
                        raise FileTransportError("proxy upload response does not match the prepared pending file").with_record(record)
        except FileTransportError as error:
            raise error.with_prior_records(records)
        result = {"file_id": file_id, "upload_accepted": True}
        if all(proxy for _, _, proxy in targets):
            result["status"] = "pending"
        return result, {"operation": "file.upload", "method": "PUT",
                        "path": expected_path if all(proxy for _, _, proxy in targets) else "[storage-targets]",
                        "parts": records, "part_count": len(records),
                        "acceptance": "HTTP acceptance only; Ready is asserted after product complete",
                        "elapsed_ms": round(sum(record["elapsed_ms"] for record in records), 2)}

    def complete(self, session_id: str, file_id: str):
        return self._json("POST", self._path(session_id, f"/{_segment(file_id)}/complete"), operation="file.complete")

    def metadata(self, session_id: str, file_id: str):
        return self._json("GET", self._path(session_id, f"/{_segment(file_id)}"), operation="file.metadata")

    def download(self, session_id: str, file_id: str):
        return self._download(self._path(session_id, f"/{_segment(file_id)}/content"),
                              operation="file.download", include_auth=True)

    def share(self, session_id: str, file_id: str):
        data, record = self._json("POST", self._path(session_id, f"/{_segment(file_id)}/share"),
                                  operation="file.share", body={}, expected=(201,))
        token, target = data.get("share_token"), data.get("share_url")
        if isinstance(token, str) and token and isinstance(target, str) and target:
            self.register_share_target(token, target)
        return data, record

    def register_share_target(self, token: str, target: str) -> None:
        if not isinstance(token, str) or not token:
            raise FileTransportError("share binding requires the issued token")
        url = self._url(target, share=True)
        parts = urlsplit(url)
        if (parts.path != "/sessions/shared-file/content"
                or parse_qsl(parts.query, keep_blank_values=True) != [("token", token)]):
            raise FileTransportError("returned share URL does not match the issued token route")
        self.share_targets[token] = target

    def _share_url(self, token: str) -> str:
        path = self._shared_path(token)
        target = self.share_targets.get(token, self.base + path)
        url = self._url(target, share=True)
        parts = urlsplit(url)
        if (parts.path != urlsplit(path).path or parse_qsl(parts.query, keep_blank_values=True) != [("token", token)]):
            raise FileTransportError("returned share URL does not match the issued token route")
        return target

    def _shared_path(self, token: str) -> str:
        if not isinstance(token, str) or not token:
            raise FileTransportError("shared download requires the issued share token")
        return self.file_path_prefix + "/sessions/shared-file/content?token=" + quote(token, safe="")

    def shared_download(self, token: str):
        if self._share_sso_required():
            return self.shared_download_sso(token)
        return self._download(self._share_url(token), operation="file.shared_download",
                               include_auth=False, share=True)

    @staticmethod
    def _shared_metadata_path(token: str) -> str:
        if not isinstance(token, str) or not token:
            raise FileTransportError("shared metadata requires the issued share token")
        return "/sessions/shared-file?token=" + quote(token, safe="")

    def _shared_metadata_url(self, token: str) -> str:
        target = self.share_targets.get(token)
        if target is None:
            return self._shared_metadata_path(token)
        parts = urlsplit(self._url(target, share=True))
        metadata = parts._replace(path="/sessions/shared-file",
                                  query="token=" + quote(token, safe=""))
        return self._url(metadata.geturl(), share=True)

    def shared_metadata(self, token: str):
        if self._share_sso_required():
            _, denial = self.probe_share_login_required(token)
            response = self._send(
                "GET", self._shared_metadata_url(token),
                operation="file.shared_metadata", include_auth=True, share=True,
            )
            try:
                data = json.loads(response.body, object_pairs_hook=unique_keys)
            except (UnicodeError, json.JSONDecodeError):
                raise FileTransportError("shared metadata response is not valid JSON").with_record(response.record) from None
            if not isinstance(data, dict) or "session_id" in data:
                raise FileTransportError("shared metadata must be a dictionary without session_id").with_record(response.record)
            response.record["access_checks"] = [denial]
            response.record["response_projection"] = {
                "share_access_contract": PREPROD_SHARE_SSO,
                "unauthenticated_access": "DENIED",
                "auth_mode": "SSO_AND_SHARE_TOKEN",
            }
            return data, response.record
        response = self._send(
            "GET", self._shared_metadata_url(token),
            operation="file.shared_metadata", include_auth=False, share=True,
        )
        try:
            data = json.loads(response.body, object_pairs_hook=unique_keys)
        except (UnicodeError, json.JSONDecodeError):
            raise FileTransportError("shared metadata response is not valid JSON").with_record(response.record) from None
        if not isinstance(data, dict) or "session_id" in data:
            raise FileTransportError("shared metadata must be a dictionary without session_id").with_record(response.record)
        return data, response.record

    def shared_metadata_verify_deleted(self, token: str):
        denial = None
        if self._share_sso_required():
            _, denial = self.probe_share_login_required(token)
        response = self._send(
            "GET", self._shared_metadata_url(token),
            operation="file.shared_metadata_verify_deleted",
            expected=(404,), include_auth=self._share_sso_required(), share=True,
        )
        record = response.record
        # Plain exact not-found dict carries no envelope request ID; keep the
        # correlation assessment explicit like the gateway branch.
        record["response_envelope_request_id_absent"] = True
        try:
            data = json.loads(response.body, object_pairs_hook=unique_keys)
        except (UnicodeError, json.JSONDecodeError):
            raise FileTransportError("deleted shared metadata response is not valid JSON").with_record(response.record) from None
        if data != {"error": "NOT_FOUND", "message": "shared file not found"}:
            raise FileTransportError("deleted shared metadata did not reach the exact not-found response").with_record(response.record)
        if denial is not None:
            response.record["access_checks"] = [denial]
            response.record["response_projection"] = {
                "share_access_contract": PREPROD_SHARE_SSO,
                "unauthenticated_access": "DENIED",
                "auth_mode": "SSO_AND_SHARE_TOKEN",
            }
        return None, response.record

    def delete(self, session_id: str, file_id: str):
        data, record = self._json("DELETE", self._path(session_id, f"/{_segment(file_id)}"), operation="file.delete")
        if data.get("deleted") is not True:
            raise FileTransportError("file deletion was not explicitly confirmed").with_record(record)
        return data, record

    def _verify_absent(self, path: str, error_code: str, *, operation: str,
                       include_auth: bool, share: bool = False):
        response = self._send("GET", path, operation=operation, expected=(404,),
                              include_auth=include_auth, share=share)
        record = response.record
        try:
            payload = self._envelope(response.body)
            if not record_response_id(record, payload['request_id'], source='ENVELOPE',
                                      sensitive_values=response.sensitive_values):
                raise FileTransportError('file response request ID is invalid')
            if payload["code"] == 20000 or payload["data"].get("error_code") != error_code:
                raise FileTransportError("not-found response does not prove the exact file outcome")
            return None, record
        except FileTransportError as error:
            raise error.with_record(record)

    def verify_deleted(self, session_id: str, file_id: str):
        path = self._path(session_id, f"/{_segment(file_id)}")
        if self.station_gateway:
            response = self._send("GET", path, operation="file.verify_deleted",
                                  expected=(404,), include_auth=True)
            record = response.record
            # The station legacy face answers with an exact plain dict that
            # carries no envelope request ID. Mark the correlation assessment
            # explicit so absence evidence matches the gateway contract shape
            # instead of silently skipping correlation.
            record["response_envelope_request_id_absent"] = True
            try:
                data = json.loads(response.body, object_pairs_hook=unique_keys)
            except (UnicodeError, json.JSONDecodeError):
                raise FileTransportError("deleted station file did not return exact JSON").with_record(response.record) from None
            if data != {"error": "FILE_NOT_FOUND",
                        "message": f"not found: file {file_id}"}:
                raise FileTransportError("not-found response does not prove the exact file outcome").with_record(response.record)
            return None, response.record
        return self._verify_absent(path, "session_file_not_found",
                                   operation="file.verify_deleted", include_auth=True)

    def verify_shared_deleted(self, token: str):
        if self._share_sso_required():
            path = self._share_url(token)
            response = self._send("GET", path, operation="file.shared_verify_deleted",
                                  expected=(404,), include_auth=True, share=True)
            record = response.record
            # Plain exact not-found share dict carries no envelope request ID;
            # keep the correlation assessment explicit like the gateway branch.
            record["response_envelope_request_id_absent"] = True
            try:
                data = json.loads(response.body, object_pairs_hook=unique_keys)
            except (UnicodeError, json.JSONDecodeError):
                raise FileTransportError("deleted shared content did not return exact JSON").with_record(response.record) from None
            if data != {"error": "NOT_FOUND", "message": "shared file not found"}:
                raise FileTransportError("not-found response does not prove the exact shared outcome").with_record(response.record)
            return None, response.record
        return self._verify_absent(self._share_url(token), "shared_file_not_found",
                                   operation="file.shared_verify_deleted", include_auth=False,
                                   share=True)

    def upload_to_signed_url(self, upload_url: str, content: bytes):
        return self._request("PUT", upload_url, operation="file.signed_upload", body=content,
                             content_type="application/octet-stream", expected=tuple(range(200, 300)), include_auth=False, storage=True)
