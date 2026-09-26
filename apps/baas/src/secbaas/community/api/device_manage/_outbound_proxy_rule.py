"""K8s outbound proxy rule model for Envoy sidecar configuration.

Defines the K8sOutboundProxyRule Pydantic model used to configure
outbound HTTP traffic rewriting rules for the Envoy sidecar proxy.
"""

from pydantic import BaseModel, ConfigDict, Field


class K8sOutboundProxyRule(BaseModel):
    """K8s-specific outbound proxy rule for Envoy sidecar.

    Defines a single URL rewriting rule that the Envoy sidecar applies
    to outbound HTTP traffic. v1 uses url_pattern + rewrite_target only.
    method and headers are v2 reserved fields (PRX-06).
    """

    model_config = ConfigDict(extra="forbid")

    url_pattern: str = Field(
        ...,
        description="URL prefix to match for outbound proxy rewriting (e.g., '/api/v1/')",
    )
    rewrite_target: str = Field(
        ...,
        description="Rewrite target URL prefix (e.g., '/api/v2/')",
    )
    method: str | None = Field(
        default=None,
        description="HTTP method filter (v2 reserved, PRX-06)",
    )
    headers: dict[str, str] | None = Field(
        default=None,
        description="HTTP header match conditions (v2 reserved, PRX-06)",
    )
