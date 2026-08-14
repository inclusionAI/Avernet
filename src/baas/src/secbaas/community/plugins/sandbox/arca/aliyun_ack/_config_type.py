"""Aliyun ACK template (second template kind) configuration.

The Aliyun ACK backend uses a **two-template model**:
- the ARCA **device template** (`baas_device_template`, ``ArcaTemplateConfig``)
  carries exactly the ``ArcaCredentials`` surface (including ``arca_template_id``);
- the **Aliyun ACK template** holds all the Aliyun ACK / Pod runtime config,
  keyed by an ``ALIYUN_ACK_TEMPLATE_xxx`` id.

The ACK template id (``ALIYUN_ACK_TEMPLATE_ID``) is carried in the device
template's ``arca_template_id`` field. When the arca selector is ``aliyun_ack``,
the plugin resolves the ``AliyunAckTemplateConfig`` for that id from
``application.yaml`` (loaded by the DI ``ConfigLoader``) and uses it to build
the Kubernetes client and the Pod spec.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AliyunAckClusterConfig(BaseModel):
    """Aliyun ACK cluster connection. Secret values come from env references,
    never hard-coded."""

    model_config = ConfigDict(extra="allow")

    endpoint: str = Field(default="", description="ACK API server endpoint")
    region: str = Field(default="", description="Aliyun region")
    cluster_name: str = Field(default="", description="ACK cluster name")
    kubeconfig: str = Field(
        default="", description="Inline kubeconfig YAML for cluster authentication"
    )
    context: str = Field(default="", description="kubeconfig context name")
    access_key_id: str = Field(default="", description="RAM access key id (secret ref)")
    access_key_secret: str = Field(
        default="", description="RAM access key secret (secret ref)"
    )


class AliyunAckPodConfig(BaseModel):
    """Per-template Pod runtime configuration."""

    model_config = ConfigDict(extra="allow")

    image: str = Field(default="ubuntu:22.04", description="Container image")
    namespace: str = Field(default="default", description="Target namespace")
    service_account: str = Field(default="", description="Pod ServiceAccount")
    cpu_request: str = Field(default="", description="CPU request (e.g. 500m)")
    cpu_limit: str = Field(default="", description="CPU limit (e.g. 1)")
    memory_request: str = Field(default="", description="Memory request (e.g. 512Mi)")
    memory_limit: str = Field(default="", description="Memory limit (e.g. 1Gi)")
    storage_class: str = Field(default="", description="PVC storage class")
    envs: dict[str, str] = Field(
        default_factory=dict, description="Environment variables for the Pod"
    )


class AliyunAckTemplateConfig(BaseModel):
    """One Aliyun ACK template entry, keyed by ``ALIYUN_ACK_TEMPLATE_xxx``."""

    model_config = ConfigDict(extra="allow")

    template_id: str = Field(
        ...,
        description="Aliyun ACK template id (value of the device's arca_template_id)",
    )
    cluster: AliyunAckClusterConfig = Field(
        default_factory=AliyunAckClusterConfig, description="Cluster connection"
    )
    pod: AliyunAckPodConfig = Field(
        default_factory=AliyunAckPodConfig, description="Pod runtime spec"
    )


def build_aliyun_ack_template(template_id: str, raw: dict) -> AliyunAckTemplateConfig:
    """Build an ``AliyunAckTemplateConfig`` from a raw DI config dict.

    The DI ``Configuration`` yields plain dicts for the
    ``aliyun_ack_templates`` block; this helper validates/coerces them into the
    typed model for the plugin.
    """
    body = dict(raw or {})
    body.pop("template_id", None)
    return AliyunAckTemplateConfig(template_id=template_id, **body)
