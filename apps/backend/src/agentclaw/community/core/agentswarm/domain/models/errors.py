"""agentswarm-owned error markers — domain-level, zero dependencies.

The D17 isolation gate bans bot_management imports inside
``core/agentswarm/**``, so the retry-classification marker lives here
while the translation from platform quota errors stays at the plugin
edge (``plugins/community/agentswarm/host.py``).
"""
from __future__ import annotations


class NonRetryableProvisioningError(RuntimeError):
    """agentswarm-owned marker: provisioning failed in a way that will
    deterministically recur (e.g. a space quota ceiling), so retrying is
    futile.
    """
