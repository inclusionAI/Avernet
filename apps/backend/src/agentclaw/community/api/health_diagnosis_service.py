"""Service API for Bot health diagnosis.

Re-export only. The Protocol is defined in its owning core module
(``core/harness/health_diagnosis_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.harness.health_diagnosis_service_protocol import (
    HealthDiagnosisServiceProtocol,
)

__all__ = [
    "HealthDiagnosisServiceProtocol",
]
