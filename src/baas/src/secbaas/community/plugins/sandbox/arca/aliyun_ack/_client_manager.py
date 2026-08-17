"""Aliyun ACK ApiClient lifecycle manager.

Provides thread-safe lazy-init of a Kubernetes ApiClient for an Aliyun ACK
managed cluster, built from the resolved ``AliyunAckTemplateConfig.cluster``
connection. All Kubernetes SDK calls are isolated behind this factory so unit
tests can inject a mock client.

An ACK cluster exposes a standard Kubernetes API server and ships an inline
``kubeconfig`` (downloadable from the Aliyun console/CLI) for authentication, so
the existing ``kubernetes`` SDK is reused. Construction fails fast when no
kubeconfig is configured.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

from secbaas.community.logger import get_logger

if TYPE_CHECKING:
    from kubernetes.client import ApiClient

logger = get_logger("plugin-sandbox")


class AliyunAckClusterConfigLike(Protocol):
    """Cluster connection surface required to build an ACK ApiClient."""

    endpoint: str
    region: str
    cluster_name: str
    kubeconfig: str
    context: str
    access_key_id: str
    access_key_secret: str


class AliyunAckClientManager:
    """Thread-safe Aliyun ACK ApiClient lifecycle manager.

    Lazily initializes one ApiClient from an inline ACK ``kubeconfig`` and reuses
    it across operations. Construction fails fast when the required kubeconfig
    is missing.
    """

    def __init__(self, cluster: AliyunAckClusterConfigLike | None = None) -> None:
        """Initialize the manager with the given Aliyun ACK cluster config.

        Args:
            cluster: The resolved Aliyun ACK cluster connection.

        Raises:
            ValueError: If the ACK configuration is missing the kubeconfig.
        """
        self._cluster = cluster
        self._lock = threading.Lock()
        self._client: ApiClient | None = None

    def _validate_config(self) -> None:
        """Fail fast on invalid or incomplete ACK configuration.

        Requires an inline ``kubeconfig`` for the ACK cluster.

        Raises:
            ValueError: If no kubeconfig is configured.
        """
        if self._cluster is None or not getattr(self._cluster, "kubeconfig", None):
            raise ValueError(
                "AliyunAckClientManager requires a cluster config with an inline "
                "kubeconfig to connect to the Aliyun ACK cluster"
            )

    def validate(self) -> None:
        """Public validation of the ACK configuration (fail fast)."""
        self._validate_config()

    def build_client(self) -> ApiClient:
        """Build a Kubernetes ApiClient from the ACK inline kubeconfig.

        Mirror of the repo's existing K8s plugin consumption pattern:
        kubeconfig string -> yaml.safe_load ->
        ``new_client_from_config_dict(config_dict, context=..., persist_config=False)``.
        """
        self._validate_config()
        import yaml
        from kubernetes import config as k8s_config

        kubeconfig_dict = yaml.safe_load(self._cluster.kubeconfig)
        return k8s_config.new_client_from_config_dict(
            config_dict=kubeconfig_dict,
            context=self._cluster.context or None,
            persist_config=False,
        )

    def get_client(self) -> ApiClient:
        """Return a lazily-initialized, thread-safe ApiClient (cached)."""
        with self._lock:
            if self._client is None:
                self._client = self.build_client()
                logger.info(
                    "Created new Aliyun ACK ApiClient (cluster=%s)",
                    self._cluster.cluster_name
                    or getattr(self._cluster, "endpoint", None)
                    or "default",
                )
            return self._client

    def close(self) -> None:
        """Close the cached ApiClient connection pool, if any."""
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    logger.warning("Error closing Aliyun ACK ApiClient", exc_info=True)
                self._client = None
                logger.info("AliyunAckClientManager closed its ApiClient")
