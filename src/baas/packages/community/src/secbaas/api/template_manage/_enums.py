"""Device template management enums.

Extracted from api/domain/device_template_manage.py as part of
api/domain → api/{domain_name}/ refactoring.
"""

from enum import StrEnum


class TemplateStatus(StrEnum):
    """Device template status enumeration."""

    CREATED = "CREATED"
    AUDITED = "AUDITED"
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
