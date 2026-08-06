"""AICoding provisioning strategy at the Arca boundary.

Single collection point for every AICoding-owned Arca-side interpretation.
``theta_key`` is the first field; future AICoding credential/runtime fields
land as additional methods (or additional branches inside these methods), not
as new resolver classes — mirroring the backend ``AicodingProvisioningStrategy``.
"""

from __future__ import annotations

from typing import Any

from secbaas.community.core.utils.secret_utils import symmetric_decrypt
from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox.arca import ArcaProvisioningStrategy
from secbaas.community.spi.secret import SecretStorePlugin

_AICODING_NAMESPACE = "aicoding"
_THETA_KEY_PROPERTY = "theta_key"
_THETA_CIPHER_PREFIX = "enc:v1:"
_THETA_SECRET_NAME = "other_manual_aixharness_theta_key"


class AicodingArcaProvisioningStrategy(ArcaProvisioningStrategy):
    """Interpret the AICoding property namespace at the Arca boundary."""

    def __init__(self, secret_plugin: SecretStorePlugin) -> None:
        self._secret_plugin = secret_plugin
        self._logger = get_logger("core-service")

    @property
    def namespace(self) -> str:
        return _AICODING_NAMESPACE

    def resolve_request_api_key(
        self, extra_properties: dict[str, Any] | None
    ) -> str | None:
        if not isinstance(extra_properties, dict):
            return None
        properties = extra_properties.get(_AICODING_NAMESPACE)
        if not isinstance(properties, dict):
            return None
        theta_key = properties.get(_THETA_KEY_PROPERTY)
        if not isinstance(theta_key, str) or not theta_key.strip():
            return None

        theta_key = theta_key.strip()
        if not theta_key.startswith(_THETA_CIPHER_PREFIX):
            self._logger.warning(
                "[arca_credential] fallback=fixed "
                "reason=unsupported_theta_cipher_format"
            )
            return None
        encrypted_payload = theta_key[len(_THETA_CIPHER_PREFIX) :]
        if not encrypted_payload:
            self._logger.warning(
                "[arca_credential] fallback=fixed reason=theta_ciphertext_empty"
            )
            return None

        try:
            _, decrypt_key = self._secret_plugin.get_kv_secret(_THETA_SECRET_NAME)
            if not decrypt_key:
                self._logger.warning(
                    "[arca_credential] fallback=fixed reason=mist_secret_empty"
                )
                return None
            # aixharness ThetaKeyVault format:
            # enc:v1: + base64url(nonce[12] + AES-GCM ciphertext/tag).
            api_key = symmetric_decrypt(encrypted_payload, decrypt_key)
            if not api_key or not api_key.strip():
                self._logger.warning(
                    "[arca_credential] fallback=fixed reason=decrypted_key_empty"
                )
                return None
            self._logger.info("[arca_credential] source=engine_extension")
            return api_key.strip()
        except Exception as exc:
            self._logger.warning(
                "[arca_credential] fallback=fixed reason=resolve_failed error_type=%s",
                type(exc).__name__,
            )
            return None
