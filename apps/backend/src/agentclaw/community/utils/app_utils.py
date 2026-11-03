"""Application identity helpers shared by durable task state."""

from __future__ import annotations

import os

DEFAULT_EXECUTOR_APP = "agentclawengine"
_EXECUTOR_APP_MAX_LENGTH = 32
_APP_NAME_ENV_VAR = "AVERNET_APP_NAME"


def get_current_executor_app() -> str:
    """Return the deployment's application identity for executor scoping.

    The source of truth is the registered configuration provider's top-level
    ``app_name``. Independently deploying ``agentclaw`` and ``agentclawengine``
    under different names therefore scopes durable Relay state even when they
    share one repository and database. A local/test harness without a provider
    may use ``AVERNET_APP_NAME``; with neither source, rows use the archived
    Relay executor default ``agentclawengine``.

    A configured name that cannot be stored faithfully raises instead of being
    silently replaced by another deployment's default name.
    """
    from agentclaw.community.core.config import provider as config_provider

    from_config_provider = config_provider.has_config_provider()
    if from_config_provider:
        from agentclaw.community.core.config import sofa

        app_name = str(getattr(sofa.sofa_config, "app_name", "") or "")
    else:
        app_name = os.getenv(_APP_NAME_ENV_VAR, "")

    if app_name != app_name.strip():
        raise ValueError(
            "app_name has leading or trailing whitespace; it scopes durable "
            "task state and is stored in a padded collation on OceanBase"
        )
    app_name = app_name.strip()
    if not app_name:
        if from_config_provider:
            raise ValueError(
                "app_name is empty; it scopes durable Relay state and must "
                "name the deployment that owns task rows"
            )
        return DEFAULT_EXECUTOR_APP
    if len(app_name) > _EXECUTOR_APP_MAX_LENGTH:
        raise ValueError(
            f"app_name exceeds {_EXECUTOR_APP_MAX_LENGTH} characters "
            f"({len(app_name)}); it scopes durable task state"
        )
    return app_name
