"""Publish domain exceptions.

Extracted from domain/model/publish.py as part of api/domain → api/{domain}/ refactoring.
"""

from secbaas.community.api import DomainError


# ==============================================================================
# Publish Domain Exceptions
# ==============================================================================
class PublishNotFoundError(DomainError):
    error_code = "PUBLISH_NOT_FOUND"
    http_status = 404

    def __init__(self, publish_id: str | int = ""):
        self.publish_id = publish_id
        self.message = f"Publish not found or access denied: {publish_id}"
        super().__init__(self.message)


class PublishConflictError(DomainError):
    error_code = "PUBLISH_CONFLICT"
    http_status = 409

    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(message)
