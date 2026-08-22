"""Aliyun ACK ApiClient lifecycle manager.

Provides thread-safe lazy-init of a Kubernetes ApiClient for an Aliyun ACK
managed cluster, using bearer token authentication against the API server.
"""

from __future__ import annotations

import threading
import warnings

from kubernetes.client import ApiClient, Configuration

from secbaas.community.logger import get_logger

warnings.filterwarnings("ignore", message="Unverified HTTPS request")
logger = get_logger("plugin-sandbox")


class AliyunAckClusterConfig:
    """Cluster connection config for building an ACK ApiClient."""

    def __init__(self, api_server: str = "", token: str = "", namespace: str = "default") -> None:
        self.api_server = api_server
        self.token = token
        self.namespace = namespace


class AliyunAckClientManager:
    """Thread-safe Aliyun ACK ApiClient lifecycle manager.

    Lazily initializes one ApiClient from ``api_server`` + ``token`` and reuses
    it across operations. Construction fails fast when required fields are missing.
    """

    def __init__(self, cluster: AliyunAckClusterConfig | None = None) -> None:
        self._cluster = cluster
        self._lock = threading.Lock()
        self._client: ApiClient | None = None

    def _validate_config(self) -> None:
        if self._cluster is None or not getattr(self._cluster, "api_server", None):
            raise ValueError(
                "AliyunAckClientManager requires a cluster config with api_server"
            )
        if not getattr(self._cluster, "token", None):
            raise ValueError(
                "AliyunAckClientManager requires a cluster config with token"
            )

    def validate(self) -> None:
        """Public validation of the ACK configuration (fail fast)."""
        self._validate_config()

    def build_client(self) -> ApiClient:
        """Build a Kubernetes ApiClient using bearer token auth."""
        self._validate_config()
        configuration = Configuration(
            host=self._cluster.api_server,
            api_key={"authorization": f"Bearer {self._cluster.token}"},
        )
        configuration.verify_ssl = False
        return ApiClient(configuration)

    def get_client(self) -> ApiClient:
        """Return a lazily-initialized, thread-safe ApiClient (cached)."""
        with self._lock:
            if self._client is None:
                self._client = self.build_client()
                logger.info(
                    "Created new Aliyun ACK ApiClient (api_server=%s)",
                    self._cluster.api_server,
                )
            return self._client

    def close(self) -> None:
        """Close the cached ApiClient connection pool, if any."""
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    logger.warning("Error closing AliyunAck ApiClient", exc_info=True)
                self._client = None
                logger.info("AliyunAckClientManager closed its ApiClient")
