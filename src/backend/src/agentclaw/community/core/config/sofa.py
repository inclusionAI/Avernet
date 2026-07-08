"""Module-level config handle for the backend.

``sofa_config`` used to be sofapy's ``Config`` evaluated at import time
(``get_config()``), which forced every core import to resolve the
company-internal ``sofapy_base`` package. It is now a **lazy proxy** over the
:func:`~agentclaw.community.core.config.provider.load_config` registry: the active
provider (corp → sofapy, community/test → YAML) is resolved on first attribute
access, so ``core/`` no longer imports sofapy for configuration. Every consumer
already reads ``sofa_config`` inside a function, so the laziness is safe.

The name is kept (``sofa_config`` / module ``sofa``) to avoid rippling a rename
through ~8 importers; it is no longer sofapy-specific.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.config.provider import load_config
from agentclaw.community.core.devices.models import Env
from agentclaw.community.utils import env_utils


class _LazyAppConfig:
    """Resolves the active :class:`AppConfig` on first attribute access.

    Holds no state of its own; every attribute read forwards to
    ``load_config()`` (cached after the first call), so ``sofa_config.workers``,
    ``.user_config``, ``.model_dump()`` and ``getattr(sofa_config, "bcsfuse",
    None)`` behave exactly as the eager object did.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(load_config(), name)


sofa_config = _LazyAppConfig()
server_env = Env.from_string(env_utils.get_current_env()).value
