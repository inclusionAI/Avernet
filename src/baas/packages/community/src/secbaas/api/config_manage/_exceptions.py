"""System config management exceptions.

Extracted from api/domain/system_config_manage.py as part of
api/domain → api/{domain_name}/ refactoring.
"""

from secbaas.api import DomainError


class SystemConfigNotFoundError(DomainError):
    error_code = "CONFIG_NOT_FOUND"
    http_status = 404

    def __init__(self, conf_key: str = ""):
        self.conf_key = conf_key
        self.message = f"System config not found: {conf_key}"
        super().__init__(self.message)
