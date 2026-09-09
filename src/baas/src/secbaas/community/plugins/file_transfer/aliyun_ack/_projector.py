"""Aliyun ACK session file URL projector.

Projects service-generated presigned OSS URLs to the BaaS-domain
``/api/v1/file-transfer-proxy/`` prefix so aliyun-tenant clients PUT/GET
through the BaaS entry point (gateway auth + nginx streaming proxy)
instead of talking to OSS directly.

The transform is a pure string operation via ``urllib.parse``: the query
component is preserved byte-for-byte and never dict-rebuilt or re-encoded
(D-03 — the OSS V1 signature binds path + query, so any reordering or
re-encoding breaks verification).
"""

from urllib.parse import urlsplit, urlunsplit

from secbaas.community.api.session_file_sharing import (
    SessionFileTransferProxyUnavailableError,
)
from secbaas.community.logger import get_logger
from secbaas.community.spi.file_transfer import SessionFileUrlProjector

logger = get_logger("plugin-file-transfer")

_ALIYUN_TENANT = "aliyun"


class AliyunAckSessionFileUrlProjector(SessionFileUrlProjector):
    """Project session file URLs behind the BaaS domain for the aliyun tenant.

    Guard semantics (D-01 / D-06 / WR-02-89):
    - ``deploy_tenant != "aliyun"`` → WARNING
      ``SESSION_URL_PROJECTOR_PASSTHROUGH`` and the URL is returned
      unchanged — main-site misconfiguration must never alter
      main-site behavior.
    - ``deploy_tenant == "aliyun"`` with an empty ``proxy_base_url``
      → ``SessionFileTransferProxyUnavailableError`` (503): a bare OSS
      URL is never handed out in that tenant.  There is no escape hatch —
      serving bare URLs requires fixing the config and restarting.
    - ``deploy_tenant == "aliyun"`` with a *malformed non-empty*
      ``proxy_base_url`` (no ``http(s)`` scheme or empty authority, e.g.
      ``bff.example.com``) → ``SessionFileTransferProxyUnavailableError``
      **at construction** (container resolution): the config schema's
      ``pattern`` guard only runs when the schema is instantiated with the
      value, and YAML overrides bypass it — so the projector re-checks here
      instead of emitting a relative client URL at first request.
    """

    def __init__(self, proxy_base_url: str, deploy_tenant: str):
        self._proxy_base_url = proxy_base_url.rstrip("/")
        self._deploy_tenant = deploy_tenant
        self._validate_proxy_base_url()

    def _validate_proxy_base_url(self) -> None:
        """Fail closed at construction on malformed aliyun proxy bases.

        An empty base is legal (D-06 request-time 503) and a non-aliyun
        tenant never reads the base (D-01 passthrough) — both rows are
        left untouched.
        """
        base = self._proxy_base_url
        if not base:
            return
        if self._deploy_tenant != _ALIYUN_TENANT:
            return
        parts = urlsplit(base)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise SessionFileTransferProxyUnavailableError(
                reason=(
                    "session_file_url_proxy.proxy_base_url is not a valid "
                    "absolute http(s) URL "
                    f"for deploy_tenant={self._deploy_tenant!r}: {base!r}"
                )
            )

    def project(self, url: str) -> str:
        if self._deploy_tenant != _ALIYUN_TENANT:
            logger.warning(
                "[SESSION_URL_PROJECTOR_PASSTHROUGH] deploy_tenant=%s is not "
                "aliyun — returning the URL unchanged "
                "(main-site misconfiguration immunity)",
                self._deploy_tenant,
            )
            return url

        if not self._proxy_base_url:
            raise SessionFileTransferProxyUnavailableError(
                reason=(
                    "session_file_url_proxy.proxy_base_url is not configured "
                    f"for deploy_tenant={self._deploy_tenant}"
                )
            )

        parts = urlsplit(url)
        proxy = urlsplit(self._proxy_base_url)
        return urlunsplit(
            (
                proxy.scheme,
                proxy.netloc,
                "/api/v1/file-transfer-proxy" + parts.path,
                parts.query,
                "",
            )
        )
