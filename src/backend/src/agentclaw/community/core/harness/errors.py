"""Domain errors for public health diagnosis orchestration."""


class HealthDiagnosisError(Exception):
    """Base class for health diagnosis failures."""


class HealthDiagnosisConflictError(HealthDiagnosisError):
    """A recent diagnosis is already running for the Bot."""


class HealthDiagnosisNotFoundError(HealthDiagnosisError):
    """The requested diagnosis is absent or outside the authorized Bot scope."""


class HealthDiagnosisUnavailableError(HealthDiagnosisError):
    """Diagnosis persistence is temporarily unavailable."""


__all__ = [
    "HealthDiagnosisConflictError",
    "HealthDiagnosisError",
    "HealthDiagnosisNotFoundError",
    "HealthDiagnosisUnavailableError",
]
