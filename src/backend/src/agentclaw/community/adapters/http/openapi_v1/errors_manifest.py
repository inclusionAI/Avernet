"""Error→(status, fixed message) rows for the config-manifest surface (W1, #1469).

Kept in its own module and merged into ``responses.ENVELOPE_ERRORS`` with a
``**`` — the giant single map in ``responses.py`` sits at the architecture
line cap, so a group that needs rows brings them instead of growing the map
(the skill-center / space-skill modules already do exactly this).

``ManifestInvalidError`` carries the per-entry violation list; the map entry
is only the map-of-last-resort — the manifest PUT handler renders that error
with its structured ``data`` itself. ``ManifestDisabledError`` is the dark
launch (404) answer.
"""
from __future__ import annotations

from agentclaw.community.adapters.http.openapi_v1.errors import ManifestDisabledError
from agentclaw.community.core.bot_config_manifest.manifest_schema import (
    ManifestInvalidError,
)

CONFIG_MANIFEST_ENVELOPE_ERRORS: dict[type[Exception], tuple[int, str]] = {
    ManifestInvalidError: (422, "Config manifest violates schema v1"),
    ManifestDisabledError: (404, "Not found"),
}
