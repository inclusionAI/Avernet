"""Lightweight Aliyun KMS HTTP client.

Replaces the heavyweight ``alibabacloud-kms20160120`` SDK (which drags in the
entire ``alibabacloud-tea-openapi`` / ``alibabacloud-credentials`` chain and an
incompatible ``aiofiles<25`` pin) with a direct call to the Aliyun KMS RPC
endpoint. Only the ``GetSecretValue`` action that the plugin requires is
implemented. Signing uses Aliyun's RPC signature (HMAC-SHA1) with nothing but
the Python standard library plus ``httpx`` for transport.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from urllib.parse import quote

import httpx

_KMS_API_VERSION = "2016-01-20"


def _pct_encode(value: str) -> str:
    """Percent-encode per Aliyun RPC spec (uppercase hex, keep unreserved)."""
    return quote(str(value), safe="")


class KmsError(RuntimeError):
    """Raised when Aliyun KMS rejects or reports an error for a request."""


@dataclass
class KmsGetSecretValueRequest:
    """Minimal stand-in for the SDK's ``GetSecretValueRequest``."""

    secret_name: str


def _sign(access_key_secret: str, string_to_sign: str) -> str:
    signing_key = f"{access_key_secret}&"
    digest = hmac.new(
        signing_key.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


class KmsClient:
    """Thin Aliyun KMS client that calls the public RPC endpoint directly.

    Args:
        access_key_id: Aliyun AccessKey ID.
        access_key_secret: Aliyun AccessKey Secret.
        endpoint: Full KMS endpoint host (e.g. ``kms.cn-hangzhou.aliyuncs.com``).
            If unset, derives ``kms.<region_id>.aliyuncs.com``.
        region_id: Aliyun region the secret lives in.
        timeout: Per-request HTTP timeout in seconds.
    """

    def __init__(
        self,
        *,
        access_key_id: str,
        access_key_secret: str,
        endpoint: str,
        region_id: str,
        timeout: float = 10.0,
    ) -> None:
        if not endpoint:
            endpoint = f"kms.{region_id}.aliyuncs.com"
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._endpoint = endpoint
        self._region_id = region_id
        self._timeout = timeout

    def get_secret_value(self, request: KmsGetSecretValueRequest) -> object:
        """Fetch a plain secret value from KMS.

        Returns:
            An object with a ``secret_data`` attribute, mirroring the SDK
            response shape the plugin consumes.

        Raises:
            KmsError: If the KMS request fails (HTTP/network/response).
        """
        params = {
            "Action": "GetSecretValue",
            "Version": _KMS_API_VERSION,
            "SecretName": request.secret_name,
        }
        body = self._call(params)
        secret_data = body.get("SecretData")
        if secret_data is None:
            raise KmsError(f"KMS secret {request.secret_name} was not found")
        return _SecretValueResult(secret_data)

    def _call(self, params: dict[str, str]) -> dict[str, object]:
        signed = self._signed_params(params)
        url = f"https://{self._endpoint}/"
        try:
            response = httpx.get(url, params=signed, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise KmsError(f"KMS request error: {exc}") from exc

        if response.status_code != 200:
            raise KmsError(f"KMS http error {response.status_code}: {response.text}")
        try:
            body = response.json()
        except ValueError as exc:
            raise KmsError(f"KMS invalid json response: {exc}") from exc
        if not isinstance(body, dict):
            raise KmsError("KMS response is not an object")
        error = body.get("Code")
        if error and error != "OK":
            message = body.get("Message", error)
            raise KmsError(f"KMS error {error}: {message}")
        return body

    def _signed_params(self, params: dict[str, str]) -> dict[str, str]:
        signed: dict[str, str] = {
            "AccessKeyId": self._access_key_id,
            "Action": params["Action"],
            "Format": "JSON",
            "RegionId": self._region_id,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": str(uuid.uuid4()),
            "SignatureVersion": "1.0",
            "Timestamp": _utcnow_rfc3339(),
            "Version": params["Version"],
        }
        if "SecretName" in params:
            signed["SecretName"] = params["SecretName"]

        canonical = "&".join(
            f"{_pct_encode(k)}={_pct_encode(v)}" for k, v in sorted(signed.items())
        )
        string_to_sign = f"GET&{_pct_encode('/')}&{_pct_encode(canonical)}"
        signed["Signature"] = _sign(self._access_key_secret, string_to_sign)
        return signed


@dataclass
class _SecretValueResult:
    secret_data: str


def _utcnow_rfc3339() -> str:
    import datetime

    now = datetime.datetime.now(datetime.UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")
