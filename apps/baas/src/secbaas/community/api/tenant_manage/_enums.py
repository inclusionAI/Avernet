"""Tenant management enums.

Extracted from api/domain/tenant_manage.py as part of
api/domain → api/{domain_name}/ refactoring.
"""

from enum import StrEnum


class TenantType(StrEnum):
    """Tenant platform type enumeration."""

    SIGMA = "Sigma"
    ARCA = "ARCA"
    LOCAL = "Local"
    POOLAB = "Poolab"
    TECLAW = "TeClaw"
    K8S = "K8S"  # [D-01] Kubernetes PaaS platform type
    DOCKER = "Docker"


class ImagePullPolicy(StrEnum):
    """Docker image pull policy enumeration."""

    ALWAYS = "always"
    IF_NOT_PRESENT = "if_not_present"
    NEVER = "never"
