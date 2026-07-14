"""Device template management exceptions.

Extracted from api/domain/device_template_manage.py as part of
api/domain → api/{domain_name}/ refactoring.
"""

from secbaas.community.api import DomainError


class TemplateNotFoundError(DomainError):
    error_code = "TEMPLATE_NOT_FOUND"
    http_status = 404

    def __init__(self, template_uuid: str | int = ""):
        self.template_uuid = template_uuid
        self.message = f"Template with uuid not found: {template_uuid}"
        super().__init__(self.message)


class TemplateByIdNotFoundError(DomainError):
    error_code = "TEMPLATE_BY_ID_NOT_FOUND"
    http_status = 404

    def __init__(self, template_id: int = 0):
        self.template_id = template_id
        self.message = f"Template not found by ID: {template_id}"
        super().__init__(self.message)
