"""Common domain types for the packages tree (secbaas.* import convention)."""


class DomainError(Exception):
    """Base domain error for the SecBaaS platform.

    All domain-specific exception classes inherit from this.
    """

    error_code: str = "DOMAIN_ERROR"
    http_status: int = 500

    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(message)