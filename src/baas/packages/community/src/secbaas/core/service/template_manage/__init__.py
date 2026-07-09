"""Device template management service package — aligns with api/template_manage/."""

from ._device_template_service import (
    DefaultDeviceTemplateService,
    _ensure_api_key_encrypted,
    _record_to_response,
)

__all__ = [
    "DefaultDeviceTemplateService",
    "_ensure_api_key_encrypted",
    "_record_to_response",
]
