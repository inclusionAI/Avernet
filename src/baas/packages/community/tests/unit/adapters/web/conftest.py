"""Configure the DI container early so module-level imports in test files
(such as ``from secbaas.adapters.web.app import lifespan``) do not trigger
``Selector value is undefined`` during test collection.

Runs at conftest module-import time (before any test module import) so that
by the time ``test_app.py`` / ``test_app_cron.py`` / ``test_paas_facade_router.py``
import ``secbaas.bootstrap.ApplicationContainer`` the config has already been
populated, allowing the ``providers.Selector`` inside ``CoreRepositoryContainer``
to resolve without raising an error.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from secbaas.bootstrap import get_container  # noqa: E402

get_container().config.from_dict(
    {
        "plugins": {
            "database": {
                "plugin_database": os.environ.get("PLUGIN_DATABASE", "ZDAS_ORM"),
            },
        },
    }
)


def iter_api_routes(app_or_router) -> Iterator:
    """Yield all APIRoute objects from an app or router's route tree.

    FastAPI 0.136+ wraps routes included via ``include_router()`` in
    ``_IncludedRouter`` objects that do not expose ``dependant`` directly.
    This helper recursively descends through ``_IncludedRouter`` wrappers
    to yield the actual ``APIRoute`` instances.
    """
    for route in app_or_router.routes:
        if hasattr(route, "dependant"):
            yield route
        elif hasattr(route, "original_router"):
            yield from iter_api_routes(route.original_router)
