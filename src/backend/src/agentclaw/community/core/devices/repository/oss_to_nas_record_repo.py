"""ac_oss_to_nas_record — module retained for backward-compat imports.

The ORM model now lives in ``agentclaw.community.plugin_api.models.OssToNasRecord``.
The Protocol lives in ``agentclaw.community.core.repository.protocols.devices.OssToNasRecordRepository``.
Implementations: ``plugins/{prod,local}/oss_to_nas_record_repository.py``.

This file re-exports ``OssToNasRecord`` for callers that still import
it from the old path; new code should import directly from
``agentclaw.community.plugin_api.models``.
"""
from agentclaw.community.plugin_api.models import OssToNasRecord  # noqa: F401
