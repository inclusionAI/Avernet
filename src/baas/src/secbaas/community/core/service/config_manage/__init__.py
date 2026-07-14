"""System config management service package — aligns with api/config_manage/."""

from ._system_config_service import (
    DefaultSystemConfigManageService,
    _record_to_response,
)

__all__ = [
    "DefaultSystemConfigManageService",
    "_record_to_response",
]
